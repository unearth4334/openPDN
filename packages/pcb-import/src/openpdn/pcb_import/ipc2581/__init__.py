"""IPC-2581 importer -- openPDN's reference PCB interchange adapter.

IPC-2581 is the first implementation target (ADR-0006). That makes it the
*reference* importer, not a privileged one: everything it produces is a plain
canonical `Board`, and nothing downstream can tell which format was read.

Everything IPC-2581-shaped stops here. No XML element, namespace, revision or
IPC vocabulary may appear in the domain, the application layer, the solver
contract or the UI.

Status: **in development.** Implemented today are the boundary pieces that the
rest of the importer has to be built on and that are worth getting right before
any semantics exist:

* secure XML parsing of untrusted documents (`secure_xml`);
* format revision detection and explicit refusal of unsupported revisions
  (`revision`);
* unit normalisation to SI at the boundary (`units`).

Structural extraction -- stackup, layers, nets, pads, vias, components -- is
milestone 1; see `docs/architecture/roadmap.md`. `IPC2581Importer.load()`
reports what it recognised and refuses to invent the rest.
"""

from openpdn.pcb_import.ipc2581.importer import (
    DocumentSummary,
    IPC2581Importer,
    inspect_document,
)
from openpdn.pcb_import.ipc2581.revision import (
    SUPPORTED_REVISIONS,
    IPC2581Revision,
    detect_revision,
)
from openpdn.pcb_import.ipc2581.secure_xml import (
    UnsafeXmlError,
    XmlLimits,
    parse_secure,
)
from openpdn.pcb_import.ipc2581.units import IPC2581_UNIT_SCALE_TO_M, to_metres

__all__ = [
    "IPC2581_UNIT_SCALE_TO_M",
    "SUPPORTED_REVISIONS",
    "DocumentSummary",
    "IPC2581Importer",
    "IPC2581Revision",
    "UnsafeXmlError",
    "XmlLimits",
    "detect_revision",
    "inspect_document",
    "parse_secure",
    "to_metres",
]
