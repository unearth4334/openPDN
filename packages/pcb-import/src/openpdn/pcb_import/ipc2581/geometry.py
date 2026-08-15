"""Geometry resolution for the IPC-2581 adapter.

Turns syntax-model primitives (strokes, arcs, contours, flashes) into canonical
`Polygon2D` copper outlines in board coordinates and SI metres. Shapely is used
here, behind the importer boundary, for the operations that need robust
computational geometry: buffering stroked paths into outlines and validating
rings. Nothing Shapely-shaped escapes this module.

Every function takes and returns coordinates already scaled to metres; the
single scale-by-declared-unit conversion happens in `extract.py` before calling
in here, so this module cannot mix unit systems.

Tolerances are named, documented and chosen for fabrication-scale geometry:

* `ARC_SAGITTA_TOLERANCE_M` -- maximum deviation of a tessellated chord from the
  true arc. 1 um is an order of magnitude below the finest feature in ordinary
  PCB artwork (~50 um traces), so tessellation error cannot influence copper
  area or clearances at solver-relevant scales.
* `COINCIDENT_POINT_TOLERANCE_M` -- two points closer than 1 nm are the same
  point. Used to detect full-circle arcs (start == end) and to drop duplicate
  polygon vertices; 1 nm is far below fabrication resolution and far above
  double-precision noise at board scale (~1e-10 m for metre-scale values).
* `MIN_STROKE_WIDTH_M` -- a stroke narrower than 1 nm has no physical area;
  such features are reported as degenerate rather than buffered into slivers.
* `ARC_FULL_CIRCLE_TOLERANCE_M` / `ARC_DEGENERATE_CHORD_M` -- how an arc whose
  endpoints nearly coincide is read. See `classify_arc`.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal

from openpdn.domain.geometry import Polygon2D
from openpdn.pcb_import.ipc2581.syntax import (
    IpcCurveStep,
    IpcPolygon,
    IpcSegmentStep,
    IpcXform,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shapely.geometry import Polygon as ShapelyPolygon

ARC_SAGITTA_TOLERANCE_M: Final = 1e-6
COINCIDENT_POINT_TOLERANCE_M: Final = 1e-9
MIN_STROKE_WIDTH_M: Final = 1e-9

#: An arc whose endpoints are this close is a closed circle. Deliberately at
#: floating-point round-trip scale (1 pm) rather than fabrication scale:
#: generators write *exactly* equal endpoints to mean "full circle", so only
#: representation noise should be absorbed here.
ARC_FULL_CIRCLE_TOLERANCE_M: Final = 1e-12

#: An arc whose endpoints differ by less than this -- but by more than
#: representation noise -- is a degenerate segment, not a circle.
#:
#: Endpoints 12 nm apart cannot describe a manufacturable feature: no PCB
#: process resolves it, and no artwork intends it. They arise when a generator
#: rounds a zero-length segment. The distinction matters enormously because
#: the sweep is computed modulo a full turn: read as an open arc, a 12 nm
#: backwards displacement becomes a 359.99 degree sweep, which paints a
#: complete ring of copper where the design has none.
ARC_DEGENERATE_CHORD_M: Final = 1e-6

#: Bounds on segments used to approximate one full circle. The lower bound
#: keeps tiny circles (where the sagitta criterion would allow a triangle)
#: recognisably round; the upper bound caps vertex counts on huge arcs.
_MIN_SEGMENTS_PER_CIRCLE: Final = 16
_MAX_SEGMENTS_PER_CIRCLE: Final = 720


class DegenerateFeatureError(ValueError):
    """A primitive cannot bound any copper area (zero width, zero radius...).

    Raised for the extraction layer to catch and convert into a diagnostic --
    a degenerate feature must be *reported*, never silently dropped and never
    "repaired" into invented copper.
    """


Point = tuple[float, float]


class ArcClosure(StrEnum):
    """How an arc's endpoints relate, which decides how far it sweeps."""

    #: Endpoints are distinct: sweep from start to end in the stated direction.
    OPEN = "open"
    #: Endpoints coincide exactly: a complete circle, per IPC-2581 convention.
    FULL_CIRCLE = "full_circle"
    #: Endpoints differ by less than fabrication resolution: a zero-length
    #: segment a generator rounded, never a circle.
    DEGENERATE = "degenerate"


