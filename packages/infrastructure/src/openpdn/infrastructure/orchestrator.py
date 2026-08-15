"""The simulation orchestrator: claims jobs and supervises worker processes.

One long-running process (`openpdn orchestrator`) owns execution:

    poll store -> claim (transactional lease) -> spawn worker subprocess
        -> watch: renew nothing (the worker heartbeats its own lease),
                  kill on cancellation request or timeout
        -> reconcile the final state

Simulations never run inside API request handlers (ADR-0011); the API only
writes queue rows. Recovery is lease-based: at startup and periodically, jobs
whose worker vanished (expired lease) are requeued below the attempt cap --
that path only triggers for silent deaths, because a worker that *reports* a
failure transitions the job itself and releases nothing to retry.

Numerical-library thread counts are pinned per worker so concurrent jobs
cannot oversubscribe the host (job concurrency x BLAS threads <= CPUs).
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openpdn.application.simulation_models import JobState
from openpdn.infrastructure.process import worker_environment

if TYPE_CHECKING:
    from openpdn.application.simulation_models import WorkerLimits
    from openpdn.application.simulation_ports import JobStore, SimulationArtifactStore

_logger = logging.getLogger(__name__)

#: Seconds between orchestrator poll cycles.
POLL_INTERVAL_S = 1.0

#: Seconds between lease-expiry recovery sweeps.
RECOVERY_INTERVAL_S = 30.0

#: Grace period between SIGTERM and SIGKILL when stopping a worker.
TERMINATE_GRACE_S = 10.0


@dataclass
class _RunningWorker:
    """One spawned worker process under supervision."""

    job_id: str
    worker_id: str
    process: subprocess.Popen[bytes]
    started_at: float
    terminating_since: float | None = None


@dataclass
class Orchestrator:
    """Claims queued jobs and supervises isolated solver workers."""

    jobs: JobStore
    artifacts: SimulationArtifactStore
    limits: WorkerLimits
    _running: dict[str, _RunningWorker] = field(default_factory=dict)
    _stop_requested: bool = False

    def run_forever(self) -> None:
        """Main loop; returns only on SIGINT/SIGTERM."""
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        _logger.info(
            "orchestrator.started",
            extra={
                "event": "orchestrator.started",
                "max_concurrent": self.limits.max_concurrent_jobs,
            },
        )
        # Crash recovery before the first claim: stale working dirs are
        # partial artifacts that must never publish; expired leases requeue.
        removed = self.artifacts.cleanup_stale_working()  # type: ignore[attr-defined]
        recovered = self.jobs.recover_expired(time.time(), self.limits.max_attempts)
        if removed or recovered:
            _logger.info(
                "orchestrator.recovered",
                extra={
                    "event": "orchestrator.recovered",
                    "stale_working_dirs": removed,
                    "requeued_jobs": recovered,
                },
            )
        last_recovery = time.time()
        while not self._stop_requested:
            self._reap_finished()
            self._enforce_cancellations_and_timeouts()
            self._claim_and_spawn()
            if time.time() - last_recovery > RECOVERY_INTERVAL_S:
                self.jobs.recover_expired(time.time(), self.limits.max_attempts)
                last_recovery = time.time()
            time.sleep(POLL_INTERVAL_S)
        self._shutdown()

    def tick(self) -> None:
        """One supervision cycle (used by tests and embedded mode)."""
        self._reap_finished()
        self._enforce_cancellations_and_timeouts()
        self._claim_and_spawn()

    # -- internals --------------------------------------------------------------------

    def _request_stop(self, signum: int, frame: object) -> None:
        del signum, frame
        self._stop_requested = True

    def _claim_and_spawn(self) -> None:
        while len(self._running) < self.limits.max_concurrent_jobs:
            worker_id = f"worker-{uuid.uuid4().hex[:12]}"
            record = self.jobs.claim_next(worker_id, self.limits.lease_seconds)
            if record is None:
                return
            process = self._spawn(record.spec.job_id, worker_id)
            self._running[record.spec.job_id] = _RunningWorker(
                job_id=record.spec.job_id,
                worker_id=worker_id,
                process=process,
                started_at=time.time(),
            )
            _logger.info(
                "orchestrator.spawned",
                extra={
                    "event": "orchestrator.spawned",
                    "job_id": record.spec.job_id,
                    "worker": worker_id,
                    "pid": process.pid,
                },
            )

    def _spawn(self, job_id: str, worker_id: str) -> subprocess.Popen[bytes]:
        """Start an isolated worker with pinned numerical thread counts."""
        import multiprocessing

        cpu_count = multiprocessing.cpu_count()
        threads = max(1, cpu_count // max(1, self.limits.max_concurrent_jobs))
        env = worker_environment(threads)
        # Structured argv, never a shell: job ids are persisted strings and
        # persisted strings are input.
        argv = [
            sys.executable,
            "-m",
            "openpdn.cli.main",
            "solver-worker",
            "--job-id",
            job_id,
            "--worker-id",
            worker_id,
        ]
        return subprocess.Popen(argv, env=env, close_fds=True)  # noqa: S603

    def _reap_finished(self) -> None:
        for job_id in list(self._running):
            worker = self._running[job_id]
            code = worker.process.poll()
            if code is None:
                continue
            del self._running[job_id]
            record = self.jobs.get(job_id)
            state = record.state if record is not None else None
            _logger.info(
                "orchestrator.worker_exited",
                extra={
                    "event": "orchestrator.worker_exited",
                    "job_id": job_id,
                    "exit_code": code,
                    "state": state.value if state else None,
                },
            )
            if state is JobState.CANCELLING:
                self.artifacts.discard_working(job_id)
                self.jobs.transition(job_id, JobState.CANCELLED, message="worker terminated")
            elif state is not None and not state.is_terminal:
                # The worker died without reporting: an infrastructure
                # failure. Release the lease immediately rather than waiting
                # for expiry; recover_expired applies the attempt cap.
                self.artifacts.discard_working(job_id)
                self.jobs.recover_expired(
                    time.time() + self.limits.lease_seconds + 1.0, self.limits.max_attempts
                )

    def _enforce_cancellations_and_timeouts(self) -> None:
        now = time.time()
        for job_id, worker in list(self._running.items()):
            record = self.jobs.get(job_id)
            overdue = now - worker.started_at > self.limits.max_job_seconds
            cancelling = record is not None and record.state is JobState.CANCELLING
            if overdue and not cancelling and record is not None:
                self.jobs.request_cancel(job_id)
                self.jobs.update_stage(job_id, record.stage, "cancelled: exceeded time limit")
                cancelling = True
                _logger.warning(
                    "orchestrator.timeout",
                    extra={"event": "orchestrator.timeout", "job_id": job_id},
                )
            if not cancelling:
                continue
            if worker.terminating_since is None:
                worker.process.terminate()
                worker.terminating_since = now
            elif now - worker.terminating_since > TERMINATE_GRACE_S:
                worker.process.kill()

    def _shutdown(self) -> None:
        """Terminate workers; their jobs recover by lease on the next start."""
        for worker in self._running.values():
            worker.process.terminate()
        deadline = time.time() + TERMINATE_GRACE_S
        for worker in self._running.values():
            remaining = max(0.1, deadline - time.time())
            try:
                worker.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                worker.process.kill()
        _logger.info("orchestrator.stopped", extra={"event": "orchestrator.stopped"})
