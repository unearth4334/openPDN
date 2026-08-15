"""Use case: turn a PCB source file into a canonical board."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from openpdn.application import events
from openpdn.application.errors import ImportRequestError
from openpdn.pcb_import.api import PCBImportError, UnsupportedFormatError

if TYPE_CHECKING:
    from pathlib import Path

    from openpdn.pcb_import.api import ImporterRegistry, ImportResult, PCBImporter

_logger = logging.getLogger(__name__)


class BoardImportService:
    """Selects an importer and produces a canonical board.

    Knows nothing about IPC-2581, XML, ODB++ or archives: format handling lives
    entirely behind `PCBImporter` implementations (ADR-0002, ADR-0006).
    """

    def __init__(self, importers: ImporterRegistry, default_importer: str | None = None) -> None:
        """Store the importer registry and the deployment's forced importer, if any."""
        self._importers = importers
        self._default_importer = default_importer

    def import_board(self, source: Path, importer_name: str | None = None) -> ImportResult:
        """Import `source`, optionally forcing a specific importer.

        Args:
            source: Path to the PCB source, already placed in a trusted
                location by the caller. Untrusted uploads must be staged into
                an isolated workspace first (see SECURITY.md).
            importer_name: Registry key of the importer to use. When omitted,
                the deployment's configured importer is used, and when that is
                unset the format is detected from the document itself --
                users should not have to name what openPDN can identify.

        Raises:
            ImportRequestError: If no suitable importer is available.
            MalformedSourceError: If the source cannot be read.
        """
        importer = self._select(source, importer_name or self._default_importer)
        descriptor = importer.describe()
        started = time.perf_counter()
        _logger.info(
            events.PCB_IMPORT_STARTED,
            extra={"event": events.PCB_IMPORT_STARTED, "importer": descriptor.name},
        )
        try:
            result = importer.load(source)
        except PCBImportError as exc:
            _logger.warning(
                events.PCB_IMPORT_FAILED,
                extra={
                    "event": events.PCB_IMPORT_FAILED,
                    "importer": descriptor.name,
                    "reason": type(exc).__name__,
                },
            )
            raise

        _logger.info(
            events.PCB_IMPORT_FINISHED,
            extra={
                "event": events.PCB_IMPORT_FINISHED,
                "importer": descriptor.name,
                "board_id": str(result.board.id),
                "net_count": len(result.board.nets),
                "copper_region_count": len(result.board.copper_regions),
                "via_count": len(result.board.vias),
                "diagnostic_count": len(result.diagnostics),
                "duration_seconds": round(time.perf_counter() - started, 6),
            },
        )
        return result

    def _select(self, source: Path, importer_name: str | None) -> PCBImporter:
        """Return the importer to use for `source`."""
        if importer_name is not None:
            try:
                return self._importers.get(importer_name)
            except UnsupportedFormatError as exc:
                raise ImportRequestError(f"Unknown importer {importer_name!r}") from exc

        recognised_but_unavailable: list[str] = []
        for descriptor in self._importers.available():
            candidate = self._importers.get(descriptor.name)
            if not candidate.can_load(source):
                continue
            if descriptor.available:
                return candidate
            # The format was identified; the adapter just cannot finish yet.
            # Saying so beats reporting a recognised format as unrecognised.
            recognised_but_unavailable.append(
                f"{descriptor.name} ({descriptor.source_format}): {descriptor.unavailable_reason}"
            )

        if recognised_but_unavailable:
            raise ImportRequestError(
                f"{source.name!r} was recognised, but no importer for it is ready: "
                + "; ".join(recognised_but_unavailable)
            )
        raise ImportRequestError(
            f"No available importer recognises {source.name!r}; "
            "select one explicitly to see why it was rejected"
        )
