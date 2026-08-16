"""Ports the simulation pipeline depends on.

Contracts only: the durable job store (SQLite adapter today, ADR-0011), the
artifact store (filesystem adapter) and the planner (mesh estimation and
connectivity pre-checks, implemented beside the FEM solver because they share
its sizing logic). Application code and surfaces depend on these protocols,
never on the adapters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from openpdn.application.simulation_models import (
        ConnectivityIssue,
        JobRecord,
        JobState,
        ResolvedMeshSpec,
        SimulationEstimate,
    )
    from openpdn.domain.board import Board
    from openpdn.geometry.api import NormalizedGeometry


@runtime_checkable
class JobStore(Protocol):
    """Durable job state with transactional claiming.

    Implementations must survive process restarts and guarantee that a job is
    claimed by at most one worker at a time (lease-based).
    """

    def enqueue(self, record: JobRecord) -> None:
        """Persist a new job in `QUEUED` state."""
        ...

    def get(self, job_id: str) -> JobRecord | None:
        """Return one job, or None."""
        ...

    def list_recent(self, limit: int = 50) -> Sequence[JobRecord]:
        """Jobs, most recently created first."""
        ...

    def find_active_by_signature(self, signature: str) -> JobRecord | None:
        """An active (queued/claimed/running) job with this signature, if any."""
        ...

    def find_completed_by_signature(self, signature: str) -> JobRecord | None:
        """The most recent successfully completed job with this signature."""
        ...

    def claim_next(self, worker_id: str, lease_seconds: float) -> JobRecord | None:
        """Atomically claim the oldest queued job, or return None.

        The claim writes `claimed_by` and a lease expiry in the same
        transaction that transitions `QUEUED -> CLAIMED`, so two workers can
        never hold the same job.
        """
        ...

    def renew_lease(self, job_id: str, worker_id: str, lease_seconds: float) -> bool:
        """Extend the lease; False when the job is no longer this worker's."""
        ...

    def update_stage(self, job_id: str, stage: str, message: str = "") -> None:
        """Record pipeline progress for a running job."""
        ...

    def transition(
        self,
        job_id: str,
        to_state: JobState,
        *,
        message: str = "",
        result_summary_json: str | None = None,
    ) -> bool:
        """Move a job to `to_state` if the transition is legal; else False."""
        ...

    def request_cancel(self, job_id: str) -> bool:
        """Mark an active job for cancellation.

        A queued job cancels immediately; a claimed/running job enters
        `CANCELLING` and the orchestrator terminates its worker.
        """
        ...

    def recover_expired(self, now_epoch_s: float, max_attempts: int) -> int:
        """Requeue or fail jobs whose worker lease has expired.

        Returns the number of jobs recovered. Requeue only below the attempt
        cap -- an expired lease is an infrastructure failure signal, and
        infrastructure failures are the only class retried automatically.
        """
        ...


@runtime_checkable
class SimulationArtifactStore(Protocol):
    """Durable storage for board documents and result artifacts.

    Results are published atomically: a job writes into a working area and
    `publish` moves it into place in one filesystem operation, so a partial
    result can never be read as complete.
    """

    def save_board_document(self, digest: str, document_json: str) -> None:
        """Persist a board's canonical JSON, content-addressed by digest."""
        ...

    def load_board_document(self, digest: str) -> str | None:
        """Load a persisted board document, or None."""
        ...

    def working_dir(self, job_id: str) -> Path:
        """Create and return the job's private working directory."""
        ...

    def publish(self, job_id: str) -> None:
        """Atomically promote the working directory to the published result."""
        ...

    def discard_working(self, job_id: str) -> None:
        """Delete a job's working directory (cancellation, failure)."""
        ...

    def result_dir(self, job_id: str) -> Path | None:
        """The published result directory, or None if not published."""
        ...

    def delete_result(self, job_id: str) -> None:
        """Remove a published result's artifacts."""
        ...


@runtime_checkable
class SimulationPlanner(Protocol):
    """Pre-queue estimation and connectivity checking.

    Implemented beside the FEM solver: the estimate must come from the same
    sizing logic the mesher uses, or it is fiction.
    """

    def estimate(
        self,
        board: Board,
        normalized: NormalizedGeometry,
        net_id: str,
        mesh: ResolvedMeshSpec,
        budget_dofs: int,
    ) -> SimulationEstimate:
        """Estimate mesh size and resource class without triangulating."""
        ...

    def check_connectivity(
        self,
        board: Board,
        normalized: NormalizedGeometry,
        net_id: str,
        terminal_ids: Sequence[str],
        via_ids: Sequence[str] = (),
    ) -> ConnectivityIssue | None:
        """Cheap region-graph reachability check between study terminals and vias.

        `via_ids` names vias that are themselves attachment points, not just
        the vias that happen to join copper along the way. Returns the first
        issue found, or None when every terminal and via shares one
        component. This is a pre-check; the solver still verifies
        connectivity exactly on the real mesh.
        """
        ...
