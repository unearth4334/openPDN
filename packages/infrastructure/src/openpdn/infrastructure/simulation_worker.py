"""Execution of one claimed simulation job, in an isolated process.

The orchestrator spawns `openpdn solver-worker --job-id X --worker-id Y` for
each claimed job; this module is what that command runs. Isolation gives
clean cancellation (the orchestrator kills the process), memory recovery and
crash containment (ADR-0011).

Failure classification (spec'd retry policy): a failure the worker *reports*
-- invalid geometry, disconnected terminals, meshing failure, missing
physical properties, a singular system -- is terminal and never retried
automatically; only a worker that dies silently (crash, OOM kill, host
reboot) leaves an expired lease behind, and only those are requeued by the
orchestrator, below the attempt cap.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from openpdn.application.accuracy import VERIFICATION_REFINEMENT_FACTOR, refine_mesh_spec
from openpdn.application.simulation_models import (
    JobRecord,
    JobState,
    ResultQuality,
    SimulationJobSpec,
    SimulationKind,
)
from openpdn.application.version import get_version
from openpdn.domain.provenance import Quantity
from openpdn.domain.results import DiagnosticSeverity
from openpdn.domain.study import (
    AnalysisStudy,
    AttachmentGroup,
    CurrentLoad,
    LoadId,
    MeshSettings,
    ProbeId,
    ResistanceProbe,
    SourceId,
    StudyId,
    VoltageSource,
)
from openpdn.domain.units import AMPERE, METRE, VOLT
from openpdn.solver.api import SolverError
from openpdn.solver.fem import SOLVER_VERSION, FemFieldData, FemSheetSolver
from openpdn.solver.fem.adaptive import (
    AdaptiveOutcome,
    AdaptivePolicy,
    AdaptiveResume,
    AdaptiveStatus,
    Generation,
    solve_adaptive,
)
from openpdn.solver.fem.controls import RefinementField
from openpdn.solver.fem.solver import PROBE_TEST_CURRENT_A

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from openpdn.application.simulation_ports import JobStore, SimulationArtifactStore
    from openpdn.domain.board import Board, NetId, TerminalId
    from openpdn.domain.results import ElectricalAnalysisResult
    from openpdn.geometry.api import GeometryNormalizer

    #: Decodes a persisted canonical board document: (json, source name, digest).
    BoardDecoder = Callable[[str, str, str], Board]

_logger = logging.getLogger(__name__)

#: Fraction of the lease after which the heartbeat renews it.
_HEARTBEAT_FRACTION = 1.0 / 3.0

#: Relative change in engineering quantities below which the Verification
#: comparison reports convergence.
_CONVERGENCE_TARGET = 0.01


class _Heartbeat:
    """Background lease renewal while the job runs."""

    def __init__(self, jobs: JobStore, job_id: str, worker_id: str, lease_s: float) -> None:
        self._jobs = jobs
        self._job_id = job_id
        self._worker_id = worker_id
        self._lease_s = lease_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        interval = max(1.0, self._lease_s * _HEARTBEAT_FRACTION)
        while not self._stop.wait(interval):
            self._jobs.renew_lease(self._job_id, self._worker_id, self._lease_s)


def run_job(
    *,
    job_id: str,
    worker_id: str,
    jobs: JobStore,
    artifacts: SimulationArtifactStore,
    lease_seconds: float,
    normalizer: GeometryNormalizer,
    board_decoder: BoardDecoder,
    time_budget_seconds: float | None = None,
) -> int:
    """Execute one claimed job to a terminal state. Returns an exit code."""
    record = jobs.get(job_id)
    if record is None:
        _logger.error("worker.unknown_job", extra={"event": "worker.unknown_job", "job_id": job_id})
        return 2
    if record.state is not JobState.CLAIMED or record.claimed_by != worker_id:
        _logger.error(
            "worker.not_mine",
            extra={"event": "worker.not_mine", "job_id": job_id, "state": record.state.value},
        )
        return 2

    jobs.transition(job_id, JobState.RUNNING)
    with _Heartbeat(jobs, job_id, worker_id, lease_seconds):
        try:
            summary = _execute(
                record, jobs, artifacts, normalizer, board_decoder, time_budget_seconds
            )
        except SolverError as exc:
            # Numerical/configuration failure: terminal, diagnose don't retry.
            artifacts.discard_working(job_id)
            artifacts.discard_checkpoint(job_id)
            jobs.transition(job_id, JobState.FAILED, message=str(exc))
            _logger.warning(
                "worker.numerical_failure",
                extra={"event": "worker.numerical_failure", "job_id": job_id},
            )
            return 1
        except Exception as exc:
            artifacts.discard_working(job_id)
            artifacts.discard_checkpoint(job_id)
            jobs.transition(job_id, JobState.FAILED, message=f"{type(exc).__name__}: {exc}")
            _logger.exception("worker.failed", extra={"event": "worker.failed", "job_id": job_id})
            return 1

    final_state = (
        JobState.COMPLETED_WITH_WARNINGS if summary.get("has_warnings") else JobState.COMPLETED
    )
    if not jobs.transition(job_id, final_state, result_summary_json=json.dumps(summary)):
        # The job was cancelled while we were finishing: artifacts stay
        # unpublished-or-published per the transition that won; nothing to do.
        _logger.info(
            "worker.transition_lost",
            extra={"event": "worker.transition_lost", "job_id": job_id},
        )
        return 0
    return 0


def _execute(
    record: JobRecord,
    jobs: JobStore,
    artifacts: SimulationArtifactStore,
    normalizer: GeometryNormalizer,
    board_decoder: BoardDecoder,
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    """The pipeline: load board, solve, serialise, publish."""
    spec = record.spec
    job_id = spec.job_id
    timings: dict[str, float] = {}

    jobs.update_stage(job_id, "loading_board")
    started = time.perf_counter()
    board = _load_board(spec, artifacts, board_decoder)
    timings["load_board_s"] = time.perf_counter() - started

    study = _study_from_spec(spec, board, refine_factor=1.0)
    solver = FemSheetSolver(normalizer=normalizer)

    if spec.reference_policy is not None:
        return _execute_reference(
            record, jobs, artifacts, normalizer, board, study, timings, time_budget_seconds
        )

    jobs.update_stage(job_id, "meshing")
    started = time.perf_counter()
    prepared = solver.prepare(board, study)
    timings["mesh_and_assembly_s"] = time.perf_counter() - started

    jobs.update_stage(job_id, "solving")
    started = time.perf_counter()
    result, fields = prepared.solve_with_fields(study)
    timings["solve_s"] = time.perf_counter() - started

    convergence: dict[str, Any] | None = None
    if spec.verify_convergence:
        jobs.update_stage(job_id, "verifying_convergence")
        started = time.perf_counter()
        fine_study = _study_from_spec(spec, board, refine_factor=VERIFICATION_REFINEMENT_FACTOR)
        fine_result, fine_fields = solver.solve_with_fields(board, fine_study)
        timings["verification_s"] = time.perf_counter() - started
        convergence = _compare_meshes(spec, result, fields, fine_result, fine_fields)
        # The finer mesh is the better answer: publish it.
        result, fields = fine_result, fine_fields

    jobs.update_stage(job_id, "serializing")
    started = time.perf_counter()
    working = artifacts.working_dir(job_id)
    summary = _write_artifacts(working, spec, board, result, fields, convergence, timings)
    timings["serialize_s"] = time.perf_counter() - started
    _write_manifest(working, spec, result, timings, normalizer.version)

    artifacts.publish(job_id)
    return summary


def _execute_reference(
    record: JobRecord,
    jobs: JobStore,
    artifacts: SimulationArtifactStore,
    normalizer: GeometryNormalizer,
    board: Board,
    study: AnalysisStudy,
    timings: dict[str, Any],
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    """Run an adaptive Reference job and publish its convergence evidence.

    A Reference spec froze a policy rather than a mesh, so the fixed-mesh
    path above would silently ignore everything the user asked for and hand
    back a single coarse solve wearing a Reference label. That is precisely
    the quiet degradation this tier exists to prevent, so adaptive specs
    branch here instead.

    Three durability behaviours live here rather than in the loop:

    * every completed generation is checkpointed, so a worker that dies
      silently resumes on requeue instead of restarting (ADR-0015 §9);
    * SIGTERM and a CANCELLING job state stop the loop at the next pass
      boundary and publish what exists as a partial, never-converged result
      -- losing three finished generations to a cancel button is waste, but
      presenting them as Reference quality would be a lie, so they are kept
      *and* labelled;
    * the loop self-limits to a fraction of the orchestrator's hard timeout,
      because the alternative is a SIGKILL mid-pass that keeps nothing.
    """
    spec = record.spec
    job_id = spec.job_id
    policy = spec.reference_policy
    assert policy is not None  # noqa: S101 - guarded by the caller

    resume = _load_checkpoint(artifacts, spec)
    if resume is not None:
        _logger.info(
            "worker.reference_resumed",
            extra={
                "event": "worker.reference_resumed",
                "job_id": job_id,
                "completed_generations": len(resume.generations),
            },
        )

    stop_event = threading.Event()
    previous_handler = signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    def _should_stop() -> bool:
        if stop_event.is_set():
            return True
        current = jobs.get(job_id)
        return current is not None and current.state is JobState.CANCELLING

    try:
        jobs.update_stage(job_id, "solving")
        started = time.perf_counter()
        outcome = solve_adaptive(
            board,
            study,
            normalizer,
            AdaptivePolicy(
                target_qoi_rel_change=policy.target_qoi_rel_change,
                max_passes=policy.max_passes,
                max_dofs=policy.max_dofs,
                theta=policy.theta,
                refinement_ratio=policy.refinement_ratio,
                goal_oriented=policy.goal_oriented,
                max_seconds=time_budget_seconds,
            ),
            resume=resume,
            on_generation=lambda state: _save_checkpoint(artifacts, spec, state),
            should_stop=_should_stop,
        )
        timings["adaptive_s"] = time.perf_counter() - started
    finally:
        signal.signal(signal.SIGTERM, previous_handler)

    jobs.update_stage(job_id, "serializing")
    started = time.perf_counter()
    working = artifacts.working_dir(job_id)
    if outcome.field_data is None:  # pragma: no cover - the loop always solves
        raise RuntimeError("Adaptive run produced no field data to publish")
    summary = _write_artifacts(
        working,
        spec,
        board,
        outcome.result,
        outcome.field_data,
        None,
        timings,
        reference=_reference_convergence(outcome),
    )
    quality = _quality_of(outcome)
    summary["reference_quality"] = quality.value
    if outcome.status == AdaptiveStatus.CANCELLED_PARTIAL:
        summary["partial"] = True
        summary["has_warnings"] = True
    timings["serialize_s"] = time.perf_counter() - started
    _write_manifest(working, spec, outcome.result, timings, normalizer.version)

    artifacts.publish(job_id)
    artifacts.discard_checkpoint(job_id)

    if outcome.status == AdaptiveStatus.CANCELLED_PARTIAL:
        # The cancel wins the lifecycle -- the run is CANCELLED, not
        # completed -- but the partial evidence is published and the summary
        # travels with the terminal state so the UI can say "cancelled,
        # partial result available" rather than showing nothing.
        jobs.transition(
            job_id, JobState.CANCELLED, result_summary_json=json.dumps(summary)
        )
    return summary


def _quality_of(outcome: AdaptiveOutcome) -> ResultQuality:
    """Map an adaptive outcome onto the reportable result quality."""
    return {
        AdaptiveStatus.CONVERGED: ResultQuality.CONVERGED,
        AdaptiveStatus.CONVERGED_WITH_MODEL_LIMITATIONS: (
            ResultQuality.CONVERGED_WITH_MODEL_LIMITATIONS
        ),
        AdaptiveStatus.RESOURCE_LIMITED: ResultQuality.RESOURCE_LIMITED,
        AdaptiveStatus.NOT_CONVERGED: ResultQuality.NOT_CONVERGED,
        AdaptiveStatus.CANCELLED_PARTIAL: ResultQuality.NOT_CONVERGED,
    }[outcome.status]


#: Checkpoint format version. Bump on any change to what is stored.
_CHECKPOINT_SCHEMA = 1


def _save_checkpoint(
    artifacts: SimulationArtifactStore, spec: SimulationJobSpec, state: AdaptiveResume
) -> None:
    """Persist one pass boundary, atomically.

    The checkpoint is small on purpose: re-meshing is deterministic from
    board, study and sizing field (ADR-0013 §9), so the stored field seeds
    and generation metrics are sufficient to continue -- no mesh, no
    solution, and therefore no `allow_pickle` question at all.
    """
    payload = {
        "schema": _CHECKPOINT_SCHEMA,
        "signature": spec.signature,
        "streak": state.streak,
        "generations": [
            {
                "index": g.index,
                "dof_count": g.dof_count,
                "element_count": g.element_count,
                "quantity_of_interest": g.quantity_of_interest,
                "qoi_rel_change": g.qoi_rel_change,
                "estimated_error": g.estimated_error,
                "current_imbalance_fraction": g.current_imbalance_fraction,
                "power_mismatch_fraction": g.power_mismatch_fraction,
                "marked_elements": g.marked_elements,
                "quantities": g.quantities,
                "floor_clamped_seeds": g.floor_clamped_seeds,
            }
            for g in state.generations
        ],
        "field": (
            None
            if state.field is None
            else {
                "points": state.field.points.tolist(),
                "sizes": state.field.sizes.tolist(),
            }
        ),
    }
    directory = artifacts.checkpoint_dir(spec.job_id)
    temp = directory / "checkpoint.json.tmp"
    temp.write_text(json.dumps(payload))
    temp.replace(directory / "checkpoint.json")


def _load_checkpoint(
    artifacts: SimulationArtifactStore, spec: SimulationJobSpec
) -> AdaptiveResume | None:
    """Load a resumable checkpoint, or None.

    A checkpoint whose signature does not match this spec is discarded, not
    trusted: the signature hashes every solver-affecting input, so a
    mismatch means the stored state describes a different computation.
    """
    directory = artifacts.load_checkpoint_dir(spec.job_id)
    if directory is None:
        return None
    path = directory / "checkpoint.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        artifacts.discard_checkpoint(spec.job_id)
        return None
    if payload.get("schema") != _CHECKPOINT_SCHEMA or payload.get("signature") != spec.signature:
        artifacts.discard_checkpoint(spec.job_id)
        return None
    generations = tuple(
        Generation(
            index=item["index"],
            dof_count=item["dof_count"],
            element_count=item["element_count"],
            quantity_of_interest=item["quantity_of_interest"],
            qoi_rel_change=item["qoi_rel_change"],
            estimated_error=item["estimated_error"],
            current_imbalance_fraction=item["current_imbalance_fraction"],
            power_mismatch_fraction=item["power_mismatch_fraction"],
            marked_elements=item["marked_elements"],
            quantities=dict(item["quantities"]),
            floor_clamped_seeds=int(item.get("floor_clamped_seeds", 0)),
        )
        for item in payload["generations"]
    )
    raw_field = payload.get("field")
    field = (
        None
        if raw_field is None
        else RefinementField(
            np.asarray(raw_field["points"], dtype=np.float64),
            np.asarray(raw_field["sizes"], dtype=np.float64),
        )
    )
    return AdaptiveResume(generations=generations, field=field, streak=int(payload["streak"]))


def _reference_convergence(outcome: AdaptiveOutcome) -> dict[str, Any]:
    """The per-generation history, as published evidence rather than a log."""
    return {
        "status": outcome.status,
        "converged": outcome.converged,
        "generations": [
            {
                "index": generation.index,
                "dofs": generation.dof_count,
                "elements": generation.element_count,
                "quantity_of_interest": generation.quantity_of_interest,
                "qoi_rel_change": generation.qoi_rel_change,
                "estimated_error": generation.estimated_error,
                "marked_elements": generation.marked_elements,
                "quantities": generation.quantities,
                "floor_clamped_seeds": generation.floor_clamped_seeds,
            }
            for generation in outcome.generations
        ],
        "quantities": [
            {
                "name": quantity.name,
                "converged": quantity.converged,
                "singular": quantity.singular,
                "rel_change": quantity.rel_change,
                "extrapolated": quantity.extrapolated,
                "observed_order": quantity.observed_order,
            }
            for quantity in outcome.quantities
        ],
    }


def _load_board(
    spec: SimulationJobSpec,
    artifacts: SimulationArtifactStore,
    board_decoder: BoardDecoder,
) -> Board:
    """Load the persisted canonical board document."""
    document_json = artifacts.load_board_document(spec.board_digest)
    if document_json is None:
        raise FileNotFoundError(f"Board document {spec.board_digest} is not in the artifact store")
    return board_decoder(document_json, spec.board_name, spec.board_digest)


def run_inline(
    spec: SimulationJobSpec, board: Board, normalizer: GeometryNormalizer
) -> tuple[ElectricalAnalysisResult, FemFieldData, dict[str, Any] | None]:
    """Solve a spec directly in-process (CLI debugging path, no queue).

    A Reference spec runs its adaptive loop here too -- the inline path had
    the same silent-degradation hole the queued path had, where an adaptive
    spec was quietly solved as a fixed mesh. The third element is the
    convergence history in exactly the shape the published `metrics.json`
    carries (None for fixed-mesh runs), so callers render one format whether
    the run was inline or queued -- and so the CLI never has to import the
    concrete solver package, which the architecture boundary forbids.
    """
    study = _study_from_spec(spec, board, refine_factor=1.0)
    if spec.reference_policy is not None:
        policy = spec.reference_policy
        outcome = solve_adaptive(
            board,
            study,
            normalizer,
            AdaptivePolicy(
                target_qoi_rel_change=policy.target_qoi_rel_change,
                max_passes=policy.max_passes,
                max_dofs=policy.max_dofs,
                theta=policy.theta,
                refinement_ratio=policy.refinement_ratio,
                goal_oriented=policy.goal_oriented,
            ),
        )
        if outcome.field_data is None:  # pragma: no cover - the loop always solves
            raise RuntimeError("Adaptive run produced no field data")
        return outcome.result, outcome.field_data, _reference_convergence(outcome)
    solver = FemSheetSolver(normalizer=normalizer)
    result, fields = solver.solve_with_fields(board, study)
    return result, fields, None


def _study_from_spec(spec: SimulationJobSpec, board: Board, refine_factor: float) -> AnalysisStudy:
    """Build the immutable spec's `AnalysisStudy`, optionally refined."""
    resolved = refine_mesh_spec(spec.mesh, refine_factor) if refine_factor != 1.0 else spec.mesh
    mesh = MeshSettings(
        target_element_size=Quantity.configured(resolved.max_element_m, METRE),
        minimum_element_size=Quantity.configured(resolved.min_element_m, METRE),
        elements_across_feature=resolved.elements_across_feature,
        growth_rate=resolved.growth_rate,
    )
    sources = (
        VoltageSource(
            id=SourceId("source"),
            attachment=_attachment(spec.source_terminal_ids, spec.source_via_ids),
            voltage=Quantity.configured(spec.source_voltage_v, VOLT),
        ),
    )
    loads = tuple(
        CurrentLoad(
            id=LoadId(f"load-{index}"),
            attachment=_attachment(load.terminal_ids, load.via_ids),
            current=Quantity.configured(load.current_a, AMPERE),
        )
        for index, load in enumerate(spec.loads)
    )
    probes: tuple[ResistanceProbe, ...] = ()
    if spec.kind is SimulationKind.RESISTANCE and spec.to_terminal_ids:
        probes = (
            ResistanceProbe(
                id=ProbeId("probe"),
                from_terminal_id=_terminal(spec.source_terminal_ids[0]),
                to_terminal_id=_terminal(spec.to_terminal_ids[0]),
            ),
        )
        # Drive the normalised test current through the main excitation so
        # the published fields show the test-current distribution and the
        # integrated loss numerically equals R at 1 A (P = I^2 R) -- an
        # energy-consistency check the metrics carry for free (ADR-0011).
        loads = (
            CurrentLoad(
                id=LoadId("probe-test-current"),
                attachment=_attachment(spec.to_terminal_ids, spec.to_via_ids),
                current=Quantity.configured(PROBE_TEST_CURRENT_A, AMPERE),
            ),
        )
    plating = (
        Quantity.assumed(spec.via_plating_m, METRE, "study-level plating assumption")
        if spec.via_plating_m is not None
        else None
    )
    suffix = "" if refine_factor == 1.0 else "-fine"
    return AnalysisStudy(
        id=StudyId(f"{spec.job_id}{suffix}"),
        name=spec.name,
        board_id=str(board.id),
        net_ids=(_net(spec.net_id),),
        sources=sources,
        loads=loads,
        probes=probes,
        mesh=mesh,
        via_plating_thickness=plating,
    )


