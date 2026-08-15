"""Planar geometry value objects."""

from __future__ import annotations

import pytest

from openpdn.domain.errors import InvalidGeometryError
from openpdn.domain.geometry import BoundingBox2D, Point2D, Polygon2D


class TestPolygon:
    def test_rectangle_area(self):
        rectangle = Polygon2D.rectangle(Point2D(0.0, 0.0), 0.040, 0.008)
        assert rectangle.area_m2 == pytest.approx(0.040 * 0.008)

    def test_winding_direction_does_not_change_area(self):
        clockwise = Polygon2D.from_coordinates([(0, 0), (0, 1), (1, 1), (1, 0)])
        counterclockwise = Polygon2D.from_coordinates([(0, 0), (1, 0), (1, 1), (0, 1)])
        assert clockwise.area_m2 == pytest.approx(counterclockwise.area_m2)

    def test_holes_are_subtracted(self):
        with_hole = Polygon2D.from_coordinates(
            [(0, 0), (4, 0), (4, 4), (0, 4)],
            [[(1, 1), (2, 1), (2, 2), (1, 2)]],
        )
        assert with_hole.area_m2 == pytest.approx(16.0 - 1.0)

    def test_a_ring_needs_three_vertices(self):
        with pytest.raises(InvalidGeometryError):
            Polygon2D.from_coordinates([(0, 0), (1, 1)])

    def test_bounding_box_covers_the_exterior(self):
        polygon = Polygon2D.rectangle(Point2D(0.005, 0.005), 0.040, 0.008)
        box = polygon.bounding_box
        assert box.min_x_m == pytest.approx(0.005)
        assert box.width_m == pytest.approx(0.040)
        assert box.height_m == pytest.approx(0.008)


class TestBoundingBox:
    def test_merging_covers_both_inputs(self):
        merged = BoundingBox2D(0, 0, 1, 1).merged_with(BoundingBox2D(2, 3, 4, 5))
        assert (merged.min_x_m, merged.min_y_m) == (0, 0)
        assert (merged.max_x_m, merged.max_y_m) == (4, 5)

    def test_inverted_boxes_are_rejected(self):
        with pytest.raises(InvalidGeometryError):
            BoundingBox2D(1.0, 0.0, 0.0, 1.0)

    def test_enclosing_requires_points(self):
        with pytest.raises(InvalidGeometryError):
            BoundingBox2D.enclosing([])


class TestPoint:
    def test_distance(self):
        assert Point2D(0.0, 0.0).distance_to_m(Point2D(3.0, 4.0)) == pytest.approx(5.0)
