"""Use case: import a PCB source, normalise its copper, and review the result.

Orchestrates the import service, the geometry-normaliser port and the board
store. Owns the mapping from domain objects to the review DTOs, including the
derived values the review UI shows (layer Z positions, via span kinds, per-net
and per-layer statistics). Knows nothing about IPC-2581, Shapely, HTTP or
rendering.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING

from openpdn.application import events
from openpdn.application.board_store import StoredBoard
from openpdn.application.errors import BoardNotFoundError
from openpdn.application.review_models import (
    BoardGeometry,
    BoardListEntry,
    BoardReview,
    BoundsSummary,
    ComponentSummary,
    GeometryView,
    ImportTimings,
    LayerGeometry,
    LayerGeometryStats,
    LayerSummary,
    NetSummary,
    PolygonGeometry,
    RegionGeometry,
    TerminalSummary,
    ViaGroupSummary,
    ViaSpanKind,
    ViaSummary,
)
from openpdn.domain.provenance import Quantity
from openpdn.domain.units import METRE
from openpdn.pcb_import.api import (
    ImportCapabilityReport,
    SimulationReadiness,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openpdn.application.board_store import BoardStore
    from openpdn.application.import_service import BoardImportService
    from openpdn.domain.board import Board, LayerId, NetId, Via, ViaId
    from openpdn.domain.geometry import BoundingBox2D, Point2D, Polygon2D
    from openpdn.geometry.api import GeometryNormalizer, NormalizedGeometry
    from openpdn.pcb_import.api import ImporterRegistry

_logger = logging.getLogger(__name__)


class BoardReviewService:
    """Imports, normalises, stores and summarises boards for review."""

    def __init__(
        self,
        import_service: BoardImportService,
        importers: ImporterRegistry,
        normalizer: GeometryNormalizer,
        store: BoardStore,
    ) -> None:
        """Store collaborators; all of them are ports or application services."""
        self._import_service = import_service
        self._importers = importers
        self._normalizer = normalizer
        self._store = store

    # -- use cases -----------------------------------------------------------------
    def import_and_review(self, source: Path, importer_name: str | None = None) -> BoardReview:
        """Import `source`, normalise its copper, store everything, and review it.

        Re-importing identical content through the same pipeline versions is a
        cache hit: the stored board is returned without re-parsing.
        """
        result = self._import_service.import_board(source, importer_name)
        board = result.board
        board_id = self._board_key(board)

        existing = self._store.get(board_id)
        if existing is not None:
            _logger.info(
                events.CACHE_HIT,
                extra={"event": events.CACHE_HIT, "board_id": board_id},
            )
            return self._review_of(existing)

        _logger.info(
            events.GEOMETRY_NORMALIZATION_STARTED,
            extra={"event": events.GEOMETRY_NORMALIZATION_STARTED, "board_id": board_id},
        )
        normalize_started = time.perf_counter()
        normalized = self._normalizer.normalize(board)
        normalize_seconds = time.perf_counter() - normalize_started
        _logger.info(
            events.GEOMETRY_NORMALIZATION_FINISHED,
            extra={
                "event": events.GEOMETRY_NORMALIZATION_FINISHED,
                "board_id": board_id,
                "input_regions": len(board.copper_regions),
                "output_regions": len(normalized.regions),
                "duration_seconds": round(normalize_seconds, 6),
            },
        )

        record = StoredBoard(
            board_id=board_id,
            source_name=source.name,
            stored_at_epoch_s=time.time(),
            import_result=result,
            normalized=normalized,
            normalize_seconds=normalize_seconds,
        )
        self._store.put(record)
        return self._review_of(record)

    def review(self, board_id: str) -> BoardReview:
        """Return the review of a stored board.

        Raises:
            BoardNotFoundError: If no board with `board_id` is stored.
        """
        return self._review_of(self._required(board_id))

    def geometry(self, board_id: str, view: GeometryView) -> BoardGeometry:
        """Return one geometry view of a stored board.

        Raises:
            BoardNotFoundError: If no board with `board_id` is stored.
        """
        record = self._required(board_id)
        board = record.import_result.board
        if view is GeometryView.NORMALIZED:
            layers = self._normalized_layers(board, record.normalized)
        else:
            layers = self._imported_layers(board)
        return BoardGeometry(
            board_id=board_id,
            view=view,
            bounds=_bounds_of(board.bounding_box),
            profile=tuple(
                _polygon_geometry(outline)
                for outline in (board.profile.outlines if board.profile else ())
            ),
            layers=layers,
        )

    def list_boards(self) -> tuple[BoardListEntry, ...]:
        """List every stored board, most recent first."""
        entries = []
        for record in self._store.list_all():
            report = record.import_result.capability_report
            entries.append(
                BoardListEntry(
                    board_id=record.board_id,
                    name=record.import_result.board.name,
                    source_name=record.source_name,
                    readiness=report.readiness if report else SimulationReadiness.NOT_READY,
                    stored_at_epoch_s=record.stored_at_epoch_s,
                )
            )
        return tuple(entries)

    # -- internals -------------------------------------------------------------------
    def _required(self, board_id: str) -> StoredBoard:
        record = self._store.get(board_id)
        if record is None:
            raise BoardNotFoundError(f"No imported board with id {board_id!r}")
        return record

    def _board_key(self, board: Board) -> str:
        """Cache identity: source digest + importer version + normaliser version."""
        provenance = board.provenance
        digest = provenance.source_digest if provenance else None
        importer_version = "unknown"
        if provenance is not None:
            try:
                importer_version = self._importers.get(provenance.importer).describe().version
            except Exception:
                importer_version = "unknown"
        material = f"{digest or board.id}:{importer_version}:{self._normalizer.version}"
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    def _review_of(self, record: StoredBoard) -> BoardReview:
        board = record.import_result.board
        normalized = record.normalized
        report = record.import_result.capability_report or ImportCapabilityReport(
            source_format=board.provenance.source_format if board.provenance else "unknown"
        )
        stats = record.import_result.stats

        layers, total_thickness = _layer_summaries(board)
        conductive_ids = [layer.id for layer in board.stackup.conductive_layers]
        span_kinds = {via.id: _span_kind(board, via) for via in board.vias}

        return BoardReview(
            board_id=record.board_id,
            name=board.name,
            source_name=record.source_name,
            source_format=report.source_format,
            format_revision=report.format_revision,
            source_digest=board.provenance.source_digest if board.provenance else None,
            stored_at_epoch_s=record.stored_at_epoch_s,
            readiness=report.readiness,
            capability_items=report.items,
            diagnostics=(*record.import_result.diagnostics, *normalized.diagnostics),
            bounds=_bounds_of(board.bounding_box),
            total_thickness=total_thickness,
            layers=layers,
            nets=_net_summaries(board, normalized),
            vias=tuple(
                ViaSummary(
                    id=via.id,
                    net_id=via.net_id,
                    x_m=via.position.x_m,
                    y_m=via.position.y_m,
                    from_layer_id=via.from_layer_id,
                    to_layer_id=via.to_layer_id,
                    span_kind=span_kinds[via.id],
                    drill_diameter=via.drill_diameter,
                    finished_hole_diameter=via.finished_hole_diameter,
                    plating_thickness=via.plating_thickness,
                    padstack_name=via.padstack_name,
                )
                for via in board.vias
            ),
            via_groups=_via_groups(board, span_kinds),
            components=tuple(
                ComponentSummary(
                    id=component.id,
                    reference_designator=component.reference_designator,
                    part_number=component.part_number,
                    terminal_count=len(component.terminal_ids),
                )
                for component in board.components
            ),
            terminals=tuple(
                TerminalSummary(
                    id=terminal.id,
                    name=terminal.name,
                    net_id=terminal.net_id,
                    component_id=terminal.component_id,
                    pad_ids=terminal.pad_ids,
                )
                for terminal in board.terminals
            ),
            layer_stats=_layer_stats(board, normalized, conductive_ids),
            timings=ImportTimings(
                parse_seconds=stats.parse_seconds if stats else None,
                extract_seconds=stats.extract_seconds if stats else None,
                normalize_seconds=record.normalize_seconds,
                source_bytes=stats.source_bytes if stats else None,
                element_count=stats.element_count if stats else None,
                feature_counts=dict(stats.feature_counts) if stats else {},
                boolean_operations=(
                    normalized.stats.boolean_operations if normalized.stats else None
                ),
                repaired_region_count=(
                    normalized.stats.repaired_region_count if normalized.stats else None
                ),
                discarded_degenerate_count=(
                    normalized.stats.discarded_degenerate_count if normalized.stats else None
                ),
            ),
        )

    def _normalized_layers(
        self, board: Board, normalized: NormalizedGeometry
    ) -> tuple[LayerGeometry, ...]:
        source_ref_by_id = {
            region.id: region.source_ref
            for region in board.copper_regions
            if region.source_ref is not None
        }
        layers = []
        for layer in board.stackup.conductive_layers:
            regions = tuple(
                RegionGeometry(
                    id=region.id,
                    net_id=region.net_id,
                    exterior=_ring(region.polygon.exterior),
                    holes=tuple(_ring(hole) for hole in region.polygon.holes),
                    source_refs=tuple(
                        sorted(
                            {
                                source_ref_by_id[source_id]
                                for source_id in region.source_region_ids
                                if source_id in source_ref_by_id
                            }
                        )
                    ),
                    source_region_ids=region.source_region_ids,
                )
                for region in normalized.regions_on(layer.id)
            )
            layers.append(LayerGeometry(layer_id=layer.id, regions=regions))
        return tuple(layers)

    def _imported_layers(self, board: Board) -> tuple[LayerGeometry, ...]:
        layers = []
        for layer in board.stackup.conductive_layers:
            regions = tuple(
                RegionGeometry(
                    id=str(region.id),
                    net_id=region.net_id,
                    exterior=_ring(region.outline.exterior),
                    holes=tuple(_ring(hole) for hole in region.outline.holes),
                    source_refs=(region.source_ref,) if region.source_ref else (),
                    source_region_ids=(region.id,),
                )
                for region in board.copper_regions
                if region.layer_id == layer.id
            )
            layers.append(LayerGeometry(layer_id=layer.id, regions=regions))
        return tuple(layers)


# --- pure derivations -----------------------------------------------------------
def _layer_summaries(board: Board) -> tuple[tuple[LayerSummary, ...], Quantity | None]:
    """Summarise stackup layers with derived Z positions and total thickness."""
    summaries: list[LayerSummary] = []
    z_known = True
    z_m = 0.0
    for layer in board.stackup.layers:
        thickness_m = layer.thickness.require_unit(METRE) if layer.thickness is not None else None
        z_top = Quantity.derived(z_m, METRE) if z_known else None
        if thickness_m is None:
            z_known = False
        z_bottom = (
            Quantity.derived(z_m + thickness_m, METRE)
            if z_known and thickness_m is not None
            else None
        )
        if z_known and thickness_m is not None:
            z_m += thickness_m
        summaries.append(
            LayerSummary(
                id=layer.id,
                name=layer.name,
                function=layer.function.value,
                index=layer.index,
                is_conductive=layer.function.is_conductive,
                thickness=layer.thickness,
                z_top=z_top,
                z_bottom=z_bottom,
                material_name=layer.material.name if layer.material else None,
            )
        )
    total = Quantity.derived(z_m, METRE) if z_known else None
    return tuple(summaries), total


def _net_summaries(board: Board, normalized: NormalizedGeometry) -> tuple[NetSummary, ...]:
    """Per-net footprint derived from the normalised copper."""
    layers_by_net: dict[NetId, set[LayerId]] = {}
    area_by_net: dict[NetId, float] = {}
    regions_by_net: dict[NetId, int] = {}
    for region in normalized.regions:
        if region.net_id is None:
            continue
        layers_by_net.setdefault(region.net_id, set()).add(region.layer_id)
        area_by_net[region.net_id] = area_by_net.get(region.net_id, 0.0) + region.polygon.area_m2
        regions_by_net[region.net_id] = regions_by_net.get(region.net_id, 0) + 1

    layer_order = {layer.id: layer.index for layer in board.stackup.layers}
    terminal_counts: dict[NetId, int] = {}
    for terminal in board.terminals:
        terminal_counts[terminal.net_id] = terminal_counts.get(terminal.net_id, 0) + 1
    via_counts: dict[NetId, int] = {}
    for via in board.vias:
        if via.net_id is not None:
            via_counts[via.net_id] = via_counts.get(via.net_id, 0) + 1

    return tuple(
        NetSummary(
            id=net.id,
            name=net.name,
            layer_ids=tuple(
                sorted(layers_by_net.get(net.id, ()), key=lambda lid: layer_order.get(lid, 0))
            ),
            region_count=regions_by_net.get(net.id, 0),
            via_count=via_counts.get(net.id, 0),
            copper_area_m2=area_by_net.get(net.id, 0.0),
            terminal_count=terminal_counts.get(net.id, 0),
        )
        for net in sorted(board.nets, key=lambda net: net.name.lower())
    )


def _span_kind(board: Board, via: Via) -> ViaSpanKind:
    """Classify a via span against the outer conductive layers."""
    conductive = board.stackup.conductive_layers
    if not conductive:
        return ViaSpanKind.UNKNOWN
    outer = {conductive[0].id, conductive[-1].id}
    ends = {via.from_layer_id, via.to_layer_id}
    touching_outer = len(ends & outer)
    if touching_outer == 2:
        return ViaSpanKind.THROUGH
    if touching_outer == 1:
        return ViaSpanKind.BLIND
    return ViaSpanKind.BURIED


def _via_groups(board: Board, span_kinds: dict[ViaId, ViaSpanKind]) -> tuple[ViaGroupSummary, ...]:
    """Group vias by (span, drill) for the review table.

    The padstack name is *not* part of the key: some generators write a unique
    instance name per via, which would explode the table into one row per via.
    A group shows the name only when every member agrees on it.
    """
    groups: dict[tuple[LayerId, LayerId, float | None], list[Via]] = {}
    for via in board.vias:
        drill_m = via.drill_diameter.require_unit(METRE) if via.drill_diameter is not None else None
        groups.setdefault((via.from_layer_id, via.to_layer_id, drill_m), []).append(via)
    summaries = []
    for (_from_id, _to_id, drill_m), vias in sorted(groups.items(), key=lambda item: -len(item[1])):
        names = {via.padstack_name for via in vias}
        only_name = names.pop() if len(names) == 1 else None
        summaries.append(
            ViaGroupSummary(
                from_layer_id=vias[0].from_layer_id,
                to_layer_id=vias[0].to_layer_id,
                span_kind=span_kinds[vias[0].id],
                drill_diameter_m=drill_m,
                padstack_name=only_name,
                count=len(vias),
                via_ids=tuple(via.id for via in vias),
            )
        )
    return tuple(summaries)


def _layer_stats(
    board: Board, normalized: NormalizedGeometry, conductive_ids: list[LayerId]
) -> tuple[LayerGeometryStats, ...]:
    """Per-conductive-layer counts that make importer regressions visible."""
    layer_index = {layer.id: layer.index for layer in board.stackup.layers}
    stats = []
    for layer_id in conductive_ids:
        regions = normalized.regions_on(layer_id)
        nets = {region.net_id for region in regions if region.net_id is not None}
        index = layer_index[layer_id]
        via_count = sum(
            1
            for via in board.vias
            if layer_index[via.from_layer_id] <= index <= layer_index[via.to_layer_id]
        )
        stats.append(
            LayerGeometryStats(
                layer_id=layer_id,
                source_feature_count=sum(
                    1 for region in board.copper_regions if region.layer_id == layer_id
                ),
                normalized_region_count=len(regions),
                copper_area_m2=sum(region.polygon.area_m2 for region in regions),
                net_count=len(nets),
                via_count=via_count,
            )
        )
    return tuple(stats)


def _bounds_of(box: BoundingBox2D | None) -> BoundsSummary | None:
    """Domain bounding box to DTO."""
    if box is None:
        return None
    return BoundsSummary(
        min_x_m=box.min_x_m, min_y_m=box.min_y_m, max_x_m=box.max_x_m, max_y_m=box.max_y_m
    )


def _polygon_geometry(polygon: Polygon2D) -> PolygonGeometry:
    """Domain polygon to renderable DTO."""
    return PolygonGeometry(
        exterior=_ring(polygon.exterior),
        holes=tuple(_ring(hole) for hole in polygon.holes),
    )


def _ring(points: tuple[Point2D, ...]) -> tuple[tuple[float, float], ...]:
    """Point ring to plain coordinate pairs."""
    return tuple((point.x_m, point.y_m) for point in points)
