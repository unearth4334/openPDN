"""Reference jobs freeze a policy, not a mesh (ADR-0015).

Two rules carry most of the weight here. A Reference spec pins the adaptive
*policy* to absolute numbers, because the mesh is the run's output rather
than its input -- which keeps ADR-0011's reproducibility rationale intact
while relaxing its letter. And result quality is a property of the result,
mapped onto job states that already exist, so `ALLOWED_TRANSITIONS` remains
the single source of truth for the lifecycle.
"""

from __future__ import annotations

import json

import pytest

from openpdn.application.simulation_models import (
    AccuracyProfile,
    JobState,
    LoadSpec,
    ReferencePolicy,
    ReferenceTier,
    ResolvedMeshSpec,
    ResultQuality,
    SimulationDraft,
    SimulationJobSpec,
    SimulationKind,
    SimulationRequestError,
    WorkerLimits,
    analysis_signature,
)
from openpdn.application.simulation_service import SimulationService


def _mesh() -> ResolvedMeshSpec:
    return ResolvedMeshSpec(
        max_element_m=1e-3, min_element_m=1e-5, elements_across_feature=4, growth_rate=0.7
    )


def _spec(**overrides) -> SimulationJobSpec:
    base = {
        "job_id": "job-1",
        "name": "reference run",
        "kind": SimulationKind.IR_DROP,
        "board_id": "board-1",
        "board_digest": "digest-1",
        "board_name": "board",
        "net_id": "net-1",
        "net_name": "NET1",
        "source_terminal_ids": ("t-a",),
        "source_via_ids": (),
        "source_voltage_v": 1.0,
        "loads": (LoadSpec(current_a=1.0, terminal_ids=("t-b",)),),
        "to_terminal_ids": (),
        "to_via_ids": (),
        "accuracy": AccuracyProfile.REFERENCE,
        "mesh": _mesh(),
        "verify_convergence": False,
        "via_plating_m": None,
        "solver_name": "fem-2p5d",
        "created_at_epoch_s": 1000.0,
        "signature": "sig-1",
        "reference_policy": ReferencePolicy(),
    }
    return SimulationJobSpec(**{**base, **overrides})


class TestResultQualityMapping:
    def test_only_a_converged_run_reports_a_clean_completion(self):
        assert ResultQuality.CONVERGED.job_state is JobState.COMPLETED

    @pytest.mark.parametrize(
        "quality",
        [
            ResultQuality.CONVERGED_WITH_MODEL_LIMITATIONS,
            ResultQuality.RESOURCE_LIMITED,
            ResultQuality.NOT_CONVERGED,
        ],
    )
    def test_every_qualified_finish_maps_to_completed_with_warnings(self, quality):
        # The single failure mode this tier exists to prevent: a run that hit
        # its ceiling while still moving must not present as a clean tick.
        assert quality.job_state is JobState.COMPLETED_WITH_WARNINGS

    def test_a_numerical_failure_is_a_failure(self):
        assert ResultQuality.NUMERICAL_FAILURE.job_state is JobState.FAILED

    def test_only_converged_results_are_trustworthy_as_answers(self):
        trustworthy = {q for q in ResultQuality if q.is_trustworthy}
        assert trustworthy == {
            ResultQuality.CONVERGED,
            ResultQuality.CONVERGED_WITH_MODEL_LIMITATIONS,
        }

    def test_no_new_lifecycle_states_were_introduced(self):
        # ADR-0015 §3: quality is a result property. Adding states to the
        # lifecycle would need them in ALLOWED_TRANSITIONS first, or not at
        # all -- so every mapping target must already exist.
        assert {q.job_state for q in ResultQuality} <= set(JobState)


class TestPolicyValidation:
    def test_a_sane_policy_is_accepted(self):
        assert ReferencePolicy().max_passes >= 1

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("target_qoi_rel_change", 0.0),
            ("target_qoi_rel_change", 1.5),
            ("max_passes", 0),
            ("max_dofs", 0),
            ("theta", 0.0),
            ("theta", 1.5),
            ("refinement_ratio", 1.0),
            ("element_order", "p3"),
            ("linear_backend", "magic"),
        ],
    )
    def test_a_policy_that_could_not_work_is_refused(self, field: str, value):
        with pytest.raises(SimulationRequestError):
            ReferencePolicy(**{field: value})

    def test_a_policy_round_trips_through_its_payload(self):
        policy = ReferencePolicy(max_passes=7, theta=0.6, element_order="p1")
        assert ReferencePolicy.from_payload(policy.to_payload()) == policy