def classify_arc(start: Point, end: Point) -> ArcClosure:
    """Decide whether an arc is open, a full circle, or a rounded-away segment.

    The three-way split exists because the two closed cases are read in
    opposite ways and are only distinguishable by *how* closed they are:

    * exactly coincident endpoints are the standard way to write a circle;
    * endpoints a few nanometres apart are a rounded zero-length segment, and
      reading them as an open arc sweeps almost a full turn -- painting a ring
      of copper the design does not contain.
    """
    chord_m = math.hypot(start[0] - end[0], start[1] - end[1])
    if chord_m <= ARC_FULL_CIRCLE_TOLERANCE_M:
        return ArcClosure.FULL_CIRCLE
    if chord_m <= ARC_DEGENERATE_CHORD_M:
        return ArcClosure.DEGENERATE
    return ArcClosure.OPEN


# --- arcs ---------------------------------------------------------------------
def tessellate_arc(
    start: Point,
    end: Point,
    center: Point,
    clockwise: bool,
    sagitta_tolerance_m: float = ARC_SAGITTA_TOLERANCE_M,
) -> list[Point]:
    """Approximate a circular arc with chords, including both endpoints.

    Endpoint handling follows `classify_arc`: exactly coincident endpoints mean
    a full circle, near-coincident endpoints mean a degenerate segment, and
    anything else sweeps from start to end in the stated direction.

    The radius is taken from the start point; if the end point disagrees (a
    common generator rounding artefact) the chain still lands exactly on the
    given end point so adjacent segments stay connected.

    Raises:
        DegenerateFeatureError: If the radius is zero.
    """
    radius_m = math.hypot(start[0] - center[0], start[1] - center[1])
    if radius_m <= COINCIDENT_POINT_TOLERANCE_M:
        raise DegenerateFeatureError("Arc has zero radius")

    closure = classify_arc(start, end)
    if closure is ArcClosure.DEGENERATE:
        # Sweeping this would wrap almost all the way round the circle.
        return [start, end]

    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])

    full_circle = closure is ArcClosure.FULL_CIRCLE
    if full_circle:
        sweep = 2.0 * math.pi
    elif clockwise:
        sweep = (start_angle - end_angle) % (2.0 * math.pi)
    else:
        sweep = (end_angle - start_angle) % (2.0 * math.pi)
    if sweep <= 0.0:
        sweep = 2.0 * math.pi if full_circle else 0.0
    if sweep == 0.0:
        return [start, end]

    segments = _segments_for(radius_m, sweep, sagitta_tolerance_m)
    direction = -1.0 if clockwise else 1.0
    points: list[Point] = [start]
    for index in range(1, segments):
        angle = start_angle + direction * sweep * index / segments
        points.append(
            (center[0] + radius_m * math.cos(angle), center[1] + radius_m * math.sin(angle))
        )
    points.append(start if full_circle else end)
    return points


def circle_outline(center: Point, diameter_m: float) -> list[Point]:
    """Return a closed-ring approximation of a circle (no repeated last point).

    Raises:
        DegenerateFeatureError: If the diameter is not positive.
    """
    if diameter_m <= 0.0:
        raise DegenerateFeatureError("Circle has non-positive diameter")
    radius_m = 0.5 * diameter_m
    segments = _segments_for(radius_m, 2.0 * math.pi, ARC_SAGITTA_TOLERANCE_M)
    return [
        (
            center[0] + radius_m * math.cos(2.0 * math.pi * index / segments),
            center[1] + radius_m * math.sin(2.0 * math.pi * index / segments),
        )
        for index in range(segments)
    ]


