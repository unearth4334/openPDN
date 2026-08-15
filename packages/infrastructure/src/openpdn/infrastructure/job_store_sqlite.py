"""Durable job store on SQLite (ADR-0011).

The authoritative queue must survive API, orchestrator and worker restarts
and host reboots -- never a list in process memory. For the current
single-host deployment SQLite in WAL mode provides exactly the required
guarantees with zero additional services: transactional claiming (one
`UPDATE ... RETURNING` claims a job atomically), leases with expiry, and a
state machine enforced in SQL (`WHERE state IN (...)`) so an illegal
transition cannot be written even by a buggy caller. A PostgreSQL adapter
implements the same `JobStore` port when multi-host workers exist.

Large numerical data never lands in these rows -- artifacts live in the
filesystem store; rows hold specs, lifecycle state and compact summaries.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, Final

from openpdn.application.simulation_models import (
    ALLOWED_TRANSITIONS,
    JobRecord,
    JobState,
    SimulationJobSpec,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    state TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    lease_expires_at REAL,
    created_at REAL NOT NULL,
    queued_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    spec_json TEXT NOT NULL,
    result_summary_json TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS jobs_signature ON jobs(signature);
"""


class SqliteJobStore:
    """`JobStore` implementation on a single SQLite database file."""

    def __init__(self, database_path: Path) -> None:
        """Open (and initialise) the database at `database_path`."""
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = database_path
        self._local = threading.local()
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(
                self._path, timeout=10.0, isolation_level=None, check_same_thread=False
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.row_factory = sqlite3.Row
            self._local.connection = connection
        return connection

    # -- writes -----------------------------------------------------------------------

    def enqueue(self, record: JobRecord) -> None:
        """Persist a new job in `QUEUED` state."""
        self._connect().execute(
            """
            INSERT INTO jobs (id, signature, state, created_at, queued_at, spec_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.spec.job_id,
                record.spec.signature,
                JobState.QUEUED.value,
                record.spec.created_at_epoch_s,
                record.queued_at_epoch_s,
                record.spec.to_json(),
            ),
        )

    def claim_next(self, worker_id: str, lease_seconds: float) -> JobRecord | None:
        """Atomically claim the oldest queued job."""
        now = time.time()
        row = (
            self._connect()
            .execute(
                """
            UPDATE jobs SET
                state = ?,
                claimed_by = ?,
                lease_expires_at = ?,
                attempt = attempt + 1
            WHERE id = (
                SELECT id FROM jobs WHERE state = ? ORDER BY queued_at LIMIT 1
            ) AND state = ?
            RETURNING *
            """,
                (
                    JobState.CLAIMED.value,
                    worker_id,
                    now + lease_seconds,
                    JobState.QUEUED.value,
                    JobState.QUEUED.value,
                ),
            )
            .fetchone()
        )
        return _record_from(row) if row is not None else None

    def renew_lease(self, job_id: str, worker_id: str, lease_seconds: float) -> bool:
        """Extend the lease while the job still belongs to this worker."""
        cursor = self._connect().execute(
            """
            UPDATE jobs SET lease_expires_at = ?
            WHERE id = ? AND claimed_by = ? AND state IN (?, ?, ?)
            """,
            (
                time.time() + lease_seconds,
                job_id,
                worker_id,
                JobState.CLAIMED.value,
                JobState.RUNNING.value,
                JobState.CANCELLING.value,
            ),
        )
        return cursor.rowcount > 0

    def update_stage(self, job_id: str, stage: str, message: str = "") -> None:
        """Record pipeline progress for an active job."""
        self._connect().execute(
            "UPDATE jobs SET stage = ?, message = ? WHERE id = ? AND state IN (?, ?)",
            (stage, message, job_id, JobState.CLAIMED.value, JobState.RUNNING.value),
        )

    def transition(
        self,
        job_id: str,
        to_state: JobState,
        *,
        message: str = "",
        result_summary_json: str | None = None,
    ) -> bool:
        """Apply a legal state transition; refuse anything else."""
        legal_sources = [
            state.value for state, targets in ALLOWED_TRANSITIONS.items() if to_state in targets
        ]
        if not legal_sources:
            return False
        placeholders = ",".join("?" for _ in legal_sources)
        now = time.time()
        started = ", started_at = COALESCE(started_at, ?)" if to_state is JobState.RUNNING else ""
        finished = ", finished_at = ?, stage = ''" if to_state.is_terminal else ""
        sql = (
            f"UPDATE jobs SET state = ?, message = ?{started}{finished}, "  # noqa: S608
            "result_summary_json = COALESCE(?, result_summary_json) "
            f"WHERE id = ? AND state IN ({placeholders})"
        )
        params: list[object] = [to_state.value, message]
        if started:
            params.append(now)
        if finished:
            params.append(now)
        params.append(result_summary_json)
        params.append(job_id)
        params.extend(legal_sources)
        cursor = self._connect().execute(sql, params)
        return cursor.rowcount > 0

    def request_cancel(self, job_id: str) -> bool:
        """Cancel a queued job immediately, or mark an active one CANCELLING."""
        connection = self._connect()
        cursor = connection.execute(
            "UPDATE jobs SET state = ?, finished_at = ? WHERE id = ? AND state = ?",
            (JobState.CANCELLED.value, time.time(), job_id, JobState.QUEUED.value),
        )
        if cursor.rowcount > 0:
            return True
        cursor = connection.execute(
            "UPDATE jobs SET state = ? WHERE id = ? AND state IN (?, ?)",
            (
                JobState.CANCELLING.value,
                job_id,
                JobState.CLAIMED.value,
                JobState.RUNNING.value,
            ),
        )
        return cursor.rowcount > 0

    def recover_expired(self, now_epoch_s: float, max_attempts: int) -> int:
        """Requeue (or fail) jobs whose worker lease expired."""
        connection = self._connect()
        requeued = connection.execute(
            """
            UPDATE jobs SET state = ?, claimed_by = NULL, lease_expires_at = NULL,
                            message = 'requeued after worker lease expired'
            WHERE state IN (?, ?) AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ? AND attempt < ?
            """,
            (
                JobState.QUEUED.value,
                JobState.CLAIMED.value,
                JobState.RUNNING.value,
                now_epoch_s,
                max_attempts,
            ),
        ).rowcount
        failed = connection.execute(
            """
            UPDATE jobs SET state = ?, finished_at = ?,
                            message = 'worker lease expired repeatedly; giving up'
            WHERE state IN (?, ?) AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ? AND attempt >= ?
            """,
            (
                JobState.FAILED.value,
                now_epoch_s,
                JobState.CLAIMED.value,
                JobState.RUNNING.value,
                now_epoch_s,
                max_attempts,
            ),
        ).rowcount
        # A cancelling job whose worker vanished is safely cancelled.
        cancelled = connection.execute(
            """
            UPDATE jobs SET state = ?, finished_at = ?
            WHERE state = ? AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
            """,
            (JobState.CANCELLED.value, now_epoch_s, JobState.CANCELLING.value, now_epoch_s),
        ).rowcount
        return int(requeued + failed + cancelled)

    # -- reads ------------------------------------------------------------------------

    def get(self, job_id: str) -> JobRecord | None:
        """One job by id."""
        row = self._connect().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _record_from(row) if row is not None else None

    def list_recent(self, limit: int = 50) -> Sequence[JobRecord]:
        """Jobs, newest first."""
        rows = (
            self._connect()
            .execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            )
            .fetchall()
        )
        return [_record_from(row) for row in rows]

    def find_active_by_signature(self, signature: str) -> JobRecord | None:
        """An active job with this signature."""
        row = (
            self._connect()
            .execute(
                "SELECT * FROM jobs WHERE signature = ? AND state IN (?, ?, ?, ?) "
                "ORDER BY created_at DESC LIMIT 1",
                (
                    signature,
                    JobState.QUEUED.value,
                    JobState.CLAIMED.value,
                    JobState.RUNNING.value,
                    JobState.CANCELLING.value,
                ),
            )
            .fetchone()
        )
        return _record_from(row) if row is not None else None

    def find_completed_by_signature(self, signature: str) -> JobRecord | None:
        """The most recent successful job with this signature."""
        row = (
            self._connect()
            .execute(
                "SELECT * FROM jobs WHERE signature = ? AND state IN (?, ?) "
                "ORDER BY finished_at DESC LIMIT 1",
                (
                    signature,
                    JobState.COMPLETED.value,
                    JobState.COMPLETED_WITH_WARNINGS.value,
                ),
            )
            .fetchone()
        )
        return _record_from(row) if row is not None else None

    def iter_states(self) -> Iterator[tuple[str, str]]:
        """(job id, state) pairs, for health reporting."""
        for row in self._connect().execute("SELECT id, state FROM jobs"):
            yield row["id"], row["state"]


def _record_from(row: sqlite3.Row) -> JobRecord:
    """Rehydrate a `JobRecord` from a database row."""
    return JobRecord(
        spec=SimulationJobSpec.from_json(row["spec_json"]),
        state=JobState(row["state"]),
        stage=row["stage"],
        message=row["message"],
        attempt=row["attempt"],
        claimed_by=row["claimed_by"],
        lease_expires_at_epoch_s=row["lease_expires_at"],
        queued_at_epoch_s=row["queued_at"],
        started_at_epoch_s=row["started_at"],
        finished_at_epoch_s=row["finished_at"],
        result_summary_json=row["result_summary_json"],
    )


def summary_json(payload: dict[str, object]) -> str:
    """Serialise a compact result summary for the jobs table."""
    return json.dumps(payload, sort_keys=True)
