"""Shapely-backed geometry normalisation engine.

The one place openPDN performs Boolean unions on copper. Imported
`CopperRegion` polygons -- one per source feature, frequently overlapping --
are grouped by `(net, physical layer)` and unioned into disjoint polygons with
holes. The output is what a 2.5-D sheet solver meshes over and what the viewer
renders in its "normalized" mode.

Invalid input rings (self-intersections, bow-ties) are repaired with
`make_valid` and *counted*: a repair changes geometry, so it is reported as a
diagnostic rather than performed silently.

Provenance is preserved many-to-many: each output polygon records which
imported regions contributed copper to it, resolved with an STRtree so the
cost stays near-linear in region count.
"""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING, Final

from openpdn.domain.geometry import Point2D
from openpdn.domain.results import Diagnostic, DiagnosticSeverity
from openpdn.domain.units import METRE
from openpdn.geometry.api import (
    ConsolidatedVia,
    GeometryNormalizationError,
    NormalizationStats,
    NormalizedGeometry,
    NormalizedRegion,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.geometry.base import BaseGeometry

    from openpdn.domain.board import Board, CopperRegion, CopperRegionId, Via
    from openpdn.domain.geometry import Polygon2D

#: Bump when normalisation semantics change; invalidates every derived cache.
NORMALIZER_VERSION: Final = "1"

#: Output polygons smaller than this are numerical slivers, not copper.
#: 1e-14 m^2 is a 10 nm x 1 nm rectangle -- far below fabrication resolution,
#: far above double-precision noise for board-scale coordinates.
MIN_REGION_AREA_M2: Final = 1e-14

#: Vias whose barrel centres are closer than this are the same physical via,
#: listed twice by the source. 100 nm sits about 8x above the 12 nm generator
#: rounding noise observed in arc endpoints (`ipc2581.geometry`) and about
#: 4000x below the smallest real via pitch measured on a production board
#: (405 um) -- wide enough to absorb representation noise, nowhere near wide
#: enough to catch two legitimately distinct, closely placed vias.
VIA_COINCIDENT_TOLERANCE_M: Final = 1e-7


class ShapelyGeometryNormalizer:
    """`GeometryNormalizer` implementation on Shapely."""

    @property
    def version(self) -> str:
        """Engine version for cache keys."""
        return NORMALIZER_VERSION

    def normalize(self, board: Board) -> NormalizedGeometry:
        """Union the board's copper per `(net, physical layer)`.

        Raises:
            GeometryNormalizationError: If Shapely fails on the board's
                geometry; the message names the group, not the coordinates.
        """
        started = perf_counter()
        diagnostics: list[Diagnostic] = []
        regions: list[NormalizedRegion] = []
        boolean_operations = 0
        repaired = 0
        discarded = 0

        groups: dict[tuple[str, str | None], list[CopperRegion]] = {}
        for region in board.copper_regions:
            groups.setdefault((str(region.layer_id), region.net_id), []).append(region)

        # Deterministic output ordering: stackup order, then net id.
        layer_order = {str(layer.id): layer.index for layer in board.stackup.layers}
        for (layer_id, net_id), members in sorted(
            groups.items(),
            key=lambda item: (layer_order.get(item[0][0], 1_000_000), item[0][1] or ""),
        ):
            try:
                merged, group_repaired = _union_group(members)
            except Exception as exc:  # Shapely's exception surface is broad.
                raise GeometryNormalizationError(
                    f"Boolean union failed for net {net_id or '(unassigned)'} on layer {layer_id}"
                ) from exc
            boolean_operations += 1
            repaired += group_repaired

            parts = _areal_parts(merged)
            provenance = _ProvenanceIndex(members)
            for part_index, part in enumerate(parts):
                if part.area < MIN_REGION_AREA_M2:
                    discarded += 1
                    continue
                regions.append(
                    NormalizedRegion(
                        id=f"n-{layer_id}-{net_id or 'unassigned'}-{part_index:03d}",
                        layer_id=members[0].layer_id,
                        net_id=members[0].net_id,
                        polygon=_to_domain_polygon(part),
                        source_region_ids=provenance.contributors(part),
                    )
                )

        if repaired:
            diagnostics.append(
                Diagnostic(
                    code="geometry.repaired_invalid_regions",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        "Imported copper outlines were geometrically invalid "
                        "(self-intersecting or degenerate rings) and were repaired before "
                        "unioning; review the affected areas."
                    ),
                    context={"count": str(repaired)},
                )
            )
        if discarded:
            diagnostics.append(
                Diagnostic(
                    code="geometry.discarded_slivers",
                    severity=DiagnosticSeverity.INFO,
                    message="Numerical sliver polygons below the minimum area were discarded.",
                    context={"count": str(discarded)},
                )
            )

        consolidated_vias, via_diagnostics = _consolidate_vias(board)
        diagnostics.extend(via_diagnostics)

        return NormalizedGeometry(
            normalizer_version=NORMALIZER_VERSION,
            regions=tuple(regions),
            vias=consolidated_vias,
            diagnostics=tuple(diagnostics),
            stats=NormalizationStats(
                input_region_count=len(board.copper_regions),
                output_region_count=len(regions),
                boolean_operations=boolean_operations,
                repaired_region_count=repaired,
                discarded_degenerate_count=discarded,
                duration_seconds=perf_counter() - started,
            ),
        )


