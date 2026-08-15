"""Importer selection, exercised against stubs only.

The service must never need to know which formats exist -- these stubs stand in
for IPC-2581, ODB++ and anything else, and the service cannot tell.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openpdn.application.errors import ImportRequestError
from openpdn.application.import_service import BoardImportService
from openpdn.domain.board import Board
from openpdn.pcb_import.api import (
    ImporterDescriptor,
    ImportResult,
    UnsupportedFormatError,
)


class _StubImporter:
    def __init__(
        self,
        name: str,
        board: Board,
        *,
        recognises: bool = True,
        available: bool = True,
        reason: str | None = None,
    ) -> None:
        self.name = name
        self._board = board
        self._recognises = recognises
        self._available = available
        self._reason = reason
        self.loads = 0

    def describe(self) -> ImporterDescriptor:
        return ImporterDescriptor(
            name=self.name,
            version="0.0.0",
            summary="stub",
            source_format=self.name.upper(),
            file_extensions=(".stub",),
            available=self._available,
            unavailable_reason=self._reason,
        )

    def can_load(self, source: Path) -> bool:
        del source
        return self._recognises

    def load(self, source: Path) -> ImportResult:
        del source
        self.loads += 1
        return ImportResult(board=self._board)


class _StubRegistry:
    def __init__(self, importers: list[_StubImporter]) -> None:
        self._importers = {importer.name: importer for importer in importers}

    def available(self) -> list[ImporterDescriptor]:
        return [importer.describe() for importer in self._importers.values()]

    def get(self, name: str) -> _StubImporter:
        try:
            return self._importers[name]
        except KeyError as exc:
            raise UnsupportedFormatError(name) from exc


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "board.stub"
    path.write_text("stub")
    return path


class TestSelection:
    def test_the_format_is_detected_when_no_importer_is_named(
        self, simple_board: Board, source: Path
    ):
        # Users should not have to name what openPDN can identify itself.
        wrong = _StubImporter("other", simple_board, recognises=False)
        right = _StubImporter("stub", simple_board)
        service = BoardImportService(_StubRegistry([wrong, right]))

        service.import_board(source)

        assert right.loads == 1
        assert wrong.loads == 0

    def test_a_configured_default_importer_is_honoured(self, simple_board: Board, source: Path):
        forced = _StubImporter("forced", simple_board, recognises=False)
        detected = _StubImporter("detected", simple_board)
        service = BoardImportService(_StubRegistry([forced, detected]), default_importer="forced")

        service.import_board(source)

        assert forced.loads == 1, "the configured importer must win over detection"
        assert detected.loads == 0

    def test_an_explicit_argument_beats_the_configured_default(
        self, simple_board: Board, source: Path
    ):
        first = _StubImporter("first", simple_board)
        second = _StubImporter("second", simple_board)
        service = BoardImportService(_StubRegistry([first, second]), default_importer="first")

        service.import_board(source, importer_name="second")

        assert second.loads == 1
        assert first.loads == 0

    def test_an_unknown_importer_name_is_a_request_error(self, simple_board: Board, source: Path):
        service = BoardImportService(_StubRegistry([_StubImporter("stub", simple_board)]))
        with pytest.raises(ImportRequestError, match="Unknown importer"):
            service.import_board(source, importer_name="nope")


class TestUnreadyImporters:
    def test_a_recognised_format_with_an_unready_adapter_says_so(
        self, simple_board: Board, source: Path
    ):
        # This is the IPC-2581 case today: reporting a format the user can see
        # is supported as "unrecognised" would be actively misleading.
        unready = _StubImporter(
            "ipc2581-like",
            simple_board,
            available=False,
            reason="structural extraction is in development",
        )
        service = BoardImportService(_StubRegistry([unready]))

        with pytest.raises(ImportRequestError, match="recognised, but no importer") as exc_info:
            service.import_board(source)

        assert "structural extraction is in development" in str(exc_info.value)
        assert unready.loads == 0

    def test_an_available_importer_still_wins_over_an_unready_one(
        self, simple_board: Board, source: Path
    ):
        unready = _StubImporter("unready", simple_board, available=False, reason="wip")
        ready = _StubImporter("ready", simple_board)
        service = BoardImportService(_StubRegistry([unready, ready]))

        service.import_board(source)

        assert ready.loads == 1

    def test_an_unrecognised_source_reports_that_instead(self, simple_board: Board, source: Path):
        blind = _StubImporter("blind", simple_board, recognises=False)
        service = BoardImportService(_StubRegistry([blind]))
        with pytest.raises(ImportRequestError, match="No available importer recognises"):
            service.import_board(source)
