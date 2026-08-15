"""The PCB importer contract.

Contains no format knowledge and no third-party imports. Nothing here mentions
IPC-2581, ODB++, XML or archives: this is the boundary those formats terminate
at (ADR-0006).

Deviation from the original sketch `load(source: Path) -> Board`: `load`
returns an `ImportResult`, which pairs the board with the diagnostics produced
while reading it. Importers routinely have to repair outlines and stand in for
missing thicknesses; those facts have to reach the user, and the alternatives
were to hide them in logs or to write them into the board itself. See
ADR-0002.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from openpdn.domain.board import Board
    from openpdn.domain.results import Diagnostic


class PCBImportError(Exception):
    """Base class for every failure raised by an importer adapter."""


class UnsupportedFormatError(PCBImportError):
    """The source is not in a format this importer understands."""


class MalformedSourceError(PCBImportError):
    """The source is the right format but cannot be read.

    Imported files are untrusted input (see SECURITY.md): adapters raise this
    instead of propagating a third-party parser's exception, and never include
    raw file content in the message.
    """


class UnsupportedRevisionError(PCBImportError):
    """The source is a known format at a revision this adapter cannot read.

    Interchange formats are revised, and revisions change semantics. Reading a
    document with the wrong revision's rules produces a plausible board that is
    quietly wrong, so adapters fail here instead of guessing.
    """


class ImporterNotReadyError(PCBImportError):
    """The adapter recognises the source but cannot yet produce a board.

    Used while an importer is under construction: the user learns what *was*
    understood and what is still missing, rather than seeing a format they can
    see is supported reported as unrecognised.
    """


class ImportCapability(StrEnum):
    """Whether one ingredient of a simulation-ready board was obtained."""

    PRESENT = "present"
    PARTIAL = "partial"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class SimulationReadiness(StrEnum):
    """How usable an imported board is for electrical analysis."""

    READY = "ready"
    READY_WITH_ASSUMPTIONS = "ready_with_assumptions"
    NOT_READY = "not_ready"


@dataclass(frozen=True, slots=True)
class ImportCapabilityItem:
    """One line of an import readiness report."""

    name: str
    status: ImportCapability
    note: str | None = None


@dataclass(frozen=True)
class ImportCapabilityReport:
    """What an importer managed to obtain from a source document.

    A board can be structurally valid and still unusable for analysis -- copper
    thickness absent, via plating unknown, connectivity partial. This report is
    how an importer says so in a form a UI can render as a checklist and a CLI
    as a table, rather than burying it in prose.

    Format-independent by construction: `source_format` and `format_revision`
    are strings the adapter fills in, and no field names a particular format.
    """

    source_format: str
    format_revision: str | None = None
    items: tuple[ImportCapabilityItem, ...] = field(default_factory=tuple)
    readiness: SimulationReadiness = SimulationReadiness.NOT_READY

    def missing(self) -> tuple[ImportCapabilityItem, ...]:
        """Return the ingredients that were absent or only partly obtained."""
        return tuple(
            item
            for item in self.items
            if item.status in (ImportCapability.ABSENT, ImportCapability.PARTIAL)
        )


@dataclass(frozen=True, slots=True)
class ImportRunStats:
    """Performance and volume instrumentation for one import.

    Format-independent by construction: `feature_counts` is keyed by labels the
    adapter chooses (e.g. `"strokes"`, `"contours"`, `"flashes"`); nothing here
    names a particular interchange format. Used for regression baselines and
    for the structured `pcb.import.finished` log event.
    """

    source_bytes: int | None = None
    parse_seconds: float | None = None
    extract_seconds: float | None = None
    element_count: int | None = None
    feature_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportResult:
    """A canonical board plus everything worth telling the user about it."""

    board: Board
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)
    capability_report: ImportCapabilityReport | None = None
    stats: ImportRunStats | None = None


@dataclass(frozen=True, slots=True)
class ImporterDescriptor:
    """Identity and reach of one registered importer.

    Attributes:
        name: Stable registry key, e.g. `"ipc2581"`, `"canonical-json"`.
        version: Adapter version.
        summary: One line shown in the UI.
        source_format: Human-readable format label, e.g. `"IPC-2581"`.
        file_extensions: Extensions the importer recognises, lowercase, with a
            leading dot. Empty for directory-based formats.
        available: False when an external dependency is missing; the importer
            stays listed so the UI can explain why it cannot be used.
        unavailable_reason: Populated when `available` is False.
    """

    name: str
    version: str
    summary: str
    source_format: str
    file_extensions: tuple[str, ...] = ()
    available: bool = True
    unavailable_reason: str | None = None


@runtime_checkable
class PCBImporter(Protocol):
    """Converts a foreign PCB representation into the canonical model."""

    def describe(self) -> ImporterDescriptor:
        """Return identity and reach. Must not read `source`."""
        ...

    def can_load(self, source: Path) -> bool:
        """Cheap check -- prefer inspecting the document over trusting its name.

        A filename extension is a hint, not evidence: `.xml` says nothing about
        which schema is inside. Sniff a bounded prefix of the document where
        the format allows it. Never a full parse.
        """
        ...

    def load(self, source: Path) -> ImportResult:
        """Read `source` and return the canonical board.

        Raises:
            UnsupportedFormatError: If the source is not this importer's format.
            UnsupportedRevisionError: If the format revision is not supported.
            MalformedSourceError: If the source is unreadable or inconsistent.
            ImporterNotReadyError: If the adapter is still under construction.
        """
        ...


class ImporterRegistry(Protocol):
    """Read-only lookup of the importers this deployment offers."""

    def available(self) -> Sequence[ImporterDescriptor]:
        """Describe every registered importer, including unavailable ones."""
        ...

    def get(self, name: str) -> PCBImporter:
        """Return the importer registered under `name`.

        Raises:
            UnsupportedFormatError: If no such importer is registered.
        """
        ...
