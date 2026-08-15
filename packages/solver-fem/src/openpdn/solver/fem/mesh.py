"""Feature-aware triangulation of one copper polygon.

The mesher turns one normalised copper region -- a polygon with holes, in
board metres -- into a triangle mesh whose element size follows the local
conductor width: a wide plane receives coarse elements, a narrow trace
receives `elements_across_feature` elements across its width, and terminal
pads and via barrels are represented by mandatory vertices.

The algorithm is a graded, filtered Delaunay triangulation:

1. sample the boundary and measure local conductor *width* (inward ray) and
   *clearance* (outward ray) at each sample;
2. place boundary points at a spacing graded by width/clearance, preserving
   corners exactly;
3. fill the interior with hexagonal point lattices at power-of-two size
   levels selected by the local sizing field;
4. Delaunay-triangulate the point set (SciPy/Qhull) and keep only triangles
   that lie inside the copper, testing the centroid and several samples along
   every edge so no element bridges a slot between two arms of the polygon.

Why not Triangle or Gmsh: Shewchuk's Triangle is licensed for non-commercial
use only and Gmsh is GPL, so neither can be a hard dependency of an
Apache-2.0 tool; Qhull (SciPy) is permissively licensed (ADR-0010). The cost
is that boundary conformity is achieved by sampling density plus containment
filtering rather than by constrained triangulation -- which is why every mesh
reports its coverage ratio and why connectivity is verified downstream, never
assumed.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import shapely
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import LineString
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry.polygon import orient

from openpdn.solver.fem.controls import (
    INTERIOR_BOUNDARY_CLEARANCE,
    MANDATORY_SUPPRESSION_FRACTION,
    MAX_POINTS_PER_REGION,
    PILOT_SPACING_FRACTION,
    RAY_REACH_IN_TARGET_SIZES,
    SLIVER_AREA_FRACTION,
)
from openpdn.solver.fem.errors import MeshGenerationError

if TYPE_CHECKING:
    import numpy.typing as npt

    from openpdn.solver.fem.controls import MeshControls

#: Boundary vertices whose interior turn deviates from straight by more than
#: this are corners and are always kept; smaller deviations are treated as
#: tessellation points (imported arcs) and may be resampled at mesh spacing.
CORNER_TURN_RADIANS = math.radians(15.0)

#: Rings shorter than this many maximum element sizes keep every original
#: vertex: re-chording a small pad or hole at mesh spacing would visibly
#: shrink it (sagitta error ~h^2/8R), whereas keeping tens of points is free.
SMALL_RING_PERIMETER_IN_SIZES = 20.0

#: Number of interior sample points tested along each triangle edge when
#: filtering. Five samples reject any edge that bridges a slot wider than
#: about one sixth of the edge length; thinner slots are protected by the
#: clearance-graded boundary spacing (see `_boundary_spacing`).
EDGE_CONTAINMENT_SAMPLES = 5

#: Containment tests run against the polygon dilated by this much. Imported
#: arcs are tessellated with a 1 um sagitta tolerance (the importer's
#: ARC_SAGITTA_TOLERANCE_M), so a chord between adjacent hole-ring vertices
#: legitimately dips up to ~1 um into the hole; without tolerance every
#: triangle touching a hole ring is culled and annular pads lose their inner
#: band. 2 um absorbs tessellation sag while remaining far below the ~25 um
#: minimum slot any fabrication process produces -- it cannot bridge a real
#: clearance.
CONTAINMENT_TOLERANCE_M = 2e-6


@dataclass(frozen=True, slots=True)
class RegionMeshQuality:
    """Honest quality numbers for one region's mesh."""

    coverage_ratio: float
    min_angle_deg: float
    p05_angle_deg: float
    sliver_count: int


