"""Canonical board model invariants."""

from __future__ import annotations

import math

import pytest

from openpdn.domain.board import (
    Board,
    BoardId,
    CopperRegion,
    CopperRegionId,
    Layer,
    LayerFunction,
    LayerId,
    Net,
    NetId,
    Stackup,
    Via,
    ViaId,
)
from openpdn.domain.errors import InvalidBoardError, MissingPhysicalPropertyError
from openpdn.domain.geometry import Point2D, Polygon2D
from openpdn.domain.materials import COPPER_ANNEALED
from openpdn.domain.provenance import Quantity
from openpdn.domain.units import METRE


def _layer(layer_id: str, index: int, *, thickness: Quantity | None = None) -> Layer:
    return Layer(
        id=LayerId(layer_id),
        name=layer_id,
        function=LayerFunction.SIGNAL,
        index=index,
        thickness=thickness,
        material=COPPER_ANNEALED,
    )


class TestLayer:
    def test_a_conductive_layer_needs_a_material(self):
        # Importers must state the conductor, marking it assumed if unknown.
        with pytest.raises(InvalidBoardError, match="material"):
            Layer(
                id=LayerId("L1"),
                name="TOP",
                function=LayerFunction.PLANE,
                index=0,
                material=None,
            )

    def test_a_dielectric_layer_needs_no_material(self):
        layer = Layer(
            id=LayerId("L2"),
            name="CORE",
            function=LayerFunction.DIELECTRIC,
            index=1,
            thickness=Quantity.imported(1.5e-3, METRE),
        )
        assert not layer.function.is_conductive

    def test_missing_thickness_fails_loudly_rather_than_defaulting(self):
        layer = _layer("L1", 0)
        with pytest.raises(MissingPhysicalPropertyError, match="thickness"):
            layer.require_thickness_m()

    def test_known_thickness_is_returned_in_metres(self):
        layer = _layer("L1", 0, thickness=Quantity.imported(35e-6, METRE))
        assert layer.require_thickness_m() == pytest.approx(35e-6)


class TestStackup:
    def test_layers_must_be_ordered_top_to_bottom(self):
        with pytest.raises(InvalidBoardError, match="ordered"):
            Stackup((_layer("L2", 1), _layer("L1", 0)))

    def test_duplicate_layer_ids_are_rejected(self):
        with pytest.raises(InvalidBoardError, match="unique"):
            Stackup((_layer("L1", 0), _layer("L1", 1)))

    def test_conductive_layers_are_filtered(self, simple_board: Board):
        assert len(simple_board.stackup.conductive_layers) == 2


class TestBoardIntegrity:
    def test_a_region_on_an_unknown_layer_is_rejected(self):
        with pytest.raises(InvalidBoardError, match="unknown layer"):
            Board(
                id=BoardId("b"),
                name="b",
                stackup=Stackup((_layer("L1", 0),)),
                nets=(Net(NetId("N1"), "VCC"),),
                copper_regions=(
                    CopperRegion(
                        id=CopperRegionId("CU1"),
                        net_id=NetId("N1"),
                        layer_id=LayerId("L9"),
                        outline=Polygon2D.rectangle(Point2D(0, 0), 1e-3, 1e-3),
                    ),
                ),
            )

    def test_a_region_on_an_unknown_net_is_rejected(self):
        with pytest.raises(InvalidBoardError, match="unknown net"):
            Board(
                id=BoardId("b"),
                name="b",
                stackup=Stackup((_layer("L1", 0),)),
                copper_regions=(
                    CopperRegion(
                        id=CopperRegionId("CU1"),
                        net_id=NetId("N_MISSING"),
                        layer_id=LayerId("L1"),
                        outline=Polygon2D.rectangle(Point2D(0, 0), 1e-3, 1e-3),
                    ),
                ),
            )

    def test_a_via_cannot_connect_a_layer_to_itself(self):
        with pytest.raises(InvalidBoardError, match="itself"):
            Via(
                id=ViaId("V1"),
                net_id=NetId("N1"),
                from_layer_id=LayerId("L1"),
                to_layer_id=LayerId("L1"),
                position=Point2D(0.0, 0.0),
            )


class TestBoardQueries:
    def test_copper_is_grouped_by_net_and_layer(self, simple_board: Board):
        # (net, layer) is the grouping a 2.5-D sheet solver meshes over.
        regions = simple_board.copper_regions_on(NetId("NET_VCC"), LayerId("L1"))
        assert [region.id for region in regions] == ["CU1"]
        assert simple_board.copper_regions_on(NetId("NET_VCC"), LayerId("L2")) == ()

    def test_bounding_box_spans_all_copper(self, simple_board: Board):
        box = simple_board.bounding_box
        assert box is not None
        assert box.width_m == pytest.approx(0.040)

    def test_unknown_lookups_raise(self, simple_board: Board):
        with pytest.raises(InvalidBoardError):
            simple_board.net(NetId("NOPE"))


class TestViaGeometry:
    def test_barrel_cross_section_is_the_annulus(self, simple_board: Board):
        via = simple_board.vias[0]
        inner_r = 0.5 * 0.0003
        outer_r = inner_r + 25e-6
        expected = math.pi * (outer_r**2 - inner_r**2)
        assert via.require_barrel_cross_section_m2() == pytest.approx(expected)

    def test_unknown_plating_is_not_guessed(self):
        via = Via(
            id=ViaId("V1"),
            net_id=NetId("N1"),
            from_layer_id=LayerId("L1"),
            to_layer_id=LayerId("L2"),
            position=Point2D(0.0, 0.0),
            finished_hole_diameter=Quantity.imported(0.0003, METRE),
        )
        with pytest.raises(MissingPhysicalPropertyError, match="plating"):
            via.require_barrel_cross_section_m2()
