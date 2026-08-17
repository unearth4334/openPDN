"""Use cases: estimate, queue, cancel and inspect simulations.

The service validates untrusted drafts against the board, resolves accuracy
profiles into immutable specifications, enforces server-side resource
budgets, persists the board document for out-of-process workers, and hands
jobs to the durable store. It never executes a solve -- execution belongs to
the orchestrator/worker pair (ADR-0011), and FastAPI request handlers must
stay fast.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from openpdn.application.accuracy import (
    VERIFICATION_REFINEMENT_FACTOR,
    refine_mesh_spec,
    resolve_profile,
)
from openpdn.application.errors import BoardNotFoundError
from openpdn.application.simulation_models import (
    JobRecord,
    JobState,
    LoadSpec,
    SimulationDraft,
    SimulationEstimate,
    SimulationJobSpec,
    SimulationKind,
    SimulationRequestError,
    WorkerLimits,
    analysis_signature,
    new_job_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openpdn.application.board_store import BoardStore, StoredBoard
    from openpdn.application.simulation_ports import (
        JobStore,
        SimulationArtifactStore,
        SimulationPlanner,
    )
    from openpdn.domain.board import Board, Terminal, Via

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class QueuedSimulation:
    """Outcome of queueing: the new job, or the duplicate it matched."""

    job: JobRecord
    duplicate_of: str | None = None


@dataclass(frozen=True, slots=True)
class SimulationPlan:
    """A validated draft with its resolved spec-to-be and estimate."""

    estimate: SimulationEstimate
    resolved_spec: SimulationJobSpec
    connectivity_ok: bool
    connectivity_message: str | None


class SimulationService:
    """Validates, estimates and queues simulation jobs."""

    def __init__(
        self,
        *,
        boards: BoardStore,
        jobs: JobStore,
        artifacts: SimulationArtifactStore,
        planner: SimulationPlanner,
        limits: WorkerLimits,
        solver_name: str,
        solver_version: str,
        board_to_document_json: Callable[[Board], str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Wire the ports; `board_to_document_json` persists boards for workers."""
        self._boards = boards
        self._jobs = jobs
        self._artifacts = artifacts
        self._planner = planner
        self._limits = limits
        self._solver_name = solver_name
        self._solver_version = solver_version
        self._board_to_document_json = board_to_document_json
        self._clock = clock

    # -- planning ---------------------------------------------------------------------

    def plan(self, draft: SimulationDraft) -> SimulationPlan:
        """Validate a draft and produce its estimate and resolved spec.

        Raises:
            BoardNotFoundError: Unknown board.
            SimulationRequestError: Any invalid reference or value.
        """
        record = self._stored_board(draft.board_id)
        board = record.import_result.board
        self._validate_references(draft, board)
        self._enforce_reference_ceilings(draft)

        diagonal_m = _diagonal_m(board)
        mesh, verify = resolve_profile(draft.accuracy, diagonal_m)

        estimate = self._planner.estimate(
            board, record.normalized, draft.net_id, mesh, self._limits.max_dofs
        )
        if verify:
            # Verification's automatic comparison solve runs at a finer mesh
            # than the one just estimated (see accuracy.refine_mesh_spec) and
            # is not separately budget-checked at solve time -- so the worst
            # of the two must gate `over_budget` here, or a job that looks
            # affordable at queue time can still blow the worker's memory
            # budget on its second, unchecked solve.
            refined_estimate = self._planner.estimate(
                board,
                record.normalized,
                draft.net_id,
                refine_mesh_spec(mesh, VERIFICATION_REFINEMENT_FACTOR),
                self._limits.max_dofs,
            )
            if refined_estimate.over_budget:
                estimate = replace(
                    estimate,
                    over_budget=True,
                    assumptions=(
                        *estimate.assumptions,
                        f"Verification's refined comparison mesh needs an estimated "
                        f"{refined_estimate.dofs:,} DOFs, over the "
                        f"{refined_estimate.budget_dofs:,}-DOF budget.",
                    ),
                )
        issue = self._planner.check_connectivity(
            board,
            record.normalized,
            draft.net_id,
            self._draft_terminal_ids(draft),
            self._draft_via_ids(draft),
        )

        source_digest = _board_digest(record)
        signature = analysis_signature(
            board_digest=source_digest,
            kind=draft.kind,
            net_id=draft.net_id,
            source_terminal_ids=draft.source_terminal_ids,
            source_via_ids=draft.source_via_ids,
            source_voltage_v=draft.source_voltage_v,
            loads=draft.loads,
            to_terminal_ids=draft.to_terminal_ids,
            to_via_ids=draft.to_via_ids,
            mesh=mesh,
            verify_convergence=verify,
            via_plating_m=draft.via_plating_m,
            solver_name=self._solver_name,
            solver_version=self._solver_version,
            reference_policy=draft.reference_policy,
        )
        now = self._clock()
        nets_by_id = {str(net.id): net for net in board.nets}
        spec = SimulationJobSpec(
            job_id=new_job_id(signature, now),
            name=draft.name or _default_name(draft, board),
            kind=draft.kind,
            board_id=draft.board_id,
            board_digest=source_digest,
            board_name=board.name,
            net_id=draft.net_id,
            net_name=nets_by_id[draft.net_id].name,
            source_terminal_ids=draft.source_terminal_ids,
            source_via_ids=draft.source_via_ids,
            source_voltage_v=draft.source_voltage_v,
            loads=draft.loads,
            to_terminal_ids=draft.to_terminal_ids,
            to_via_ids=draft.to_via_ids,
            accuracy=draft.accuracy,
            mesh=mesh,
            verify_convergence=verify,
            via_plating_m=draft.via_plating_m,
            solver_name=self._solver_name,
            created_at_epoch_s=now,
            signature=signature,
            reference_policy=draft.reference_policy,
            estimated_dofs=(
                draft.reference_policy.max_dofs
                if draft.reference_policy is not None
                else estimate.dofs
            ),
        )
        return SimulationPlan(
            estimate=estimate,
            resolved_spec=spec,
            connectivity_ok=issue is None,
            connectivity_message=issue.message if issue else None,
        )

    # -- queueing ---------------------------------------------------------------------

    def queue(self, draft: SimulationDraft) -> QueuedSimulation:
        """Validate, enforce budgets, persist the board and enqueue.

        Raises:
            SimulationRequestError: Validation, connectivity, or a resource
                budget violation -- the job is refused, never silently
                degraded.
        """
        plan = self.plan(draft)
        if not plan.connectivity_ok:
            raise SimulationRequestError(
                plan.connectivity_message or "Study terminals are not electrically connected"
            )
        if plan.estimate.over_budget:
            raise SimulationRequestError(
                f"Estimated problem size ({plan.estimate.dofs} DOFs) exceeds the "
                f"configured execution budget of {plan.estimate.budget_dofs} DOFs. "
                "Reduce accuracy or raise the worker budget; accuracy is never "
                "silently lowered."
            )

        active = self._jobs.find_active_by_signature(plan.resolved_spec.signature)
        if active is not None:
            _logger.info(
                "simulation.duplicate_active",
                extra={"event": "simulation.duplicate_active", "job_id": active.spec.job_id},
            )
            return QueuedSimulation(job=active, duplicate_of=active.spec.job_id)

        record = self._stored_board(draft.board_id)
        self._artifacts.save_board_document(
            plan.resolved_spec.board_digest,
            self._board_to_document_json(record.import_result.board),
        )

        job = JobRecord(
            spec=plan.resolved_spec,
            state=JobState.QUEUED,
            queued_at_epoch_s=self._clock(),
        )
        self._jobs.enqueue(job)
        _logger.info(
            "simulation.queued",
            extra={
                "event": "simulation.queued",
                "job_id": job.spec.job_id,
                "kind": job.spec.kind.value,
                "net": job.spec.net_id,
                "accuracy": job.spec.accuracy.value,
                "estimated_dofs": plan.estimate.dofs,
            },
        )
        return QueuedSimulation(job=job)

    # -- lifecycle --------------------------------------------------------------------

    def cancel(self, job_id: str) -> bool:
        """Request cancellation; real termination is the orchestrator's job."""
        return self._jobs.request_cancel(job_id)

    def get(self, job_id: str) -> JobRecord | None:
        """One job's current record."""
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> Sequence[JobRecord]:
        """Recent jobs, newest first."""
        return self._jobs.list_recent(limit)

    def previous_result(self, signature: str) -> JobRecord | None:
        """A completed job with the identical analysis signature, if any."""
        return self._jobs.find_completed_by_signature(signature)

    # -- internals --------------------------------------------------------------------

    def _stored_board(self, board_id: str) -> StoredBoard:
        record = self._boards.get(board_id)
        if record is None:
            raise BoardNotFoundError(f"Board {board_id!r} is not loaded")
        return record

    def _draft_terminal_ids(self, draft: SimulationDraft) -> list[str]:
        terminals = [*draft.source_terminal_ids]
        for load in draft.loads:
            terminals.extend(load.terminal_ids)
        terminals.extend(draft.to_terminal_ids)
        return terminals

    def _draft_via_ids(self, draft: SimulationDraft) -> list[str]:
        vias = [*draft.source_via_ids]
        for load in draft.loads:
            vias.extend(load.via_ids)
        vias.extend(draft.to_via_ids)
        return vias

    def _enforce_reference_ceilings(self, draft: SimulationDraft) -> None:
        """Refuse an adaptive policy that exceeds the administrative maxima.

        Server-side, whatever the client sent (ADR-0015 §7). Refused rather
        than clamped: a run silently held to a lower ceiling would report
        RESOURCE_LIMITED for a limit the user never chose, which is exactly
        the kind of quiet degradation this tier is built to avoid.
        """
        policy = draft.reference_policy
        if policy is None:
            return
        if policy.max_passes > self._limits.max_reference_passes:
            raise SimulationRequestError(
                f"Requested {policy.max_passes} adaptive passes, above the configured "
                f"maximum of {self._limits.max_reference_passes}"
            )
        if policy.max_dofs > self._limits.max_reference_dofs:
            raise SimulationRequestError(
                f"Requested a {policy.max_dofs:,}-DOF ceiling, above the configured "
                f"maximum of {self._limits.max_reference_dofs:,}"
            )

    def _validate_references(self, draft: SimulationDraft, board: Board) -> None:
        nets_by_id = {str(net.id): net for net in board.nets}
        if draft.net_id not in nets_by_id:
            raise SimulationRequestError(f"Unknown net {draft.net_id!r}")
        terminals: dict[str, Terminal] = {
            str(terminal.id): terminal for terminal in board.terminals
        }
        for terminal_id in self._draft_terminal_ids(draft):
            terminal = terminals.get(terminal_id)
            if terminal is None:
                raise SimulationRequestError(f"Unknown terminal {terminal_id!r}")
            if str(terminal.net_id) != draft.net_id:
                raise SimulationRequestError(
                    f"Terminal {terminal.name!r} sits on net {terminal.net_id!r}, "
                    f"not on the studied net {draft.net_id!r}"
                )
        vias: dict[str, Via] = {str(via.id): via for via in board.vias}
        for via_id in self._draft_via_ids(draft):
            via = vias.get(via_id)
            if via is None:
                raise SimulationRequestError(f"Unknown via {via_id!r}")
            if str(via.net_id) != draft.net_id:
                raise SimulationRequestError(
                    f"Via {via_id!r} sits on net {via.net_id!r}, "
                    f"not on the studied net {draft.net_id!r}"
                )


