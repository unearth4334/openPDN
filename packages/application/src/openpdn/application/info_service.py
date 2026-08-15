"""Reports what this deployment is and what it can do.

Backs both `/api/health` + `/api/info` and `openpdn info`, so the two surfaces
cannot drift -- the vertical slice that proves the layering (ADR-0001).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from openpdn.application.dto import (
    ApplicationInfo,
    CapabilityInfo,
    CapabilityStatus,
    ImporterInfo,
    SolverInfo,
)
from openpdn.application.version import API_VERSION, APPLICATION_NAME, get_version

if TYPE_CHECKING:
    from openpdn.pcb_import.api import ImporterRegistry
    from openpdn.solver.api import SolverRegistry

#: The headline capabilities openPDN advertises, with their real status.
#: Promoting an entry to IMPLEMENTED requires validation tests that pass
#: against analytical references -- see `.agents/skills/testing/SKILL.md`.
#: This tuple is the single source of truth: the README table, the UI panel and
#: `/api/info` all describe what is here, so they cannot drift apart.
HEADLINE_CAPABILITIES: Final = (
    CapabilityInfo(
        name="IPC-2581 import",
        status=CapabilityStatus.IMPLEMENTED,
        detail=(
            "Reference interchange format (revision B): secure parsing, stackup, nets, "
            "copper geometry, padstacks, vias, components, diagnostics and readiness."
        ),
    ),
    CapabilityInfo(
        name="Geometry normalisation",
        status=CapabilityStatus.IMPLEMENTED,
        detail=(
            "Imported copper resolved and unioned per (net, layer) into solver-ready "
            "conductive regions with provenance."
        ),
    ),
    CapabilityInfo(
        name="Board review UI",
        status=CapabilityStatus.IMPLEMENTED,
        detail="Interactive viewer: layers, nets, stackup, vias, diagnostics, readiness.",
    ),
    CapabilityInfo(
        name="ODB++ import",
        status=CapabilityStatus.PLANNED,
        detail="Planned second importer, behind the same contract as IPC-2581.",
    ),
    CapabilityInfo(
        name="Canonical board model",
        status=CapabilityStatus.IMPLEMENTED,
        detail="Format-independent board, stackup, nets, copper, vias and terminals.",
    ),
    CapabilityInfo(
        name="IR-drop analysis",
        status=CapabilityStatus.PLANNED,
        detail="Requires the 2.5-D sheet-conduction solver.",
    ),
    CapabilityInfo(
        name="Current-density analysis",
        status=CapabilityStatus.PLANNED,
    ),
    CapabilityInfo(
        name="Via current",
        status=CapabilityStatus.PLANNED,
    ),
    CapabilityInfo(
        name="Terminal-to-terminal resistance",
        status=CapabilityStatus.PLANNED,
    ),
    CapabilityInfo(
        name="Resistive power-loss mapping",
        status=CapabilityStatus.PLANNED,
    ),
    CapabilityInfo(
        name="Electrothermal / 3-D refinement",
        status=CapabilityStatus.PLANNED,
        detail="Planned ElmerFEM backend behind the same solver contract.",
    ),
)


class ApplicationInfoService:
    """Describes the running deployment."""

    def __init__(
        self,
        environment: str,
        solvers: SolverRegistry,
        importers: ImporterRegistry,
    ) -> None:
        """Store the environment label and the adapter registries."""
        self._environment = environment
        self._solvers = solvers
        self._importers = importers

    def describe(self) -> ApplicationInfo:
        """Return identity, adapters and honest capability statuses."""
        return ApplicationInfo(
            name=APPLICATION_NAME,
            version=get_version(),
            api_version=API_VERSION,
            environment=self._environment,
            solvers=tuple(
                SolverInfo(
                    name=descriptor.name,
                    version=descriptor.version,
                    summary=descriptor.summary,
                    fidelity=str(descriptor.capabilities.fidelity),
                    available=descriptor.available,
                    unavailable_reason=descriptor.unavailable_reason,
                    supports_resistance_probes=(descriptor.capabilities.supports_resistance_probes),
                    supports_current_density=descriptor.capabilities.supports_current_density,
                )
                for descriptor in self._solvers.available()
            ),
            importers=tuple(
                ImporterInfo(
                    name=descriptor.name,
                    version=descriptor.version,
                    summary=descriptor.summary,
                    source_format=descriptor.source_format,
                    file_extensions=descriptor.file_extensions,
                    available=descriptor.available,
                    unavailable_reason=descriptor.unavailable_reason,
                )
                for descriptor in self._importers.available()
            ),
            capabilities=HEADLINE_CAPABILITIES,
        )
