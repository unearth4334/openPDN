"""Checkpoint, resume, and graceful cancellation of Reference runs.

ADR-0015 §9: a Reference run that completed three of six passes holds
genuinely useful, already-paid-for work. These tests pin the mechanics that
preserve it -- the checkpoint round-trip, resumption from a pass boundary,
signature validation, and the loop's cooperative stop -- and one exactness
property that makes the whole design sound: because re-meshing is
deterministic, a resumed run continues the *same* computation, not a similar
one.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from openpdn.application.simulation_models import (
    AccuracyProfile,
    LoadSpec,
    ReferencePolicy,
    ResolvedMeshSpec,
    SimulationJobSpec,
    SimulationKind,
)
from openpdn.domain.provenance import Quantity
from openpdn.domain.study import (
    AnalysisStudy,
    AttachmentGroup,
    CurrentLoad,
    LoadId,
    MeshSettings,
    SourceId,
    StudyId,
    VoltageSource,
)
from openpdn.domain.units import AMPERE, METRE, VOLT
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer
from openpdn.infrastructure.simulation_artifacts import FilesystemArtifactStore
from openpdn.infrastructure.simulation_worker import _load_checkpoint, _save_checkpoint
from openpdn.solver.fem.adaptive import (
    AdaptivePolicy,
    AdaptiveResume,
    AdaptiveStatus,
    solve_adaptive,
)
from tests.validation.boards import NET, plane_neck_plane_board

_JOB_ID = "job-0123456789abcdef"


def _study() -> AnalysisStudy:
    board = plane_neck_plane_board()
    return AnalysisStudy(
        id=StudyId("ckpt"),
        name="ckpt",
        board_id=str(board.id),
        net_ids=(NET,),
        sources=(
            VoltageSource(
                id=SourceId("src"),
                attachment=AttachmentGroup(terminal_ids=("term-a",)),  # type: ignore[arg-type]
                voltage=Quantity.configured(0.0, VOLT),
            ),
        ),
        loads=(
            CurrentLoad(
                id=LoadId("load"),
                attachment=AttachmentGroup(terminal_ids=("term-b",)),  # type: ignore[arg-type]
                current=Quantity.configured(1.0, AMPERE),
            ),
        ),
        mesh=MeshSettings(
            target_element_size=Quantity.configured(1.0e-3, METRE),
            elements_across_feature=4,
            growth_rate=0.7,
        ),
    )


def _spec(signature: str = "sig-checkpoint") -> SimulationJobSpec:
    return SimulationJobSpec(
        job_id=_JOB_ID,
        name="ckpt",
        kind=SimulationKind.IR_DROP,
        board_id="board-1",
        board_digest="digest-1",
        board_name="board",
        net_id=str(NET),
        net_name="DUT",
        source_terminal_ids=("term-a",),
        source_via_ids=(),
        source_voltage_v=0.0,
        loads=(LoadSpec(current_a=1.0, terminal_ids=("term-b",)),),
        to_terminal_ids=(),
        to_via_ids=(),
        accuracy=AccuracyProfile.REFERENCE,
        mesh=ResolvedMeshSpec(
            max_element_m=1e-3,
            min_element_m=1e-5,
            elements_across_feature=4,
            growth_rate=0.7,
        ),
        verify_convergence=False,
        via_plating_m=None,
        solver_name="fem-2p5d",
        created_at_epoch_s=1000.0,
        signature=signature,
        reference_policy=ReferencePolicy(),
    )


@pytest.fixture
def store(tmp_path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path)


@pytest.fixture(scope="module")
def four_pass_run():
    board = plane_neck_plane_board()
    captured: list[AdaptiveResume] = []
    outcome = solve_adaptive(
        board,
        _study(),
        ShapelyGeometryNormalizer(),
        AdaptivePolicy(target_qoi_rel_change=1e-9, max_passes=4, max_dofs=400_000),
        on_generation=captured.append,
    )
    return board, outcome, captured


class TestCheckpointRoundTrip:
    def test_a_saved_pass_boundary_reloads_identically(self, store, four_pass_run):
        _, _, captured = four_pass_run
        state = captured[1]
        _save_checkpoint(store, _spec(), state)
        loaded = _load_checkpoint(store, _spec())
        assert loaded is not None
        assert loaded.generations == state.generations
        assert loaded.streak == state.streak
        assert np.array_equal(loaded.field.points, state.field.points)
        assert np.array_equal(loaded.field.sizes, state.field.sizes)

    def test_a_checkpoint_for_a_different_signature_is_discarded(self, store, four_pass_run):
        # The signature hashes every solver-affecting input; a mismatch means
        # the stored state belongs to a different computation and resuming
        # from it would quietly produce answers for the wrong question.
        _, _, captured = four_pass_run
        _save_checkpoint(store, _spec(signature="sig-old"), captured[0])
        assert _load_checkpoint(store, _spec(signature="sig-new")) is None
        assert store.load_checkpoint_dir(_JOB_ID) is None  # actively deleted

    def test_a_corrupt_checkpoint_is_discarded_not_fatal(self, store):
        directory = store.checkpoint_dir(_JOB_ID)
        (directory / "checkpoint.json").write_text("{ not json")
        assert _load_checkpoint(store, _spec()) is None

    def test_no_checkpoint_means_none(self, store):
        assert _load_checkpoint(store, _spec()) is None

    def test_checkpoints_survive_the_stale_working_sweep(self, store, four_pass_run):
        # The whole reason checkpoints live outside `results/`: the sweep
        # that cleans crashed workers' partial artifacts on orchestrator
        # start must not destroy the state that makes the crashed run
        # resumable.
        _, _, captured = four_pass_run
        _save_checkpoint(store, _spec(), captured[0])
        store.working_dir(_JOB_ID)  # a crashed worker's leftover
        store.cleanup_stale_working()
        assert _load_checkpoint(store, _spec()) is not None


class TestResume:
    def test_a_resumed_run_continues_the_same_computation(self, four_pass_run):
        # The exactness property the design rests on: determinism means a
        # resume from pass k reproduces passes k+1..N bit-for-bit, so a
        # crash-and-requeue changes nothing about the published answer.
        board, full, captured = four_pass_run
        resumed = solve_adaptive(
            board,
            _study(),
            ShapelyGeometryNormalizer(),
            AdaptivePolicy(target_qoi_rel_change=1e-9, max_passes=4, max_dofs=400_000),
            resume=captured[1],  # two generations done, resume at pass 2
        )
        assert len(resumed.generations) == len(full.generations)
        for ours, theirs in zip(resumed.generations, full.generations, strict=True):
            assert ours.dof_count == theirs.dof_count
            assert ours.quantity_of_interest == theirs.quantity_of_interest
        assert resumed.status == full.status

    def test_a_checkpoint_already_at_the_pass_ceiling_still_yields_a_result(
        self, four_pass_run
    ):
        # Edge: the worker died after the final pass boundary was saved but
        # before publishing. The resumed run has nothing left to iterate --
        # it must still produce a result and fields (by re-solving the last
        # generation's deterministic mesh), not crash or return nothing.
        board, _, captured = four_pass_run
        outcome = solve_adaptive(
            board,
            _study(),
            ShapelyGeometryNormalizer(),
            AdaptivePolicy(target_qoi_rel_change=1e-9, max_passes=2, max_dofs=400_000),
            resume=captured[1],  # already holds two generations
        )
        assert outcome.result is not None
        assert outcome.field_data is not None
        assert outcome.status == AdaptiveStatus.RESOURCE_LIMITED


class TestCooperativeStop:
    def test_a_stop_request_ends_the_run_as_cancelled_partial(self):
        board = plane_neck_plane_board()
        calls = {"count": 0}

        def stop_after_second_boundary() -> bool:
            calls["count"] += 1
            return calls["count"] >= 2

        outcome = solve_adaptive(
            board,
            _study(),
            ShapelyGeometryNormalizer(),
            AdaptivePolicy(target_qoi_rel_change=1e-9, max_passes=6, max_dofs=400_000),
            should_stop=stop_after_second_boundary,
        )
        assert outcome.status == AdaptiveStatus.CANCELLED_PARTIAL
        assert not outcome.converged
        assert len(outcome.generations) >= 1  # what existed is kept

    def test_a_time_budget_stops_the_run_as_resource_limited(self):
        board = plane_neck_plane_board()
        outcome = solve_adaptive(
            board,
            _study(),
            ShapelyGeometryNormalizer(),
            AdaptivePolicy(
                target_qoi_rel_change=1e-9,
                max_passes=6,
                max_dofs=400_000,
                max_seconds=1e-9,  # expires at the first boundary
            ),
        )
        assert outcome.status == AdaptiveStatus.RESOURCE_LIMITED
        assert len(outcome.generations) == 1


class TestInlineReference:
    def test_the_cli_inline_path_runs_the_adaptive_loop(self):
        # The inline (no-queue) path had the same silent-degradation hole
        # the queued path had: a Reference spec quietly solved as one fixed
        # mesh. The returned history is the proof the loop actually ran.
        from openpdn.infrastructure.simulation_worker import run_inline

        board = plane_neck_plane_board()
        result, fields, history = run_inline(
            _spec(), board, ShapelyGeometryNormalizer()
        )
        assert result is not None
        assert fields is not None
        assert history is not None
        assert len(history["generations"]) >= 2  # it refined, not one solve
        assert history["status"] in {
            AdaptiveStatus.CONVERGED,
            AdaptiveStatus.CONVERGED_WITH_MODEL_LIMITATIONS,
            AdaptiveStatus.RESOURCE_LIMITED,
        }

    def test_a_fixed_mesh_spec_returns_no_history(self):
        from dataclasses import replace

        from openpdn.infrastructure.simulation_worker import run_inline

        board = plane_neck_plane_board()
        spec = replace(
            _spec(), accuracy=AccuracyProfile.STANDARD, reference_policy=None
        )
        _, _, history = run_inline(spec, board, ShapelyGeometryNormalizer())
        assert history is None


class TestCheckpointFileFormat:
    def test_the_stored_form_is_plain_versioned_json(self, store, four_pass_run):
        # No pickles, ever: persisted state is data. A version field is what
        # lets a future format change refuse old files instead of
        # misreading them.
        _, _, captured = four_pass_run
        _save_checkpoint(store, _spec(), captured[0])
        payload = json.loads(
            (store.checkpoint_dir(_JOB_ID) / "checkpoint.json").read_text()
        )
        assert payload["schema"] == 1
        assert payload["signature"] == "sig-checkpoint"
        assert isinstance(payload["generations"], list)
