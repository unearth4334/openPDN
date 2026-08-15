"""Data returned by application services.

Plain frozen dataclasses. The HTTP layer maps them to Pydantic response models
and the CLI renders them as text or JSON; neither shape leaks back in here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CapabilityStatus(StrEnum):
    """How far a headline capability actually is.

    Reported by `/api/info` and `openpdn info` so that neither the UI nor the
    README can drift into claiming analyses openPDN cannot yet perform.

    `IN_DEVELOPMENT` means work has started and something real exists, but the
    capability cannot be used end to end. It is not a softer word for
    `IMPLEMENTED`: a user must never be offered a control backed by it.
    """

    IMPLEMENTED = "implemented"
    EXPERIMENTAL = "experimental"
    IN_DEVELOPMENT = "in_development"
    PLANNED = "planned"


@dataclass(frozen=True, slots=True)
class CapabilityInfo:
    """One headline capability and its status."""

    name: str
    status: CapabilityStatus
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SolverInfo:
    """A solver as presented to a user interface."""

    name: str
    version: str
    summary: str
    fidelity: str
    available: bool
    unavailable_reason: str | None = None
    supports_resistance_probes: bool = False
    supports_current_density: bool = False


@dataclass(frozen=True, slots=True)
class ImporterInfo:
    """An importer as presented to a user interface."""

    name: str
    version: str
    summary: str
    source_format: str
    file_extensions: tuple[str, ...]
    available: bool
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ApplicationInfo:
    """Everything a surface needs to describe this deployment."""

    name: str
    version: str
    api_version: str
    environment: str
    solvers: tuple[SolverInfo, ...] = field(default_factory=tuple)
    importers: tuple[ImporterInfo, ...] = field(default_factory=tuple)
    capabilities: tuple[CapabilityInfo, ...] = field(default_factory=tuple)