def _terminal(value: str) -> TerminalId:
    from openpdn.domain.board import TerminalId as RealTerminalId

    return RealTerminalId(value)


def _attachment(terminal_ids: tuple[str, ...], via_ids: tuple[str, ...]) -> AttachmentGroup:
    from openpdn.domain.board import TerminalId as RealTerminalId
    from openpdn.domain.board import ViaId as RealViaId

    return AttachmentGroup(
        terminal_ids=tuple(RealTerminalId(t) for t in terminal_ids),
        via_ids=tuple(RealViaId(v) for v in via_ids),
    )


def _net(value: str) -> NetId:
    from openpdn.domain.board import NetId as RealNetId

    return RealNetId(value)


def _engineering_quantities(
    spec: SimulationJobSpec, result: ElectricalAnalysisResult, fields: FemFieldData
) -> dict[str, float]:
    """The quantities mesh convergence is judged on (never the raw J peak)."""
    quantities: dict[str, float] = {
        "total_loss_w": fields.conservation.dissipated_power_w,
    }
    if result.probes:
        quantities["resistance_ohm"] = result.probes[0].resistance_ohm
    if spec.kind is SimulationKind.IR_DROP and result.terminals:
        # `_terminal_results` always appends the study's one source first.
        source_v = result.terminals[0].voltage_v
        worst = max((source_v - t.voltage_v) for t in result.terminals)
        quantities["worst_drop_v"] = worst
    j99 = _j99(fields)
    if j99 > 0.0:
        quantities["j99_a_per_m2"] = j99
    return quantities