class _ProvenanceIndex:
    """Answers "which imported regions contributed to this output polygon?"."""

    def __init__(self, members: Sequence[CopperRegion]) -> None:
        from shapely import STRtree

        self._members = members
        self._geometries = [_to_shapely(member.outline) for member in members]
        self._tree = STRtree(self._geometries)

    def contributors(self, part: ShapelyPolygon) -> tuple[CopperRegionId, ...]:
        candidates = self._tree.query(part, predicate="intersects")
        return tuple(self._members[int(index)].id for index in sorted(candidates))


def _union_group(members: Sequence[CopperRegion]) -> tuple[BaseGeometry, int]:
    """Union one `(net, layer)` group, repairing invalid rings first."""
    from shapely import make_valid, unary_union

    repaired = 0
    geometries: list[BaseGeometry] = []
    for member in members:
        geometry: BaseGeometry = _to_shapely(member.outline)
        if not geometry.is_valid:
            geometry = make_valid(geometry)
            repaired += 1
        geometries.append(geometry)
    return unary_union(geometries), repaired


def _areal_parts(geometry: BaseGeometry) -> list[ShapelyPolygon]:
    """Flatten a union result into its polygonal parts."""
    from shapely.geometry import MultiPolygon, Polygon

    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]
    if isinstance(geometry, MultiPolygon):
        return [part for part in geometry.geoms if not part.is_empty]
    parts: list[Polygon] = []
    for part in getattr(geometry, "geoms", ()):
        if isinstance(part, Polygon) and not part.is_empty:
            parts.append(part)
    return parts


def _to_shapely(polygon: Polygon2D) -> ShapelyPolygon:
    """Domain polygon to Shapely polygon."""
    from shapely.geometry import Polygon

    return Polygon(
        [(point.x_m, point.y_m) for point in polygon.exterior],
        [[(point.x_m, point.y_m) for point in hole] for hole in polygon.holes],
    )


def _to_domain_polygon(polygon: ShapelyPolygon) -> Polygon2D:
    """Shapely polygon to domain polygon, dropping the repeated closing vertex."""
    from openpdn.domain.geometry import Polygon2D as DomainPolygon

    exterior = [(x, y) for x, y in polygon.exterior.coords[:-1]]
    holes = [[(x, y) for x, y in ring.coords[:-1]] for ring in polygon.interiors]
    return DomainPolygon.from_coordinates(exterior, [h for h in holes if len(h) >= 3])


def _barrel_radius_m(via: Via) -> float | None:
    """Outer radius of the copper barrel, if known.

    Drill diameter bounds the barrel from the outside: plating deposits
    copper onto the drilled wall, narrowing the hole that remains
    (`finished_hole_diameter`) inward from it. The drilled diameter is
    therefore the better estimate of where the copper actually reaches;
    the finished diameter is used only when drill diameter is absent.
    """
    if via.drill_diameter is not None:
        return via.drill_diameter.require_unit(METRE) / 2.0
    if via.finished_hole_diameter is not None:
        return via.finished_hole_diameter.require_unit(METRE) / 2.0
    return None


