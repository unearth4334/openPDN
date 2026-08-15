"""Simulation drafts, immutable job specifications and job records.

The lifecycle is deliberately one-way (ADR-0011):

    draft  ->  validated + estimated  ->  immutable SimulationJobSpec  ->  queued

Once queued, a specification never changes; editing anything creates a new
job. The `analysis_signature` hashes every solver-affecting input so exact
duplicates can be recognised and stale results invalidated -- results are
never reused because a display name matches.

Everything here is a plain dataclass with a hand-written JSON codec (stdlib
only): job specs outlive processes, so their storage format is part of the
application contract, not an ORM detail.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from openpdn.application.errors import ApplicationError


class SimulationRequestError(ApplicationError):
    """A draft that cannot become a valid simulation job."""


class SimulationKind(StrEnum):
    """The analysis a job performs."""

    IR_DROP = "ir_drop"
    RESISTANCE = "resistance"


class AccuracyProfile(StrEnum):
    """User-facing accuracy levels; each resolves to concrete mesh numbers."""

    PREVIEW = "preview"
    STANDARD = "standard"
    HIGH = "high"
    VERIFICATION = "verification"


class JobState(StrEnum):
    """Job lifecycle states. Transitions are enforced by the job store."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """True when no further transition may occur."""
        return self in {
            JobState.COMPLETED,
            JobState.COMPLETED_WITH_WARNINGS,
            JobState.FAILED,
            JobState.CANCELLED,
        }

    @property
    def is_active(self) -> bool:
        """True while the job may still consume worker resources."""
        return self in {JobState.QUEUED, JobState.CLAIMED, JobState.RUNNING, JobState.CANCELLING}


#: Legal state transitions. The job store refuses anything else.
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.CLAIMED, JobState.CANCELLING, JobState.CANCELLED}),
    JobState.CLAIMED: frozenset(
        {JobState.RUNNING, JobState.QUEUED, JobState.FAILED, JobState.CANCELLING}
    ),
    JobState.RUNNING: frozenset(
        {
            JobState.COMPLETED,
            JobState.COMPLETED_WITH_WARNINGS,
            JobState.FAILED,
            JobState.CANCELLING,
            JobState.QUEUED,  # lease-expiry recovery of an infra failure
        }
    ),
    JobState.CANCELLING: frozenset({JobState.CANCELLED, JobState.FAILED}),
    JobState.COMPLETED: frozenset(),
    JobState.COMPLETED_WITH_WARNINGS: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class LoadSpec:
    """One load terminal drawing a fixed current."""

    terminal_id: str
    current_a: float

    def __post_init__(self) -> None:
        """Reject non-physical load currents (untrusted input)."""
        if not math.isfinite(self.current_a) or self.current_a <= 0.0:
            raise SimulationRequestError(
                f"Load current must be a finite positive number, got {self.current_a!r}"
            )


@dataclass(frozen=True, slots=True)
class ResolvedMeshSpec:
    """Mesh sizing resolved from an accuracy profile, frozen into the spec.

    Profiles resolve against the board (element sizes scale with its
    diagonal), so the numbers -- not the profile name -- are what the spec
    stores: re-running the job years later must not depend on profile
    definitions of the day.
    """

    max_element_m: float
    min_element_m: float
    elements_across_feature: int
    growth_rate: float

    def __post_init__(self) -> None:
        """Reject non-physical sizing."""
        if not (
            math.isfinite(self.max_element_m)
            and math.isfinite(self.min_element_m)
            and 0.0 < self.min_element_m <= self.max_element_m
        ):
            raise SimulationRequestError("Mesh element sizes must be finite and ordered")
        if self.elements_across_feature < 1 or self.growth_rate <= 0.0:
            raise SimulationRequestError("Invalid mesh refinement controls")


@dataclass(frozen=True, slots=True)
class SimulationDraft:
    """A user's simulation request, before validation and estimation.

    All fields are treated as untrusted input; the service validates every
    reference against the board and every number for physical sanity.
    """

    kind: SimulationKind
    board_id: str
    net_id: str
    source_terminal_id: str
    accuracy: AccuracyProfile
    name: str = ""
    source_voltage_v: float = 0.0
    loads: tuple[LoadSpec, ...] = ()
    to_terminal_id: str | None = None
    via_plating_m: float | None = None

    def __post_init__(self) -> None:
        """Validate the shape of the request (referential checks come later)."""
        if not math.isfinite(self.source_voltage_v):
            raise SimulationRequestError("Source voltage must be finite")
        if self.via_plating_m is not None and not (
            math.isfinite(self.via_plating_m) and 0.0 < self.via_plating_m < 1e-3
        ):
            raise SimulationRequestError(
                "Via plating assumption must be a positive thickness below 1 mm"
            )
        if self.kind is SimulationKind.RESISTANCE:
            if self.to_terminal_id is None:
                raise SimulationRequestError("A resistance study needs a second terminal")
            if self.to_terminal_id == self.source_terminal_id:
                raise SimulationRequestError("Resistance endpoints must differ")
        if self.kind is SimulationKind.IR_DROP and not self.loads:
            raise SimulationRequestError("An IR-drop study needs at least one load")


@dataclass(frozen=True, slots=True)
class SimulationJobSpec:
    """The immutable, fully resolved description of one queued simulation."""

    job_id: str
    name: str
    kind: SimulationKind
    board_id: str
    board_digest: str
    board_name: str
    net_id: str
    net_name: str
    source_terminal_id: str
    source_voltage_v: float
    loads: tuple[LoadSpec, ...]
    to_terminal_id: str | None
    accuracy: AccuracyProfile
    mesh: ResolvedMeshSpec
    #: Verification profile: run a refined second mesh and compare.
    verify_convergence: bool
    via_plating_m: float | None
    solver_name: str
    created_at_epoch_s: float
    signature: str

    def to_json(self) -> str:
        """Serialise for durable storage."""
        payload: dict[str, Any] = {
            "schema": 1,
            "job_id": self.job_id,
            "name": self.name,
            "kind": self.kind.value,
            "board_id": self.board_id,
            "board_digest": self.board_digest,
            "board_name": self.board_name,
            "net_id": self.net_id,
            "net_name": self.net_name,
            "source_terminal_id": self.source_terminal_id,
            "source_voltage_v": self.source_voltage_v,
            "loads": [
                {"terminal_id": load.terminal_id, "current_a": load.current_a}
                for load in self.loads
            ],
            "to_terminal_id": self.to_terminal_id,
            "accuracy": self.accuracy.value,
            "mesh": {
                "max_element_m": self.mesh.max_element_m,
                "min_element_m": self.mesh.min_element_m,
                "elements_across_feature": self.mesh.elements_across_feature,
                "growth_rate": self.mesh.growth_rate,
            },
            "verify_convergence": self.verify_convergence,
            "via_plating_m": self.via_plating_m,
            "solver_name": self.solver_name,
            "created_at_epoch_s": self.created_at_epoch_s,
            "signature": self.signature,
        }
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> SimulationJobSpec:
        """Deserialise a stored specification."""
        data = json.loads(raw)
        return cls(
            job_id=data["job_id"],
            name=data["name"],
            kind=SimulationKind(data["kind"]),
            board_id=data["board_id"],
            board_digest=data["board_digest"],
            board_name=data["board_name"],
            net_id=data["net_id"],
            net_name=data["net_name"],
            source_terminal_id=data["source_terminal_id"],
            source_voltage_v=data["source_voltage_v"],
            loads=tuple(
                LoadSpec(terminal_id=item["terminal_id"], current_a=item["current_a"])
                for item in data["loads"]
            ),
            to_terminal_id=data["to_terminal_id"],
            accuracy=AccuracyProfile(data["accuracy"]),
            mesh=ResolvedMeshSpec(
                max_element_m=data["mesh"]["max_element_m"],
                min_element_m=data["mesh"]["min_element_m"],
                elements_across_feature=data["mesh"]["elements_across_feature"],
                growth_rate=data["mesh"]["growth_rate"],
            ),
            verify_convergence=data["verify_convergence"],
            via_plating_m=data["via_plating_m"],
            solver_name=data["solver_name"],
            created_at_epoch_s=data["created_at_epoch_s"],
            signature=data["signature"],
        )


def analysis_signature(
    *,
    board_digest: str,
    kind: SimulationKind,
    net_id: str,
    source_terminal_id: str,
    source_voltage_v: float,
    loads: tuple[LoadSpec, ...],
    to_terminal_id: str | None,
    mesh: ResolvedMeshSpec,
    verify_convergence: bool,
    via_plating_m: float | None,
    solver_name: str,
    solver_version: str,
) -> str:
    """Deterministic hash over every solver-affecting input.

    Identical signatures mean identical numerical outcomes (same code, same
    inputs); anything else -- including a solver version bump -- changes the
    signature and therefore never silently reuses a stale result.
    """
    digest = hashlib.sha256()
    payload = json.dumps(
        {
            "board": board_digest,
            "kind": kind.value,
            "net": net_id,
            "source": [source_terminal_id, source_voltage_v],
            "loads": sorted((load.terminal_id, load.current_a) for load in loads),
            "to": to_terminal_id,
            "mesh": [
                mesh.max_element_m,
                mesh.min_element_m,
                mesh.elements_across_feature,
                mesh.growth_rate,
            ],
            "verify": verify_convergence,
            "plating": via_plating_m,
            "solver": [solver_name, solver_version],
        },
        sort_keys=True,
    )
    digest.update(payload.encode())
    return digest.hexdigest()


class ComputeClass(StrEnum):
    """Coarse compute-cost label shown before queueing."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


