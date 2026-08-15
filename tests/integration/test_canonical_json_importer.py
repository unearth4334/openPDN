"""The importer boundary: fixture in, canonical board out."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openpdn.domain.board import LayerId, NetId
from openpdn.domain.provenance import Provenance
from openpdn.pcb_import.api import MalformedSourceError, UnsupportedFormatError
from openpdn.pcb_import.canonical_json import (
    CanonicalJsonImporter,
    board_from_document,
    board_to_document,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def importer() -> CanonicalJsonImporter:
    return CanonicalJsonImporter()


class TestImport:
    def test_the_fixture_board_loads(
        self, importer: CanonicalJsonImporter, two_layer_rail_path: Path
    ):
        board = importer.load(two_layer_rail_path).board
        assert board.name.startswith("Two-layer")
        assert len(board.stackup.layers) == 3
        assert len(board.stackup.conductive_layers) == 2
        assert {net.name for net in board.nets} == {"VCC0V85", "GND"}

    def test_provenance_survives_the_import(
        self, importer: CanonicalJsonImporter, two_layer_rail_path: Path
    ):
        # An assumed thickness must not arrive looking like a measured one.
        board = importer.load(two_layer_rail_path).board
        top = board.stackup.layer(LayerId("L1"))
        bottom = board.stackup.layer(LayerId("L3"))
        assert top.thickness is not None
        assert bottom.thickness is not None
        assert top.thickness.provenance is Provenance.IMPORTED
        assert bottom.thickness.provenance is Provenance.ASSUMED
        assert bottom.thickness.note

    def test_import_records_where_the_board_came_from(
        self, importer: CanonicalJsonImporter, two_layer_rail_path: Path
    ):
        board = importer.load(two_layer_rail_path).board
        assert board.provenance is not None
        assert board.provenance.importer == "canonical-json"
        assert board.provenance.source_digest  # sha256 of the source bytes

    def test_copper_groups_by_net_and_layer(
        self, importer: CanonicalJsonImporter, two_layer_rail_path: Path
    ):
        board = importer.load(two_layer_rail_path).board
        vcc_top = board.copper_regions_on(NetId("NET_VCC"), LayerId("L1"))
        vcc_bottom = board.copper_regions_on(NetId("NET_VCC"), LayerId("L3"))
        assert len(vcc_top) == 1
        assert len(vcc_bottom) == 1

    def test_diagnostics_report_missing_via_geometry(
        self, importer: CanonicalJsonImporter, two_layer_rail_path: Path
    ):
        result = importer.load(two_layer_rail_path)
        codes = {diagnostic.code for diagnostic in result.diagnostics}
        assert "import.incomplete_via_geometry" in codes


class TestRoundTrip:
    def test_a_board_survives_serialisation(
        self, importer: CanonicalJsonImporter, two_layer_rail_path: Path, tmp_path: Path
    ):
        original = importer.load(two_layer_rail_path).board
        copy_path = tmp_path / "roundtrip.json"
        copy_path.write_text(json.dumps(board_to_document(original)))
        restored = importer.load(copy_path).board

        assert restored.id == original.id
        assert restored.stackup.layers == original.stackup.layers
        assert restored.copper_regions == original.copper_regions
        assert restored.vias == original.vias
        assert restored.terminals == original.terminals


class TestUntrustedInput:
    def test_a_non_json_file_is_rejected_without_leaking_content(
        self, importer: CanonicalJsonImporter, tmp_path: Path
    ):
        bad = tmp_path / "board.json"
        bad.write_text("SECRET-CONTENT-NOT-JSON {{{")
        with pytest.raises(MalformedSourceError) as exc_info:
            importer.load(bad)
        assert "SECRET-CONTENT" not in str(exc_info.value)

    def test_a_missing_file_is_rejected(self, importer: CanonicalJsonImporter, tmp_path: Path):
        with pytest.raises(UnsupportedFormatError):
            importer.load(tmp_path / "absent.json")

    def test_an_oversized_document_is_refused(self, tmp_path: Path):
        source = tmp_path / "big.json"
        source.write_text("{}" + " " * 1024)
        with pytest.raises(MalformedSourceError, match="limit"):
            CanonicalJsonImporter(max_document_bytes=16).load(source)

    def test_an_unknown_format_version_is_refused(self):
        with pytest.raises(MalformedSourceError, match="openpdn_canonical_board"):
            board_from_document({"openpdn_canonical_board": 99}, source_name="x")

    def test_a_structurally_valid_but_impossible_board_is_refused(self):
        document = {
            "openpdn_canonical_board": 1,
            "board": {
                "id": "b",
                "name": "b",
                "stackup": [
                    {
                        "id": "L1",
                        "name": "TOP",
                        "function": "signal",
                        "index": 0,
                        "material": None,  # a conductor with no material
                    }
                ],
            },
        }
        with pytest.raises(MalformedSourceError, match="Inconsistent board"):
            board_from_document(document, source_name="x")
