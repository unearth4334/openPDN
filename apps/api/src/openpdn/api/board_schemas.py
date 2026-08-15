"""HTTP models for board import review and geometry.

The wire twin of `openpdn.application.review_models`. Mapping is explicit --
a rename in the DTOs must not silently change the public API. All lengths are
SI metres on the wire; engineering-unit formatting is the client's job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from openpdn.application.review_models import (
        BoardGeometry,
        BoardListEntry,
        BoardReview,
        BoundsSummary,
    )
    from openpdn.domain.provenance import Quantity
    from openpdn.domain.results import Diagnostic


class QuantityResponse(BaseModel):
    """A physical value with its unit and provenance."""

    value: float
    unit: str
    provenance: str = Field(description="imported | configured | assumed | derived")
    note: str | None = None


class BoundsResponse(BaseModel):
    """Axis-aligned extent in metres."""

    min_x_m: float
    min_y_m: float
    max_x_m: float
    max_y_m: float


class DiagnosticResponse(BaseModel):
    """One import or normalisation diagnostic."""

    code: str
    severity: str = Field(description="info | warning | error")
    message: str
    context: dict[str, str]


class CapabilityItemResponse(BaseModel):
    """One line of the import capability report."""

    name: str
    status: str = Field(description="present | partial | absent | unknown")
    note: str | None = None


class LayerResponse(BaseModel):
    """One stackup layer."""

    id: str
    name: str
    function: str
    index: int
    is_conductive: bool
    thickness: QuantityResponse | None
    z_top: QuantityResponse | None
    z_bottom: QuantityResponse | None
    material_name: str | None


class NetResponse(BaseModel):
    """One net's board footprint."""

    id: str
    name: str
    layer_ids: list[str]
    region_count: int
    via_count: int
    copper_area_m2: float
    terminal_count: int


class ViaResponse(BaseModel):
    """One via."""

    id: str
    net_id: str | None
    x_m: float
    y_m: float
    from_layer_id: str
    to_layer_id: str
    span_kind: str = Field(description="through | blind | buried | unknown")
    drill_diameter: QuantityResponse | None
    finished_hole_diameter: QuantityResponse | None
    plating_thickness: QuantityResponse | None
    padstack_name: str | None


class ViaGroupResponse(BaseModel):
    """Vias grouped by span, drill and padstack."""

    from_layer_id: str
    to_layer_id: str
    span_kind: str
    drill_diameter_m: float | None
    padstack_name: str | None
    count: int
    via_ids: list[str]


class ComponentResponse(BaseModel):
    """One placed component."""

    id: str
    reference_designator: str
    part_number: str | None
    terminal_count: int


class TerminalResponse(BaseModel):
    """One terminal (future source/load attachment point)."""

    id: str
    name: str
    net_id: str
    component_id: str | None
    pad_ids: list[str]


class LayerStatsResponse(BaseModel):
    """Per-layer geometry statistics."""

    layer_id: str
    source_feature_count: int
    normalized_region_count: int
    copper_area_m2: float
    net_count: int
    via_count: int


class TimingsResponse(BaseModel):
    """Import pipeline instrumentation."""

    parse_seconds: float | None
    extract_seconds: float | None
    normalize_seconds: float | None
    source_bytes: int | None
    element_count: int | None
    feature_counts: dict[str, int]
    boolean_operations: int | None
    repaired_region_count: int | None
    discarded_degenerate_count: int | None