class TestTierPresets:
    """Named tiers resolve to numbers and are then forgotten (ADR-0011)."""

    def test_the_ladder_orders_strictly(self):
        low = ReferencePolicy.for_tier(ReferenceTier.LOW)
        medium = ReferencePolicy.for_tier(ReferenceTier.MEDIUM)
        high = ReferencePolicy.for_tier(ReferenceTier.HIGH)
        assert low.target_qoi_rel_change > medium.target_qoi_rel_change
        assert medium.target_qoi_rel_change > high.target_qoi_rel_change
        assert low.max_passes < medium.max_passes < high.max_passes
        assert low.max_dofs < medium.max_dofs < high.max_dofs
        # Stronger claims demand more consecutive confirming passes,
        # because two non-nested meshes can agree by accident.
        assert low.confirmations < medium.confirmations < high.confirmations

    def test_bare_defaults_are_the_medium_tier(self):
        # An unqualified Reference request means "medium": the same numbers
        # whether or not the tier name is spelled out.
        assert ReferencePolicy() == ReferencePolicy.for_tier(ReferenceTier.MEDIUM)

    def test_every_tier_fits_the_default_administrative_ceilings(self):
        # A preset the server would refuse out of the box is a trap, not a
        # convenience.
        limits = WorkerLimits(
            max_dofs=1_500_000,
            max_concurrent_jobs=1,
            max_job_seconds=1800.0,
            lease_seconds=60.0,
            max_attempts=3,
        )
        for tier in ReferenceTier:
            policy = ReferencePolicy.for_tier(tier)
            assert policy.max_passes <= limits.max_reference_passes
            assert policy.max_dofs <= limits.max_reference_dofs

    def test_the_resolved_policy_carries_no_tier_name(self):
        # The spec stores resolved values only; "medium" must not be
        # recoverable from (or hashed into) the frozen policy.
        payload = ReferencePolicy.for_tier(ReferenceTier.MEDIUM).to_payload()
        assert "tier" not in payload


class TestDraftValidation:
    def _draft(self, **overrides) -> SimulationDraft:
        base = {
            "kind": SimulationKind.IR_DROP,
            "board_id": "board-1",
            "net_id": "net-1",
            "accuracy": AccuracyProfile.REFERENCE,
            "source_terminal_ids": ("t-a",),
            "source_voltage_v": 1.0,
            "loads": (LoadSpec(current_a=1.0, terminal_ids=("t-b",)),),
            "reference_policy": ReferencePolicy(),
        }
        return SimulationDraft(**{**base, **overrides})

    def test_reference_requires_a_policy(self):
        with pytest.raises(SimulationRequestError, match="adaptive policy"):
            self._draft(reference_policy=None)

    def test_a_fixed_mesh_profile_refuses_a_policy(self):
        # Ignoring it would let a user believe a Standard job was refining.
        with pytest.raises(SimulationRequestError, match="cannot carry an adaptive policy"):
            self._draft(accuracy=AccuracyProfile.STANDARD)

    def test_a_fixed_mesh_profile_without_a_policy_is_fine(self):
        draft = self._draft(accuracy=AccuracyProfile.STANDARD, reference_policy=None)
        assert draft.reference_policy is None

    def test_only_reference_is_adaptive(self):
        adaptive = {p for p in AccuracyProfile if p.is_adaptive}
        assert adaptive == {AccuracyProfile.REFERENCE}


class TestSpecPersistence:
    def test_a_reference_spec_round_trips(self):
        spec = _spec()
        reloaded = SimulationJobSpec.from_json(spec.to_json())
        assert reloaded.reference_policy == spec.reference_policy
        assert reloaded.accuracy is AccuracyProfile.REFERENCE

    def test_the_schema_version_advanced(self):
        assert json.loads(_spec().to_json())["schema"] == 3

    def test_a_schema_two_row_loads_without_a_policy(self):
        # Rows queued before the tier existed were queued against a fixed
        # mesh and must re-run that way, not silently acquire adaptivity.
        payload = json.loads(_spec(accuracy=AccuracyProfile.STANDARD).to_json())
        payload["schema"] = 2
        del payload["reference_policy"]
        reloaded = SimulationJobSpec.from_json(json.dumps(payload))
        assert reloaded.reference_policy is None