def _consolidate_vias(board: Board) -> tuple[tuple[ConsolidatedVia, ...], list[Diagnostic]]:
    """Merge exactly-coincident vias and flag every other overlap.

    A generator can list the same physical via twice (e.g. once per padstack
    instance that landed on it); those merge silently, since it changes
    nothing electrically. Two vias at the same position that disagree on net
    or layer span are ambiguous, not a duplicate, and are flagged instead of
    guessed at. Two distinct barrels close enough to physically touch short
    two nets together and are flagged the same way.

    Layer span is compared by stackup index range, not endpoint identity, so
    an L1-L3 via and an L2-L4 via are correctly checked for overlap even
    though they name no layer in common.

    At `VIA_COINCIDENT_TOLERANCE_M` against real via pitch (measured >=405 um
    on a production board), a coincident cluster is always tight pairwise, so
    union-find's transitive merging cannot chain together vias that are not
    all mutually coincident.
    """
    diagnostics: list[Diagnostic] = []
    vias = list(board.vias)
    count = len(vias)
    layer_index = {layer.id: layer.index for layer in board.stackup.layers}
    parent = list(range(count))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_j] = root_i

    missing_radius = sum(1 for via in vias if _barrel_radius_m(via) is None)

    for i in range(count):
        for j in range(i + 1, count):
            via_a, via_b = vias[i], vias[j]
            distance_m = via_a.position.distance_to_m(via_b.position)

            if distance_m <= VIA_COINCIDENT_TOLERANCE_M:
                same_span = {via_a.from_layer_id, via_a.to_layer_id} == {
                    via_b.from_layer_id,
                    via_b.to_layer_id,
                }
                if via_a.net_id == via_b.net_id and same_span:
                    union(i, j)
                else:
                    diagnostics.append(
                        Diagnostic(
                            code="geometry.via_position_conflict",
                            severity=DiagnosticSeverity.ERROR,
                            message=(
                                "Two vias occupy the same position but disagree on net or "
                                "layer span; this is ambiguous and was not merged."
                            ),
                            context={
                                "via_a": str(via_a.id),
                                "via_b": str(via_b.id),
                                "x_mm": f"{via_a.position.x_m * 1000:.4f}",
                                "y_mm": f"{via_a.position.y_m * 1000:.4f}",
                            },
                        )
                    )
                continue

            span_a = sorted((layer_index[via_a.from_layer_id], layer_index[via_a.to_layer_id]))
            span_b = sorted((layer_index[via_b.from_layer_id], layer_index[via_b.to_layer_id]))
            if span_a[1] < span_b[0] or span_b[1] < span_a[0]:
                continue  # Barrels share no layer; they cannot physically touch.

            radius_a = _barrel_radius_m(via_a)
            radius_b = _barrel_radius_m(via_b)
            if radius_a is None or radius_b is None:
                continue  # Barrel size unknown; nothing to check.

            if distance_m < radius_a + radius_b:
                diagnostics.append(
                    Diagnostic(
                        code="geometry.via_overlap",
                        severity=DiagnosticSeverity.ERROR,
                        message=(
                            "Two via barrels physically overlap without being the same via; "
                            "this shorts them together and was not resolved automatically."
                        ),
                        context={
                            "via_a": str(via_a.id),
                            "via_b": str(via_b.id),
                            "x_mm": f"{via_a.position.x_m * 1000:.4f}",
                            "y_mm": f"{via_a.position.y_m * 1000:.4f}",
                            "separation_um": f"{distance_m * 1e6:.2f}",
                        },
                    )
                )

    clusters: dict[int, list[Via]] = {}
    for index, via in enumerate(vias):
        clusters.setdefault(find(index), []).append(via)

    merged_count = sum(len(members) - 1 for members in clusters.values() if len(members) > 1)
    consolidated: list[ConsolidatedVia] = []
    for members in clusters.values():
        members.sort(key=lambda via: str(via.id))
        first = members[0]
        if len(members) == 1:
            consolidated_id = str(first.id)
            position = first.position
        else:
            consolidated_id = "merged:" + "+".join(str(via.id) for via in members)
            position = Point2D(
                sum(via.position.x_m for via in members) / len(members),
                sum(via.position.y_m for via in members) / len(members),
            )
        drill_diameter = next(
            (via.drill_diameter for via in members if via.drill_diameter is not None), None
        )
        consolidated.append(
            ConsolidatedVia(
                id=consolidated_id,
                via_ids=tuple(via.id for via in members),
                net_id=first.net_id,
                from_layer_id=first.from_layer_id,
                to_layer_id=first.to_layer_id,
                position=position,
                drill_diameter=drill_diameter,
            )
        )
    consolidated.sort(key=lambda consolidated_via: consolidated_via.id)

    if merged_count:
        diagnostics.append(
            Diagnostic(
                code="geometry.merged_coincident_vias",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    "Vias at the exact same position, net and layer span were merged; the "
                    "source data listed the same physical via more than once."
                ),
                context={"count": str(merged_count)},
            )
        )
    if missing_radius:
        diagnostics.append(
            Diagnostic(
                code="geometry.via_overlap_check_incomplete",
                severity=DiagnosticSeverity.INFO,
                message=(
                    "Some vias have neither a drill nor a finished-hole diameter, so the "
                    "overlap check could not be applied to them."
                ),
                context={"count": str(missing_radius)},
            )
        )

    return tuple(consolidated), diagnostics