class BoardReviewResponse(BaseModel):
    """Everything the review UI shows about one imported board."""

    board_id: str
    name: str
    source_name: str
    source_format: str
    format_revision: str | None
    source_digest: str | None
    stored_at_epoch_s: float
    readiness: str = Field(description="ready | ready_with_assumptions | not_ready")
    capability_items: list[CapabilityItemResponse]
    diagnostics: list[DiagnosticResponse]
    bounds: BoundsResponse | None
    total_thickness: QuantityResponse | None
    layers: list[LayerResponse]
    nets: list[NetResponse]
    vias: list[ViaResponse]
    via_groups: list[ViaGroupResponse]
    components: list[ComponentResponse]
    terminals: list[TerminalResponse]
    layer_stats: list[LayerStatsResponse]
    timings: TimingsResponse

    @classmethod
    def from_dto(cls, review: BoardReview) -> BoardReviewResponse:
        """Map the application DTO onto the wire model."""
        return cls(
            board_id=review.board_id,
            name=review.name,
            source_name=review.source_name,
            source_format=review.source_format,
            format_revision=review.format_revision,
            source_digest=review.source_digest,
            stored_at_epoch_s=review.stored_at_epoch_s,
            readiness=review.readiness.value,
            capability_items=[
                CapabilityItemResponse(name=item.name, status=item.status.value, note=item.note)
                for item in review.capability_items
            ],
            diagnostics=[_diagnostic(diagnostic) for diagnostic in review.diagnostics],
            bounds=_bounds(review.bounds),
            total_thickness=_quantity(review.total_thickness),
            layers=[
                LayerResponse(
                    id=str(layer.id),
                    name=layer.name,
                    function=layer.function,
                    index=layer.index,
                    is_conductive=layer.is_conductive,
                    thickness=_quantity(layer.thickness),
                    z_top=_quantity(layer.z_top),
                    z_bottom=_quantity(layer.z_bottom),
                    material_name=layer.material_name,
                )
                for layer in review.layers
            ],
            nets=[
                NetResponse(
                    id=str(net.id),
                    name=net.name,
                    layer_ids=[str(layer_id) for layer_id in net.layer_ids],
                    region_count=net.region_count,
                    via_count=net.via_count,
                    copper_area_m2=net.copper_area_m2,
                    terminal_count=net.terminal_count,
                )
                for net in review.nets
            ],
            vias=[
                ViaResponse(
                    id=str(via.id),
                    net_id=None if via.net_id is None else str(via.net_id),
                    x_m=via.x_m,
                    y_m=via.y_m,
                    from_layer_id=str(via.from_layer_id),
                    to_layer_id=str(via.to_layer_id),
                    span_kind=via.span_kind.value,
                    drill_diameter=_quantity(via.drill_diameter),
                    finished_hole_diameter=_quantity(via.finished_hole_diameter),
                    plating_thickness=_quantity(via.plating_thickness),
                    padstack_name=via.padstack_name,
                )
                for via in review.vias
            ],
            via_groups=[
                ViaGroupResponse(
                    from_layer_id=str(group.from_layer_id),
                    to_layer_id=str(group.to_layer_id),
                    span_kind=group.span_kind.value,
                    drill_diameter_m=group.drill_diameter_m,
                    padstack_name=group.padstack_name,
                    count=group.count,
                    via_ids=[str(via_id) for via_id in group.via_ids],
                )
                for group in review.via_groups
            ],
            components=[
                ComponentResponse(
                    id=str(component.id),
                    reference_designator=component.reference_designator,
                    part_number=component.part_number,
                    terminal_count=component.terminal_count,
                )
                for component in review.components
            ],
            terminals=[
                TerminalResponse(
                    id=str(terminal.id),
                    name=terminal.name,
                    net_id=str(terminal.net_id),
                    component_id=(
                        None if terminal.component_id is None else str(terminal.component_id)
                    ),
                    pad_ids=[str(pad_id) for pad_id in terminal.pad_ids],
                )
                for terminal in review.terminals
            ],
            layer_stats=[
                LayerStatsResponse(
                    layer_id=str(stats.layer_id),
                    source_feature_count=stats.source_feature_count,
                    normalized_region_count=stats.normalized_region_count,
                    copper_area_m2=stats.copper_area_m2,
                    net_count=stats.net_count,
                    via_count=stats.via_count,
                )
                for stats in review.layer_stats
            ],
            timings=TimingsResponse(
                parse_seconds=review.timings.parse_seconds,
                extract_seconds=review.timings.extract_seconds,
                normalize_seconds=review.timings.normalize_seconds,
                source_bytes=review.timings.source_bytes,
                element_count=review.timings.element_count,
                feature_counts=dict(review.timings.feature_counts),
                boolean_operations=review.timings.boolean_operations,
                repaired_region_count=review.timings.repaired_region_count,
                discarded_degenerate_count=review.timings.discarded_degenerate_count,
            ),
        )


