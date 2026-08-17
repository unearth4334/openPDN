"""Simulation endpoints: estimation, queueing, jobs and result artifacts.

Request handlers never solve anything (ADR-0011): queueing writes a durable
row and returns; the orchestrator process executes. Estimation and planning
are CPU-bound and run in the threadpool.

Result field payloads are served as a compact little-endian binary layout the
viewer parses with typed arrays:

    u32 point_count | u32 triangle_count
    f32 x,y * point_count            (board metres)
    u32 a,b,c * triangle_count
    f32 voltage_v * point_count
    f32 j_a_per_m2 * triangle_count
    f32 power_w * triangle_count

Display buffers are float32 -- adequate for colour mapping; the authoritative
float64 arrays stay in the artifact store.
"""

from __future__ import annotations

import json
import math
import struct
from typing import TYPE_CHECKING, Annotated, Any, Literal

import numpy as np
from fastapi import APIRouter, Body
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from openpdn.api.dependencies import ContainerDep
from openpdn.api.schemas import ErrorResponse
from openpdn.application.errors import BoardNotFoundError
from openpdn.application.simulation_models import (
    AccuracyProfile,
    JobRecord,
    LayerThicknessOverrideSpec,
    LoadSpec,
    ReferencePolicy,
    ReferenceTier,
    SimulationDraft,
    SimulationKind,
    SimulationRequestError,
)
from openpdn.domain.materials import COPPER_ANNEALED

if TYPE_CHECKING:
    from pathlib import Path

router = APIRouter(prefix="/api", tags=["simulation"])


# -- request/response schemas ---------------------------------------------------------


class LoadRequest(BaseModel):
    """One load attachment group drawing a current."""

    terminal_ids: list[str] = Field(default_factory=list, max_length=256)
    via_ids: list[str] = Field(default_factory=list, max_length=256)
    current_a: float = Field(gt=0.0, allow_inf_nan=False)


class ThicknessOverrideRequest(BaseModel):
    """A study-supplied copper thickness for one stack-up layer."""

    layer_id: str = Field(min_length=1, max_length=256)
    thickness_um: float = Field(gt=0.0, lt=1000.0)


class ReferencePolicyRequest(BaseModel):
    """Adaptive policy for a Reference run.

    Required by the `reference` profile and rejected by the others, since a
    fixed-mesh profile has nothing to adapt. A `tier` names a measured
    preset (low / medium / high); any explicitly supplied field overrides
    the preset's value, and with neither tier nor fields the medium-strength
    defaults apply. Bounds here are shape checks; the administrative
    ceilings are enforced server-side, where a client cannot see or move
    them -- so a request within these bounds can still be refused.
    """

    tier: Literal["low", "medium", "high"] | None = None
    target_qoi_rel_change: float | None = Field(default=None, gt=0.0, lt=1.0)
    max_passes: int | None = Field(default=None, ge=1, le=32)
    max_dofs: int | None = Field(default=None, gt=0)
    theta: float | None = Field(default=None, gt=0.0, le=1.0)
    refinement_ratio: float | None = Field(default=None, gt=1.0, le=8.0)
    element_order: Literal["p1", "p2"] | None = None
    goal_oriented: bool | None = None
    linear_backend: Literal["auto", "direct", "iterative"] | None = None
    linear_tolerance_fraction: float | None = Field(default=None, gt=0.0, le=1.0)

    def to_policy(self) -> ReferencePolicy:
        """Resolve tier and overrides into the frozen application policy.

        The tier itself is never stored: exactly as accuracy profiles
        resolve to mesh numbers (ADR-0011), a tier resolves to policy
        numbers here and is then forgotten, so a re-run cannot depend on
        what "medium" meant the day the job was queued.
        """
        base = (
            ReferencePolicy.for_tier(ReferenceTier(self.tier))
            if self.tier is not None
            else ReferencePolicy()
        )
        return ReferencePolicy(
            target_qoi_rel_change=(
                base.target_qoi_rel_change
                if self.target_qoi_rel_change is None
                else self.target_qoi_rel_change
            ),
            max_passes=base.max_passes if self.max_passes is None else self.max_passes,
            max_dofs=base.max_dofs if self.max_dofs is None else self.max_dofs,
            theta=base.theta if self.theta is None else self.theta,
            refinement_ratio=(
                base.refinement_ratio if self.refinement_ratio is None else self.refinement_ratio
            ),
            element_order=base.element_order if self.element_order is None else self.element_order,
            goal_oriented=base.goal_oriented if self.goal_oriented is None else self.goal_oriented,
            linear_backend=(
                base.linear_backend if self.linear_backend is None else self.linear_backend
            ),
            linear_tolerance_fraction=(
                base.linear_tolerance_fraction
                if self.linear_tolerance_fraction is None
                else self.linear_tolerance_fraction
            ),
            confirmations=base.confirmations,
        )


