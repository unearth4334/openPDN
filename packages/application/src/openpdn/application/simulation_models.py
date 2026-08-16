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
    """One load attachment group drawing a fixed current.

    `terminal_ids`/`via_ids` may together name several pads and vias sharing
    one current draw (a connector's ganged pins, a pin plus its via) --
    the equipotential-group model, not a per-member split.
    """

    current_a: float
    terminal_ids: tuple[str, ...] = ()
    via_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject non-physical load currents and empty attachments (untrusted input)."""
        if not self.terminal_ids and not self.via_ids:
            raise SimulationRequestError("A load needs at least one terminal or via")
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
    accuracy: AccuracyProfile
    source_terminal_ids: tuple[str, ...] = ()
    source_via_ids: tuple[str, ...] = ()
    name: str = ""
    source_voltage_v: float = 0.0
    loads: tuple[LoadSpec, ...] = ()
    to_terminal_ids: tuple[str, ...] = ()
    to_via_ids: tuple[str, ...] = ()
    via_plating_m: float | None = None

    def __post_init__(self) -> None:
        """Validate the shape of the request (referential checks come later)."""
        if not math.isfinite(self.source_voltage_v):
            raise SimulationRequestError("Source voltage must be finite")
        if not self.source_terminal_ids and not self.source_via_ids:
            raise SimulationRequestError("A simulation needs at least one source terminal or via")
        if self.via_plating_m is not None and not (
            math.isfinite(self.via_plating_m) and 0.0 < self.via_plating_m < 1e-3
        ):
            raise SimulationRequestError(
                "Via plating assumption must be a positive thickness below 1 mm"
            )
        if self.kind is SimulationKind.RESISTANCE:
            # A resistance probe reports R between exactly two terminals
            # (ADR-0010) -- it has no group or via-endpoint form. Accepting
            # extra members here would merge them into the excitation's
            # equipotential group (changing the measured resistance) while
            # the probe result itself only ever names one representative
            # terminal per side, silently hiding what was actually measured.
            # Refuse the ambiguity instead of solving something the result
            # can't fully describe.
            if len(self.source_terminal_ids) != 1 or self.source_via_ids:
                raise SimulationRequestError(
                    "A resistance study's source must be exactly one terminal "
                    "(no via, no additional group members)"
                )
            if len(self.to_terminal_ids) != 1 or self.to_via_ids:
                raise SimulationRequestError(
                    "A resistance study's second terminal must be exactly one "
                    "terminal (no via, no additional group members)"
                )
            if self.source_terminal_ids[0] == self.to_terminal_ids[0]:
                raise SimulationRequestError("Resistance endpoints must not share a terminal")
        if self.kind is SimulationKind.IR_DROP and not self.loads:
            raise SimulationRequestError("An IR-drop study needs at least one load")
        source_members = {*self.source_terminal_ids, *self.source_via_ids}
        load_members = {
            member for load in self.loads for member in (*load.terminal_ids, *load.via_ids)
        }
        overlap = source_members & load_members
        if overlap:
            # A source pins a node's potential; a load draws a fixed current
            # there. The same node cannot honour both -- refused here (not
            # just in AnalysisStudy.__post_init__) so the estimate/queue
            # request fails immediately instead of after a wasted mesh
            # build and solve.
            raise SimulationRequestError(f"A source and a load both attach to {sorted(overlap)!r}")


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
    source_terminal_ids: tuple[str, ...]
    source_via_ids: tuple[str, ...]
    source_voltage_v: float
    loads: tuple[LoadSpec, ...]
    to_terminal_ids: tuple[str, ...]
    to_via_ids: tuple[str, ...]
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
            "schema": 2,
            "job_id": self.job_id,
            "name": self.name,
            "kind": self.kind.value,
            "board_id": self.board_id,
            "board_digest": self.board_digest,
            "board_name": self.board_name,
            "net_id": self.net_id,
            "net_name": self.net_name,
            "source_terminal_ids": list(self.source_terminal_ids),
            "source_via_ids": list(self.source_via_ids),
            "source_voltage_v": self.source_voltage_v,
            "loads": [
                {
                    "terminal_ids": list(load.terminal_ids),
                    "via_ids": list(load.via_ids),
                    "current_a": load.current_a,
                }
                for load in self.loads
            ],
            "to_terminal_ids": list(self.to_terminal_ids),
            "to_via_ids": list(self.to_via_ids),
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
        """Deserialise a stored specification.

        Schema 1 (pre-multi-attachment) rows persist in production and must
        keep loading: their singular `source_terminal_id`/`to_terminal_id`/
        per-load `terminal_id` upgrade to one-member plural groups here
        rather than at the call site, so every reader gets the same shape.
        """
        data = json.loads(raw)
        source_terminal_ids = _upgrade_ids(data, "source_terminal_ids", "source_terminal_id")
        to_terminal_ids = _upgrade_ids(data, "to_terminal_ids", "to_terminal_id")
        return cls(
            job_id=data["job_id"],
            name=data["name"],
            kind=SimulationKind(data["kind"]),
            board_id=data["board_id"],
            board_digest=data["board_digest"],
            board_name=data["board_name"],
            net_id=data["net_id"],
            net_name=data["net_name"],
            source_terminal_ids=source_terminal_ids,
            source_via_ids=tuple(data.get("source_via_ids") or ()),
            source_voltage_v=data["source_voltage_v"],
            loads=tuple(
                LoadSpec(
                    terminal_ids=_upgrade_ids(item, "terminal_ids", "terminal_id"),
                    via_ids=tuple(item.get("via_ids") or ()),
                    current_a=item["current_a"],
                )
                for item in data["loads"]
            ),
            to_terminal_ids=to_terminal_ids,
            to_via_ids=tuple(data.get("to_via_ids") or ()),
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


def _upgrade_ids(data: dict[str, Any], plural_key: str, singular_key: str) -> tuple[str, ...]:
    """Read a schema-2 plural id list, falling back to a schema-1 singular id."""
    plural = data.get(plural_key)
    if plural is not None:
        return tuple(plural)
    singular = data.get(singular_key)
    return (singular,) if singular else ()


def analysis_signature(
    *,
    board_digest: str,
    kind: SimulationKind,
    net_id: str,
    source_terminal_ids: tuple[str, ...],
    source_via_ids: tuple[str, ...],
    source_voltage_v: float,
    loads: tuple[LoadSpec, ...],
    to_terminal_ids: tuple[str, ...],
    to_via_ids: tuple[str, ...],
    mesh: ResolvedMeshSpec,
    verify_convergence: bool,
    via_plating_m: float | None,
    solver_name: str,
    solver_version: str,
) -> str:
    """Deterministic hash over every solver-affecting input.

    Identical signatures mean identical numerical outcomes (same code, same
    inputs); anything else -- including a solver version bump -- changes the
    signature and therefore never silently reuses a stale result. Group
    membership is order-independent, so terminal/via ids are sorted before
    hashing: two drafts naming the same attachment group in a different pick
    order must still be recognised as the same study.

    This hashes group membership as sorted lists rather than the single
    string schema 1 used, so a fresh queue of an old single-terminal study
    produces a different signature than its schema-1 original -- duplicate
    detection resets across this change, but never mismatches going forward.
    """
    digest = hashlib.sha256()
    payload = json.dumps(
        {
            "board": board_digest,
            "kind": kind.value,
            "net": net_id,
            "source": [sorted(source_terminal_ids), sorted(source_via_ids), source_voltage_v],
            "loads": sorted(
                (sorted(load.terminal_ids), sorted(load.via_ids), load.current_a) for load in loads
            ),
            "to": [sorted(to_terminal_ids), sorted(to_via_ids)],
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
