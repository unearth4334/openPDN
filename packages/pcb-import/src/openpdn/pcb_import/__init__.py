"""PCB import contract and adapters.

Importers are the only code allowed to understand a foreign PCB format. They
convert IPC-2581, ODB++, Gerber or vendor data into the canonical `Board`
model, and nothing downstream learns which format was read (ADR-0002).

IPC-2581 is openPDN's reference interchange format and first implementation
target (ADR-0006); ODB++ is a planned second adapter. Neither is privileged by
this contract.

Layering note: this package's *contract* (`openpdn.pcb_import.api`) may be
imported by application services. Its *adapters* -- `ipc2581`,
`canonical_json`, and in future `odbpp` -- may not; they are wired in the
composition root. The rule is enforced by
`tests/unit/test_architecture_boundaries.py`.
"""

from openpdn.pcb_import.api import (
    ImportCapability,
    ImportCapabilityItem,
    ImportCapabilityReport,
    ImporterDescriptor,
    ImporterNotReadyError,
    ImporterRegistry,
    ImportResult,
    MalformedSourceError,
    PCBImporter,
    PCBImportError,
    SimulationReadiness,
    UnsupportedFormatError,
    UnsupportedRevisionError,
)

__all__ = [
    "ImportCapability",
    "ImportCapabilityItem",
    "ImportCapabilityReport",
    "ImportResult",
    "ImporterDescriptor",
    "ImporterNotReadyError",
    "ImporterRegistry",
    "MalformedSourceError",
    "PCBImportError",
    "PCBImporter",
    "SimulationReadiness",
    "UnsupportedFormatError",
    "UnsupportedRevisionError",
]