class SimulationDraftRequest(BaseModel):
    """A simulation request as posted by the UI. Untrusted input."""

    kind: Literal["ir_drop", "resistance"]
    net_id: str = Field(min_length=1, max_length=256)
    source_terminal_ids: list[str] = Field(default_factory=list, max_length=256)
    source_via_ids: list[str] = Field(default_factory=list, max_length=256)
    accuracy: Literal["preview", "standard", "high", "verification", "reference"]
    name: str = Field(default="", max_length=200)
    source_voltage_v: float = Field(default=0.0, allow_inf_nan=False)
    loads: list[LoadRequest] = Field(default_factory=list, max_length=64)
    to_terminal_ids: list[str] = Field(default_factory=list, max_length=256)
    to_via_ids: list[str] = Field(default_factory=list, max_length=256)
    via_plating_um: float | None = Field(default=None, gt=0.0, lt=1000.0)
    conductor_material: Literal["copper_annealed", "custom"] | None = None
    conductor_conductivity_s_per_m: float | None = Field(default=None, gt=0.0)
    thickness_overrides: list[ThicknessOverrideRequest] = Field(default_factory=list, max_length=64)
    reference: ReferencePolicyRequest | None = None

    def to_draft(self, board_id: str) -> SimulationDraft:
        """Convert to the application draft (validating shape invariants).

        `conductor_material` names a preset; like a Reference tier, it
        resolves to plain numbers here and is then forgotten -- the draft and
        the job spec it becomes never store which preset was picked.
        """
        conductivity: float | None = None
        material_name: str | None = None
        if self.conductor_material == "custom":
            if self.conductor_conductivity_s_per_m is None:
                raise SimulationRequestError("Custom conductor material needs a conductivity value")
            conductivity, material_name = self.conductor_conductivity_s_per_m, "Custom"
        elif self.conductor_material == "copper_annealed":
            if self.conductor_conductivity_s_per_m is not None:
                raise SimulationRequestError(
                    "Copper (annealed) is a fixed material and cannot carry a custom conductivity"
                )
            conductivity, material_name = COPPER_ANNEALED.conductivity_s_per_m, COPPER_ANNEALED.name
        return SimulationDraft(
            kind=SimulationKind(self.kind),
            board_id=board_id,
            net_id=self.net_id,
            source_terminal_ids=tuple(self.source_terminal_ids),
            source_via_ids=tuple(self.source_via_ids),
            accuracy=AccuracyProfile(self.accuracy),
            name=self.name,
            source_voltage_v=self.source_voltage_v,
            loads=tuple(
                LoadSpec(
                    current_a=load.current_a,
                    terminal_ids=tuple(load.terminal_ids),
                    via_ids=tuple(load.via_ids),
                )
                for load in self.loads
            ),
            to_terminal_ids=tuple(self.to_terminal_ids),
            to_via_ids=tuple(self.to_via_ids),
            via_plating_m=None if self.via_plating_um is None else self.via_plating_um * 1e-6,
            conductor_conductivity_s_per_m=conductivity,
            conductor_material_name=material_name,
            thickness_overrides=tuple(
                LayerThicknessOverrideSpec(
                    layer_id=override.layer_id, thickness_m=override.thickness_um * 1e-6
                )
                for override in self.thickness_overrides
            ),
            reference_policy=None if self.reference is None else self.reference.to_policy(),
        )


class EstimateResponse(BaseModel):
    """Pre-queue estimate and validity for the UI."""

    mesh_points: int
    triangles: int
    dofs: int
    estimated_memory_bytes: int
    compute_class: str
    over_budget: bool
    budget_dofs: int
    connectivity_ok: bool
    connectivity_message: str | None
    warnings: list[str]
    assumptions: list[str]
    duplicate_result_job_id: str | None


