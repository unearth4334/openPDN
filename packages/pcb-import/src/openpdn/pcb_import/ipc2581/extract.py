"""Semantic extraction: IPC-2581 syntax model to canonical board.

This is where IPC-2581 vocabulary is translated into openPDN concepts and then
stops existing. Everything returned from here is a plain canonical `Board`
plus format-independent diagnostics and a capability report.

Rules the extraction lives by (see `.agents/skills/ipc2581-import/SKILL.md`):

* One unit conversion: every dimension is multiplied by the document's declared
  scale exactly once, here.
* No invented physics: absent plating, conductivity or thickness stays absent
  (or explicitly assumed with a note), and a diagnostic says so.
* Nothing disappears silently: every skipped, degenerate or unsupported
  construct produces a diagnostic with a count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from openpdn.domain.board import (
    Board,
    BoardId,
    BoardProfile,
    ComponentId,
    CopperRegion,
    CopperRegionId,
    ImportProvenance,
    Layer,
    LayerFunction,
    LayerId,
    Net,
    NetId,
    Pad,
    PadId,
    PhysicalComponent,
    Stackup,
    Terminal,
    TerminalId,
    Via,
    ViaId,
)
from openpdn.domain.errors import DomainError
from openpdn.domain.geometry import Point2D, Polygon2D
from openpdn.domain.materials import COPPER_ANNEALED, Material
from openpdn.domain.provenance import Quantity
from openpdn.domain.results import Diagnostic, DiagnosticSeverity
from openpdn.domain.units import METRE
from openpdn.pcb_import.api import (
    ImportCapability,
    ImportCapabilityItem,
    ImportCapabilityReport,
    MalformedSourceError,
    SimulationReadiness,
)
from openpdn.pcb_import.ipc2581.geometry import (
    ArcClosure,
    DegenerateFeatureError,
    apply_xform,
    circle_outline,
    classify_arc,
    polygon_ring,
    polyline_path,
    rectangle_ring,
    stroke_to_polygons,
    tessellate_arc,
)
from openpdn.pcb_import.ipc2581.syntax import (
    IPC2581Document,
    IpcArc,
    IpcCircle,
    IpcContour,
    IpcLayerHole,
    IpcLayerPad,
    IpcLine,
    IpcLineDesc,
    IpcPadStack,
    IpcPoint,
    IpcPolyline,
    IpcPrimitive,
    IpcRectCenter,
    IpcStandardPrimitiveRef,
    IpcXform,
)

if TYPE_CHECKING:
    from openpdn.pcb_import.ipc2581.syntax import IpcLayerFeature, IpcSet

#: IPC-2581 layer functions that conduct, mapped to the canonical vocabulary.
_CONDUCTIVE_FUNCTIONS: Final[dict[str, LayerFunction]] = {
    "CONDUCTOR": LayerFunction.SIGNAL,
    "SIGNAL": LayerFunction.SIGNAL,
    "MIXED": LayerFunction.MIXED,
    "PLANE": LayerFunction.PLANE,
    "POWER_GROUND": LayerFunction.PLANE,
    "POWER": LayerFunction.PLANE,
    "GROUND": LayerFunction.PLANE,
}

#: Non-conductive IPC-2581 layer functions that are physically part of the
#: finished board and therefore belong in the canonical stackup.
_PHYSICAL_FUNCTIONS: Final[dict[str, LayerFunction]] = {
    "DIELCORE": LayerFunction.DIELECTRIC,
    "DIELPREG": LayerFunction.DIELECTRIC,
    "DIELADHV": LayerFunction.DIELECTRIC,
    "DIELBASE": LayerFunction.DIELECTRIC,
    "DIELCOAT": LayerFunction.DIELECTRIC,
    "SOLDERMASK": LayerFunction.SOLDER_MASK,
    "SILKSCREEN": LayerFunction.SILKSCREEN,
    "LEGEND": LayerFunction.SILKSCREEN,
}

#: Net names generators write as a placeholder for "not connected to any net".
#: Altium writes "No Net"; treating it as a real electrical net would invent
#: connectivity, so it maps to unassigned copper (with a diagnostic).
_PLACEHOLDER_NET_NAMES: Final = frozenset({"no net", ""})

#: `platingStatus` values describing a plated, conducting hole.
_PLATED_STATUSES: Final = frozenset({"VIA", "PLATED"})


@dataclass(frozen=True)
class ExtractedBoard:
    """Everything semantic extraction produces from one document."""

    board: Board
    diagnostics: tuple[Diagnostic, ...]
    capability_report: ImportCapabilityReport
    feature_counts: dict[str, int] = field(default_factory=dict)


def extract_board(
    document: IPC2581Document,
    *,
    source_name: str,
    digest: str | None,
    scale_to_m: float,
) -> ExtractedBoard:
    """Translate a syntax-model document into a canonical board.

    Args:
        document: The syntax model from `syntax.read_document`.
        source_name: Display name of the source file, for provenance.
        digest: Content hash of the source, for provenance and caching.
        scale_to_m: Factor converting document units to metres, from
            `units.unit_scale_to_m`.

    Raises:
        MalformedSourceError: If the document lacks the structure a board needs
            (no step, no conductive layers) or violates a domain invariant.
    """
    extraction = _Extraction(document, scale_to_m)
    extraction.run()

    board_id = BoardId(f"ipc2581-{digest[:16]}" if digest else f"ipc2581-{_slug(source_name)}")
    step_name = document.step.name if document.step is not None else source_name

    try:
        board = Board(
            id=board_id,
            name=step_name,
            stackup=Stackup(tuple(extraction.layers)),
            profile=extraction.profile,
            nets=tuple(extraction.nets.values()),
            copper_regions=tuple(extraction.regions),
            vias=tuple(extraction.vias),
            pads=tuple(extraction.pads),
            terminals=tuple(extraction.terminals.values()),
            components=tuple(extraction.components),
            provenance=ImportProvenance(
                importer="ipc2581",
                source_format="IPC-2581",
                source_name=source_name,
                source_digest=digest,
            ),
        )
    except DomainError as exc:
        # A syntactically valid document can still describe an inconsistent
        # board; that is a source defect, not an internal fault.
        raise MalformedSourceError(f"Inconsistent IPC-2581 board: {exc}") from exc

    report = _capability_report(document, extraction)
    return ExtractedBoard(
        board=board,
        diagnostics=tuple(extraction.diagnostics),
        capability_report=report,
        feature_counts=dict(extraction.counts),
    )


class _Extraction:
    """Mutable working state of one extraction run."""

    def __init__(self, document: IPC2581Document, scale_to_m: float) -> None:
        self.document = document
        self.scale = scale_to_m
        self.diagnostics: list[Diagnostic] = []
        self.counts: dict[str, int] = {}
        self.layers: list[Layer] = []
        self.layer_ids: dict[str, LayerId] = {}
        self.conductive_refs: set[str] = set()
        self.nets: dict[NetId, Net] = {}
        self._net_by_name: dict[str, NetId] = {}
        self.profile: BoardProfile | None = None
        self.regions: list[CopperRegion] = []
        self.vias: list[Via] = []
        self.pads: list[Pad] = []
        self.terminals: dict[str, Terminal] = {}
        self.components: list[PhysicalComponent] = []
        self.copper_thickness_present = 0
        self.copper_thickness_missing = 0
        self.dielectric_present = False
        self.placeholder_net_count = 0
        self.degenerate: list[str] = []
        self.missing_dictionary: dict[str, int] = {}
        self.negative_polarity_count = 0
        self.skipped_nonconductive_features = 0
        self.non_plated_hole_count = 0
        self.via_missing_span = 0
        self.declared_pin_nets: dict[tuple[str, str], str] = {}
        self.degenerate_arc_count = 0
        self.netlist_mismatches = 0
        self._region_seq = 0

    # -- orchestration ---------------------------------------------------------
    def run(self) -> None:
        self._extract_layers()
        self._extract_logical_nets()
        self._extract_profile()
        self._extract_copper_features()
        self._extract_padstacks()
        self._extract_components()
        self._finish_diagnostics()

    # -- layers and stackup ------------------------------------------------------
    def _extract_layers(self) -> None:
        document = self.document
        function_by_ref = {layer.name: layer.layer_function.upper() for layer in document.layers}
        material_by_ref = {
            spec.name: spec.material_name for spec in document.specs if spec.material_name
        }

        ordered_refs: list[tuple[str, float | None]]
        if document.stackup is not None and document.stackup.entries:
            entries = sorted(
                document.stackup.entries,
                key=lambda entry: entry.sequence if entry.sequence is not None else 1_000_000,
            )
            ordered_refs = [(entry.layer_ref, entry.thickness) for entry in entries]
        else:
            self._diagnose(
                "import.stackup_missing",
                DiagnosticSeverity.WARNING,
                "The document declares no stackup; layer order is taken from the layer list "
                "and thicknesses are unknown.",
            )
            ordered_refs = [(layer.name, None) for layer in document.layers]

        index = 0
        used_ids: set[str] = set()
        for layer_ref, thickness_raw in ordered_refs:
            ipc_function = function_by_ref.get(layer_ref)
            if ipc_function is None:
                self._diagnose(
                    "import.unknown_layer_reference",
                    DiagnosticSeverity.WARNING,
                    "A stackup row references a layer that is not declared; the row was skipped.",
                    layer=layer_ref,
                )
                continue

            function = _CONDUCTIVE_FUNCTIONS.get(ipc_function)
            thickness_m = (
                thickness_raw * self.scale if thickness_raw and thickness_raw > 0.0 else None
            )
            if function is None:
                physical = _PHYSICAL_FUNCTIONS.get(ipc_function)
                if physical is None or thickness_m is None:
                    # Paste, legend, documentation and drill-drawing layers are
                    # artwork about the board, not part of the physical board.
                    continue
                function = physical

            material: Material | None = None
            if function.is_conductive:
                material_name = material_by_ref.get(layer_ref, "Copper")
                # The document names the conductor but gives no conductivity;
                # IEC 60028 annealed copper is assumed and diagnosed below.
                material = Material(
                    name=f"{material_name} (conductivity assumed, IEC 60028)",
                    conductivity_s_per_m=COPPER_ANNEALED.conductivity_s_per_m,
                    temperature_coefficient_per_k=COPPER_ANNEALED.temperature_coefficient_per_k,
                    reference_temperature_k=COPPER_ANNEALED.reference_temperature_k,
                )
                if thickness_m is not None:
                    self.copper_thickness_present += 1
                else:
                    self.copper_thickness_missing += 1
                    self._diagnose(
                        "import.missing_layer_thickness",
                        DiagnosticSeverity.WARNING,
                        "A conductive layer has no thickness in the source; supply one in "
                        "the study before solving.",
                        layer=layer_ref,
                    )
                self.conductive_refs.add(layer_ref)
            elif function is LayerFunction.DIELECTRIC:
                self.dielectric_present = True

            layer_id = _unique_slug(f"layer-{_slug(layer_ref)}", used_ids)
            thickness = Quantity.imported(thickness_m, METRE) if thickness_m is not None else None
            self.layers.append(
                Layer(
                    id=LayerId(layer_id),
                    name=layer_ref,
                    function=function,
                    index=index,
                    thickness=thickness,
                    material=material,
                )
            )
            self.layer_ids[layer_ref] = LayerId(layer_id)
            index += 1

        if not any(layer.function.is_conductive for layer in self.layers):
            raise MalformedSourceError(
                "The document declares no conductive layers; there is no board to analyse"
            )
        if self.conductive_refs:
            self._diagnose(
                "import.assumed_material_conductivity",
                DiagnosticSeverity.WARNING,
                "The source names conductor materials but gives no conductivity; annealed "
                "copper per IEC 60028 (5.8001e7 S/m at 20 degC) is assumed for all "
                "conductive layers.",
                layers=str(len(self.conductive_refs)),
            )

    # -- nets -----------------------------------------------------------------------
    def _extract_logical_nets(self) -> None:
        step = self.document.step
        if step is None:
            return
        for logical_net in step.logical_nets:
            self._net_id_for(logical_net.name)
            for pin in logical_net.pins:
                self.declared_pin_nets[(pin.component_ref, pin.pin)] = logical_net.name
        if not step.logical_nets:
            self._diagnose(
                "import.no_logical_netlist",
                DiagnosticSeverity.INFO,
                "The document declares no logical netlist; net membership is taken from "
                "the artwork's net attributes.",
            )

    def _net_id_for(self, name: str | None) -> NetId | None:
        """Return the canonical net id for a source net name.

        Placeholder names ("No Net") map to `None`: the copper exists but is
        electrically unassigned, and inventing a net for it would fabricate
        connectivity.
        """
        if name is None or name.strip().lower() in _PLACEHOLDER_NET_NAMES:
            if name is not None:
                self.placeholder_net_count += 1
            return None
        existing = self._net_by_name.get(name)
        if existing is not None:
            return existing
        net_id = NetId(_unique_slug(f"net-{_slug(name)}", set(self.nets)))
        self.nets[net_id] = Net(id=net_id, name=name)
        self._net_by_name[name] = net_id
        return net_id

    # -- profile ----------------------------------------------------------------------
    def _extract_profile(self) -> None:
        step = self.document.step
        if step is None or step.profile is None or not step.profile.polygons:
            self._diagnose(
                "import.no_board_profile",
                DiagnosticSeverity.WARNING,
                "The document has no board profile; the board outline is unknown and the "
                "copper extent stands in for it.",
            )
            return

        outlines: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
        for polygon in step.profile.polygons:
            try:
                outlines.append((polygon_ring(polygon, self.scale), []))
            except DegenerateFeatureError:
                self.degenerate.append("Profile/Polygon")
        cutout_rings: list[list[tuple[float, float]]] = []
        for cutout in step.profile.cutouts:
            try:
                cutout_rings.append(polygon_ring(cutout, self.scale))
            except DegenerateFeatureError:
                self.degenerate.append("Profile/Cutout")

        # Non-plated holes are physically absent board material: they belong to
        # the profile, not to the copper model.
        for layer_feature in step.layer_features:
            for feature_set in layer_feature.sets:
                for hole in feature_set.holes:
                    if hole.plating_status == "NONPLATED":
                        cutout_rings.append(
                            circle_outline(
                                (hole.x * self.scale, hole.y * self.scale),
                                hole.diameter * self.scale,
                            )
                        )
                        self.non_plated_hole_count += 1

        if not outlines:
            self._diagnose(
                "import.no_board_profile",
                DiagnosticSeverity.WARNING,
                "Every profile outline was degenerate; the board outline is unknown.",
            )
            return

        _assign_cutouts(outlines, cutout_rings)
        polygons = tuple(
            Polygon2D.from_coordinates(exterior, holes) for exterior, holes in outlines
        )
        self.profile = BoardProfile(outlines=polygons)
        if self.non_plated_hole_count:
            self._diagnose(
                "import.non_plated_holes",
                DiagnosticSeverity.INFO,
                "Non-plated holes were added to the board profile as cutouts.",
                count=str(self.non_plated_hole_count),
            )

    # -- copper artwork ------------------------------------------------------------------
    def _extract_copper_features(self) -> None:
        step = self.document.step
        if step is None:
            raise MalformedSourceError("The document has no Step; there is no board content")
        for layer_feature in step.layer_features:
            if layer_feature.layer_ref in self.conductive_refs:
                self._extract_conductive_layer(layer_feature)
            else:
                self.skipped_nonconductive_features += sum(
                    len(feature_set.features) for feature_set in layer_feature.sets
                )

    def _extract_conductive_layer(self, layer_feature: IpcLayerFeature) -> None:
        layer_id = self.layer_ids[layer_feature.layer_ref]
        for set_index, feature_set in enumerate(layer_feature.sets):
            net_id = self._net_id_for(feature_set.net)
            if feature_set.polarity and feature_set.polarity.upper() == "NEGATIVE":
                # Subtractive artwork changes the meaning of every other
                # feature on the layer; importing around it would produce
                # confidently wrong copper.
                self.negative_polarity_count += sum(
                    len(feature.primitives) for feature in feature_set.features
                )
                continue
            self._extract_set_features(
                layer_feature.layer_ref, layer_id, net_id, set_index, feature_set
            )

    def _extract_set_features(
        self,
        layer_ref: str,
        layer_id: LayerId,
        net_id: NetId | None,
        set_index: int,
        feature_set: IpcSet,
    ) -> None:
        for feature_index, feature in enumerate(feature_set.features):
            offset = _scaled_point(feature.location, self.scale)
            for primitive_index, primitive in enumerate(feature.primitives):
                source_ref = (
                    f"{layer_ref}/Set[{set_index}]/Features[{feature_index}]"
                    f"/{primitive.__class__.__name__.removeprefix('Ipc')}[{primitive_index}]"
                )
                try:
                    polygons = self._resolve_primitive(
                        primitive, _scaled_xform(feature.xform, self.scale), offset
                    )
                except DegenerateFeatureError:
                    self.degenerate.append(source_ref)
                    continue
                for polygon in polygons:
                    self._add_region(layer_id, net_id, polygon, source_ref)

    def _resolve_primitive(
        self,
        primitive: IpcPrimitive,
        xform: IpcXform | None,
        offset: tuple[float, float],
    ) -> list[Polygon2D]:
        """Resolve one primitive into absolute board-coordinate polygons."""
        scale = self.scale
        if isinstance(primitive, IpcLine):
            self._count("strokes")
            path = [
                (primitive.start_x * scale, primitive.start_y * scale),
                (primitive.end_x * scale, primitive.end_y * scale),
            ]
            return self._stroke(path, primitive.line_desc, xform, offset)
        if isinstance(primitive, IpcArc):
            self._count("arcs")
            start = (primitive.start_x * scale, primitive.start_y * scale)
            end = (primitive.end_x * scale, primitive.end_y * scale)
            if classify_arc(start, end) is ArcClosure.DEGENERATE:
                # Endpoints rounded to nearly the same point; read as an open
                # arc this would sweep a whole turn. Counted, then drawn as
                # the zero-length segment it is.
                self.degenerate_arc_count += 1
            path = tessellate_arc(
                start,
                end,
                (primitive.center_x * scale, primitive.center_y * scale),
                primitive.clockwise,
            )
            return self._stroke(path, primitive.line_desc, xform, offset)
        if isinstance(primitive, IpcPolyline):
            self._count("strokes")
            begin = (primitive.begin.x * scale, primitive.begin.y * scale)
            path = polyline_path(begin, primitive.steps, scale)
            return self._stroke(path, primitive.line_desc, xform, offset)
        if isinstance(primitive, IpcContour):
            self._count("contours")
            exterior = apply_xform(polygon_ring(primitive.polygon, scale), xform, offset)
            holes = []
            for cutout in primitive.cutouts:
                try:
                    holes.append(apply_xform(polygon_ring(cutout, scale), xform, offset))
                except DegenerateFeatureError:
                    self.degenerate.append("Contour/Cutout")
            return [Polygon2D.from_coordinates(exterior, holes)]
        if isinstance(primitive, IpcCircle):
            self._count("flashes")
            ring = apply_xform(
                circle_outline((0.0, 0.0), primitive.diameter * scale), xform, offset
            )
            return [Polygon2D.from_coordinates(ring)]
        if isinstance(primitive, IpcRectCenter):
            self._count("flashes")
            ring = apply_xform(
                rectangle_ring(primitive.width * scale, primitive.height * scale), xform, offset
            )
            return [Polygon2D.from_coordinates(ring)]
        if isinstance(primitive, IpcStandardPrimitiveRef):
            entry = self.document.dictionary.get(primitive.entry_id)
            if entry is None:
                self.missing_dictionary[primitive.entry_id] = (
                    self.missing_dictionary.get(primitive.entry_id, 0) + 1
                )
                return []
            return self._resolve_primitive(entry, xform, offset)
        raise DegenerateFeatureError("Unresolvable primitive")

    def _stroke(
        self,
        path: list[tuple[float, float]],
        line_desc: IpcLineDesc | None,
        xform: IpcXform | None,
        offset: tuple[float, float],
    ) -> list[Polygon2D]:
        if line_desc is None:
            raise DegenerateFeatureError("Stroked feature carries no line description")
        width_m = line_desc.line_width * self.scale
        placed = apply_xform(path, xform, offset)
        round_ends = line_desc.line_end != "NONE"
        return stroke_to_polygons(placed, width_m, round_ends)

    def _add_region(
        self,
        layer_id: LayerId,
        net_id: NetId | None,
        polygon: Polygon2D,
        source_ref: str,
    ) -> None:
        self._region_seq += 1
        self.regions.append(
            CopperRegion(
                id=CopperRegionId(f"r{self._region_seq:05d}"),
                net_id=net_id,
                layer_id=layer_id,
                outline=polygon,
                source_ref=source_ref,
            )
        )

    # -- padstacks: vias, pads, terminals ---------------------------------------------------
    def _extract_padstacks(self) -> None:
        step = self.document.step
        if step is None:
            return
        via_seq = 0
        pad_seq = 0
        for padstack_index, padstack in enumerate(step.padstacks):
            net_id = self._net_id_for(padstack.net)
            # Non-plated padstack holes are board cutouts, not conductors; the
            # profile picks those up from the drill-layer sets.
            plated_holes = [
                hole for hole in padstack.holes if hole.plating_status in _PLATED_STATUSES
            ]
            for hole in plated_holes:
                via_seq += 1
                via = self._build_via(via_seq, padstack_index, net_id, hole)
                if via is not None:
                    self.vias.append(via)
            pad_seq = self._extract_padstack_pads(padstack_index, padstack, net_id, pad_seq)

    def _build_via(
        self, sequence: int, padstack_index: int, net_id: NetId | None, hole: IpcLayerHole
    ) -> Via | None:
        if hole.span is None:
            self.via_missing_span += 1
            return None
        from_id = self.layer_ids.get(hole.span.from_layer)
        to_id = self.layer_ids.get(hole.span.to_layer)
        if from_id is None or to_id is None:
            self._diagnose(
                "import.unknown_layer_reference",
                DiagnosticSeverity.WARNING,
                "A via span references a layer that is not in the stackup; the via was skipped.",
                padstack=str(padstack_index),
            )
            return None
        return Via(
            id=ViaId(f"via-{sequence:04d}"),
            net_id=net_id,
            from_layer_id=from_id,
            to_layer_id=to_id,
            position=Point2D(hole.x * self.scale, hole.y * self.scale),
            drill_diameter=Quantity.imported(hole.diameter * self.scale, METRE),
            padstack_name=hole.name,
        )

    def _extract_padstack_pads(
        self, padstack_index: int, padstack: IpcPadStack, net_id: NetId | None, pad_seq: int
    ) -> int:
        for pad in padstack.pads:
            if pad.layer_ref not in self.conductive_refs:
                self.skipped_nonconductive_features += 1
                continue
            layer_id = self.layer_ids[pad.layer_ref]
            polygons = self._resolve_pad_polygons(pad, padstack_index)
            if polygons is None:
                continue
            source_ref = f"PadStack[{padstack_index}]/LayerPad[{pad.layer_ref}]"
            for polygon in polygons:
                self._add_region(layer_id, net_id, polygon, source_ref)
            self._count("pad_flashes")

            pad_seq += 1
            pad_id = PadId(f"pad-{pad_seq:05d}")
            position = _scaled_point(pad.location, self.scale)
            self.pads.append(
                Pad(
                    id=pad_id,
                    layer_id=layer_id,
                    position=Point2D(position[0], position[1]),
                    net_id=net_id,
                    outline=polygons[0] if polygons else None,
                )
            )
            if pad.pin_ref is not None:
                self._attach_terminal(
                    pad.pin_ref.component_ref, pad.pin_ref.pin, net_id, pad_id, padstack.net
                )
        return pad_seq

    def _resolve_pad_polygons(
        self, pad: IpcLayerPad, padstack_index: int
    ) -> list[Polygon2D] | None:
        if pad.primitive is None:
            self.degenerate.append(f"PadStack[{padstack_index}]/LayerPad[{pad.layer_ref}]")
            return None
        offset = _scaled_point(pad.location, self.scale)
        try:
            return self._resolve_primitive(
                pad.primitive, _scaled_xform(pad.xform, self.scale), offset
            )
        except DegenerateFeatureError:
            self.degenerate.append(f"PadStack[{padstack_index}]/LayerPad[{pad.layer_ref}]")
            return None

    def _attach_terminal(
        self,
        component_ref: str,
        pin: str,
        net_id: NetId | None,
        pad_id: PadId,
        artwork_net_name: str | None,
    ) -> None:
        declared = self.declared_pin_nets.get((component_ref, pin))
        if declared is not None and artwork_net_name is not None and declared != artwork_net_name:
            self.netlist_mismatches += 1
            self._diagnose(
                "import.netlist_artwork_mismatch",
                DiagnosticSeverity.WARNING,
                "A pin's declared logical net disagrees with the net on its pad artwork; "
                "the artwork net was kept.",
                component=component_ref,
                pin=pin,
            )
        resolved_net = net_id if net_id is not None else self._net_id_for(declared)
        if resolved_net is None:
            # A terminal without a net cannot take a source or a load; the pad
            # itself is still imported.
            return
        key = f"{component_ref}.{pin}"
        existing = self.terminals.get(key)
        if existing is not None:
            self.terminals[key] = Terminal(
                id=existing.id,
                name=existing.name,
                net_id=existing.net_id,
                pad_ids=(*existing.pad_ids, pad_id),
                component_id=existing.component_id,
            )
            return
        terminal_id = TerminalId(_unique_slug(f"t-{_slug(key)}", set()))
        self.terminals[key] = Terminal(
            id=terminal_id,
            name=key,
            net_id=resolved_net,
            pad_ids=(pad_id,),
            component_id=ComponentId(f"c-{_slug(component_ref)}"),
        )

    # -- components -------------------------------------------------------------------------
    def _extract_components(self) -> None:
        step = self.document.step
        if step is None:
            return
        terminal_ids_by_component: dict[str, list[TerminalId]] = {}
        for key, terminal in self.terminals.items():
            component_ref = key.rsplit(".", 1)[0]
            terminal_ids_by_component.setdefault(component_ref, []).append(terminal.id)

        seen: set[str] = set()
        for component in step.components:
            component_id = f"c-{_slug(component.ref_des)}"
            if component_id in seen:
                continue
            seen.add(component_id)
            self.components.append(
                PhysicalComponent(
                    id=ComponentId(component_id),
                    reference_designator=component.ref_des,
                    terminal_ids=tuple(terminal_ids_by_component.get(component.ref_des, ())),
                    part_number=component.part,
                )
            )
        # Terminals may reference components the step never places (rare, but a
        # netlist can outlive a placement); keep the board consistent by
        # dropping the dangling component reference.
        placed = {component.id for component in self.components}
        for key, terminal in list(self.terminals.items()):
            if terminal.component_id is not None and terminal.component_id not in placed:
                self.terminals[key] = Terminal(
                    id=terminal.id,
                    name=terminal.name,
                    net_id=terminal.net_id,
                    pad_ids=terminal.pad_ids,
                    component_id=None,
                )

    # -- diagnostics and reporting -----------------------------------------------------------
    def _finish_diagnostics(self) -> None:
        if self.placeholder_net_count:
            self._diagnose(
                "import.placeholder_net",
                DiagnosticSeverity.INFO,
                "Features declared with a placeholder net name were imported as "
                "electrically unassigned copper.",
                count=str(self.placeholder_net_count),
            )
        if self.negative_polarity_count:
            self._diagnose(
                "import.negative_polarity_unsupported",
                DiagnosticSeverity.ERROR,
                "The document uses negative (subtractive) artwork, which this importer "
                "does not yet resolve; the affected layers' copper is incomplete and the "
                "board is not usable for simulation.",
                count=str(self.negative_polarity_count),
            )
        if self.degenerate_arc_count:
            self._diagnose(
                "import.degenerate_arc",
                DiagnosticSeverity.INFO,
                "Arcs whose endpoints round to the same point were imported as zero-length "
                "segments rather than as full circles; the source is ambiguous at that scale "
                "and reading them as arcs would add copper the design does not contain.",
                count=str(self.degenerate_arc_count),
            )
        if self.degenerate:
            self._diagnose(
                "import.degenerate_feature",
                DiagnosticSeverity.WARNING,
                "Features that bound no physical copper area were skipped.",
                count=str(len(self.degenerate)),
                first=self.degenerate[0],
            )
        for entry_id, count in self.missing_dictionary.items():
            self._diagnose(
                "import.missing_dictionary_entry",
                DiagnosticSeverity.WARNING,
                "A feature references a primitive dictionary entry that does not exist; "
                "its copper is missing from the import.",
                entry=entry_id,
                count=str(count),
            )
        if self.skipped_nonconductive_features:
            self._diagnose(
                "import.nonconductive_artwork_skipped",
                DiagnosticSeverity.INFO,
                "Artwork on non-conductive layers (legend, paste, documentation, covering) "
                "is not part of the electrical model and was not imported.",
                count=str(self.skipped_nonconductive_features),
            )
        if self.vias and all(via.plating_thickness is None for via in self.vias):
            self._diagnose(
                "import.missing_via_plating",
                DiagnosticSeverity.WARNING,
                "Via plating thickness is not present in the source. Simulation "
                "requiring via resistance will need a user-supplied or explicitly "
                "accepted default value.",
                count=str(len(self.vias)),
            )
        if self.vias and all(via.finished_hole_diameter is None for via in self.vias):
            self._diagnose(
                "import.missing_finished_hole_diameter",
                DiagnosticSeverity.INFO,
                "Finished hole diameters are not present; only drill diameters were imported.",
                count=str(len(self.vias)),
            )
        if self.via_missing_span:
            self._diagnose(
                "import.via_missing_span",
                DiagnosticSeverity.WARNING,
                "Plated holes without a declared layer span were skipped.",
                count=str(self.via_missing_span),
            )
        for label, count in sorted(self.document.unknown_constructs.items()):
            self._diagnose(
                "import.unsupported_construct",
                DiagnosticSeverity.WARNING,
                "The document uses a construct this importer does not understand; "
                "anything it describes is missing from the import.",
                construct=label,
                count=str(count),
            )

    def _diagnose(
        self, code: str, severity: DiagnosticSeverity, message: str, **context: str
    ) -> None:
        self.diagnostics.append(
            Diagnostic(code=code, severity=severity, message=message, context=dict(context))
        )

    def _count(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


def _capability_report(
    document: IPC2581Document, extraction: _Extraction
) -> ImportCapabilityReport:
    """Derive the import capability report from what extraction obtained."""
    conductive = [layer for layer in extraction.layers if layer.function.is_conductive]

    def status(present: bool, partial: bool = False) -> ImportCapability:
        if present:
            return ImportCapability.PRESENT
        return ImportCapability.PARTIAL if partial else ImportCapability.ABSENT

    thickness_status = status(
        extraction.copper_thickness_missing == 0 and extraction.copper_thickness_present > 0,
        partial=extraction.copper_thickness_present > 0,
    )
    items = (
        ImportCapabilityItem("Board outline", status(extraction.profile is not None)),
        ImportCapabilityItem("Copper geometry", status(bool(extraction.regions))),
        ImportCapabilityItem(
            "Layer ordering",
            status(document.stackup is not None and bool(conductive), partial=bool(conductive)),
        ),
        ImportCapabilityItem("Copper thickness", thickness_status),
        ImportCapabilityItem("Dielectric stackup", status(extraction.dielectric_present)),
        ImportCapabilityItem(
            "Net connectivity",
            status(bool(extraction.nets)),
            note=None
            if document.step is None or document.step.logical_nets
            else "taken from artwork net attributes; no logical netlist was declared",
        ),
        ImportCapabilityItem("Components", status(bool(extraction.components))),
        ImportCapabilityItem("Pin mapping", status(bool(extraction.terminals))),
        ImportCapabilityItem(
            "Drill geometry",
            status(
                bool(extraction.vias)
                and all(via.drill_diameter is not None for via in extraction.vias)
            ),
        ),
        ImportCapabilityItem(
            "Via spans",
            status(
                bool(extraction.vias) and extraction.via_missing_span == 0,
                partial=bool(extraction.vias),
            ),
        ),
        ImportCapabilityItem(
            "Via plating",
            status(
                bool(extraction.vias)
                and any(via.plating_thickness is not None for via in extraction.vias)
            ),
            note="required for via resistance; a study must supply it" if extraction.vias else None,
        ),
        ImportCapabilityItem(
            "Material conductivity",
            ImportCapability.ABSENT,
            note="assumed IEC 60028 annealed copper",
        ),
    )

    required_for_review = ("Copper geometry", "Layer ordering")
    by_name = {item.name: item for item in items}
    if (
        any(by_name[name].status is ImportCapability.ABSENT for name in required_for_review)
        or extraction.negative_polarity_count > 0
    ):
        readiness = SimulationReadiness.NOT_READY
    elif all(item.status is ImportCapability.PRESENT for item in items):
        readiness = SimulationReadiness.READY
    else:
        readiness = SimulationReadiness.READY_WITH_ASSUMPTIONS

    return ImportCapabilityReport(
        source_format="IPC-2581",
        format_revision=f"IPC-2581{document.revision.value}",
        items=items,
        readiness=readiness,
    )


# --- helpers -----------------------------------------------------------------------
def _scaled_point(point: IpcPoint | None, scale: float) -> tuple[float, float]:
    """Scale an optional location to metres, defaulting to the origin."""
    if point is None:
        return (0.0, 0.0)
    return (point.x * scale, point.y * scale)


def _scaled_xform(xform: IpcXform | None, scale: float) -> IpcXform | None:
    """Scale a transform's offsets to metres; rotation and mirror are unitless."""
    if xform is None or (xform.x_offset == 0.0 and xform.y_offset == 0.0):
        return xform
    return IpcXform(
        rotation_deg=xform.rotation_deg,
        mirror=xform.mirror,
        x_offset=xform.x_offset * scale,
        y_offset=xform.y_offset * scale,
    )


