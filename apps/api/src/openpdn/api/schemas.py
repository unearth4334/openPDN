"""HTTP response models.

Pydantic stops here. Application DTOs are plain dataclasses; these models are
their wire representation, and the mapping is explicit so a rename in one does
not silently change the public API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from openpdn.application.dto import ApplicationInfo


class HealthResponse(BaseModel):
    """Liveness and identity of the running instance."""

    status: Literal["ok"] = Field(description="Always 'ok'; failures surface as HTTP 5xx.")
    name: str = Field(description="Application name.")
    version: str = Field(description="Installed openPDN version.")
    api_version: str = Field(description="HTTP API contract version.")
    environment: str = Field(description="Deployment environment label.")


class CapabilityResponse(BaseModel):
    """One headline capability and how far it actually is."""

    name: str
    status: Literal["implemented", "experimental", "in_development", "planned"]
    detail: str | None = None


class SolverResponse(BaseModel):
    """A solver offered by this deployment."""

    name: str
    version: str
    summary: str
    fidelity: str = Field(
        description="Physics actually applied: mock | sheet_2p5d | volume_3d.",
    )
    available: bool
    unavailable_reason: str | None = None
    supports_resistance_probes: bool
    supports_current_density: bool


class ImporterResponse(BaseModel):
    """A PCB importer offered by this deployment."""

    name: str
    version: str
    summary: str
    source_format: str
    file_extensions: list[str]
    available: bool
    unavailable_reason: str | None = None


class InfoResponse(BaseModel):
    """Everything a client needs to describe this deployment."""

    name: str
    version: str
    api_version: str
    environment: str
    solvers: list[SolverResponse]
    importers: list[ImporterResponse]
    capabilities: list[CapabilityResponse]

    @classmethod
    def from_dto(cls, info: ApplicationInfo) -> InfoResponse:
        """Map an application DTO onto the wire model."""
        return cls(
            name=info.name,
            version=info.version,
            api_version=info.api_version,
            environment=info.environment,
            solvers=[
                SolverResponse(
                    name=solver.name,
                    version=solver.version,
                    summary=solver.summary,
                    fidelity=solver.fidelity,
                    available=solver.available,
                    unavailable_reason=solver.unavailable_reason,
                    supports_resistance_probes=solver.supports_resistance_probes,
                    supports_current_density=solver.supports_current_density,
                )
                for solver in info.solvers
            ],
            importers=[
                ImporterResponse(
                    name=importer.name,
                    version=importer.version,
                    summary=importer.summary,
                    source_format=importer.source_format,
                    file_extensions=list(importer.file_extensions),
                    available=importer.available,
                    unavailable_reason=importer.unavailable_reason,
                )
                for importer in info.importers
            ],
            capabilities=[
                CapabilityResponse(
                    name=capability.name,
                    status=capability.status.value,
                    detail=capability.detail,
                )
                for capability in info.capabilities
            ],
        )


class ErrorResponse(BaseModel):
    """A failure, in a shape the UI can render without guessing."""

    error: str = Field(description="Stable error class name.")
    detail: str = Field(description="Human-readable explanation.")