class JobResponse(BaseModel):
    """One job's public state."""

    job_id: str
    name: str
    kind: str
    state: str
    stage: str
    message: str
    accuracy: str
    net_id: str
    net_name: str
    board_id: str
    created_at_epoch_s: float
    finished_at_epoch_s: float | None
    result_summary: dict[str, Any] | None

    @classmethod
    def from_record(cls, record: JobRecord) -> JobResponse:
        """Map a stored job record."""
        return cls(
            job_id=record.spec.job_id,
            name=record.spec.name,
            kind=record.spec.kind.value,
            state=record.state.value,
            stage=record.stage,
            message=record.message,
            accuracy=record.spec.accuracy.value,
            net_id=record.spec.net_id,
            net_name=record.spec.net_name,
            board_id=record.spec.board_id,
            created_at_epoch_s=record.spec.created_at_epoch_s,
            finished_at_epoch_s=record.finished_at_epoch_s,
            result_summary=record.result_summary,
        )


class QueueResponse(BaseModel):
    """Outcome of a queue request."""

    job: JobResponse
    duplicate_of: str | None


# -- endpoints ------------------------------------------------------------------------


def _error(status: int, kind: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=ErrorResponse(error=kind, detail=detail).model_dump()
    )


@router.post(
    "/boards/{board_id}/simulations/estimate",
    response_model=EstimateResponse,
    summary="Validate and estimate a simulation draft",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def estimate_simulation(
    board_id: str,
    request: Annotated[SimulationDraftRequest, Body()],
    container: ContainerDep,
) -> EstimateResponse | JSONResponse:
    """Estimate mesh/resource cost and check connectivity without queueing."""
    try:
        draft = request.to_draft(board_id)
        plan = await run_in_threadpool(container.simulation_service.plan, draft)
    except BoardNotFoundError as exc:
        return _error(404, "BoardNotFound", str(exc))
    except SimulationRequestError as exc:
        return _error(422, "InvalidSimulation", str(exc))
    previous = container.simulation_service.previous_result(plan.resolved_spec.signature)
    estimate = plan.estimate
    return EstimateResponse(
        mesh_points=estimate.mesh_points,
        triangles=estimate.triangles,
        dofs=estimate.dofs,
        estimated_memory_bytes=estimate.estimated_memory_bytes,
        compute_class=estimate.compute_class.value,
        over_budget=estimate.over_budget,
        budget_dofs=estimate.budget_dofs,
        connectivity_ok=plan.connectivity_ok,
        connectivity_message=plan.connectivity_message,
        warnings=list(estimate.warnings),
        assumptions=list(estimate.assumptions),
        duplicate_result_job_id=previous.spec.job_id if previous else None,
    )


@router.post(
    "/boards/{board_id}/simulations",
    response_model=QueueResponse,
    status_code=201,
    summary="Queue a simulation job",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def queue_simulation(
    board_id: str,
    request: Annotated[SimulationDraftRequest, Body()],
    container: ContainerDep,
) -> QueueResponse | JSONResponse:
    """Validate, enforce budgets and enqueue for the orchestrator."""
    try:
        draft = request.to_draft(board_id)
        queued = await run_in_threadpool(container.simulation_service.queue, draft)
    except BoardNotFoundError as exc:
        return _error(404, "BoardNotFound", str(exc))
    except SimulationRequestError as exc:
        return _error(422, "InvalidSimulation", str(exc))
    return QueueResponse(job=JobResponse.from_record(queued.job), duplicate_of=queued.duplicate_of)


@router.get("/jobs", response_model=list[JobResponse], summary="List recent jobs")
async def list_jobs(container: ContainerDep, limit: int = 50) -> list[JobResponse]:
    """Recent jobs, newest first. The UI polls this while its queue is open."""
    records = await run_in_threadpool(
        container.simulation_service.list_jobs, max(1, min(limit, 200))
    )
    return [JobResponse.from_record(record) for record in records]


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="One job's state",
    responses={404: {"model": ErrorResponse}},
)
async def get_job(job_id: str, container: ContainerDep) -> JobResponse | JSONResponse:
    """Current state of one job."""
    record = await run_in_threadpool(container.simulation_service.get, job_id)
    if record is None:
        return _error(404, "JobNotFound", f"No job {job_id!r}")
    return JobResponse.from_record(record)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobResponse,
    summary="Cancel a job",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def cancel_job(job_id: str, container: ContainerDep) -> JobResponse | JSONResponse:
    """Request cancellation; the orchestrator terminates any running worker."""
    if not await run_in_threadpool(container.simulation_service.cancel, job_id):
        record = container.simulation_service.get(job_id)
        if record is None:
            return _error(404, "JobNotFound", f"No job {job_id!r}")
        return _error(409, "NotCancellable", f"Job is {record.state.value}")
    record = container.simulation_service.get(job_id)
    if record is None:  # pragma: no cover - cancel succeeded moments ago
        return _error(404, "JobNotFound", f"No job {job_id!r}")
    return JobResponse.from_record(record)


@router.get(
    "/results/{job_id}/metrics",
    summary="A result's full metrics document",
    responses={404: {"model": ErrorResponse}},
)
async def result_metrics(job_id: str, container: ContainerDep) -> Response:
    """The published metrics.json for a completed job."""
    directory = await run_in_threadpool(_result_dir, container, job_id)
    if directory is None:
        return _error(404, "ResultNotFound", f"No published result for {job_id!r}")
    return Response(
        content=(directory / "metrics.json").read_bytes(), media_type="application/json"
    )


@router.get(
    "/results/{job_id}/manifest",
    summary="A result's provenance manifest",
    responses={404: {"model": ErrorResponse}},
)
async def result_manifest(job_id: str, container: ContainerDep) -> Response:
    """The published manifest.json (provenance) for a completed job."""
    directory = await run_in_threadpool(_result_dir, container, job_id)
    if directory is None:
        return _error(404, "ResultNotFound", f"No published result for {job_id!r}")
    return Response(
        content=(directory / "manifest.json").read_bytes(), media_type="application/json"
    )


@router.get(
    "/results/{job_id}/fields/{layer_index}",
    summary="Binary mesh + field payload for one layer",
    responses={404: {"model": ErrorResponse}},
)
async def result_fields(job_id: str, layer_index: int, container: ContainerDep) -> Response:
    """One layer's mesh and scalar fields in the documented binary layout."""
    payload = await run_in_threadpool(_field_payload, container, job_id, layer_index)
    if payload is None:
        return _error(404, "ResultNotFound", "No such result layer")
    return Response(content=payload, media_type="application/octet-stream")


def _result_dir(container: Any, job_id: str) -> Path | None:
    """Resolve a published result directory through the artifact store only."""
    try:
        return container.artifact_store.result_dir(job_id)  # type: ignore[no-any-return]
    except Exception:
        # Invalid job-id shapes are 404s, not 500s.
        return None


def _field_payload(container: Any, job_id: str, layer_index: int) -> bytes | None:
    """Assemble the binary field payload for one layer."""
    directory = _result_dir(container, job_id)
    if directory is None or not 0 <= layer_index < 512:
        return None
    metrics = json.loads((directory / "metrics.json").read_text())
    files = metrics.get("layer_files", [])
    entry = next((item for item in files if item["file"] == f"layers/{layer_index}.npz"), None)
    if entry is None:
        return None
    with np.load(directory / entry["file"], allow_pickle=False) as data:
        points = np.ascontiguousarray(data["points"], dtype=np.float32)
        triangles = np.ascontiguousarray(data["triangles"], dtype=np.uint32)
        voltage = np.ascontiguousarray(data["voltage_v"], dtype=np.float32)
        j_mag = np.ascontiguousarray(data["j_a_per_m2"], dtype=np.float32)
        power = np.ascontiguousarray(data["power_w"], dtype=np.float32)
    voltage = np.nan_to_num(voltage, nan=float("nan"))
    header = struct.pack("<II", len(points), len(triangles))
    return b"".join(
        [
            header,
            points.tobytes(),
            triangles.tobytes(),
            voltage.tobytes(),
            j_mag.tobytes(),
            power.tobytes(),
        ]
    )


def _finite(value: float) -> float:
    """Guard for JSON serialisation of possibly-NaN floats."""
    return value if math.isfinite(value) else 0.0
