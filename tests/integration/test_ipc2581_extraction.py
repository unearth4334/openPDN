"""Structural extraction against the committed IPC-2581 fixtures.

Golden assertions are semantic -- counts, ordering, names, areas with stated
tolerances -- never raw polygon dumps (see the testing skill). Every fixture is
hand-written; expected values are computable from the fixture by inspection.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from openpdn.domain.provenance import Provenance
from openpdn.domain.results import DiagnosticSeverity
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer
from openpdn.pcb_import.api import SimulationReadiness
from openpdn.pcb_import.ipc2581 import IPC2581Importer
from openpdn.pcb_import.ipc2581.geometry import ARC_SAGITTA_TOLERANCE_M

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ipc2581"


@pytest.fixture(scope="module")
def importer() -> IPC2581Importer:
    return IPC2581Importer()


@pytest.fixture(scope="module")
def four_layer_result(importer: IPC2581Importer):
    return importer.load(FIXTURES / "four-layer-stackup" / "board.xml")


@pytest.fixture(scope="module")
def via_result(importer: IPC2581Importer):
    return importer.load(FIXTURES / "via-through-board" / "board.xml")


@pytest.fixture(scope="module")
def copper_result(importer: IPC2581Importer):
    return importer.load(FIXTURES / "plane-and-trace" / "board.xml")


@pytest.fixture(scope="module")
def negative_result(importer: IPC2581Importer):
    return importer.load(FIXTURES / "negative-features" / "board.xml")


class TestFourLayerStackup:
    def test_four_conductive_layers_in_physical_order(self, four_layer_result):
        conductive = four_layer_result.board.stackup.conductive_layers
        assert [layer.name for layer in conductive] == ["Top", "In1", "In2", "Bottom"]

    def test_the_physical_stackup_keeps_dielectrics_and_masks(self, four_layer_result):
        functions = [layer.function.value for layer in four_layer_result.board.stackup.layers]
        assert functions == [
            "solder_mask",
            "signal",
            "dielectric",
            "plane",
            "dielectric",
            "plane",
            "dielectric",
            "signal",
            "solder_mask",
        ]

    def test_zero_thickness_paste_and_legend_are_not_physical_layers(self, four_layer_result):
        names = [layer.name for layer in four_layer_result.board.stackup.layers]
        assert "Paste" not in names
        assert "Legend" not in names

    def test_thicknesses_are_imported_in_metres(self, four_layer_result):
        top = four_layer_result.board.stackup.conductive_layers[0]
        assert top.thickness is not None
        assert top.thickness.value == pytest.approx(35e-6)
        assert top.thickness.provenance is Provenance.IMPORTED

    def test_the_missing_inner_thickness_is_diagnosed_not_defaulted(self, four_layer_result):
        in2 = next(layer for layer in four_layer_result.board.stackup.layers if layer.name == "In2")
        assert in2.thickness is None
        assert any(
            diagnostic.code == "import.missing_layer_thickness"
            and diagnostic.context.get("layer") == "In2"
            for diagnostic in four_layer_result.diagnostics
        )

    def test_readiness_reflects_the_gaps(self, four_layer_result):
        assert (
            four_layer_result.capability_report.readiness
            is SimulationReadiness.READY_WITH_ASSUMPTIONS
        )


class TestViaSpans:
    def test_three_vias_with_their_declared_spans(self, via_result):
        board = via_result.board
        layer_names = {layer.id: layer.name for layer in board.stackup.layers}
        spans = {
            (layer_names[via.from_layer_id], layer_names[via.to_layer_id]) for via in board.vias
        }
        assert spans == {("Top", "Bottom"), ("Top", "In1"), ("In1", "In2")}

    def test_drill_diameters_are_imported_and_plating_is_not_invented(self, via_result):
        via_by_drill = {
            round(via.drill_diameter.value, 6): via
            for via in via_result.board.vias
            if via.drill_diameter is not None
        }
        assert set(via_by_drill) == {0.0003, 0.0001, 0.0002}
        assert all(via.plating_thickness is None for via in via_result.board.vias)
        assert any(
            diagnostic.code == "import.missing_via_plating" for diagnostic in via_result.diagnostics
        )

    def test_a_pin_pad_becomes_a_pad_a_terminal_and_a_component_link(self, via_result):
        board = via_result.board
        assert len(board.terminals) == 1
        terminal = board.terminals[0]
        assert terminal.name == "U1.1"
        assert board.net(terminal.net_id).name == "VCC"
        [component] = board.components
        assert component.reference_designator == "U1"
        assert component.terminal_ids == (terminal.id,)

    def test_via_lands_become_copper_regions(self, via_result):
        # 3 vias x 2 lands + 1 pin pad + 1 trace = 8 copper regions.
        assert len(via_result.board.copper_regions) == 8


class TestCopperGeometry:
    def test_a_contour_void_subtracts_from_the_plane(self, copper_result):
        board = copper_result.board
        plane = next(
            region
            for region in board.copper_regions
            if region.net_id is not None and region.outline.holes
        )
        assert plane.area_m2 == pytest.approx(96e-6, rel=1e-9)

    def test_stroked_trace_area_matches_the_analytical_value(self, copper_result):
        # 8 mm x 1 mm body plus round caps: 8 + pi/4 mm^2.
        trace = next(
            region
            for region in copper_result.board.copper_regions
            if region.source_ref is not None and "Line" in region.source_ref
        )
        assert trace.area_m2 == pytest.approx((8 + math.pi / 4) * 1e-6, rel=1e-3)

    def test_a_full_circle_arc_stroke_is_an_annulus(self, copper_result):
        ring = next(
            region
            for region in copper_result.board.copper_regions
            if region.source_ref is not None and "Arc" in region.source_ref
        )
        # Annulus r=2 mm, w=0.5 mm: pi (2.25^2 - 1.75^2) = 2 pi mm^2, with a hole.
        assert ring.outline.holes
        assert ring.area_m2 == pytest.approx(2 * math.pi * 1e-6, rel=1e-3)

    def test_a_rotated_rectangular_flash_lands_rotated(self, copper_result):
        flash = next(
            region
            for region in copper_result.board.copper_regions
            if region.net_id is None  # the "No Net" placeholder set
        )
        box = flash.outline.bounding_box
        # 4 x 1 rectangle rotated 90 degrees about (30, 15): 1 wide, 4 tall.
        assert box.width_m == pytest.approx(1e-3, rel=1e-9)
        assert box.height_m == pytest.approx(4e-3, rel=1e-9)
        assert (box.min_x_m + box.max_x_m) / 2 == pytest.approx(30e-3, rel=1e-9)

    def test_two_islands_normalise_into_two_regions(self, copper_result):
        normalized = ShapelyGeometryNormalizer().normalize(copper_result.board)
        board = copper_result.board
        gnd = next(net.id for net in board.nets if net.name == "GND")
        islands = [region for region in normalized.regions if region.net_id == gnd]
        assert len(islands) == 2
        assert sum(region.polygon.area_m2 for region in islands) == pytest.approx(18e-6, rel=1e-9)

    def test_placeholder_net_copper_is_diagnosed(self, copper_result):
        assert any(
            diagnostic.code == "import.placeholder_net" for diagnostic in copper_result.diagnostics
        )


class TestRefusalsAndDiagnostics:
    def test_negative_polarity_marks_the_board_not_ready(self, negative_result):
        assert negative_result.capability_report.readiness is SimulationReadiness.NOT_READY
        assert any(
            diagnostic.code == "import.negative_polarity_unsupported"
            and diagnostic.severity is DiagnosticSeverity.ERROR
            for diagnostic in negative_result.diagnostics
        )

    def test_negative_copper_is_not_imported_as_positive(self, negative_result):
        # The 10x10 plane imports; the 2x2 negative square must not add area.
        total = sum(region.area_m2 for region in negative_result.board.copper_regions)
        assert total == pytest.approx(100e-6, rel=1e-9)

    def test_an_unknown_primitive_surfaces_as_a_diagnostic(self, negative_result):
        unsupported = [
            diagnostic
            for diagnostic in negative_result.diagnostics
            if diagnostic.code == "import.unsupported_construct"
        ]
        assert any("MadeUpPrimitive" in d.context.get("construct", "") for d in unsupported)


@pytest.fixture(scope="module")
def arc_result(importer: IPC2581Importer):
    return importer.load(FIXTURES / "degenerate-arc" / "board.xml")


class TestArcEndpointClosure:
    """Two arcs that both look closed must import as very different copper."""

    def _region(self, arc_result, net_name: str):
        board = arc_result.board
        net_id = next(net.id for net in board.nets if net.name == net_name)
        return next(region for region in board.copper_regions if region.net_id == net_id)

    def test_exactly_coincident_endpoints_import_as_an_annulus(self, arc_result):
        region = self._region(arc_result, "A_FULL")
        # Stroke 0.1 mm wide around r = 0.5 mm: pi (0.55^2 - 0.45^2) mm^2.
        assert region.outline.holes
        assert region.area_m2 == pytest.approx(math.pi * 0.1 * 1e-6, rel=1e-3)

    def test_nanometre_apart_endpoints_import_as_a_dot_not_a_ring(self, arc_result):
        # The regression: read as an open arc this swept 359.998 degrees and
        # painted a 0.3048 mm wide ring of radius 0.3178 mm.
        region = self._region(arc_result, "A_DEGENERATE")
        assert not region.outline.holes
        # The round cap is a polygon inscribed in the true disc, so it sits
        # inside it by at most the sagitta tolerance -- that bound, not a
        # round-number percentage, is what this asserts.
        box = region.outline.bounding_box
        # A round-capped stroke over the 12 nm the endpoints are apart: as wide
        # as the stroke plus that residual, and exactly the stroke tall. The
        # round cap is a polygon inscribed in the true disc, so it sits inside
        # it by at most the sagitta tolerance -- that bound, not a round-number
        # percentage, is the criterion.
        assert 0.3048e-3 - box.height_m <= 2 * ARC_SAGITTA_TOLERANCE_M
        assert box.width_m - box.height_m == pytest.approx(12e-9, abs=1e-10)
        # Area follows from the same bound: ~0.8 % under the exact disc, and
        # nowhere near the 0.6 mm^2 ring the misread arc used to paint.
        assert region.area_m2 == pytest.approx(math.pi * 0.1524**2 * 1e-6, rel=1e-2)

    def test_the_reinterpretation_is_reported(self, arc_result):
        # An importer that silently "fixes" artwork is worse than one that fails.
        assert any(
            diagnostic.code == "import.degenerate_arc" and diagnostic.context["count"] == "1"
            for diagnostic in arc_result.diagnostics
        )