@dataclass(frozen=True, slots=True)
class SimulationEstimate:
    """Pre-queue resource estimate, advisory for the UI.

    Limits are enforced server-side at queue time regardless of what a client
    displays.
    """

    mesh_points: int
    triangles: int
    dofs: int
    estimated_memory_bytes: int
    compute_class: ComputeClass
    over_budget: bool
    budget_dofs: int
    warnings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JobRecord:
    """One job as stored: the immutable spec plus mutable lifecycle state."""

    spec: SimulationJobSpec
    state: JobState
    stage: str = ""
    message: str = ""
    attempt: int = 0
    claimed_by: str | None = None
    lease_expires_at_epoch_s: float | None = None
    queued_at_epoch_s: float = 0.0
    started_at_epoch_s: float | None = None
    finished_at_epoch_s: float | None = None
    #: Compact headline metrics, populated on completion (JSON object).
    result_summary_json: str | None = None

    @property
    def result_summary(self) -> dict[str, Any] | None:
        """Parsed headline metrics, or None before completion."""
        if self.result_summary_json is None:
            return None
        parsed: dict[str, Any] = json.loads(self.result_summary_json)
        return parsed


#: Ordered pipeline stages a running job reports. Stage-based progress, not
#: fabricated percentages.
JOB_STAGES: tuple[str, ...] = (
    "validating",
    "loading_board",
    "meshing",
    "assembling",
    "solving",
    "postprocessing",
    "verifying_convergence",
    "serializing",
)


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    """Server-side resource limits enforced on every queue request."""

    max_dofs: int
    max_concurrent_jobs: int
    max_job_seconds: float
    lease_seconds: float
    max_attempts: int


def new_job_id(signature: str, created_at_epoch_s: float) -> str:
    """A short, collision-resistant job identifier."""
    raw = hashlib.sha256(f"{signature}:{created_at_epoch_s}".encode()).hexdigest()
    return f"job-{raw[:16]}"


@dataclass(frozen=True, slots=True)
class ConnectivityIssue:
    """Why a study is electrically impossible as posed."""

    message: str
    terminal_a: str
    terminal_b: str
