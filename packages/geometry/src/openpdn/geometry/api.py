"""The geometry normalisation contract.

Normalised conductive geometry is what a 2.5-D sheet solver meshes over: the
copper of one net on one physical layer, unioned into disjoint polygons with
holes, in board coordinates and metres. It is *derived* data belonging to the
solver pipeline -- never written back onto the `Board` -- and it is cacheable,
keyed by board content and normaliser version (see the `pcb-domain-model`
skill and ADR-0007).

This module is the contract: no third-party imports, no Shapely types. The
concrete engine lives beside it and is named only by the composition root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openpdn.domain.board import Board, CopperRegionId, LayerId, NetId, ViaId
    from openpdn.domain.geometry import Point2D, Polygon2D
    from openpdn.domain.provenance import Quantity
    from openpdn.domain.results import Diagnostic


class GeometryNormalizationError(Exception):
    """Raised when a board's copper cannot be normalised.

    Engines translate their computational-geometry library's exceptions into
    this, so callers never catch a third-party type.
    """


@dataclass(frozen=True, slots=True)
class NormalizedRegion:
    """One disjoint piece of solver-ready copper.

    Attributes:
        id: Stable identifier, deterministic for identical input.
        layer_id: Physical layer the copper sits on.
        net_id: Net the copper belongs to; `None` for unassigned copper.
        polygon: Absolute board-coordinate outline with holes, in metres.
        source_region_ids: The imported `CopperRegion`s that contributed
            copper to this polygon. Many-to-many by nature -- a union merges
            features -- and kept so a reviewer can trace normalised copper
            back to source artwork.
    """

    id: str
    layer_id: LayerId
    net_id: NetId | None
    polygon: Polygon2D
    source_region_ids: tuple[CopperRegionId, ...] = ()


@dataclass(frozen=True, slots=True)
class ConsolidatedVia:
    """One solver-ready via barrel, after coincident duplicates are merged.

    Fabrication data can list the same physical via twice (e.g. once per
    padstack instance that happened to land on it), or a design can carry
    barrels that overlap without being the same via -- both are geometry
    problems a solver cannot resolve on its own, so they are resolved (or
    flagged) here rather than downstream.

    Attributes:
        id: Stable identifier, deterministic for identical input.
        via_ids: The imported `Via`s this barrel represents. More than one
            only when exactly-coincident duplicates were merged.
        net_id: Net the via belongs to; `None` for an unassigned via.
        from_layer_id: Upper connected conductive layer.
        to_layer_id: Lower connected conductive layer.
        position: Centre of the barrel in board coordinates.
        drill_diameter: Tool diameter, if known; the outer bound of the
            copper barrel, since plating narrows the hole inward from it.
    """

    id: str
    via_ids: tuple[ViaId, ...]
    net_id: NetId | None
    from_layer_id: LayerId
    to_layer_id: LayerId
    position: Point2D
    drill_diameter: Quantity | None = None


@dataclass(frozen=True, slots=True)
class NormalizationStats:
    """Volume and performance instrumentation for one normalisation run."""

    input_region_count: int
    output_region_count: int
    boolean_operations: int
    repaired_region_count: int
    discarded_degenerate_count: int
    duration_seconds: float


@dataclass(frozen=True)
class NormalizedGeometry:
    """Solver-ready conductive geometry for one board."""

    normalizer_version: str
    regions: tuple[NormalizedRegion, ...]
    vias: tuple[ConsolidatedVia, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)
    stats: NormalizationStats | None = None

    def regions_on(self, layer_id: LayerId) -> tuple[NormalizedRegion, ...]:
        """Return the normalised copper of one layer."""
        return tuple(region for region in self.regions if region.layer_id == layer_id)


@runtime_checkable
class GeometryNormalizer(Protocol):
    """Turns a board's raw copper regions into solver-ready geometry."""

    @property
    def version(self) -> str:
        """Engine version, part of every cache key derived from its output."""
        ...

    def normalize(self, board: Board) -> NormalizedGeometry:
        """Union the board's copper per `(net, physical layer)`.

        Also consolidates the board's vias: exactly-coincident duplicates are
        merged, and physically overlapping-but-distinct barrels are flagged
        as diagnostics rather than merged, since merging them would be a
        guess about which one is real.

        Raises:
            GeometryNormalizationError: If the copper cannot be normalised.
        """
        ...
