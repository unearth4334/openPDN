"""The board store port.

Imported boards and their derived geometry are kept so that UI interactions --
toggling layers, selecting nets, switching views -- never trigger a re-parse.
The store key is derived from the source content digest plus the importer and
normaliser versions, so a stale cache cannot survive an incompatible pipeline
change (ADR-0007).

This is a contract: the concrete store (in-memory today; a persistent one when
persistence requirements exist) lives in infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openpdn.geometry.api import NormalizedGeometry
    from openpdn.pcb_import.api import ImportResult


@dataclass(frozen=True)
class StoredBoard:
    """One imported board with everything derived from it."""

    board_id: str
    source_name: str
    stored_at_epoch_s: float
    import_result: ImportResult
    normalized: NormalizedGeometry
    normalize_seconds: float


@runtime_checkable
class BoardStore(Protocol):
    """Keeps imported boards for the lifetime the deployment chooses."""

    def put(self, record: StoredBoard) -> None:
        """Store `record`, replacing any record with the same id."""
        ...

    def get(self, board_id: str) -> StoredBoard | None:
        """Return the stored board, or `None` when unknown."""
        ...

    def list_all(self) -> Sequence[StoredBoard]:
        """Return every stored board, most recently stored first."""
        ...
