"""`PCBImporter` implementation for the canonical JSON format."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final

from openpdn.domain.board import Board
from openpdn.domain.provenance import Provenance
from openpdn.domain.results import Diagnostic, DiagnosticSeverity
from openpdn.pcb_import.api import (
    ImporterDescriptor,
    ImportResult,
    MalformedSourceError,
    UnsupportedFormatError,
)
from openpdn.pcb_import.canonical_json.codec import board_from_document

#: Refuse documents larger than this. Imported files are untrusted input; a
#: bounded read keeps a hostile or corrupt file from exhausting memory.
DEFAULT_MAX_DOCUMENT_BYTES: Final = 64 * 1024 * 1024

_ADAPTER_VERSION: Final = "0.0.1"


class CanonicalJsonImporter:
    """Reads boards written in openPDN's own canonical JSON format."""

    def __init__(self, max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES) -> None:
        """Store the read limit applied to source files."""
        self._max_document_bytes = max_document_bytes

    def describe(self) -> ImporterDescriptor:
        """Return identity and reach."""
        return ImporterDescriptor(
            name="canonical-json",
            version=_ADAPTER_VERSION,
            summary="openPDN canonical board JSON (fixtures and round-tripping)",
            source_format="openPDN canonical JSON",
            file_extensions=(".json",),
        )

    def can_load(self, source: Path) -> bool:
        """True when `source` looks like a canonical JSON file."""
        return source.is_file() and source.suffix.lower() == ".json"

    def load(self, source: Path) -> ImportResult:
        """Read `source` and return the canonical board with its diagnostics."""
        if not source.is_file():
            raise UnsupportedFormatError(f"Not a readable file: {source.name}")
        size_bytes = source.stat().st_size
        if size_bytes > self._max_document_bytes:
            raise MalformedSourceError(
                f"Document is {size_bytes} bytes, above the {self._max_document_bytes} byte limit"
            )

        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # Deliberately does not echo file content back to the caller.
            raise MalformedSourceError(f"{source.name} is not valid UTF-8 JSON") from exc

        board = board_from_document(document, source_name=source.name, digest=digest)
        return ImportResult(board=board, diagnostics=_diagnose(board))


def _diagnose(board: Board) -> tuple[Diagnostic, ...]:
    """Report assumptions and gaps that would affect an electrical solve."""
    diagnostics: list[Diagnostic] = []
    for layer in board.stackup.conductive_layers:
        if layer.thickness is None:
            diagnostics.append(
                Diagnostic(
                    code="import.missing_layer_thickness",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"Conductive layer {layer.name!r} has no copper thickness. "
                        "Set one in the study before solving."
                    ),
                    context={"layer": str(layer.id)},
                )
            )
        elif layer.thickness.provenance is Provenance.ASSUMED:
            diagnostics.append(
                Diagnostic(
                    code="import.assumed_layer_thickness",
                    severity=DiagnosticSeverity.INFO,
                    message=(
                        f"Copper thickness of layer {layer.name!r} is assumed: "
                        f"{layer.thickness.note}"
                    ),
                    context={"layer": str(layer.id)},
                )
            )

    incomplete_vias = [
        via
        for via in board.vias
        if via.finished_hole_diameter is None or via.plating_thickness is None
    ]
    if incomplete_vias:
        diagnostics.append(
            Diagnostic(
                code="import.incomplete_via_geometry",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    f"{len(incomplete_vias)} via(s) lack a hole diameter or plating "
                    "thickness; via resistance cannot be computed for them."
                ),
                context={"via_count": str(len(incomplete_vias))},
            )
        )
    return tuple(diagnostics)
