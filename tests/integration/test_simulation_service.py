"""Estimate and queue, through the real service, for every accuracy profile.

This file exists because of a bug it would have caught. `AccuracyProfile`
gained `REFERENCE`, and nothing added a matching entry to the accuracy
resolver -- so `SimulationService.plan` raised `KeyError` for every Reference
job. The adaptive loop, the worker branch and the job semantics around it
were all tested individually and all passed; nothing exercised the path a
real request takes, so the tier was unreachable end-to-end and looked fine.

The lesson generalises: profiles are parameterised over here deliberately, so
adding one without wiring it fails immediately rather than at the first user
request.
"""

from __future__ import annotations

import time

import pytest

from openpdn.application.board_store import StoredBoard
from openpdn.application.simulation_models import (
    AccuracyProfile,
    LoadSpec,
    ReferencePolicy,
    ReferenceTier,
    SimulationDraft,
    SimulationKind,
    SimulationRequestError,
    WorkerLimits,
)
from openpdn.application.simulation_service import SimulationService
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer
from openpdn.infrastructure.board_store import InMemoryBoardStore
from openpdn.infrastructure.fem_planner import FemSimulationPlanner
from openpdn.infrastructure.job_store_sqlite import SqliteJobStore
from openpdn.infrastructure.simulation_artifacts import FilesystemArtifactStore
from openpdn.pcb_import.api import ImportResult
from tests.validation.boards import NET, plane_neck_plane_board

_BOARD_ID = "board-under-test"


@pytest.fixture
def service(tmp_path):
    board = plane_neck_plane_board()
    normalizer = ShapelyGeometryNormalizer()
    boards = InMemoryBoardStore()
    boards.put(
        StoredBoard(
            board_id=_BOARD_ID,
            source_name="synthetic",
            stored_at_epoch_s=time.time(),
            import_result=ImportResult(board=board),
            normalized=normalizer.normalize(board),
            normalize_seconds=0.0,
        )
    )
    return SimulationService(
        boards=boards,
        jobs=SqliteJobStore(tmp_path / "jobs.sqlite"),
        artifacts=FilesystemArtifactStore(tmp_path / "artifacts"),
        planner=FemSimulationPlanner(),
        limits=WorkerLimits(
            max_dofs=5_000_000,
            max_concurrent_jobs=1,
            max_job_seconds=1800.0,
            lease_seconds=60.0,
            max_attempts=3,
            max_reference_passes=24,
            max_reference_dofs=8_000_000,
        ),
        solver_name="fem-2p5d",
        solver_version="0.1.0",
        board_to_document_json=lambda _: "{}",
    )


def _draft(accuracy: AccuracyProfile, **overrides) -> SimulationDraft:
    base = {
        "kind": SimulationKind.IR_DROP,
        "board_id": _BOARD_ID,
        "net_id": str(NET),
        "accuracy": accuracy,
        "source_terminal_ids": ("term-a",),
        "source_voltage_v": 1.0,
        "loads": (LoadSpec(current_a=1.0, terminal_ids=("term-b",)),),
        "reference_policy": ReferencePolicy() if accuracy.is_adaptive else None,
    }
    return SimulationDraft(**{**base, **overrides})


class TestEveryProfilePlans:
    @pytest.mark.parametrize("accuracy", list(AccuracyProfile))
    def test_a_draft_of_each_profile_can_be_planned(self, service, accuracy):
        # The regression guard. Every enum member must survive the real
        # planning path, not merely exist.
        plan = service.plan(_draft(accuracy))
        assert plan.resolved_spec.accuracy is accuracy
        assert plan.estimate.dofs > 0

    @pytest.mark.parametrize("accuracy", list(AccuracyProfile))
    def test_a_draft_of_each_profile_can_be_queued(self, service, accuracy):
        queued = service.queue(_draft(accuracy))
        assert queued.job.spec.accuracy is accuracy


