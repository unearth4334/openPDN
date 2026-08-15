"""The IPC-2581 adapter boundary.

Two things are under test: that hostile XML is refused, and that the adapter is
honest about how far it can currently take a document.

The attack payloads are built here rather than committed as fixtures — a
repository full of live XXE files is a hazard, and the assertion reads better
next to the attack it describes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openpdn.pcb_import.api import (
    MalformedSourceError,
    UnsupportedFormatError,
    UnsupportedRevisionError,
)
from openpdn.pcb_import.ipc2581 import (
    IPC2581Importer,
    IPC2581Revision,
    UnsafeXmlError,
    XmlLimits,
    inspect_document,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def importer() -> IPC2581Importer:
    return IPC2581Importer()


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _document(body: str = "", revision: str = "B", units: str = "MILLIMETER") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<IPC-2581 revision="{revision}">\n'
        f'  <Ecad name="t"><CadHeader units="{units}"/></Ecad>\n'
        f"{body}"
        "</IPC-2581>\n"
    )


class TestUntrustedXml:
    def test_an_external_entity_is_refused(self, tmp_path: Path):
        # XXE: without a DTD ban this reads a local file into the document.
        source = _write(
            tmp_path / "xxe.xml",
            '<?xml version="1.0"?>\n'
            "<!DOCTYPE IPC-2581 [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>\n"
            '<IPC-2581 revision="B"><Ecad><CadHeader units="MILLIMETER"/>'
            "<Note>&xxe;</Note></Ecad></IPC-2581>",
        )
        with pytest.raises(UnsafeXmlError, match="DOCTYPE"):
            inspect_document(source)

    def test_an_entity_expansion_bomb_is_refused(self, tmp_path: Path):
        # "Billion laughs": a few hundred bytes expanding to gigabytes.
        source = _write(
            tmp_path / "bomb.xml",
            '<?xml version="1.0"?>\n'
            "<!DOCTYPE lolz [\n"
            "  <!ENTITY lol 'lol'>\n"
            "  <!ENTITY lol2 '&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;'>\n"
            "  <!ENTITY lol3 '&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;'>\n"
            "]>\n"
            '<IPC-2581 revision="B"><Note>&lol3;</Note></IPC-2581>',
        )
        with pytest.raises(UnsafeXmlError):
            inspect_document(source)

    def test_a_bare_entity_declaration_is_refused(self, tmp_path: Path):
        source = _write(
            tmp_path / "entity.xml",
            "<!ENTITY payload 'x'>\n" + _document(),
        )
        with pytest.raises(UnsafeXmlError, match="entit"):
            inspect_document(source)

    def test_excessive_nesting_is_refused(self, tmp_path: Path):
        # Deep nesting exhausts the stack of any recursive consumer downstream.
        depth = 60
        nested = "".join(f"<L{i}>" for i in range(depth)) + "".join(
            f"</L{i}>" for i in reversed(range(depth))
        )
        source = _write(tmp_path / "deep.xml", _document(body=nested))
        with pytest.raises(UnsafeXmlError, match="nests deeper"):
            inspect_document(source, XmlLimits(max_depth=10))

    def test_too_many_elements_are_refused(self, tmp_path: Path):
        # The "many small elements" bomb, which no byte limit alone catches.
        source = _write(tmp_path / "many.xml", _document(body="<E/>" * 500))
        with pytest.raises(UnsafeXmlError, match="more than"):
            inspect_document(source, XmlLimits(max_elements=50))

    def test_an_oversized_document_is_refused_before_parsing(self, tmp_path: Path):
        source = _write(tmp_path / "big.xml", _document(body="<E/>" * 100))
        with pytest.raises(UnsafeXmlError, match="byte limit"):
            inspect_document(source, XmlLimits(max_bytes=64))

    def test_malformed_xml_fails_without_echoing_content(self, tmp_path: Path):
        source = _write(
            tmp_path / "broken.xml",
            '<IPC-2581 revision="B"><SECRET-CUSTOMER-NET>oops',
        )
        with pytest.raises(MalformedSourceError) as exc_info:
            inspect_document(source)
        assert "SECRET-CUSTOMER-NET" not in str(exc_info.value)

    def test_xml_that_is_not_ipc2581_is_rejected(self, tmp_path: Path):
        source = _write(tmp_path / "other.xml", "<odb><step/></odb>")
        with pytest.raises(UnsupportedFormatError, match="not an IPC-2581 document"):
            inspect_document(source)


class TestDocumentInspection:
    def test_the_minimal_fixture_is_understood(self, minimal_ipc2581_path: Path):
        summary = inspect_document(minimal_ipc2581_path)
        assert summary.revision is IPC2581Revision.B
        assert summary.units_name == "MILLIMETER"
        assert summary.unit_scale_to_m == pytest.approx(1e-3)
        assert "Ecad" in summary.sections
        assert summary.element_count > 10

    def test_units_are_normalised_at_the_boundary(self, tmp_path: Path):
        # The document says inches; the scale handed onward is SI.
        source = _write(tmp_path / "inch.xml", _document(units="INCH"))
        assert inspect_document(source).unit_scale_to_m == pytest.approx(25.4e-3)

    def test_an_unsupported_revision_is_refused(self, tmp_path: Path):
        source = _write(tmp_path / "revc.xml", _document(revision="C"))
        with pytest.raises(UnsupportedRevisionError):
            inspect_document(source)

    def test_a_document_without_units_is_refused(self, tmp_path: Path):
        source = _write(
            tmp_path / "nounits.xml",
            '<IPC-2581 revision="B"><Ecad name="t"/></IPC-2581>',
        )
        with pytest.raises(MalformedSourceError, match="declares no units"):
            inspect_document(source)


class TestAdapterContract:
    def test_it_recognises_ipc2581_by_content_not_extension(
        self, importer: IPC2581Importer, tmp_path: Path, minimal_ipc2581_path: Path
    ):
        assert importer.can_load(minimal_ipc2581_path)
        assert not importer.can_load(_write(tmp_path / "other.xml", "<odb><step/></odb>"))
        assert not importer.can_load(tmp_path / "absent.xml")

    def test_it_declares_itself_available(self, importer: IPC2581Importer):
        descriptor = importer.describe()
        assert descriptor.name == "ipc2581"
        assert descriptor.source_format == "IPC-2581"
        assert descriptor.available
        assert descriptor.unavailable_reason is None

    def test_load_produces_a_canonical_board(
        self, importer: IPC2581Importer, minimal_ipc2581_path: Path
    ):
        result = importer.load(minimal_ipc2581_path)
        board = result.board
        # Two conductive layers around a core, in stackup order.
        assert [layer.function.value for layer in board.stackup.layers] == [
            "signal",
            "dielectric",
            "signal",
        ]
        assert {net.name for net in board.nets} == {"VCC0V85", "GND"}
        # One 40 mm x 0.5 mm round-ended trace per conductive layer.
        assert len(board.copper_regions) == 2
        assert board.profile is not None
        assert board.bounding_box is not None
        assert board.bounding_box.width_m == pytest.approx(50e-3)
        assert board.bounding_box.height_m == pytest.approx(30e-3)
        report = result.capability_report
        assert report is not None
        assert report.format_revision == "IPC-2581B"

    def test_copper_thickness_is_imported_with_provenance(
        self, importer: IPC2581Importer, minimal_ipc2581_path: Path
    ):
        board = importer.load(minimal_ipc2581_path).board
        top = board.stackup.conductive_layers[0]
        assert top.thickness is not None
        assert top.thickness.value == pytest.approx(34.8e-6)
        assert top.thickness.provenance.value == "imported"

    def test_load_still_refuses_hostile_input_first(
        self, importer: IPC2581Importer, tmp_path: Path
    ):
        # Security checks must not be skippable via the not-ready path.
        source = _write(
            tmp_path / "xxe.xml",
            "<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]>\n" + _document(),
        )
        with pytest.raises(UnsafeXmlError):
            importer.load(source)