def _assign_cutouts(
    outlines: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]],
    cutouts: list[list[tuple[float, float]]],
) -> None:
    """Attach each cutout ring to the outline that contains it.

    Uses Shapely containment; a cutout inside no outline is attached to the
    first outline so it stays visible rather than disappearing.
    """
    if not cutouts:
        return
    from shapely.geometry import Polygon as ShapelyPolygon

    shells = [ShapelyPolygon(exterior) for exterior, _ in outlines]
    for ring in cutouts:
        hole = ShapelyPolygon(ring)
        target = 0
        for index, shell in enumerate(shells):
            if shell.contains(hole.representative_point()):
                target = index
                break
        outlines[target][1].append(ring)


_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """Reduce a source name to a stable, readable identifier fragment."""
    slug = _SLUG_PATTERN.sub("-", text.strip().lower()).strip("-")
    return slug or "unnamed"


def _unique_slug(base: str, used: set[str]) -> str:
    """Return `base`, suffixed if needed to stay unique within `used`."""
    candidate = base
    suffix = 1
    while candidate in used:
        suffix += 1
        candidate = f"{base}-{suffix}"
    used.add(candidate)
    return candidate


# Re-exported for the importer's stats without re-walking the tree.
__all__ = ["ExtractedBoard", "extract_board"]