class BoardListItemResponse(BaseModel):
    """One stored board."""

    board_id: str
    name: str
    source_name: str
    readiness: str
    stored_at_epoch_s: float


class BoardListResponse(BaseModel):
    """Every board currently stored."""

    boards: list[BoardListItemResponse]

    @classmethod
    def from_dto(cls, entries: tuple[BoardListEntry, ...]) -> BoardListResponse:
        """Map the application DTOs onto the wire model."""
        return cls(
            boards=[
                BoardListItemResponse(
                    board_id=entry.board_id,
                    name=entry.name,
                    source_name=entry.source_name,
                    readiness=entry.readiness.value,
                    stored_at_epoch_s=entry.stored_at_epoch_s,
                )
                for entry in entries
            ]
        )


class RegionResponse(BaseModel):
    """One renderable copper polygon.

    Rings are `[x_m, y_m]` pairs without a repeated closing vertex.
    """

    id: str
    net_id: str | None
    exterior: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]]
    source_refs: list[str]
    source_region_ids: list[str]


class LayerGeometryResponse(BaseModel):
    """All renderable copper of one conductive layer."""

    layer_id: str
    regions: list[RegionResponse]


class ProfilePolygonResponse(BaseModel):
    """One board-outline polygon with cutout holes."""

    exterior: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]]


class GeometryResponse(BaseModel):
    """One geometry view of one board. Large; fetch once per view and cache."""

    board_id: str
    view: str = Field(description="normalized | imported")
    bounds: BoundsResponse | None
    profile: list[ProfilePolygonResponse]
    layers: list[LayerGeometryResponse]

    @classmethod
    def from_dto(cls, geometry: BoardGeometry) -> GeometryResponse:
        """Map the application DTO onto the wire model."""
        return cls(
            board_id=geometry.board_id,
            view=geometry.view.value,
            bounds=_bounds(geometry.bounds),
            profile=[
                ProfilePolygonResponse(
                    exterior=list(polygon.exterior),
                    holes=[list(hole) for hole in polygon.holes],
                )
                for polygon in geometry.profile
            ],
            layers=[
                LayerGeometryResponse(
                    layer_id=str(layer.layer_id),
                    regions=[
                        RegionResponse(
                            id=region.id,
                            net_id=None if region.net_id is None else str(region.net_id),
                            exterior=list(region.exterior),
                            holes=[list(hole) for hole in region.holes],
                            source_refs=list(region.source_refs),
                            source_region_ids=[str(rid) for rid in region.source_region_ids],
                        )
                        for region in layer.regions
                    ],
                )
                for layer in geometry.layers
            ],
        )


def _quantity(quantity: Quantity | None) -> QuantityResponse | None:
    """Map an optional domain quantity."""
    if quantity is None:
        return None
    return QuantityResponse(
        value=quantity.value,
        unit=quantity.unit,
        provenance=quantity.provenance.value,
        note=quantity.note,
    )


def _bounds(bounds: BoundsSummary | None) -> BoundsResponse | None:
    """Map an optional bounds DTO."""
    if bounds is None:
        return None
    return BoundsResponse(
        min_x_m=bounds.min_x_m,
        min_y_m=bounds.min_y_m,
        max_x_m=bounds.max_x_m,
        max_y_m=bounds.max_y_m,
    )


def _diagnostic(diagnostic: Diagnostic) -> DiagnosticResponse:
    """Map a domain diagnostic."""
    return DiagnosticResponse(
        code=diagnostic.code,
        severity=diagnostic.severity.value,
        message=diagnostic.message,
        context=dict(diagnostic.context),
    )