def _j99(fields: FemFieldData) -> float:
    """Area-weighted 99th percentile of |J| from the field arrays."""
    from openpdn.solver.fem.post import CurrentDensityStats  # noqa: F401  (doc pointer)

    j = fields.tri_j_vol_a_per_m2
    if len(j) == 0:
        return 0.0
    order = np.argsort(j)
    # Approximate area weights from the stored per-triangle power/j ratio is
    # not reliable; recompute areas from the mesh.
    p = fields.points[fields.triangles]
    area = (
        np.abs(
            (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
            - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])
        )
        / 2.0
    )
    weights = area[order]
    cumulative = np.cumsum(weights)
    index = int(np.searchsorted(cumulative, 0.99 * cumulative[-1]))
    return float(j[order][min(index, len(j) - 1)])


def _compare_meshes(
    spec: SimulationJobSpec,
    coarse_result: ElectricalAnalysisResult,
    coarse_fields: FemFieldData,
    fine_result: ElectricalAnalysisResult,
    fine_fields: FemFieldData,
) -> dict[str, Any]:
    """Verification-profile convergence evidence: quantity deltas across meshes."""
    coarse = _engineering_quantities(spec, coarse_result, coarse_fields)
    fine = _engineering_quantities(spec, fine_result, fine_fields)
    comparison: dict[str, Any] = {
        "coarse_elements": coarse_result.stats.mesh_elements,
        "fine_elements": fine_result.stats.mesh_elements,
        "target_fraction": _CONVERGENCE_TARGET,
        "quantities": {},
    }
    worst = 0.0
    for key, fine_value in fine.items():
        coarse_value = coarse.get(key)
        if coarse_value is None:
            continue
        scale = max(abs(fine_value), abs(coarse_value), 1e-30)
        change = abs(fine_value - coarse_value) / scale
        worst = max(worst, change)
        comparison["quantities"][key] = {
            "coarse": coarse_value,
            "fine": fine_value,
            "relative_change": change,
        }
    comparison["worst_relative_change"] = worst
    comparison["converged"] = worst <= _CONVERGENCE_TARGET
    return comparison


