"""Durable job store semantics: claiming, leases, transitions, recovery.

These are the guarantees the orchestrator is built on; each test would catch
a corruption mode that production would otherwise discover the hard way.
"""

from __future__ import annotations

import time

import pytest

from openpdn.application.simulation_models import (
    AccuracyProfile,
    JobRecord,
    JobState,
    ResolvedMeshSpec,
    SimulationJobSpec,
    SimulationKind,
)
from openpdn.infrastructure.job_store_sqlite import SqliteJobStore

pytestmark = pytest.mark.integration


def _spec(job_id: str, signature: str = "sig-1") -> SimulationJobSpec:
    return SimulationJobSpec(
        job_id=job_id,
        name="test job",
        kind=SimulationKind.RESISTANCE,
        board_id="board-1",
        board_digest="digest-1",
        board_name="board",
        net_id="net-1",
        net_name="NET1",
        source_terminal_id="t-a",
        source_voltage_v=0.0,
        loads=(),
        to_terminal_id="t-b",
        accuracy=AccuracyProfile.STANDARD,
        mesh=ResolvedMeshSpec(
            max_element_m=1e-3,
            min_element_m=1e-5,
            elements_across_feature=4,
            growth_rate=0.7,
        ),
        verify_convergence=False,
        via_plating_m=None,
        solver_name="fem-2p5d",
        created_at_epoch_s=time.time(),
        signature=signature,
    )


def _queued(job_id: str, signature: str = "sig-1") -> JobRecord:
    return JobRecord(
        spec=_spec(job_id, signature), state=JobState.QUEUED, queued_at_epoch_s=time.time()
    )


@pytest.fixture
def store(tmp_path) -> SqliteJobStore:
    return SqliteJobStore(tmp_path / "jobs.sqlite3")


class TestClaiming:
    def test_claim_is_exclusive(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        first = store.claim_next("worker-1", 60.0)
        second = store.claim_next("worker-2", 60.0)
        assert first is not None and first.state is JobState.CLAIMED
        assert second is None  # one queued job, one claim

    def test_claims_oldest_first(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa", "sig-a"))
        time.sleep(0.01)
        store.enqueue(_queued("job-bbbbbbbbbbbbbbbb", "sig-b"))
        first = store.claim_next("worker-1", 60.0)
        assert first.spec.job_id == "job-aaaaaaaaaaaaaaaa"

    def test_lease_renewal_requires_ownership(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        store.claim_next("worker-1", 60.0)
        assert store.renew_lease("job-aaaaaaaaaaaaaaaa", "worker-1", 60.0)
        assert not store.renew_lease("job-aaaaaaaaaaaaaaaa", "worker-2", 60.0)


class TestStateMachine:
    def test_illegal_transition_is_refused(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        # QUEUED -> COMPLETED skips claiming and running: refused.
        assert not store.transition("job-aaaaaaaaaaaaaaaa", JobState.COMPLETED)
        assert store.get("job-aaaaaaaaaaaaaaaa").state is JobState.QUEUED

    def test_terminal_states_are_final(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        store.claim_next("worker-1", 60.0)
        store.transition("job-aaaaaaaaaaaaaaaa", JobState.RUNNING)
        store.transition("job-aaaaaaaaaaaaaaaa", JobState.COMPLETED)
        assert not store.transition("job-aaaaaaaaaaaaaaaa", JobState.QUEUED)
        assert not store.request_cancel("job-aaaaaaaaaaaaaaaa")

    def test_cancel_queued_is_immediate(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        assert store.request_cancel("job-aaaaaaaaaaaaaaaa")
        assert store.get("job-aaaaaaaaaaaaaaaa").state is JobState.CANCELLED

    def test_cancel_running_goes_through_cancelling(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        store.claim_next("worker-1", 60.0)
        store.transition("job-aaaaaaaaaaaaaaaa", JobState.RUNNING)
        assert store.request_cancel("job-aaaaaaaaaaaaaaaa")
        assert store.get("job-aaaaaaaaaaaaaaaa").state is JobState.CANCELLING


class TestRecovery:
    def test_expired_lease_requeues_below_attempt_cap(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        store.claim_next("worker-1", lease_seconds=0.0)
        recovered = store.recover_expired(time.time() + 1.0, max_attempts=3)
        assert recovered == 1
        record = store.get("job-aaaaaaaaaaaaaaaa")
        assert record.state is JobState.QUEUED
        assert record.attempt == 1  # the attempt is kept for the cap

    def test_expired_lease_fails_at_attempt_cap(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        for _ in range(3):
            store.claim_next("worker-x", lease_seconds=0.0)
            store.recover_expired(time.time() + 1.0, max_attempts=3)
        record = store.get("job-aaaaaaaaaaaaaaaa")
        assert record.state is JobState.FAILED
        assert "lease expired" in record.message

    def test_reported_failure_is_never_requeued(self, store):
        """A worker-reported failure is terminal; recovery must not touch it."""
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        store.claim_next("worker-1", lease_seconds=0.0)
        store.transition("job-aaaaaaaaaaaaaaaa", JobState.RUNNING)
        store.transition("job-aaaaaaaaaaaaaaaa", JobState.FAILED, message="mesh error")
        assert store.recover_expired(time.time() + 1.0, max_attempts=3) == 0
        assert store.get("job-aaaaaaaaaaaaaaaa").state is JobState.FAILED

    def test_survives_reopen(self, tmp_path):
        """The queue is durable: a new store instance sees everything."""
        first = SqliteJobStore(tmp_path / "jobs.sqlite3")
        first.enqueue(_queued("job-aaaaaaaaaaaaaaaa"))
        second = SqliteJobStore(tmp_path / "jobs.sqlite3")
        record = second.get("job-aaaaaaaaaaaaaaaa")
        assert record is not None and record.state is JobState.QUEUED


class TestSignatures:
    def test_active_signature_lookup(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa", "sig-x"))
        assert store.find_active_by_signature("sig-x") is not None
        assert store.find_active_by_signature("sig-y") is None

    def test_completed_signature_lookup(self, store):
        store.enqueue(_queued("job-aaaaaaaaaaaaaaaa", "sig-x"))
        store.claim_next("worker-1", 60.0)
        store.transition("job-aaaaaaaaaaaaaaaa", JobState.RUNNING)
        store.transition(
            "job-aaaaaaaaaaaaaaaa", JobState.COMPLETED, result_summary_json='{"r": 1.0}'
        )
        found = store.find_completed_by_signature("sig-x")
        assert found is not None
        assert found.result_summary == {"r": 1.0}
        assert store.find_active_by_signature("sig-x") is None