def _diagonal_m(board: Board) -> float:
    """Board bounding-box diagonal, the length scale profiles resolve against."""
    box = board.bounding_box
    if box is None:
        raise SimulationRequestError("Board has no geometry")
    dx = box.max_x_m - box.min_x_m
    dy = box.max_y_m - box.min_y_m
    return float((dx * dx + dy * dy) ** 0.5)


def _board_digest(record: StoredBoard) -> str:
    """Content digest identifying the board's geometry for signatures."""
    board = record.import_result.board
    if board.provenance is not None and board.provenance.source_digest:
        return board.provenance.source_digest
    return str(board.id)


def _default_name(draft: SimulationDraft, board: Board) -> str:
    """A readable default job name from the study shape."""
    nets_by_id = {str(net.id): net.name for net in board.nets}
    net_name = nets_by_id.get(draft.net_id, draft.net_id)
    if draft.kind is SimulationKind.RESISTANCE:
        return f"{net_name} resistance"
    total = sum(load.current_a for load in draft.loads)
    return f"{net_name} IR drop ({total:g} A)"


def loads_from_pairs(pairs: Sequence[tuple[str, float]]) -> tuple[LoadSpec, ...]:
    """Convenience for surfaces building loads from (terminal, current) pairs."""
    return tuple(LoadSpec(current_a=c, terminal_ids=(t,)) for t, c in pairs)
