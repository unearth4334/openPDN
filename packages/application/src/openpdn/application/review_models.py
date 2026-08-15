"""DTOs for board import review.

What a surface (HTTP API, CLI, UI) needs to answer: "did openPDN correctly
understand this PCB before I trust it as simulation input?" Plain frozen
dataclasses over domain types and contract types only -- the HTTP layer maps
them to Pydantic models, the CLI renders them as text.

Geometry payloads are separated from the summary on purpose: the summary is
small and requested often, the geometry is large and requested once per view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openpdn.domain.board import (
        ComponentId,
        CopperRegionId,
        LayerId,
        NetId,
        PadId,
        TerminalId,
        ViaId,
    )
    from openpdn.domain.provenance import Quantity
    from openpdn.domain.results import Diagnostic
    from openpdn.pcb_import.api import ImportCapabilityItem, SimulationReadiness


class ViaSpanKind(StrEnum):
    """How a via's span relates to the outer conductive layers."""

    THROUGH = "through"
    BLIND = "blind"
    BURIED = "buried"
    UNKNOWN = "unknown"


class GeometryView(StrEnum):
    """Which processing stage a geometry payload represents."""

    #: Solver-ready copper: unioned per (net, layer), disjoint, holes resolved.
    NORMALIZED = "normalized"
    #: Canonical interpretation of individual source features, pre-union.
    IMPORTED = "imported"


@dataclass(frozen=True, slots=True)
class BoundsSummary:
    """Axis-aligned board extent in metres."""

    min_x_m: float
    min_y_m: float
    max_x_m: float
    max_y_m: float


@dataclass(frozen=True, slots=True)
class LayerSummary:
    """One stackup layer as reviewed in the UI.

    `z_top`/`z_bottom` are derived from the stackup order and thicknesses
    (provenance `DERIVED`); they are `None` below the first layer of unknown
    thickness rather than silently accumulating a guess.
    """

    id: LayerId
    name: str
    function: str
    index: int
    is_conductive: bool
    thickness: Quantity | None
    z_top: Quantity | None
    z_bottom: Quantity | None
    material_name: str | None


@dataclass(frozen=True, slots=True)
class NetSummary:
    """One net's footprint across the board."""

    id: NetId
    name: str
    layer_ids: tuple[LayerId, ...]
    region_count: int
    via_count: int
    copper_area_m2: float
    terminal_count: int


@dataclass(frozen=True, slots=True)
class ViaSummary:
    """One via, fully described for inspection."""

    id: ViaId
    net_id: NetId | None
    x_m: float
    y_m: float
    from_layer_id: LayerId
    to_layer_id: LayerId
    span_kind: ViaSpanKind
    drill_diameter: Quantity | None
    finished_hole_diameter: Quantity | None
    plating_thickness: Quantity | None
    padstack_name: str | None


@dataclass(frozen=True, slots=True)
class ViaGroupSummary:
    """Vias grouped by span, drill and padstack -- the review table rows."""

    from_layer_id: LayerId
    to_layer_id: LayerId
    span_kind: ViaSpanKind
    drill_diameter_m: float | None
    padstack_name: str | None
    count: int
    via_ids: tuple[ViaId, ...]


@dataclass(frozen=True, slots=True)
class ComponentSummary:
    """One placed component, for the review tables."""

    id: ComponentId
    reference_designator: str
    part_number: str | None
    terminal_count: int


@dataclass(frozen=True, slots=True)
class TerminalSummary:
    """One terminal -- where a future study will attach sources and loads."""

    id: TerminalId
    name: str
    net_id: NetId
    component_id: ComponentId | None
    pad_ids: tuple[PadId, ...]


@dataclass(frozen=True, slots=True)
class LayerGeometryStats:
    """Per-layer geometry diagnostics that make parser regressions visible."""

    layer_id: LayerId
    source_feature_count: int
    normalized_region_count: int
    copper_area_m2: float
    net_count: int
    via_count: int


@dataclass(frozen=True, slots=True)
class ImportTimings:
    """Stage durations for one import, in seconds."""

    parse_seconds: float | None
    extract_seconds: float | None
    normalize_seconds: float | None
    source_bytes: int | None
    element_count: int | None
    feature_counts: dict[str, int] = field(default_factory=dict)
    boolean_operations: int | None = None
    repaired_region_count: int | None = None
    discarded_degenerate_count: int | None = None


@dataclass(frozen=True)
class BoardReview:
    """Everything the review UI shows about one imported board."""

    board_id: str
    name: str
    source_name: str
    source_format: str
    format_revision: str | None
    source_digest: str | None
    stored_at_epoch_s: float
    readiness: SimulationReadiness
    capability_items: tuple[ImportCapabilityItem, ...]
    diagnostics: tuple[Diagnostic, ...]
    bounds: BoundsSummary | None
    total_thickness: Quantity | None
    layers: tuple[LayerSummary, ...]
    nets: tuple[NetSummary, ...]
    vias: tuple[ViaSummary, ...]
    via_groups: tuple[ViaGroupSummary, ...]
    components: tuple[ComponentSummary, ...]
    terminals: tuple[TerminalSummary, ...]
    layer_stats: tuple[LayerGeometryStats, ...]
    timings: ImportTimings


@dataclass(frozen=True, slots=True)
class BoardListEntry:
    """One stored board, as listed by the workspace."""

    board_id: str
    name: str
    source_name: str
    readiness: SimulationReadiness
    stored_at_epoch_s: float


@dataclass(frozen=True, slots=True)
class RegionGeometry:
    """One renderable copper polygon."""

    id: str
    net_id: NetId | None
    exterior: tuple[tuple[float, float], ...]
    holes: tuple[tuple[tuple[float, float], ...], ...]
    source_refs: tuple[str, ...]
    source_region_ids: tuple[CopperRegionId, ...]


@dataclass(frozen=True, slots=True)
class LayerGeometry:
    """All renderable copper of one conductive layer."""

    layer_id: LayerId
    regions: tuple[RegionGeometry, ...]


@dataclass(frozen=True, slots=True)
class PolygonGeometry:
    """A plain polygon for non-copper outlines (the board profile)."""

    exterior: tuple[tuple[float, float], ...]
    holes: tuple[tuple[tuple[float, float], ...], ...]


@dataclass(frozen=True)
class BoardGeometry:
    """One geometry view of one board, fetched once and cached by the client."""

    board_id: str
    view: GeometryView
    bounds: BoundsSummary | None
    profile: tuple[PolygonGeometry, ...]
    layers: tuple[LayerGeometry, ...]
