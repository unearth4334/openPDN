"""Via consolidation: merging duplicates, flagging everything else.

The solver needs a via list with zero physical overlap. These cases pin the
three outcomes by hand: exact duplicates merge, same-position-but-disagreeing
vias are a position conflict, and distinct-but-touching barrels are an
overlap -- both of the latter two flagged, never merged, never dropped.
"""

from __future__ import annotations

import pytest

from openpdn.domain.board import (
    Board,
    BoardId,
    Layer,
    LayerFunction,
    LayerId,
    Net,
    NetId,
    Stackup,
    Via,
    ViaId,
)
from openpdn.domain.geometry import Point2D
from openpdn.domain.materials import COPPER_ANNEALED
from openpdn.domain.provenance import Quantity
from openpdn.domain.units import METRE
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer

pytestmark = pytest.mark.unit

_NET_A = NetId("N1")
_NET_B = NetId("N2")

# Four conductive layers so span-range overlap (by stackup index, not layer
# identity) can be exercised: L1-L2 vs L2-L3 share a layer; L1-L2 vs L3-L4 do
# not, even though every via still spans exactly two layers.
_LAYERS = tuple(
    Layer(
        id=LayerId(f"L{i + 1}"),
        name=f"L{i + 1}",
        function=LayerFunction.SIGNAL,
        index=i,
        material=COPPER_ANNEALED,
    )
    for i in range(4)
)


def _board(vias: list[Via]) -> Board:
    return Board(
        id=BoardId("b"),
        name="test",
        stackup=Stackup(_LAYERS),
        nets=(Net(id=_NET_A, name="N1"), Net(id=_NET_B, name="N2")),
        vias=tuple(vias),
    )


def _via(
    via_id: str,
    x: float,
    y: float,
    *,
    net: NetId | None = _NET_A,
    from_layer: str = "L1",
    to_layer: str = "L2",
    drill_m: float | None = 0.2e-3,
) -> Via:
    drill = Quantity.imported(drill_m, METRE) if drill_m is not None else None
    return Via(
        id=ViaId(via_id),
        net_id=net,
        from_layer_id=LayerId(from_layer),
        to_layer_id=LayerId(to_layer),
        position=Point2D(x, y),
        drill_diameter=drill,
    )


