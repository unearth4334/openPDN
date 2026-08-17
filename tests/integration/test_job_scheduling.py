"""Priority classes, claim ordering, and the schema migration that enables them.

A Reference solve can hold a worker for a long time. In one FIFO queue it
would block every short analysis behind it, so the two are scheduled as
separate classes -- a priority, never a preemption: a running job is left
alone (ADR-0015 §8).
"""

from __future__ import annotations

import sqlite3

import pytest

from openpdn.application.simulation_models import (
    AccuracyProfile,
    JobPriority,
    JobRecord,
    JobState,
    LoadSpec,
    ReferencePolicy,
    ResolvedMeshSpec,
    SimulationJobSpec,
    SimulationKind,
)
from openpdn.infrastructure.job_store_sqlite import SqliteJobStore


def _spec(job_id: str, accuracy: AccuracyProfile, queued_at: float) -> SimulationJobSpec:
    return SimulationJobSpec(
        job_id=job_id,
        name=job_id,
        kind=SimulationKind.IR_DROP,
        board_id="board-1",
        board_digest="digest-1",
        board_name="board",
        net_id="net-1",
        net_name="NET1",
        source_terminal_ids=("t-a",),
        source_via_ids=(),
        source_voltage_v=1.0,
        loads=(LoadSpec(current_a=1.0, terminal_ids=("t-b",)),),
        to_terminal_ids=(),
        to_via_ids=(),
        accuracy=accuracy,
        mesh=ResolvedMeshSpec(
            max_element_m=1e-3,
            min_element_m=1e-5,
            elements_across_feature=4,
            growth_rate=0.7,
        ),
        verify_convergence=False,
        via_plating_m=None,
        solver_name="fem-2p5d",
        created_at_epoch_s=queued_at,
        signature=f"sig-{job_id}",
        reference_policy=(ReferencePolicy() if accuracy is AccuracyProfile.REFERENCE else None),
        estimated_dofs=1_000,
    )


def _enqueue(store: SqliteJobStore, job_id: str, accuracy: AccuracyProfile, at: float) -> None:
    store.enqueue(
        JobRecord(
            spec=_spec(job_id, accuracy, at),
            state=JobState.QUEUED,
            queued_at_epoch_s=at,
        )
    )


@pytest.fixture
def store(tmp_path) -> SqliteJobStore:
    return SqliteJobStore(tmp_path / "jobs.sqlite")


class TestPriorityClass:
    def test_reference_jobs_are_a_lower_priority_class(self):
        assert JobPriority.for_accuracy(AccuracyProfile.REFERENCE) is JobPriority.REFERENCE
        assert JobPriority.REFERENCE > JobPriority.INTERACTIVE

    @pytest.mark.parametrize(
        "accuracy",
        [
            AccuracyProfile.PREVIEW,
            AccuracyProfile.STANDARD,
            AccuracyProfile.HIGH,
            AccuracyProfile.VERIFICATION,
        ],
    )
    def test_every_fixed_mesh_profile_is_interactive(self, accuracy: AccuracyProfile):
        assert JobPriority.for_accuracy(accuracy) is JobPriority.INTERACTIVE


class TestClaimOrdering:
    def test_an_interactive_job_overtakes_an_older_reference_job(self, store):
        # The whole point: a long Reference run queued first must not block a
        # short analysis queued after it.
        _enqueue(store, "reference-first", AccuracyProfile.REFERENCE, 100.0)
        _enqueue(store, "interactive-second", AccuracyProfile.STANDARD, 200.0)
        claimed = store.claim_next("worker-1", 60.0)
        assert claimed is not None
        assert claimed.spec.job_id == "interactive-second"

    def test_within_a_class_order_is_still_arrival_order(self, store):
        _enqueue(store, "older", AccuracyProfile.STANDARD, 100.0)
        _enqueue(store, "newer", AccuracyProfile.STANDARD, 200.0)
        assert store.claim_next("worker-1", 60.0).spec.job_id == "older"
        assert store.claim_next("worker-2", 60.0).spec.job_id == "newer"

    def test_reference_jobs_still_run_once_nothing_interactive_is_waiting(self, store):
        _enqueue(store, "reference", AccuracyProfile.REFERENCE, 100.0)
        _enqueue(store, "interactive", AccuracyProfile.STANDARD, 200.0)
        assert store.claim_next("worker-1", 60.0).spec.job_id == "interactive"
        assert store.claim_next("worker-2", 60.0).spec.job_id == "reference"

    def test_an_empty_queue_claims_nothing(self, store):
        assert store.claim_next("worker-1", 60.0) is None


class TestReleaseClaim:
    def test_a_released_job_returns_to_the_queue(self, store):
        _enqueue(store, "job", AccuracyProfile.STANDARD, 100.0)
        claimed = store.claim_next("worker-1", 60.0)
        assert store.release_claim("job", "worker-1") is True
        reloaded = store.get("job")
        assert reloaded.state is JobState.QUEUED
        assert reloaded.claimed_by is None
        del claimed

    def test_releasing_undoes_the_attempt_increment(self, store):
        # Admission control may defer a job repeatedly for memory. That must
        # not burn the retry budget, which bounds *execution* attempts.
        _enqueue(store, "job", AccuracyProfile.STANDARD, 100.0)
        for _ in range(5):
            claimed = store.claim_next("worker-1", 60.0)
            assert claimed is not None
            store.release_claim("job", "worker-1")
        assert store.get("job").attempt == 0

    def test_a_job_claimed_by_someone_else_is_not_released(self, store):
        _enqueue(store, "job", AccuracyProfile.STANDARD, 100.0)
        store.claim_next("worker-1", 60.0)
        assert store.release_claim("job", "worker-2") is False
        assert store.get("job").state is JobState.CLAIMED

    def test_a_released_job_can_be_claimed_again(self, store):
        _enqueue(store, "job", AccuracyProfile.STANDARD, 100.0)
        store.claim_next("worker-1", 60.0)
        store.release_claim("job", "worker-1")
        assert store.claim_next("worker-2", 60.0).spec.job_id == "job"


class TestSchemaMigration:
    def test_a_database_without_the_priority_column_is_upgraded(self, tmp_path):
        # Deployed stores hold live rows and were created by
        # `CREATE TABLE IF NOT EXISTS`, which does not backfill new columns.
        # Opening one must migrate it rather than fail.
        path = tmp_path / "legacy.sqlite"
        legacy = sqlite3.connect(path)
        legacy.executescript(
            """
            CREATE TABLE jobs (
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
            """
        )
        legacy.close()

        store = SqliteJobStore(path)
        columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(jobs)")}
        assert "priority" in columns
        # And the upgraded store is usable.
        _enqueue(store, "after-migration", AccuracyProfile.STANDARD, 100.0)
        assert store.claim_next("worker-1", 60.0).spec.job_id == "after-migration"

    def test_opening_an_existing_store_twice_is_harmless(self, tmp_path):
        path = tmp_path / "jobs.sqlite"
        SqliteJobStore(path)
        store = SqliteJobStore(path)
        _enqueue(store, "job", AccuracyProfile.STANDARD, 100.0)
        assert store.get("job") is not None
