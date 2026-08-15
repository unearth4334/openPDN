"""Geometry normalisation: imported copper to solver-ready regions.

The normaliser is what the future FEM solver meshes over; these cases pin the
Boolean semantics with geometries whose correct results are hand-computable.
"""

from __future__ import annotations

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
)
from openpdn.domain.geometry import Point2D, Polygon2D
from openpdn.domain.materials import COPPER_ANNEALED
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer

pytestmark = pytest.mark.unit

_LAYER = LayerId("L1")
_NET = NetId("N1")
_OTHER_NET = NetId("N2")


def _board(regions: list[CopperRegion]) -> Board:
    return Board(
        id=BoardId("b"),
        name="test",
        stackup=Stackup(
            (
                Layer(
                    id=_LAYER,
                    name="TOP",
                    function=LayerFunction.SIGNAL,
                    index=0,
                    material=COPPER_ANNEALED,
                ),
            )
        ),
        nets=(Net(id=_NET, name="N1"), Net(id=_OTHER_NET, name="N2")),
        copper_regions=tuple(regions),
    )


def _rect(
    region_id: str, net: NetId | None, x0: float, y0: float, w: float, h: float
) -> CopperRegion:
    return CopperRegion(
        id=CopperRegionId(region_id),
        net_id=net,
        layer_id=_LAYER,
        outline=Polygon2D.rectangle(Point2D(x0, y0), w, h),
    )


class TestUnionSemantics:
    def test_overlapping_regions_of_one_net_merge_without_double_counting(self):
        # Two 2x1 rectangles overlapping by 1x1: union area is 3, not 4.
        board = _board([_rect("a", _NET, 0, 0, 2, 1), _rect("b", _NET, 1, 0, 2, 1)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.regions) == 1
        assert normalized.regions[0].polygon.area_m2 == pytest.approx(3.0, rel=1e-12)

    def test_different_nets_never_merge(self):
        board = _board([_rect("a", _NET, 0, 0, 2, 1), _rect("b", _OTHER_NET, 1, 0, 2, 1)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.regions) == 2
        nets = {region.net_id for region in normalized.regions}
        assert nets == {_NET, _OTHER_NET}

    def test_disjoint_islands_stay_separate_regions(self):
        # Electrical continuity matters: the solver must see two domains.
        board = _board([_rect("a", _NET, 0, 0, 1, 1), _rect("b", _NET, 5, 0, 1, 1)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.regions) == 2

    def test_holes_survive_the_union(self):
        plate = CopperRegion(
            id=CopperRegionId("plate"),
            net_id=_NET,
            layer_id=_LAYER,
            outline=Polygon2D.from_coordinates(
                [(0, 0), (10, 0), (10, 10), (0, 10)],
                [[(4, 4), (6, 4), (6, 6), (4, 6)]],
            ),
        )
        normalized = ShapelyGeometryNormalizer().normalize(_board([plate]))
        [region] = normalized.regions
        assert len(region.polygon.holes) == 1
        assert region.polygon.area_m2 == pytest.approx(96.0, rel=1e-12)

    def test_unassigned_copper_is_normalised_on_its_own_group(self):
        board = _board([_rect("a", None, 0, 0, 1, 1), _rect("b", _NET, 0, 0, 1, 1)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert {region.net_id for region in normalized.regions} == {None, _NET}


class TestProvenanceAndRepair:
    def test_merged_regions_record_every_contributor(self):
        board = _board([_rect("a", _NET, 0, 0, 2, 1), _rect("b", _NET, 1, 0, 2, 1)])
        [region] = ShapelyGeometryNormalizer().normalize(board).regions
        assert set(region.source_region_ids) == {CopperRegionId("a"), CopperRegionId("b")}

    def test_an_invalid_bow_tie_is_repaired_and_reported(self):
        bow_tie = CopperRegion(
            id=CopperRegionId("bow"),
            net_id=_NET,
            layer_id=_LAYER,
            # Self-intersecting ring: (0,0)-(2,2)-(2,0)-(0,2) crosses itself.
            outline=Polygon2D.from_coordinates([(0, 0), (2, 2), (2, 0), (0, 2)]),
        )
        normalized = ShapelyGeometryNormalizer().normalize(_board([bow_tie]))
        assert any(
            diagnostic.code == "geometry.repaired_invalid_regions"
            for diagnostic in normalized.diagnostics
        )
        assert normalized.stats is not None
        assert normalized.stats.repaired_region_count == 1

    def test_identical_input_yields_identical_region_ids(self):
        # Stable ids are what let a future result refer back to geometry.
        board = _board([_rect("a", _NET, 0, 0, 2, 1), _rect("b", _NET, 5, 0, 2, 1)])
        first = ShapelyGeometryNormalizer().normalize(board)
        second = ShapelyGeometryNormalizer().normalize(board)
        assert [r.id for r in first.regions] == [r.id for r in second.regions]