def _segments_for(radius_m: float, sweep_rad: float, sagitta_tolerance_m: float) -> int:
    """Chord count meeting the sagitta tolerance, within the per-circle bounds."""
    if sagitta_tolerance_m >= radius_m:
        per_circle = _MIN_SEGMENTS_PER_CIRCLE
    else:
        # Sagitta of a chord subtending angle a: s = r (1 - cos(a / 2)).
        max_step_rad = 2.0 * math.acos(1.0 - sagitta_tolerance_m / radius_m)
        per_circle = max(
            _MIN_SEGMENTS_PER_CIRCLE,
            min(_MAX_SEGMENTS_PER_CIRCLE, math.ceil(2.0 * math.pi / max_step_rad)),
        )
    return max(1, math.ceil(per_circle * sweep_rad / (2.0 * math.pi)))


# --- polygon boundaries ---------------------------------------------------------
def polygon_ring(polygon: IpcPolygon, scale_to_m: float) -> list[Point]:
    """Resolve a syntax polygon into a metre-space ring without a closing vertex.

    Curved steps are tessellated; duplicate consecutive vertices (including a
    generator-written explicit closing vertex) are dropped.

    Raises:
        DegenerateFeatureError: If fewer than three distinct vertices remain.
    """
    points: list[Point] = [(polygon.begin.x * scale_to_m, polygon.begin.y * scale_to_m)]
    for step in polygon.steps:
        target = (step.x * scale_to_m, step.y * scale_to_m)
        if isinstance(step, IpcCurveStep):
            center = (step.center_x * scale_to_m, step.center_y * scale_to_m)
            arc_points = tessellate_arc(points[-1], target, center, step.clockwise)
            points.extend(arc_points[1:])
        else:
            points.append(target)
    ring = _dedupe(points)
    if len(ring) >= 2 and _close(ring[0], ring[-1]):
        ring.pop()
    if len(ring) < 3:
        raise DegenerateFeatureError("Polygon boundary has fewer than three distinct vertices")
    return ring


def polyline_path(
    begin: Point, steps: Sequence[IpcSegmentStep | IpcCurveStep], scale_to_m: float
) -> list[Point]:
    """Resolve an open polyline into a metre-space point chain."""
    points: list[Point] = [begin]
    for step in steps:
        target = (step.x * scale_to_m, step.y * scale_to_m)
        if isinstance(step, IpcCurveStep):
            center = (step.center_x * scale_to_m, step.center_y * scale_to_m)
            points.extend(tessellate_arc(points[-1], target, center, step.clockwise)[1:])
        else:
            points.append(target)
    return _dedupe(points)


def _dedupe(points: list[Point]) -> list[Point]:
    """Drop consecutive coincident vertices."""
    result: list[Point] = []
    for point in points:
        if not result or not _close(result[-1], point):
            result.append(point)
    return result


def _close(a: Point, b: Point) -> bool:
    """True when two points coincide within tolerance."""
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= COINCIDENT_POINT_TOLERANCE_M


# --- transforms -----------------------------------------------------------------
def apply_xform(
    points: Sequence[Point],
    xform: IpcXform | None,
    offset: Point,
) -> list[Point]:
    """Apply an IPC-2581 instance transform and translate to `offset`.

    Order follows the standard: mirror about the Y axis first, then rotate
    counter-clockwise, then translate. The transform is applied about the
    primitive's own origin, which is how dictionary shapes and flashed
    rectangles are defined.
    """
    if xform is None:
        return [(x + offset[0], y + offset[1]) for x, y in points]
    angle_rad = math.radians(xform.rotation_deg)
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    total_offset = (offset[0] + xform.x_offset, offset[1] + xform.y_offset)
    result: list[Point] = []
    for x, y in points:
        if xform.mirror:
            x = -x
        result.append(
            (x * cos_a - y * sin_a + total_offset[0], x * sin_a + y * cos_a + total_offset[1])
        )
    if xform.mirror:
        # Mirroring reverses ring orientation; reverse back so downstream
        # consumers keep a consistent winding.
        result.reverse()
    return result


def rectangle_ring(width_m: float, height_m: float) -> list[Point]:
    """Return a centre-origin rectangle ring.

    Raises:
        DegenerateFeatureError: If either dimension is not positive.
    """
    if width_m <= 0.0 or height_m <= 0.0:
        raise DegenerateFeatureError("Rectangle has non-positive dimensions")
    half_w, half_h = 0.5 * width_m, 0.5 * height_m
    return [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]


