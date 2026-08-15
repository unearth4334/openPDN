"""Geometry resolution inside the IPC-2581 adapter.

These are the primitives every imported polygon flows through; an error here
is a wrong copper area in every later analysis. Cases are chosen so the
correct answer is computable by hand.
"""

from __future__ import annotations

import itertools
import math

import pytest

from openpdn.pcb_import.ipc2581.geometry import (
    ARC_SAGITTA_TOLERANCE_M,
    ArcClosure,
    DegenerateFeatureError,
    apply_xform,
    circle_outline,
    classify_arc,
    polygon_ring,
    stroke_to_polygons,
    tessellate_arc,
)
from openpdn.pcb_import.ipc2581.syntax import (
    IpcCurveStep,
    IpcPoint,
    IpcPolygon,
    IpcSegmentStep,
    IpcXform,
)

pytestmark = pytest.mark.unit


def _ring_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


class TestArcTessellation:
    def test_a_quarter_arc_lands_exactly_on_its_endpoints(self):
        points = tessellate_arc((1.0, 0.0), (0.0, 1.0), (0.0, 0.0), clockwise=False)
        assert points[0] == (1.0, 0.0)
        assert points[-1] == (0.0, 1.0)
        # Every intermediate point sits on the circle.
        for x, y in points:
            assert math.hypot(x, y) == pytest.approx(1.0, rel=1e-9)

    def test_clockwise_and_counterclockwise_sweep_opposite_sides(self):
        ccw = tessellate_arc((1.0, 0.0), (-1.0, 0.0), (0.0, 0.0), clockwise=False)
        cw = tessellate_arc((1.0, 0.0), (-1.0, 0.0), (0.0, 0.0), clockwise=True)
        # CCW passes above the x axis, CW below.
        assert all(y >= -1e-12 for _, y in ccw)
        assert all(y <= 1e-12 for _, y in cw)

    def test_equal_endpoints_mean_a_full_circle(self):
        points = tessellate_arc((2.0, 0.0), (2.0, 0.0), (0.0, 0.0), clockwise=False)
        # A closed ring: enough points to bound the full disc area.
        area = _ring_area(points[:-1])
        assert area == pytest.approx(math.pi * 4.0, rel=1e-3)

    def test_the_chord_error_respects_the_sagitta_tolerance(self):
        radius = 0.01  # 10 mm
        points = tessellate_arc((radius, 0.0), (-radius, 0.0), (0.0, 0.0), clockwise=False)
        for (x0, y0), (x1, y1) in itertools.pairwise(points):
            midpoint_radius = math.hypot((x0 + x1) / 2.0, (y0 + y1) / 2.0)
            sagitta = radius - midpoint_radius
            assert sagitta <= ARC_SAGITTA_TOLERANCE_M * 1.01

    def test_a_zero_radius_arc_is_degenerate(self):
        with pytest.raises(DegenerateFeatureError):
            tessellate_arc((0.0, 0.0), (0.0, 0.0), (0.0, 0.0), clockwise=False)


class TestCircles:
    def test_circle_area_matches_the_analytical_disc(self):
        ring = circle_outline((0.0, 0.0), 1e-3)
        # An inscribed polygon under-counts area by about 2*sagitta/radius
        # (0.4 % at r = 0.5 mm with the 1 um sagitta tolerance).
        assert _ring_area(ring) == pytest.approx(math.pi * (0.5e-3) ** 2, rel=5e-3)

    def test_a_non_positive_diameter_is_degenerate(self):
        with pytest.raises(DegenerateFeatureError):
            circle_outline((0.0, 0.0), 0.0)


class TestPolygonRings:
    def test_curved_steps_are_tessellated_into_the_ring(self):
        # A half-disc: straight edge along y=0, arc closing over the top.
        polygon = IpcPolygon(
            begin=IpcPoint(-1.0, 0.0),
            steps=(
                IpcSegmentStep(1.0, 0.0),
                IpcCurveStep(-1.0, 0.0, center_x=0.0, center_y=0.0, clockwise=False),
            ),
        )
        ring = polygon_ring(polygon, scale_to_m=1.0)
        assert _ring_area(ring) == pytest.approx(math.pi / 2.0, rel=1e-3)

    def test_a_written_closing_vertex_is_dropped(self):
        polygon = IpcPolygon(
            begin=IpcPoint(0.0, 0.0),
            steps=(
                IpcSegmentStep(1.0, 0.0),
                IpcSegmentStep(1.0, 1.0),
                IpcSegmentStep(0.0, 1.0),
                IpcSegmentStep(0.0, 0.0),  # generators often close explicitly
            ),
        )
        ring = polygon_ring(polygon, scale_to_m=1.0)
        assert len(ring) == 4

    def test_scale_is_applied_exactly_once(self):
        polygon = IpcPolygon(
            begin=IpcPoint(0.0, 0.0),
            steps=(
                IpcSegmentStep(10.0, 0.0),
                IpcSegmentStep(10.0, 10.0),
                IpcSegmentStep(0.0, 10.0),
            ),
        )
        ring = polygon_ring(polygon, scale_to_m=1e-3)  # document in millimetres
        assert _ring_area(ring) == pytest.approx(100e-6, rel=1e-12)

    def test_a_collapsed_boundary_is_degenerate(self):
        polygon = IpcPolygon(begin=IpcPoint(0.0, 0.0), steps=(IpcSegmentStep(1.0, 0.0),))
        with pytest.raises(DegenerateFeatureError):
            polygon_ring(polygon, scale_to_m=1.0)


