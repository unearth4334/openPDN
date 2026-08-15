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
    from openpdn.domain.board import Board, CopperRegionId, LayerId, NetId
    from openpdn.domain.geometry import Polygon2D
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

        Raises:
            GeometryNormalizationError: If the copper cannot be normalised.
        """
        ...