@dataclass(frozen=True, slots=True)
class RegionMesh:
    """Triangulation of one copper polygon.

    Attributes:
        points: `(n, 2)` float64 vertex coordinates in board metres.
        triangles: `(m, 3)` int32 vertex indices, counter-clockwise.
        boundary_mask: `(n,)` bool, True for vertices on the copper boundary.
        quality: Coverage and angle statistics for this region.
    """

    points: npt.NDArray[np.float64]
    triangles: npt.NDArray[np.int32]
    boundary_mask: npt.NDArray[np.bool_]
    quality: RegionMeshQuality


def mesh_polygon(
    polygon: ShapelyPolygon,
    controls: MeshControls,
    mandatory_points: npt.NDArray[np.float64] | None = None,
    region_label: str = "",
) -> RegionMesh:
    """Triangulate one copper polygon with width-graded element sizing.

    Args:
        polygon: The copper outline, holes included, in board metres.
        controls: Resolved sizing controls.
        mandatory_points: `(k, 2)` points that must become mesh vertices
            (via centres, pad vertices). Points outside the polygon are
            ignored -- the caller decides whether that is an error.
        region_label: Identifier used in failure diagnostics.

    Raises:
        MeshGenerationError: When the polygon cannot be meshed; the message
            names the region and the reason.
    """
    if polygon.is_empty or polygon.area <= 0.0:
        raise MeshGenerationError(f"Region {region_label}: polygon is empty")
    oriented = orient(polygon, sign=1.0)

    boundary_points, boundary_sizes = _sample_boundaries(oriented, controls, region_label)
    interior = _interior_lattice(oriented, boundary_points, boundary_sizes, controls)

    points = _merge_points(
        boundary_points,
        interior,
        None if mandatory_points is None else _inside_only(mandatory_points, oriented),
        boundary_sizes,
        controls,
    )
    if len(points) > MAX_POINTS_PER_REGION:
        raise MeshGenerationError(
            f"Region {region_label}: {len(points)} mesh points exceeds the per-region "
            f"cap of {MAX_POINTS_PER_REGION}; coarsen the mesh settings"
        )
    if len(points) < 3:
        raise MeshGenerationError(f"Region {region_label}: fewer than three mesh points")

    boundary_count = len(boundary_points)
    try:
        triangulation = Delaunay(points)
    except Exception as exc:
        raise MeshGenerationError(f"Region {region_label}: Delaunay failed: {exc}") from exc

    triangles = _filter_triangles(points, triangulation.simplices, oriented, controls)
    if len(triangles) == 0:
        raise MeshGenerationError(
            f"Region {region_label}: no triangle survived containment filtering; "
            "the copper is likely narrower than the minimum element size"
        )

    points, triangles, kept_boundary = _drop_unused_points(points, triangles, boundary_count)
    quality = _quality_of(points, triangles, oriented)
    return RegionMesh(
        points=points,
        triangles=triangles,
        boundary_mask=kept_boundary,
        quality=quality,
    )


# --- boundary sampling ---------------------------------------------------------------


def _rings_of(polygon: ShapelyPolygon) -> list[npt.NDArray[np.float64]]:
    """Return exterior and hole rings as `(n, 2)` open arrays."""
    rings = [np.asarray(polygon.exterior.coords[:-1], dtype=np.float64)]
    rings.extend(np.asarray(ring.coords[:-1], dtype=np.float64) for ring in polygon.interiors)
    return rings