class TestTransforms:
    def test_rotation_is_counterclockwise(self):
        [(x, y)] = apply_xform([(1.0, 0.0)], IpcXform(rotation_deg=90.0), (0.0, 0.0))
        assert (x, y) == pytest.approx((0.0, 1.0))

    def test_mirror_flips_x_before_rotation(self):
        [(x, y)] = apply_xform([(1.0, 0.0)], IpcXform(rotation_deg=90.0, mirror=True), (0.0, 0.0))
        # Mirror: (1,0) -> (-1,0); rotate 90 CCW: (-1,0) -> (0,-1).
        assert (x, y) == pytest.approx((0.0, -1.0))

    def test_offsets_and_location_combine(self):
        [(x, y)] = apply_xform([(1.0, 2.0)], IpcXform(x_offset=10.0, y_offset=20.0), (100.0, 200.0))
        assert (x, y) == pytest.approx((111.0, 222.0))


class TestStrokeResolution:
    def test_a_round_ended_trace_has_rect_plus_disc_area(self):
        # 8 mm long, 1 mm wide: 8 mm^2 body plus a pi/4 mm^2 pair of end caps.
        polygons = stroke_to_polygons([(0.0, 0.0), (8e-3, 0.0)], 1e-3, round_ends=True)
        area = sum(polygon.area_m2 for polygon in polygons)
        expected = 8e-6 + math.pi * (0.5e-3) ** 2
        assert area == pytest.approx(expected, rel=1e-3)

    def test_a_flat_ended_trace_is_exactly_the_swept_rectangle(self):
        polygons = stroke_to_polygons([(0.0, 0.0), (8e-3, 0.0)], 1e-3, round_ends=False)
        area = sum(polygon.area_m2 for polygon in polygons)
        assert area == pytest.approx(8e-6, rel=1e-9)

    def test_a_single_point_with_round_ends_is_a_dot(self):
        polygons = stroke_to_polygons([(0.0, 0.0)], 1e-3, round_ends=True)
        area = sum(polygon.area_m2 for polygon in polygons)
        # Inscribed-polygon area deficit, as in the circle test above.
        assert area == pytest.approx(math.pi * (0.5e-3) ** 2, rel=5e-3)

    def test_a_zero_width_stroke_is_degenerate_not_invented(self):
        with pytest.raises(DegenerateFeatureError):
            stroke_to_polygons([(0.0, 0.0), (1e-3, 0.0)], 0.0, round_ends=True)


class TestArcClosure:
    """How an arc's endpoints are read decides whether copper exists at all.

    A generator writes exactly-coincident endpoints to mean a full circle. It
    also writes endpoints a few nanometres apart when it rounds a zero-length
    segment -- and reading *those* as an open arc sweeps almost a whole turn,
    painting a ring the design does not contain.
    """

    def test_exactly_coincident_endpoints_are_a_full_circle(self):
        assert classify_arc((1.0, 0.0), (1.0, 0.0)) is ArcClosure.FULL_CIRCLE

    def test_endpoints_a_few_nanometres_apart_are_degenerate(self):
        # 12 nm apart: the case observed in a real export.
        assert classify_arc((1.0, 0.0), (1.0 - 12e-9, 0.0)) is ArcClosure.DEGENERATE

    def test_ordinary_endpoints_are_open(self):
        assert classify_arc((1.0, 0.0), (0.0, 1.0)) is ArcClosure.OPEN

    def test_a_degenerate_arc_does_not_sweep_a_circle(self):
        # Radius 0.3178 mm, endpoints 12 nm apart straddling the top of the
        # circle -- so their angles differ in the opposite sense to the stated
        # direction, which is what makes a naive sweep wrap almost fully round.
        # Swept that way it would span the full 0.6356 mm diameter.
        start = (10.317806e-3, 5.3178e-3)
        end = (10.317794e-3, 5.3178e-3)
        center = (10.3178e-3, 5.0e-3)
        points = tessellate_arc(start, end, center, clockwise=True)
        span_m = max(x for x, _ in points) - min(x for x, _ in points)
        assert span_m < 1e-6
        assert len(points) == 2

    def test_a_true_full_circle_still_sweeps(self):
        start = end = (10.0e-3, 5.0e-3)
        center = (10.3178e-3, 5.0e-3)
        points = tessellate_arc(start, end, center, clockwise=True)
        span_m = max(x for x, _ in points) - min(x for x, _ in points)
        assert span_m == pytest.approx(2 * 0.3178e-3, rel=1e-3)
