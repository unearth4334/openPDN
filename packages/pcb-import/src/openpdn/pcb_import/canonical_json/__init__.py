"""Reader and writer for openPDN's own canonical board interchange format.

This is an adapter, not a contract: application code must depend on
`openpdn.pcb_import.api` and receive this class through the composition root.

The format exists for three concrete jobs:

* regression fixtures -- small, reviewable boards checked into `tests/fixtures`;
* a golden-snapshot target for format importers to be diffed against, which
  is how two independent importers of the same board get compared;
* round-tripping a board without re-reading fabrication data.

It is *not* a substitute for a real importer and carries no interchange-format
semantics of its own.
"""

from openpdn.pcb_import.canonical_json.codec import (
    CANONICAL_FORMAT_VERSION,
    board_from_document,
    board_to_document,
)
from openpdn.pcb_import.canonical_json.importer import CanonicalJsonImporter

__all__ = [
    "CANONICAL_FORMAT_VERSION",
    "CanonicalJsonImporter",
    "board_from_document",
    "board_to_document",
]