class TestReferencePlanning:
    def test_the_policy_is_carried_into_the_spec(self, service):
        policy = ReferencePolicy(max_passes=3, target_qoi_rel_change=1e-4)
        plan = service.plan(_draft(AccuracyProfile.REFERENCE, reference_policy=policy))
        spec = plan.resolved_spec
        assert spec.reference_policy == policy

    def test_the_starting_mesh_is_coarser_than_verification(self, service):
        # Reference resolves only where the adaptive loop *starts*; spending
        # DOFs uniformly up front is what adaptivity exists to avoid.
        reference = service.plan(_draft(AccuracyProfile.REFERENCE)).resolved_spec
        verification = service.plan(_draft(AccuracyProfile.VERIFICATION)).resolved_spec
        assert reference.mesh.max_element_m > verification.mesh.max_element_m

    def test_reference_does_not_also_run_the_fixed_verification_pass(self, service):
        # The adaptive loop performs its own, stronger convergence check; the
        # sqrt(2) comparison would double the cost to answer it again.
        assert service.plan(_draft(AccuracyProfile.REFERENCE)).resolved_spec.verify_convergence is (
            False
        )

    def test_admission_size_comes_from_the_policy_ceiling(self, service):
        # The achieved size is unknowable in advance, so scheduling reasons
        # about the worst case the policy allows.
        policy = ReferencePolicy(max_dofs=321_000)
        plan = service.plan(_draft(AccuracyProfile.REFERENCE, reference_policy=policy))
        spec = plan.resolved_spec
        assert spec.estimated_dofs == 321_000

    def test_a_fixed_mesh_profile_is_sized_by_its_estimate(self, service):
        plan = service.plan(_draft(AccuracyProfile.STANDARD))
        assert plan.resolved_spec.estimated_dofs == plan.estimate.dofs

    def test_the_policy_changes_the_signature(self, service):
        first = service.plan(_draft(AccuracyProfile.REFERENCE)).resolved_spec.signature
        second = service.plan(
            _draft(AccuracyProfile.REFERENCE, reference_policy=ReferencePolicy(max_passes=2))
        ).resolved_spec.signature
        assert first != second


class TestServerSideRefusals:
    def test_a_policy_over_the_pass_ceiling_is_refused(self, service):
        with pytest.raises(SimulationRequestError, match="above the configured maximum"):
            service.plan(
                _draft(AccuracyProfile.REFERENCE, reference_policy=ReferencePolicy(max_passes=99))
            )

    def test_a_policy_over_the_dof_ceiling_is_refused(self, service):
        with pytest.raises(SimulationRequestError, match="above the configured maximum"):
            service.plan(
                _draft(
                    AccuracyProfile.REFERENCE,
                    reference_policy=ReferencePolicy(max_dofs=99_000_000),
                )
            )

    def test_reference_without_a_policy_is_refused(self, service):
        with pytest.raises(SimulationRequestError, match="adaptive policy"):
            service.plan(_draft(AccuracyProfile.REFERENCE, reference_policy=None))

    def test_a_fixed_mesh_profile_carrying_a_policy_is_refused(self, service):
        with pytest.raises(SimulationRequestError, match="cannot carry an adaptive policy"):
            service.plan(_draft(AccuracyProfile.STANDARD, reference_policy=ReferencePolicy()))


class TestTierThroughTheApi:
    """The HTTP request model's tier-plus-override resolution."""

    def test_a_tier_seeds_the_policy(self):
        from openpdn.api.routes.simulation import ReferencePolicyRequest

        resolved = ReferencePolicyRequest(tier="high").to_policy()
        assert resolved == ReferencePolicy.for_tier(ReferenceTier.HIGH)

    def test_an_explicit_field_overrides_its_tier_value(self):
        from openpdn.api.routes.simulation import ReferencePolicyRequest

        resolved = ReferencePolicyRequest(tier="low", max_dofs=750_000).to_policy()
        expected_base = ReferencePolicy.for_tier(ReferenceTier.LOW)
        assert resolved.max_dofs == 750_000
        assert resolved.target_qoi_rel_change == expected_base.target_qoi_rel_change
        assert resolved.confirmations == expected_base.confirmations

    def test_no_tier_and_no_fields_means_medium(self):
        from openpdn.api.routes.simulation import ReferencePolicyRequest

        assert ReferencePolicyRequest().to_policy() == ReferencePolicy.for_tier(
            ReferenceTier.MEDIUM
        )

    def test_every_tier_queues_through_the_real_service(self, service):
        for tier in ReferenceTier:
            draft = _draft(
                AccuracyProfile.REFERENCE,
                reference_policy=ReferencePolicy.for_tier(tier),
            )
            queued = service.queue(draft)
            assert queued.job.spec.reference_policy == ReferencePolicy.for_tier(tier)
