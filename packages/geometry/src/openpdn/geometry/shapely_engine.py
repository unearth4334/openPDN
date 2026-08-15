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

from openpdn.domain.results import Diagnostic, DiagnosticSeverity
from openpdn.geometry.api import (
    GeometryNormalizationError,
    NormalizationStats,
    NormalizedGeometry,
    NormalizedRegion,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.geometry.base import BaseGeometry

    from openpdn.domain.board import Board, CopperRegion, CopperRegionId
    from openpdn.domain.geometry import Polygon2D

#: Bump when normalisation semantics change; invalidates every derived cache.
NORMALIZER_VERSION: Final = "1"

#: Output polygons smaller than this are numerical slivers, not copper.
#: 1e-14 m^2 is a 10 nm x 1 nm rectangle -- far below fabrication resolution,
#: far above double-precision noise for board-scale coordinates.
MIN_REGION_AREA_M2: Final = 1e-14


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

        return NormalizedGeometry(
            normalizer_version=NORMALIZER_VERSION,
            regions=tuple(regions),
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
