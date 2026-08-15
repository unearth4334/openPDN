"""In-memory board store.

Imported boards live for the process lifetime, bounded in count so a long
session cannot exhaust memory. Restarting the process discards everything --
acceptable while imports take fractions of a second and boards are private
uploads; a persistent store arrives with a demonstrated requirement and an ADR
(see the architecture skill's rule on persistence).
"""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from openpdn.application.board_store import StoredBoard

#: Boards kept before the least recently stored is evicted. A stored board on
#: the reference fixture is a few tens of megabytes of geometry; eight bounds
#: worst-case memory near half a gigabyte while comfortably covering a review
#: session comparing several boards.
DEFAULT_CAPACITY: Final = 8


class InMemoryBoardStore:
    """`BoardStore` implementation over an LRU-bounded dictionary."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        """Create a store keeping at most `capacity` boards."""
        if capacity < 1:
            raise ValueError("Board store capacity must be at least 1")
        self._capacity = capacity
        self._records: OrderedDict[str, StoredBoard] = OrderedDict()
        self._lock = Lock()

    def put(self, record: StoredBoard) -> None:
        """Store `record`, evicting the oldest board beyond capacity."""
        with self._lock:
            self._records.pop(record.board_id, None)
            self._records[record.board_id] = record
            while len(self._records) > self._capacity:
                self._records.popitem(last=False)

    def get(self, board_id: str) -> StoredBoard | None:
        """Return the stored board, or `None` when unknown or evicted."""
        with self._lock:
            return self._records.get(board_id)

    def list_all(self) -> list[StoredBoard]:
        """Return every stored board, most recently stored first."""
        with self._lock:
            return list(reversed(self._records.values()))
