"""The IPC-2581 adapter.

Implements `PCBImporter` end to end: secure parsing, revision handling, unit
normalisation, syntax reading (`syntax.py`) and semantic extraction
(`extract.py`). Everything IPC-2581-shaped terminates here; `load()` returns a
plain canonical `Board` with diagnostics, a capability report and run stats.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from openpdn.pcb_import.api import (
    ImporterDescriptor,
    ImportResult,
    ImportRunStats,
    MalformedSourceError,
    UnsupportedFormatError,
)
from openpdn.pcb_import.ipc2581.extract import extract_board
from openpdn.pcb_import.ipc2581.revision import (
    ROOT_LOCAL_NAME,
    SUPPORTED_REVISIONS,
    IPC2581Revision,
    detect_revision,
    local_name,
)
from openpdn.pcb_import.ipc2581.secure_xml import XmlLimits, parse_secure, sniff_prolog
from openpdn.pcb_import.ipc2581.syntax import read_document
from openpdn.pcb_import.ipc2581.units import unit_scale_to_m

if TYPE_CHECKING:
    from pathlib import Path
    from xml.etree.ElementTree import Element

_ADAPTER_VERSION: Final = "0.1.0"

#: Elements known to carry the document's `units` attribute, most authoritative
#: first. The attribute appears in several places and generators differ about
#: which they populate, so the search is ordered rather than assuming one.
_UNIT_BEARING_ELEMENTS: Final = ("CadHeader", "DictionaryStandard", "DictionaryUser", "Content")


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    """What a bounded structural pass over an IPC-2581 document established.

    Deliberately not a board: this is the evidence the importer has before any
    semantic extraction, and it is what lets the adapter say precisely what it
    recognised when it cannot yet finish the job.
    """

    revision: IPC2581Revision
    units_name: str
    unit_scale_to_m: float
    element_count: int
    sections: tuple[str, ...]


def inspect_document(source: Path, limits: XmlLimits | None = None) -> DocumentSummary:
    """Securely parse `source` and establish revision, units and structure.

    Raises:
        UnsupportedFormatError: If the document is not IPC-2581.
        UnsupportedRevisionError: If the revision is known but unsupported.
        MalformedSourceError: If the document is unreadable, declares no
            revision, or declares no units.
        UnsafeXmlError: If the document uses a refused XML feature or exceeds a
            resource limit.
    """
    root = parse_secure(source, limits)

    if local_name(root.tag) != ROOT_LOCAL_NAME:
        raise UnsupportedFormatError(
            f"{source.name} has root element <{local_name(root.tag)}>, "
            f"not <{ROOT_LOCAL_NAME}>: this is not an IPC-2581 document"
        )

    revision = detect_revision(root.get("revision"))
    units_name = _find_units(root)
    scale = unit_scale_to_m(units_name)

    return DocumentSummary(
        revision=revision,
        units_name=units_name.strip().upper(),
        unit_scale_to_m=scale,
        element_count=sum(1 for _ in root.iter()),
        sections=tuple(local_name(child.tag) for child in root),
    )


def _find_units(root: Element) -> str:
    """Return the document's declared unit name.

    Raises:
        MalformedSourceError: If no element declares units. Every dimension in
            the document would otherwise be ambiguous, and assuming
            millimetres is exactly the silent factor-of-25.4 error openPDN
            exists to avoid.
    """
    by_name: dict[str, str] = {}
    for element in root.iter():
        units = element.get("units")
        if units:
            by_name.setdefault(local_name(element.tag), units)

    for candidate in _UNIT_BEARING_ELEMENTS:
        if candidate in by_name:
            return by_name[candidate]
    if by_name:
        # Some other element carried it; use it rather than failing, and let
        # the caller see which element it came from via diagnostics later.
        return next(iter(by_name.values()))

    raise MalformedSourceError(
        f"IPC-2581 document declares no units on any of {', '.join(_UNIT_BEARING_ELEMENTS)}"
    )


class IPC2581Importer:
    """Reads IPC-2581 documents into the canonical board model."""

    def __init__(self, limits: XmlLimits | None = None) -> None:
        """Store the resource limits applied to untrusted documents."""
        self._limits = limits or XmlLimits()

    def describe(self) -> ImporterDescriptor:
        """Return identity and reach."""
        supported = "/".join(sorted(item.value for item in SUPPORTED_REVISIONS))
        return ImporterDescriptor(
            name="ipc2581",
            version=_ADAPTER_VERSION,
            summary=(
                f"IPC-2581 revision {supported} reference importer: stackup, nets, "
                "copper geometry, padstacks, vias, components and diagnostics"
            ),
            source_format="IPC-2581",
            file_extensions=(".xml", ".cvg"),
            available=True,
        )

    def can_load(self, source: Path) -> bool:
        """True when `source` looks like an IPC-2581 document.

        Sniffs the prolog rather than trusting the extension: `.xml` says
        nothing about which schema is inside, and openPDN will meet plenty of
        XML that is not IPC-2581.
        """
        if not source.is_file():
            return False
        prolog = sniff_prolog(source)
        return f"<{ROOT_LOCAL_NAME}" in prolog or f"<{ROOT_LOCAL_NAME}".lower() in prolog.lower()

    def load(self, source: Path) -> ImportResult:
        """Read `source` into a canonical board.

        Raises:
            UnsupportedFormatError: If the document is not IPC-2581.
            UnsupportedRevisionError: If the revision is known but unsupported.
            MalformedSourceError: If the document is unreadable, declares no
                revision or units, or describes an inconsistent board.
            UnsafeXmlError: If the document uses a refused XML feature or
                exceeds a resource limit.
        """
        parse_started = time.perf_counter()
        root = parse_secure(source, self._limits)

        if local_name(root.tag) != ROOT_LOCAL_NAME:
            raise UnsupportedFormatError(
                f"{source.name} has root element <{local_name(root.tag)}>, "
                f"not <{ROOT_LOCAL_NAME}>: this is not an IPC-2581 document"
            )
        revision = detect_revision(root.get("revision"))
        units_name = _find_units(root)
        scale_to_m = unit_scale_to_m(units_name)
        element_count = sum(1 for _ in root.iter())
        parse_seconds = time.perf_counter() - parse_started

        extract_started = time.perf_counter()
        document = read_document(root, revision, units_name.strip().upper())
        extracted = extract_board(
            document,
            source_name=source.name,
            digest=_sha256_of(source),
            scale_to_m=scale_to_m,
        )
        extract_seconds = time.perf_counter() - extract_started

        return ImportResult(
            board=extracted.board,
            diagnostics=extracted.diagnostics,
            capability_report=extracted.capability_report,
            stats=ImportRunStats(
                source_bytes=source.stat().st_size,
                parse_seconds=parse_seconds,
                extract_seconds=extract_seconds,
                element_count=element_count,
                feature_counts=extracted.feature_counts,
            ),
        )


def _sha256_of(source: Path) -> str:
    """Content digest used for provenance and cache identity."""
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