class TestSignature:
    def _signature(self, policy: ReferencePolicy | None) -> str:
        return analysis_signature(
            board_digest="digest-1",
            kind=SimulationKind.IR_DROP,
            net_id="net-1",
            source_terminal_ids=("t-a",),
            source_via_ids=(),
            source_voltage_v=1.0,
            loads=(LoadSpec(current_a=1.0, terminal_ids=("t-b",)),),
            to_terminal_ids=(),
            to_via_ids=(),
            mesh=_mesh(),
            verify_convergence=False,
            via_plating_m=None,
            solver_name="fem-2p5d",
            solver_version="0.1.0",
            reference_policy=policy,
        )

    def test_the_policy_changes_the_signature(self):
        # Exact-match reuse must not hand back a result computed under a
        # different target or a different ceiling.
        assert self._signature(ReferencePolicy()) != self._signature(
            ReferencePolicy(target_qoi_rel_change=1e-4)
        )

    def test_an_identical_policy_gives_an_identical_signature(self):
        assert self._signature(ReferencePolicy()) == self._signature(ReferencePolicy())

    def test_an_adaptive_run_differs_from_a_fixed_mesh_one(self):
        assert self._signature(None) != self._signature(ReferencePolicy())


class TestServerSideCeilings:
    """ADR-0015 §7: administrative maxima, enforced whatever the client sent."""

    def _limits(self) -> WorkerLimits:
        return WorkerLimits(
            max_dofs=1_500_000,
            max_concurrent_jobs=1,
            max_job_seconds=1800.0,
            lease_seconds=60.0,
            max_attempts=3,
            max_reference_passes=4,
            max_reference_dofs=100_000,
        )

    def _service(self) -> SimulationService:
        service = SimulationService.__new__(SimulationService)
        service._limits = self._limits()
        return service

    def _draft(self, **policy_overrides) -> SimulationDraft:
        return SimulationDraft(
            kind=SimulationKind.IR_DROP,
            board_id="board-1",
            net_id="net-1",
            accuracy=AccuracyProfile.REFERENCE,
            source_terminal_ids=("t-a",),
            source_voltage_v=1.0,
            loads=(LoadSpec(current_a=1.0, terminal_ids=("t-b",)),),
            reference_policy=ReferencePolicy(**policy_overrides),
        )

    def test_a_policy_within_the_ceilings_is_accepted(self):
        self._service()._enforce_reference_ceilings(self._draft(max_passes=4, max_dofs=100_000))

    def test_too_many_passes_is_refused(self):
        with pytest.raises(SimulationRequestError, match="above the configured maximum"):
            self._service()._enforce_reference_ceilings(self._draft(max_passes=5))

    def test_too_large_a_dof_ceiling_is_refused(self):
        with pytest.raises(SimulationRequestError, match="above the configured maximum"):
            self._service()._enforce_reference_ceilings(self._draft(max_dofs=200_000))

    def test_refused_rather_than_clamped(self):
        # Silently holding a run to a lower ceiling would make it report
        # RESOURCE_LIMITED for a limit the user never chose.
        draft = self._draft(max_passes=99)
        with pytest.raises(SimulationRequestError):
            self._service()._enforce_reference_ceilings(draft)
        assert draft.reference_policy is not None
        assert draft.reference_policy.max_passes == 99  # untouched

    def test_a_non_adaptive_draft_is_unaffected(self):
        draft = SimulationDraft(
            kind=SimulationKind.IR_DROP,
            board_id="board-1",
            net_id="net-1",
            accuracy=AccuracyProfile.STANDARD,
            source_terminal_ids=("t-a",),
            source_voltage_v=1.0,
            loads=(LoadSpec(current_a=1.0, terminal_ids=("t-b",)),),
        )
        self._service()._enforce_reference_ceilings(draft)


class TestAdmissionSizing:
    def test_an_adaptive_spec_is_admitted_against_its_ceiling(self):
        # The achieved size is unknowable in advance, so admission reasons
        # about the worst case the policy allows.
        spec = _spec(reference_policy=ReferencePolicy(max_dofs=750_000), estimated_dofs=750_000)
        assert spec.estimated_dofs == 750_000

    def test_estimated_dofs_survive_a_round_trip(self):
        spec = _spec(estimated_dofs=12_345)
        assert SimulationJobSpec.from_json(spec.to_json()).estimated_dofs == 12_345

    def test_a_spec_without_an_estimate_loads_as_zero(self):
        payload = json.loads(_spec().to_json())
        del payload["estimated_dofs"]
        assert SimulationJobSpec.from_json(json.dumps(payload)).estimated_dofs == 0