# --- strokes (Shapely enters here) ------------------------------------------------
def stroke_to_polygons(path: Sequence[Point], width_m: float, round_ends: bool) -> list[Polygon2D]:
    """Buffer a stroked path into copper outline polygons.

    A stroked trace of width w occupies real area; the solver and the viewer
    need the outline, not a centreline with an attribute.

    Args:
        path: Metre-space centreline, at least one point. A single point with
            round ends is a filled dot (a flashed round end).
        width_m: Stroke width in metres.
        round_ends: True for `ROUND` line ends, False for flat (`NONE`).

    Raises:
        DegenerateFeatureError: If the width is below `MIN_STROKE_WIDTH_M`, or
            the path is empty, or a flat-ended path has no length to sweep.
    """
    from shapely.geometry import LineString
    from shapely.geometry import Point as ShapelyPoint

    if not path:
        raise DegenerateFeatureError("Stroke path has no points")
    if width_m < MIN_STROKE_WIDTH_M:
        raise DegenerateFeatureError("Stroke width is below the minimum physical width")

    cap_style: Literal["round", "flat"] = "round" if round_ends else "flat"
    if len(path) == 1:
        if not round_ends:
            raise DegenerateFeatureError("A zero-length flat-ended stroke bounds no area")
        geometry = ShapelyPoint(path[0]).buffer(0.5 * width_m, quad_segs=_buffer_quad_segs(width_m))
    else:
        geometry = LineString(path).buffer(
            0.5 * width_m, cap_style=cap_style, quad_segs=_buffer_quad_segs(width_m)
        )
    if geometry.is_empty:
        raise DegenerateFeatureError("Stroke buffered to an empty region")
    return shapely_to_polygons(geometry)


def _buffer_quad_segs(width_m: float) -> int:
    """Buffer resolution meeting the sagitta tolerance at this stroke radius."""
    radius_m = 0.5 * width_m
    segments = _segments_for(radius_m, 2.0 * math.pi, ARC_SAGITTA_TOLERANCE_M)
    return max(4, segments // 4)


def rings_to_polygon(exterior: Sequence[Point], holes: Sequence[Sequence[Point]]) -> Polygon2D:
    """Build a canonical polygon from rings already resolved to metres."""
    return Polygon2D.from_coordinates(list(exterior), [list(hole) for hole in holes])


def shapely_to_polygons(geometry: object) -> list[Polygon2D]:
    """Convert a Shapely (multi)polygon into canonical polygons.

    Non-areal parts (lines, points) that can appear in degenerate boolean
    results are ignored; callers relying on area must check for emptiness.
    """
    from shapely.geometry import MultiPolygon
    from shapely.geometry import Polygon as ShapelyPolygonType
    from shapely.geometry.base import BaseGeometry

    if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
        return []
    parts: list[ShapelyPolygon]
    if isinstance(geometry, ShapelyPolygonType):
        parts = [geometry]
    elif isinstance(geometry, MultiPolygon):
        parts = list(geometry.geoms)
    else:
        collected = getattr(geometry, "geoms", None)
        parts = (
            [part for part in collected if isinstance(part, ShapelyPolygonType)]
            if collected is not None
            else []
        )
    polygons: list[Polygon2D] = []
    for part in parts:
        exterior = _ring_coordinates(list(part.exterior.coords))
        holes = [_ring_coordinates(list(ring.coords)) for ring in part.interiors]
        if len(exterior) >= 3:
            polygons.append(Polygon2D.from_coordinates(exterior, [h for h in holes if len(h) >= 3]))
    return polygons


def _ring_coordinates(coords: Sequence[Sequence[float]]) -> list[Point]:
    """Strip the repeated closing vertex Shapely rings carry."""
    ring = [(float(x), float(y)) for x, y, *_ in coords]
    if len(ring) >= 2 and _close(ring[0], ring[-1]):
        ring.pop()
    return ring