def _sample_boundaries(
    polygon: ShapelyPolygon,
    controls: MeshControls,
    region_label: str,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Place graded boundary points on every ring.

    Returns the boundary points and, for each, the local target element size.
    """
    segments = _boundary_segments(polygon)
    segment_tree = shapely.STRtree(segments)
    reach_m = RAY_REACH_IN_TARGET_SIZES * controls.max_size_m

    all_points: list[npt.NDArray[np.float64]] = []
    all_sizes: list[npt.NDArray[np.float64]] = []
    for ring in _rings_of(polygon):
        if len(ring) < 3:
            raise MeshGenerationError(f"Region {region_label}: ring with fewer than 3 vertices")
        pts, sizes = _sample_ring(ring, polygon, segment_tree, reach_m, controls)
        all_points.append(pts)
        all_sizes.append(sizes)
    return np.concatenate(all_points), np.concatenate(all_sizes)


def _boundary_segments(polygon: ShapelyPolygon) -> list[LineString]:
    """All boundary edges as individual two-point segments, for ray queries."""
    segments: list[LineString] = []
    for ring in _rings_of(polygon):
        closed = np.vstack([ring, ring[:1]])
        segments.extend(LineString([closed[i], closed[i + 1]]) for i in range(len(closed) - 1))
    return segments


def _ray_distance(
    origin: npt.NDArray[np.float64],
    direction: npt.NDArray[np.float64],
    segment_tree: shapely.STRtree,
    segments_reach_m: float,
) -> float:
    """Distance from `origin` along `direction` to the first boundary hit.

    Returns `segments_reach_m` when nothing is hit within reach. The origin
    itself lies on the boundary, so hits closer than a nanometre are ignored.
    """
    end = origin + direction * segments_reach_m
    ray = LineString([origin, end])
    origin_point = shapely.Point(origin)
    best = segments_reach_m
    for index in segment_tree.query(ray, predicate="intersects"):
        geometry = segment_tree.geometries[int(index)]
        hit = ray.intersection(geometry)
        if hit.is_empty:
            continue
        for candidate in shapely.get_parts(hit) if hit.geom_type.startswith("Multi") else [hit]:
            distance = float(shapely.distance(origin_point, candidate))
            if 1e-9 < distance < best:
                best = distance
    return best


def _sample_ring(
    ring: npt.NDArray[np.float64],
    polygon: ShapelyPolygon,
    segment_tree: shapely.STRtree,
    reach_m: float,
    controls: MeshControls,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Resample one ring at width/clearance-graded spacing.

    With `orient(sign=1.0)` the exterior winds CCW and holes wind CW, so for
    every ring the copper lies to the *left* of the direction of travel: the
    inward normal of segment direction `(dx, dy)` is `(-dy, dx)`.
    """
    closed = np.vstack([ring, ring[:1]])
    deltas = np.diff(closed, axis=0)
    seg_lengths = np.hypot(deltas[:, 0], deltas[:, 1])
    perimeter = float(seg_lengths.sum())
    if perimeter <= 0.0:
        return ring[:1], np.full(1, controls.max_size_m)

    # Pilot pass: measure width and clearance at coarse intervals.
    pilot_spacing = max(controls.max_size_m * PILOT_SPACING_FRACTION, perimeter / 4096.0)
    pilot_count = max(8, int(perimeter / pilot_spacing))
    pilot_arcs = np.linspace(0.0, perimeter, pilot_count, endpoint=False)
    pilot_xy, pilot_normals = _points_along(closed, seg_lengths, pilot_arcs)

    pilot_sizes = np.empty(pilot_count, dtype=np.float64)
    k = float(controls.elements_across_feature)
    for i in range(pilot_count):
        inward = _ray_distance(pilot_xy[i], pilot_normals[i], segment_tree, reach_m)
        outward = _ray_distance(pilot_xy[i], -pilot_normals[i], segment_tree, reach_m)
        # Width grading keeps k elements across the conductor; clearance
        # grading keeps boundary points denser than half the slot to the
        # nearest other arm of this polygon, so Delaunay edges cannot bridge
        # the slot (their empty circumcircles would contain the dense flank).
        size = min(inward / k, outward / 2.0, controls.max_size_m)
        pilot_sizes[i] = max(size, controls.min_size_m)

    # Placement pass: walk the ring, stepping by the locally measured size.
    keep_all_vertices = perimeter <= SMALL_RING_PERIMETER_IN_SIZES * controls.max_size_m
    corner_arcs = _corner_arc_positions(closed, seg_lengths, keep_all_vertices)

    placed_arcs: list[float] = []
    for start, stop in itertools.pairwise(corner_arcs):
        placed_arcs.extend(_walk_span(start, stop, pilot_arcs, pilot_sizes, perimeter))
    arcs = np.asarray(placed_arcs, dtype=np.float64)
    xy, _ = _points_along(closed, seg_lengths, arcs)
    sizes = np.interp(arcs, pilot_arcs, pilot_sizes, period=perimeter)
    del polygon  # containment is enforced later, on triangles
    return xy, sizes


def _corner_arc_positions(
    closed: npt.NDArray[np.float64],
    seg_lengths: npt.NDArray[np.float64],
    keep_all: bool,
) -> npt.NDArray[np.float64]:
    """Arc positions of vertices that must be kept exactly.

    Always includes vertex 0 and the full perimeter (closure). Corner
    detection compares each vertex's turn angle against the tessellation
    threshold; `keep_all` short-circuits for small rings.
    """
    cumulative = np.concatenate([[0.0], np.cumsum(seg_lengths)])
    if keep_all:
        return cumulative
    n = len(closed) - 1
    keep = [0.0]
    for i in range(1, n):
        prev_vec = closed[i] - closed[i - 1]
        next_vec = closed[i + 1] - closed[i]
        turn = abs(
            math.atan2(
                prev_vec[0] * next_vec[1] - prev_vec[1] * next_vec[0],
                float(prev_vec @ next_vec),
            )
        )
        if turn >= CORNER_TURN_RADIANS:
            keep.append(float(cumulative[i]))
    keep.append(float(cumulative[-1]))
    return np.asarray(keep)


def _walk_span(
    start: float,
    stop: float,
    pilot_arcs: npt.NDArray[np.float64],
    pilot_sizes: npt.NDArray[np.float64],
    perimeter: float,
) -> list[float]:
    """Place arc positions from `start` (inclusive) to `stop` (exclusive).

    Steps by the interpolated local size; the final sub-interval is stretched
    or merged so spacing stays within about 1.5x of the local target.
    """
    span = stop - start
    if span <= 0.0:
        return []
    positions = [start]
    arc = start
    while True:
        step = float(np.interp(arc % perimeter, pilot_arcs, pilot_sizes, period=perimeter))
        if arc + step >= stop:
            remainder = stop - arc
            if remainder > 0.6 * step and len(positions) >= 1:
                # Split the remainder evenly to avoid one oversized gap.
                extra = max(1, round(remainder / step))
                positions.extend(arc + remainder * i / extra for i in range(1, extra))
            break
        arc += step
        positions.append(arc)
    return positions


def _points_along(
    closed: npt.NDArray[np.float64],
    seg_lengths: npt.NDArray[np.float64],
    arcs: npt.NDArray[np.float64],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Interpolate ring points and inward normals at arc-length positions."""
    cumulative = np.concatenate([[0.0], np.cumsum(seg_lengths)])
    perimeter = cumulative[-1]
    wrapped = np.mod(arcs, perimeter)
    upper_bound = len(seg_lengths) - 1
    indices = np.clip(np.searchsorted(cumulative, wrapped, side="right") - 1, 0, upper_bound)
    local = wrapped - cumulative[indices]
    directions = np.diff(closed, axis=0)
    lengths = seg_lengths[indices]
    lengths = np.where(lengths <= 0.0, 1.0, lengths)
    unit = directions[indices] / lengths[:, None]
    xy = closed[indices] + unit * local[:, None]
    normals = np.column_stack([-unit[:, 1], unit[:, 0]])
    return xy, normals


# --- interior fill -------------------------------------------------------------------


def _interior_lattice(
    polygon: ShapelyPolygon,
    boundary_points: npt.NDArray[np.float64],
    boundary_sizes: npt.NDArray[np.float64],
    controls: MeshControls,
) -> npt.NDArray[np.float64]:
    """Fill the interior with hexagonal lattices graded by the sizing field.

    The local target size at `x` is `s_b + growth * d`, where `s_b` is the
    spacing of the nearest boundary point and `d` its distance -- element
    size grows smoothly away from refined boundaries and saturates at the
    maximum. Each power-of-two size level contributes the lattice points
    whose local target falls in `[level, 2*level)`.

    Fine levels are only generated near the boundary points that demand them
    (a pad in a large plane must not trigger a board-wide fine lattice); the
    coarsest level covers the whole region.
    """
    tree = cKDTree(boundary_points)
    min_x, min_y, max_x, max_y = polygon.bounds
    finest_needed = float(boundary_sizes.min())
    chosen: list[npt.NDArray[np.float64]] = []
    for level in _size_levels(controls, finest_needed):
        if level >= controls.max_size_m:
            candidates = _hex_lattice_full(min_x, min_y, max_x, max_y, level)
        else:
            # Only boundary points refined below 2*level can pull the local
            # target under 2*level, and only within the growth radius.
            demanding = boundary_points[boundary_sizes < 2.0 * level]
            if len(demanding) == 0:
                continue
            radius = 2.0 * level / controls.growth_rate
            candidates = _hex_lattice_near(min_x, min_y, max_x, max_y, level, demanding, radius)
        if len(candidates) == 0:
            continue
        inside = shapely.contains_xy(polygon, candidates[:, 0], candidates[:, 1])
        candidates = candidates[inside]
        if len(candidates) == 0:
            continue
        distances, nearest = tree.query(candidates)
        target = np.minimum(
            boundary_sizes[nearest] + controls.growth_rate * distances,
            controls.max_size_m,
        )
        clear = distances >= INTERIOR_BOUNDARY_CLEARANCE * level
        wanted = (target >= level) & (target < 2.0 * level) & clear
        chosen.append(candidates[wanted])
    if not chosen:
        return np.empty((0, 2), dtype=np.float64)
    return np.vstack(chosen)


def _size_levels(controls: MeshControls, finest_needed_m: float) -> list[float]:
    """Power-of-two size levels from the maximum down to the finest required.

    A level is only useful if some location's target size falls in
    `[level, 2*level)`; targets never drop below the finest boundary spacing,
    so levels entirely below it are skipped.
    """
    floor = max(controls.min_size_m, finest_needed_m / 2.0)
    levels = [controls.max_size_m]
    while levels[-1] / 2.0 >= floor:
        levels.append(levels[-1] / 2.0)
    return levels


def _hex_lattice_full(
    min_x: float, min_y: float, max_x: float, max_y: float, spacing: float
) -> npt.NDArray[np.float64]:
    """Hexagonal lattice covering a bounding box.

    Hexagonal rather than square: square lattices produce cocircular point
    quadruples that degrade Qhull's Delaunay into arbitrary diagonal choices,
    while hex lattices yield near-equilateral triangles.
    """
    row_height = spacing * math.sqrt(3.0) / 2.0
    n_rows = int((max_y - min_y) / row_height) + 2
    n_cols = int((max_x - min_x) / spacing) + 2
    if n_rows * n_cols > 8 * MAX_POINTS_PER_REGION:
        # The caller's per-region cap will reject this mesh anyway; avoid the
        # allocation blow-up here.
        raise MeshGenerationError(
            f"Interior lattice at spacing {spacing:.3e} m would need "
            f"{n_rows * n_cols} candidate points; coarsen the mesh settings"
        )
    rows = np.arange(n_rows)
    cols = np.arange(n_cols)
    xx, yy = np.meshgrid(cols.astype(np.float64), rows.astype(np.float64))
    x = min_x + (xx + 0.5 * (yy % 2.0)) * spacing
    y = min_y + yy * row_height
    return np.column_stack([x.ravel(), y.ravel()])


def _hex_lattice_near(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    spacing: float,
    centers: npt.NDArray[np.float64],
    radius_m: float,
) -> npt.NDArray[np.float64]:
    """Hex-lattice points within `radius_m` of any of `centers`.

    Points are addressed by global (row, col) lattice indices so overlapping
    neighbourhoods deduplicate exactly; the lattice never materialises outside
    the demanded neighbourhoods.
    """
    row_height = spacing * math.sqrt(3.0) / 2.0
    wanted: set[tuple[int, int]] = set()
    rows_reach = int(radius_m / row_height) + 1
    cols_reach = int(radius_m / spacing) + 1
    max_row = int((max_y - min_y) / row_height) + 1
    max_col = int((max_x - min_x) / spacing) + 1
    for cx, cy in centers:
        row0 = int((cy - min_y) / row_height)
        col0 = int((cx - min_x) / spacing)
        for row in range(max(0, row0 - rows_reach), min(max_row, row0 + rows_reach) + 1):
            for col in range(max(0, col0 - cols_reach), min(max_col, col0 + cols_reach) + 1):
                wanted.add((row, col))
        if len(wanted) > 8 * MAX_POINTS_PER_REGION:
            raise MeshGenerationError(
                f"Refined interior lattice at spacing {spacing:.3e} m exceeds the "
                "point budget; coarsen the mesh settings"
            )
    if not wanted:
        return np.empty((0, 2), dtype=np.float64)
    indices = np.asarray(sorted(wanted), dtype=np.float64)
    rows_f, cols_f = indices[:, 0], indices[:, 1]
    x = min_x + (cols_f + 0.5 * (rows_f % 2.0)) * spacing
    y = min_y + rows_f * row_height
    return np.column_stack([x, y])


# --- point merging -------------------------------------------------------------------


def _inside_only(
    points: npt.NDArray[np.float64], polygon: ShapelyPolygon
) -> npt.NDArray[np.float64]:
    """Keep only points inside (or on) the polygon."""
    if len(points) == 0:
        return points
    keep = shapely.intersects_xy(polygon, points[:, 0], points[:, 1])
    return points[keep]


def _merge_points(
    boundary: npt.NDArray[np.float64],
    interior: npt.NDArray[np.float64],
    mandatory: npt.NDArray[np.float64] | None,
    boundary_sizes: npt.NDArray[np.float64],
    controls: MeshControls,
) -> npt.NDArray[np.float64]:
    """Combine point sets; mandatory points suppress nearby generated points.

    Order matters downstream: boundary points come first so their indices can
    be tracked through triangulation, then mandatory, then interior.
    """
    kept: list[npt.NDArray[np.float64]] = [boundary]
    if mandatory is not None and len(mandatory) > 0:
        # Deduplicate mandatory points against the boundary at nanometre scale
        # (a pad vertex may lie exactly on the copper outline).
        b_tree = cKDTree(boundary)
        distances, _ = b_tree.query(mandatory)
        mandatory = mandatory[distances > 1e-9]
    if mandatory is not None and len(mandatory) > 0:
        kept.append(mandatory)
        suppressor = cKDTree(np.vstack([boundary, mandatory]))
    else:
        mandatory = None
        suppressor = cKDTree(boundary)

    if len(interior) > 0:
        b_tree = cKDTree(boundary)
        distances, nearest = b_tree.query(interior)
        local = np.minimum(
            boundary_sizes[nearest] + controls.growth_rate * distances, controls.max_size_m
        )
        near_any, _ = suppressor.query(interior)
        keep = near_any >= MANDATORY_SUPPRESSION_FRACTION * local
        kept.append(interior[keep])

    merged = np.vstack(kept)
    return merged


# --- triangle filtering and quality --------------------------------------------------


def _filter_triangles(
    points: npt.NDArray[np.float64],
    simplices: npt.NDArray[np.signedinteger],
    polygon: ShapelyPolygon,
    controls: MeshControls,
) -> npt.NDArray[np.int32]:
    """Keep triangles that genuinely lie inside the copper.

    A Delaunay triangulation of the point set covers the convex hull, so
    triangles spanning concavities, holes and slots must be culled. The test
    is: centroid inside, plus `EDGE_CONTAINMENT_SAMPLES` interior samples of
    every edge inside. Degenerate slivers are dropped by area.
    """
    tri = simplices.astype(np.int32)
    # See CONTAINMENT_TOLERANCE_M: tessellated arcs make exact containment
    # reject legitimate boundary triangles.
    tolerant = polygon.buffer(CONTAINMENT_TOLERANCE_M, quad_segs=2)
    p = points[tri]  # (m, 3, 2)
    # Signed area; also orients triangles CCW.
    area2 = (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 2, 0] - p[:, 0, 0]) * (
        p[:, 1, 1] - p[:, 0, 1]
    )
    flip = area2 < 0.0
    tri[flip] = tri[flip][:, [0, 2, 1]]
    area = np.abs(area2) / 2.0
    min_area = SLIVER_AREA_FRACTION * controls.min_size_m**2
    tri = tri[area > min_area]
    if len(tri) == 0:
        return tri
    p = points[tri]

    centroid = p.mean(axis=1)
    keep = shapely.contains_xy(tolerant, centroid[:, 0], centroid[:, 1])
    tri, p = tri[keep], p[keep]
    if len(tri) == 0:
        return tri

    fractions = np.linspace(0.0, 1.0, EDGE_CONTAINMENT_SAMPLES + 2)[1:-1]
    keep_mask = np.ones(len(tri), dtype=bool)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        start, end = p[:, a, :], p[:, b, :]
        for f in fractions:
            sample = start + (end - start) * f
            inside = shapely.intersects_xy(tolerant, sample[:, 0], sample[:, 1])
            keep_mask &= inside
            if not keep_mask.any():
                break
    return tri[keep_mask]


def _drop_unused_points(
    points: npt.NDArray[np.float64],
    triangles: npt.NDArray[np.int32],
    boundary_count: int,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int32], npt.NDArray[np.bool_]]:
    """Compact the point array to vertices actually referenced by triangles."""
    used = np.unique(triangles)
    remap = np.full(len(points), -1, dtype=np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)
    boundary_mask = used < boundary_count
    return points[used], remap[triangles], boundary_mask


def _quality_of(
    points: npt.NDArray[np.float64],
    triangles: npt.NDArray[np.int32],
    polygon: ShapelyPolygon,
) -> RegionMeshQuality:
    """Coverage ratio and minimum-angle statistics for a finished mesh."""
    p = points[triangles]
    area = (
        np.abs(
            (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
            - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])
        )
        / 2.0
    )
    coverage = float(area.sum() / polygon.area) if polygon.area > 0 else 0.0

    angles = _min_angles_deg(p)
    return RegionMeshQuality(
        coverage_ratio=coverage,
        min_angle_deg=float(angles.min()) if len(angles) else 0.0,
        p05_angle_deg=float(np.percentile(angles, 5.0)) if len(angles) else 0.0,
        sliver_count=int((angles < 5.0).sum()),
    )


def _min_angles_deg(p: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Smallest interior angle of each triangle, in degrees."""
    a = np.linalg.norm(p[:, 1] - p[:, 2], axis=1)
    b = np.linalg.norm(p[:, 2] - p[:, 0], axis=1)
    c = np.linalg.norm(p[:, 0] - p[:, 1], axis=1)
    angles = np.empty((len(p), 3))
    for i, (opp, s1, s2) in enumerate(((a, b, c), (b, c, a), (c, a, b))):
        cos = np.clip((s1**2 + s2**2 - opp**2) / (2.0 * s1 * s2 + 1e-300), -1.0, 1.0)
        angles[:, i] = np.degrees(np.arccos(cos))
    return angles.min(axis=1)