def _write_artifacts(
    working: Path,
    spec: SimulationJobSpec,
    board: Board,
    result: ElectricalAnalysisResult,
    fields: FemFieldData,
    convergence: dict[str, Any] | None,
    timings: dict[str, float],
    reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write field arrays and metrics; return the compact summary."""
    layers_dir = working / "layers"
    layers_dir.mkdir(exist_ok=True)

    # Group mesh data per physical layer for the overlay renderer.
    layer_ids = sorted(set(fields.region_layer_ids))
    layer_files: list[dict[str, Any]] = []
    region_layer = np.asarray(
        [layer_ids.index(layer) for layer in fields.region_layer_ids], dtype=np.int32
    )
    tri_layer = region_layer[fields.tri_region_index]
    for index, layer_id in enumerate(layer_ids):
        tri_mask = tri_layer == index
        if not tri_mask.any():
            continue
        tris = fields.triangles[tri_mask]
        used = np.unique(tris)
        remap = np.full(len(fields.points), -1, dtype=np.int32)
        remap[used] = np.arange(len(used), dtype=np.int32)
        file_name = f"{index}.npz"
        np.savez_compressed(
            layers_dir / file_name,
            points=fields.points[used],
            triangles=remap[tris],
            voltage_v=fields.node_voltage_v[used],
            j_a_per_m2=fields.tri_j_vol_a_per_m2[tri_mask],
            power_w=fields.tri_power_w[tri_mask],
        )
        layer_files.append(
            {
                "layer_id": layer_id,
                "file": f"layers/{file_name}",
                "points": len(used),
                "triangles": int(tri_mask.sum()),
            }
        )

    terminals = [
        {
            "terminal_id": str(t.terminal_id),
            "voltage_v": t.voltage_v,
            "current_a": t.current_a,
            # `_terminal_results` always appends the study's one source first.
            "is_source": index == 0,
            "member_terminal_ids": [str(m) for m in t.member_terminal_ids],
            "member_via_ids": [str(m) for m in t.member_via_ids],
        }
        for index, t in enumerate(result.terminals)
    ]
    vias = [
        {
            "via_id": via_id,
            "upper_layer": upper,
            "lower_layer": lower,
            "x_m": x,
            "y_m": y,
            "conductance_s": g,
            "voltage_upper_v": vu,
            "voltage_lower_v": vl,
            "current_a": g * (vu - vl) if not (np.isnan(vu) or np.isnan(vl)) else None,
            "power_w": g * (vu - vl) ** 2 if not (np.isnan(vu) or np.isnan(vl)) else None,
        }
        for (via_id, upper, lower, x, y, g, vu, vl) in fields.via_segment_detail
    ]
    diagnostics = [
        {
            "code": d.code,
            "severity": d.severity.value,
            "message": d.message,
            "context": dict(d.context),
        }
        for d in result.diagnostics
    ]
    has_warnings = any(
        d.severity in (DiagnosticSeverity.WARNING, DiagnosticSeverity.ERROR)
        for d in result.diagnostics
    ) or (convergence is not None and not convergence.get("converged", True))

    quantities = _engineering_quantities(spec, result, fields)
    metrics: dict[str, Any] = {
        "schema": 1,
        "kind": spec.kind.value,
        "terminals": terminals,
        "vias": vias,
        "probes": [
            {"probe_id": str(p.probe_id), "resistance_ohm": p.resistance_ohm} for p in result.probes
        ],
        "nets": [
            {
                "net_id": str(n.net_id),
                "max_voltage_v": n.max_voltage_v,
                "min_voltage_v": n.min_voltage_v,
                "ir_drop_v": n.ir_drop_v,
                "max_j_a_per_m2": n.max_current_density_a_per_m2,
                "loss_w": n.resistive_loss_w,
            }
            for n in result.nets
        ],
        "conservation": {
            "residual": fields.conservation.residual,
            "current_imbalance_fraction": fields.conservation.imbalance_fraction,
            "power_mismatch_fraction": fields.conservation.power_mismatch_fraction,
            "source_total_a": fields.conservation.source_total_a,
            "load_total_a": fields.conservation.load_total_a,
            "net_input_power_w": fields.conservation.terminal_power_w,
            "dissipated_power_w": fields.conservation.dissipated_power_w,
        },
        "quality": {
            "mesh_nodes": result.stats.mesh_nodes,
            "mesh_elements": result.stats.mesh_elements,
            "matrix_nonzeros": fields.matrix_nonzeros,
            "residual": result.stats.residual,
            "accuracy": spec.accuracy.value,
        },
        "engineering_quantities": quantities,
        "convergence": convergence,
        # Reference history has a different shape from the Verification
        # comparison above and is published under its own key -- writing it
        # into "convergence" crashed every consumer that renders the
        # Verification shape.
        "reference": reference,
        "diagnostics": diagnostics,
        "layer_files": layer_files,
        "timings_s": timings,
        "board_name": board.name,
    }
    (working / "metrics.json").write_text(json.dumps(metrics, sort_keys=True))

    summary: dict[str, Any] = {
        "kind": spec.kind.value,
        "has_warnings": has_warnings,
        "mesh_elements": result.stats.mesh_elements,
        **dict(quantities.items()),
    }
    if convergence is not None:
        summary["convergence_change"] = convergence["worst_relative_change"]
        summary["converged"] = convergence["converged"]
    return summary


def _write_manifest(
    working: Path,
    spec: SimulationJobSpec,
    result: ElectricalAnalysisResult,
    timings: dict[str, float],
    normalizer_version: str,
) -> None:
    """Provenance manifest: enough to reproduce and audit the run."""
    manifest = {
        "schema": 1,
        "job_id": spec.job_id,
        "spec": json.loads(spec.to_json()),
        "signature": spec.signature,
        "solver": {
            "name": result.solver.name,
            "version": result.solver.version,
            "backend": result.solver.backend,
        },
        "fidelity": result.fidelity.value,
        "app_version": get_version(),
        "normalizer_version": normalizer_version,
        "fem_version": SOLVER_VERSION,
        "mesh_nodes": result.stats.mesh_nodes,
        "mesh_elements": result.stats.mesh_elements,
        "timings_s": timings,
        "written_at_epoch_s": time.time(),
    }
    (working / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