class TestNoFalsePositives:
    def test_well_separated_vias_are_untouched(self):
        # 405 um pitch: the smallest real spacing measured on a production
        # board. Nothing here should merge or diagnose.
        board = _board([_via("a", 0.0, 0.0), _via("b", 405e-6, 0.0)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 2
        assert {cv.id for cv in normalized.vias} == {"a", "b"}
        assert not [d for d in normalized.diagnostics if d.code.startswith("geometry.via")]

    def test_touching_barrels_on_disjoint_spans_are_not_checked(self):
        # Same position on paper would overlap, but the spans share no
        # layer, so the barrels occupy different physical space and cannot
        # actually touch.
        board = _board(
            [
                _via("a", 0.0, 0.0, from_layer="L1", to_layer="L2", drill_m=1.0e-3),
                _via("b", 50e-6, 0.0, net=_NET_B, from_layer="L3", to_layer="L4", drill_m=1.0e-3),
            ]
        )
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 2
        assert not [d for d in normalized.diagnostics if d.code.startswith("geometry.via")]

    def test_missing_drill_diameter_skips_the_overlap_check_and_says_so(self):
        board = _board(
            [
                _via("a", 0.0, 0.0, drill_m=None),
                _via("b", 10e-6, 0.0, drill_m=None),
            ]
        )
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 2
        assert not [d for d in normalized.diagnostics if d.code == "geometry.via_overlap"]
        incomplete = [
            d for d in normalized.diagnostics if d.code == "geometry.via_overlap_check_incomplete"
        ]
        assert len(incomplete) == 1
        assert incomplete[0].context["count"] == "2"


class TestMerging:
    def test_exactly_coincident_matching_vias_merge(self):
        board = _board([_via("a", 10e-3, 20e-3), _via("b", 10e-3, 20e-3)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 1
        [merged] = normalized.vias
        assert set(merged.via_ids) == {ViaId("a"), ViaId("b")}
        merges = [d for d in normalized.diagnostics if d.code == "geometry.merged_coincident_vias"]
        assert len(merges) == 1
        assert merges[0].context["count"] == "1"

    def test_merge_tolerance_absorbs_representation_noise_only(self):
        # 12 nm apart: the generator rounding noise observed in real arc
        # endpoints. This should still merge.
        board = _board([_via("a", 10e-3, 20e-3), _via("b", 10e-3 + 12e-9, 20e-3)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 1

    def test_three_matching_vias_at_one_position_merge_into_one(self):
        board = _board([_via("a", 0.0, 0.0), _via("b", 0.0, 0.0), _via("c", 0.0, 0.0)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 1
        [merged] = normalized.vias
        assert set(merged.via_ids) == {ViaId("a"), ViaId("b"), ViaId("c")}
        merges = [d for d in normalized.diagnostics if d.code == "geometry.merged_coincident_vias"]
        assert merges[0].context["count"] == "2"

    def test_merged_via_keeps_a_representative_drill_diameter(self):
        board = _board([_via("a", 0.0, 0.0, drill_m=None), _via("b", 0.0, 0.0, drill_m=0.3e-3)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        [merged] = normalized.vias
        assert merged.drill_diameter is not None
        assert merged.drill_diameter.require_unit(METRE) == pytest.approx(0.3e-3)


class TestConflictsAreFlaggedNotMerged:
    def test_coincident_vias_on_different_nets_are_a_position_conflict(self):
        board = _board([_via("a", 0.0, 0.0, net=_NET_A), _via("b", 0.0, 0.0, net=_NET_B)])
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 2
        code = "geometry.via_position_conflict"
        conflicts = [d for d in normalized.diagnostics if d.code == code]
        assert len(conflicts) == 1
        assert conflicts[0].severity == "error"
        assert {conflicts[0].context["via_a"], conflicts[0].context["via_b"]} == {"a", "b"}

    def test_coincident_vias_on_different_spans_are_a_position_conflict(self):
        board = _board(
            [
                _via("a", 0.0, 0.0, from_layer="L1", to_layer="L2"),
                _via("b", 0.0, 0.0, from_layer="L1", to_layer="L3"),
            ]
        )
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 2
        assert any(d.code == "geometry.via_position_conflict" for d in normalized.diagnostics)

    def test_a_reversed_span_is_not_a_conflict(self):
        # from/to order is not meaningful; L1->L2 and L2->L1 are the same span.
        board = _board(
            [
                _via("a", 0.0, 0.0, from_layer="L1", to_layer="L2"),
                _via("b", 0.0, 0.0, from_layer="L2", to_layer="L1"),
            ]
        )
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 1
        assert not [d for d in normalized.diagnostics if d.code == "geometry.via_position_conflict"]

    def test_overlapping_but_not_coincident_barrels_are_flagged(self):
        # 1.0 mm drill each: barrels overlap when centres are under 1.0 mm
        # apart. 50 um separation is well inside that and far outside the
        # coincidence tolerance.
        board = _board(
            [
                _via("a", 0.0, 0.0, net=_NET_A, drill_m=1.0e-3),
                _via("b", 50e-6, 0.0, net=_NET_B, drill_m=1.0e-3),
            ]
        )
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 2
        overlaps = [d for d in normalized.diagnostics if d.code == "geometry.via_overlap"]
        assert len(overlaps) == 1
        assert overlaps[0].severity == "error"

    def test_barrels_just_touching_within_tolerance_do_not_double_flag_as_merge(self):
        # Separated enough to not be "coincident" but close enough that a
        # large drill still overlaps: must be an overlap error, not a merge.
        board = _board(
            [
                _via("a", 0.0, 0.0, drill_m=0.5e-3),
                _via("b", 2e-7, 0.0, drill_m=0.5e-3),
            ]
        )
        normalized = ShapelyGeometryNormalizer().normalize(board)
        assert len(normalized.vias) == 2
        assert any(d.code == "geometry.via_overlap" for d in normalized.diagnostics)
        merges = [d for d in normalized.diagnostics if d.code == "geometry.merged_coincident_vias"]
        assert not merges


class TestStability:
    def test_identical_input_yields_identical_via_ids(self):
        board = _board([_via("a", 0.0, 0.0), _via("b", 5e-3, 0.0)])
        first = ShapelyGeometryNormalizer().normalize(board)
        second = ShapelyGeometryNormalizer().normalize(board)
        assert [cv.id for cv in first.vias] == [cv.id for cv in second.vias]

    def test_a_singleton_via_keeps_its_original_id(self):
        # The overwhelmingly common case (no duplicates at all) must not
        # rename every via id -- callers elsewhere use these to look up vias.
        board = _board([_via("only-via", 0.0, 0.0)])
        [consolidated] = ShapelyGeometryNormalizer().normalize(board).vias
        assert consolidated.id == "only-via"
        assert consolidated.via_ids == (ViaId("only-via"),)
