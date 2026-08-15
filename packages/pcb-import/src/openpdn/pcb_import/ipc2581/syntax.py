"""The IPC-2581 syntax model and its XML reader.

XML terminates in this module. `read_document` walks an already-securely-parsed
element tree (`secure_xml.parse_secure`) and produces plain frozen dataclasses;
no `xml.etree` type escapes it. The dataclasses mirror the *syntax* of the
constructs openPDN reads -- they are not the canonical board model, and they
never leave the IPC-2581 adapter (see `.agents/skills/ipc2581-import/SKILL.md`).

Values are kept exactly as the document wrote them: coordinates and dimensions
stay in the document's declared unit. Scaling to SI happens once, during
semantic extraction (`extract.py`), so the syntax layer cannot half-convert a
document.

The reader is deliberately tolerant of *unknown* constructs -- it records their
tag names and counts in `IPC2581Document.unknown_constructs` so extraction can
diagnose them -- but strict about *malformed* known constructs, which raise
`MalformedSourceError` naming the element and attribute, never echoing content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from openpdn.pcb_import.api import MalformedSourceError
from openpdn.pcb_import.ipc2581.revision import IPC2581Revision, local_name

if TYPE_CHECKING:
    from collections.abc import Iterator
    from xml.etree.ElementTree import Element


# --- geometry primitives (document units) ------------------------------------
@dataclass(frozen=True, slots=True)
class IpcPoint:
    """A coordinate pair as written in the document."""

    x: float
    y: float


@dataclass(frozen=True, slots=True)
class IpcSegmentStep:
    """A straight `PolyStepSegment` to (x, y)."""

    x: float
    y: float


@dataclass(frozen=True, slots=True)
class IpcCurveStep:
    """A circular `PolyStepCurve` to (x, y) around (center_x, center_y)."""

    x: float
    y: float
    center_x: float
    center_y: float
    clockwise: bool


@dataclass(frozen=True, slots=True)
class IpcPolygon:
    """A `Polygon`/`Cutout` boundary: a begin point plus poly-steps."""

    begin: IpcPoint
    steps: tuple[IpcSegmentStep | IpcCurveStep, ...]


@dataclass(frozen=True, slots=True)
class IpcContour:
    """A filled `Contour`: one boundary polygon and zero or more cutouts."""

    polygon: IpcPolygon
    cutouts: tuple[IpcPolygon, ...]


@dataclass(frozen=True, slots=True)
class IpcLineDesc:
    """Stroke description attached to lines, arcs and polylines."""

    line_width: float
    line_end: str


@dataclass(frozen=True, slots=True)
class IpcLine:
    """A stroked straight segment."""

    start_x: float
    start_y: float
    end_x: float
    end_y: float
    line_desc: IpcLineDesc | None


@dataclass(frozen=True, slots=True)
class IpcArc:
    """A stroked circular arc; equal endpoints denote a full circle."""

    start_x: float
    start_y: float
    end_x: float
    end_y: float
    center_x: float
    center_y: float
    clockwise: bool
    line_desc: IpcLineDesc | None


@dataclass(frozen=True, slots=True)
class IpcPolyline:
    """A stroked open polyline (begin point plus poly-steps)."""

    begin: IpcPoint
    steps: tuple[IpcSegmentStep | IpcCurveStep, ...]
    line_desc: IpcLineDesc | None


@dataclass(frozen=True, slots=True)
class IpcCircle:
    """A flashed filled circle."""

    diameter: float


@dataclass(frozen=True, slots=True)
class IpcRectCenter:
    """A flashed filled rectangle, centred on its insertion point."""

    width: float
    height: float


@dataclass(frozen=True, slots=True)
class IpcStandardPrimitiveRef:
    """A reference into `DictionaryStandard` by entry id."""

    entry_id: str


#: Everything a `Features` element (directly or via `UserSpecial`) may contain.
IpcPrimitive = (
    IpcLine
    | IpcArc
    | IpcPolyline
    | IpcContour
    | IpcCircle
    | IpcRectCenter
    | IpcStandardPrimitiveRef
)


@dataclass(frozen=True, slots=True)
class IpcXform:
    """An instance transform: mirror about the Y axis, then rotate, then offset."""

    rotation_deg: float = 0.0
    mirror: bool = False
    x_offset: float = 0.0
    y_offset: float = 0.0


# --- structure ---------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class IpcFeature:
    """One `Features` element: an optional placement and its primitives."""

    xform: IpcXform | None
    location: IpcPoint | None
    primitives: tuple[IpcPrimitive, ...]


@dataclass(frozen=True, slots=True)
class IpcHole:
    """A `Hole` (drill-layer) entry, e.g. a non-plated mounting hole."""

    name: str
    diameter: float
    plating_status: str
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class IpcSet:
    """A `Set` inside a `LayerFeature`: one net's features on one layer."""

    net: str | None
    polarity: str | None
    features: tuple[IpcFeature, ...]
    holes: tuple[IpcHole, ...]


@dataclass(frozen=True, slots=True)
class IpcLayerFeature:
    """All artwork on one layer."""

    layer_ref: str
    sets: tuple[IpcSet, ...]


@dataclass(frozen=True, slots=True)
class IpcLayer:
    """A `Layer` declaration in `CadData`."""

    name: str
    layer_function: str
    side: str | None
    polarity: str | None


@dataclass(frozen=True, slots=True)
class IpcStackupLayer:
    """One `StackupLayer` row: a layer reference with its thickness."""

    layer_ref: str
    thickness: float | None
    sequence: int | None


@dataclass(frozen=True, slots=True)
class IpcStackup:
    """The document's stackup, flattened across stackup groups."""

    total_thickness: float | None
    entries: tuple[IpcStackupLayer, ...]


@dataclass(frozen=True, slots=True)
class IpcSpec:
    """A `Spec` element; openPDN reads the material name where present."""

    name: str
    material_name: str | None


@dataclass(frozen=True, slots=True)
class IpcPinRef:
    """A `PinRef`: which component pin a pad belongs to."""

    component_ref: str
    pin: str


@dataclass(frozen=True, slots=True)
class IpcLayerPad:
    """A `LayerPad`: one pad of a padstack on one layer."""

    layer_ref: str
    xform: IpcXform | None
    location: IpcPoint | None
    primitive: IpcPrimitive | None
    pin_ref: IpcPinRef | None


@dataclass(frozen=True, slots=True)
class IpcSpan:
    """The layer span of a padstack hole."""

    from_layer: str
    to_layer: str


@dataclass(frozen=True, slots=True)
class IpcLayerHole:
    """A `LayerHole`: the drilled hole of a padstack."""

    name: str | None
    diameter: float
    plating_status: str
    x: float
    y: float
    span: IpcSpan | None


@dataclass(frozen=True, slots=True)
class IpcPadStack:
    """A `PadStack` instance: a net, optional holes and per-layer pads."""

    net: str | None
    holes: tuple[IpcLayerHole, ...]
    pads: tuple[IpcLayerPad, ...]


@dataclass(frozen=True, slots=True)
class IpcComponent:
    """A placed `Component`."""

    ref_des: str
    package_ref: str | None
    layer_ref: str | None
    part: str | None
    mount_type: str | None
    xform: IpcXform | None
    location: IpcPoint | None


@dataclass(frozen=True, slots=True)
class IpcPin:
    """A logical net's `PinRef` (from `LogicalNet` declarations)."""

    component_ref: str
    pin: str


@dataclass(frozen=True, slots=True)
class IpcLogicalNet:
    """A `LogicalNet` declaration: a name and the pins it connects."""

    name: str
    pins: tuple[IpcPin, ...]


@dataclass(frozen=True, slots=True)
class IpcProfile:
    """The board `Profile`: outline polygons plus routed cutouts."""

    polygons: tuple[IpcPolygon, ...]
    cutouts: tuple[IpcPolygon, ...]


@dataclass(frozen=True, slots=True)
class IpcStep:
    """A `Step`: the board-level assembly of profile, padstacks and artwork."""

    name: str
    profile: IpcProfile | None
    padstacks: tuple[IpcPadStack, ...]
    components: tuple[IpcComponent, ...]
    layer_features: tuple[IpcLayerFeature, ...]
    logical_nets: tuple[IpcLogicalNet, ...]


@dataclass(frozen=True, slots=True)
class IPC2581Document:
    """Everything openPDN reads from one IPC-2581 document.

    `unknown_constructs` maps a construct label (usually a tag name, prefixed
    with where it appeared) to how many times the reader met it without
    understanding it. Extraction turns this into diagnostics -- an unsupported
    primitive must surface as a warning, never silently disappear.
    """

    revision: IPC2581Revision
    units_name: str
    generator: str | None
    layers: tuple[IpcLayer, ...]
    stackup: IpcStackup | None
    specs: tuple[IpcSpec, ...]
    dictionary: dict[str, IpcPrimitive]
    step: IpcStep | None
    unknown_constructs: dict[str, int] = field(default_factory=dict)


# --- reader ------------------------------------------------------------------
#: `Features` children that carry placement rather than geometry.
_PLACEMENT_TAGS: Final = frozenset({"Xform", "Location"})

#: Non-geometry children the reader deliberately ignores inside known elements
#: (presentation and metadata that carry no copper information).
_IGNORED_TAGS: Final = frozenset(
    {"ColorRef", "ColorGroup", "NonstandardAttribute", "SpecRef", "Comment"}
)


class _UnknownConstructs:
    """Mutable tally of constructs the reader met but does not understand."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def note(self, label: str) -> None:
        self._counts[label] = self._counts.get(label, 0) + 1

    def as_dict(self) -> dict[str, int]:
        return dict(self._counts)


def read_document(root: Element, revision: IPC2581Revision, units_name: str) -> IPC2581Document:
    """Translate a parsed IPC-2581 element tree into the syntax model.

    Args:
        root: The document root from `secure_xml.parse_secure`.
        revision: Revision already detected by `revision.detect_revision`.
        units_name: Unit name already located by the caller; recorded verbatim.

    Raises:
        MalformedSourceError: If a recognised construct is malformed (missing
            required attribute, unparseable number). Messages name the element
            and attribute but never echo document content.
    """
    unknown = _UnknownConstructs()

    dictionary: dict[str, IpcPrimitive] = {}
    generator: str | None = None
    layers: list[IpcLayer] = []
    stackup: IpcStackup | None = None
    specs: list[IpcSpec] = []
    step: IpcStep | None = None

    for section in root:
        tag = local_name(section.tag)
        if tag == "Content":
            for entry_id, primitive in _read_dictionaries(section, unknown):
                dictionary[entry_id] = primitive
        elif tag == "LogisticHeader":
            generator = _read_generator(section)
        elif tag == "Ecad":
            layers, stackup, specs, step = _read_ecad(section, unknown)
        elif tag in ("Bom", "HistoryRecord", "Avl"):
            # Bill-of-materials, revision history and vendor lists carry no
            # electrical structure this milestone reads.
            continue
        else:
            unknown.note(f"section:{tag}")

    return IPC2581Document(
        revision=revision,
        units_name=units_name,
        generator=generator,
        layers=tuple(layers),
        stackup=stackup,
        specs=tuple(specs),
        dictionary=dictionary,
        step=step,
        unknown_constructs=unknown.as_dict(),
    )


# --- section readers ----------------------------------------------------------
def _read_dictionaries(
    content: Element, unknown: _UnknownConstructs
) -> Iterator[tuple[str, IpcPrimitive]]:
    """Yield `(entry id, primitive)` pairs from every standard dictionary."""
    for child in content:
        if local_name(child.tag) != "DictionaryStandard":
            continue
        for entry in child:
            if local_name(entry.tag) != "EntryStandard":
                unknown.note(f"DictionaryStandard:{local_name(entry.tag)}")
                continue
            entry_id = _require_attr(entry, "id")
            primitive = _read_single_primitive(entry, unknown, where=f"EntryStandard {entry_id}")
            if primitive is not None:
                yield entry_id, primitive


def _read_generator(logistic_header: Element) -> str | None:
    """Best-effort generator identity from the logistic header."""
    enterprise: str | None = None
    for child in logistic_header:
        if local_name(child.tag) == "Enterprise":
            code = child.get("code")
            identifier = child.get("id")
            if code and code.upper() != "UNKNOWN":
                enterprise = code
            elif identifier and identifier.upper() not in ("UNKNOWN", "ENTERPRISE"):
                enterprise = identifier
    return enterprise


def _read_ecad(
    ecad: Element, unknown: _UnknownConstructs
) -> tuple[list[IpcLayer], IpcStackup | None, list[IpcSpec], IpcStep | None]:
    """Read layers, stackup, material specs and the step from `Ecad`."""
    layers: list[IpcLayer] = []
    stackup: IpcStackup | None = None
    specs: list[IpcSpec] = []
    step: IpcStep | None = None

    for child in ecad:
        tag = local_name(child.tag)
        if tag == "CadHeader":
            specs.extend(_read_spec(spec) for spec in child if local_name(spec.tag) == "Spec")
        elif tag == "CadData":
            for item in child:
                item_tag = local_name(item.tag)
                if item_tag == "Layer":
                    layers.append(
                        IpcLayer(
                            name=_require_attr(item, "name"),
                            layer_function=_require_attr(item, "layerFunction"),
                            side=item.get("side"),
                            polarity=item.get("polarity"),
                        )
                    )
                elif item_tag == "Stackup":
                    stackup = _read_stackup(item, unknown)
                elif item_tag == "Step":
                    step = _read_step(item, unknown)
                else:
                    unknown.note(f"CadData:{item_tag}")
    return layers, stackup, specs, step


def _read_spec(spec: Element) -> IpcSpec:
    """Read one material `Spec`; only the material name is meaningful today."""
    material_name: str | None = None
    for general in spec:
        if local_name(general.tag) != "General":
            continue
        if (general.get("type") or "").upper() != "MATERIAL":
            continue
        for prop in general:
            if local_name(prop.tag) == "Property":
                text = prop.get("text")
                if text:
                    material_name = text
    return IpcSpec(name=_require_attr(spec, "name"), material_name=material_name)


def _read_stackup(stackup: Element, unknown: _UnknownConstructs) -> IpcStackup:
    """Flatten stackup groups into an ordered entry list."""
    entries: list[IpcStackupLayer] = []
    for group in stackup:
        if local_name(group.tag) != "StackupGroup":
            unknown.note(f"Stackup:{local_name(group.tag)}")
            continue
        for row in group:
            if local_name(row.tag) != "StackupLayer":
                unknown.note(f"StackupGroup:{local_name(row.tag)}")
                continue
            entries.append(
                IpcStackupLayer(
                    layer_ref=_require_attr(row, "layerOrGroupRef"),
                    thickness=_optional_float(row, "thickness"),
                    sequence=_optional_int(row, "sequence"),
                )
            )
    return IpcStackup(
        total_thickness=_optional_float(stackup, "overallThickness"), entries=tuple(entries)
    )


def _read_step(step: Element, unknown: _UnknownConstructs) -> IpcStep:
    """Read one `Step`."""
    profile: IpcProfile | None = None
    padstacks: list[IpcPadStack] = []
    components: list[IpcComponent] = []
    layer_features: list[IpcLayerFeature] = []
    logical_nets: list[IpcLogicalNet] = []

    for child in step:
        tag = local_name(child.tag)
        if tag == "Profile":
            profile = _read_profile(child, unknown)
        elif tag == "PadStack":
            padstacks.append(_read_padstack(child, unknown))
        elif tag == "Component":
            components.append(_read_component(child))
        elif tag == "LayerFeature":
            layer_features.append(_read_layer_feature(child, unknown))
        elif tag == "LogicalNet":
            logical_nets.append(_read_logical_net(child))
        elif tag in ("Datum", "Package", "PhyNetGroup", "Route", "DfxMeasurementList"):
            # Datum is an editor origin; packages are assembly outlines; the
            # rest carry no conductive geometry read by this milestone.
            continue
        else:
            unknown.note(f"Step:{tag}")

    return IpcStep(
        name=_require_attr(step, "name"),
        profile=profile,
        padstacks=tuple(padstacks),
        components=tuple(components),
        layer_features=tuple(layer_features),
        logical_nets=tuple(logical_nets),
    )


def _read_profile(profile: Element, unknown: _UnknownConstructs) -> IpcProfile:
    """Read the board profile's outline polygons and cutouts."""
    polygons: list[IpcPolygon] = []
    cutouts: list[IpcPolygon] = []
    for child in profile:
        tag = local_name(child.tag)
        if tag == "Polygon":
            polygons.append(_read_polygon(child, where="Profile/Polygon"))
        elif tag == "Cutout":
            cutouts.append(_read_polygon(child, where="Profile/Cutout"))
        else:
            unknown.note(f"Profile:{tag}")
    return IpcProfile(polygons=tuple(polygons), cutouts=tuple(cutouts))


def _read_padstack(padstack: Element, unknown: _UnknownConstructs) -> IpcPadStack:
    """Read one padstack instance."""
    holes: list[IpcLayerHole] = []
    pads: list[IpcLayerPad] = []
    for child in padstack:
        tag = local_name(child.tag)
        if tag == "LayerHole":
            span: IpcSpan | None = None
            for sub in child:
                if local_name(sub.tag) == "Span":
                    span = IpcSpan(
                        from_layer=_require_attr(sub, "fromLayer"),
                        to_layer=_require_attr(sub, "toLayer"),
                    )
            holes.append(
                IpcLayerHole(
                    name=child.get("name"),
                    diameter=_require_float(child, "diameter"),
                    plating_status=(child.get("platingStatus") or "").upper(),
                    x=_require_float(child, "x"),
                    y=_require_float(child, "y"),
                    span=span,
                )
            )
        elif tag == "LayerPad":
            pads.append(_read_layer_pad(child, unknown))
        else:
            unknown.note(f"PadStack:{tag}")
    return IpcPadStack(net=padstack.get("net"), holes=tuple(holes), pads=tuple(pads))


def _read_layer_pad(layer_pad: Element, unknown: _UnknownConstructs) -> IpcLayerPad:
    """Read one per-layer pad of a padstack."""
    xform: IpcXform | None = None
    location: IpcPoint | None = None
    primitive: IpcPrimitive | None = None
    pin_ref: IpcPinRef | None = None
    for child in layer_pad:
        tag = local_name(child.tag)
        if tag == "Xform":
            xform = _read_xform(child)
        elif tag == "Location":
            location = IpcPoint(_require_float(child, "x"), _require_float(child, "y"))
        elif tag == "PinRef":
            pin_ref = IpcPinRef(
                component_ref=_require_attr(child, "componentRef"),
                pin=_require_attr(child, "pin"),
            )
        elif tag in _IGNORED_TAGS:
            continue
        else:
            candidate = _read_primitive(child, unknown, where="LayerPad")
            if candidate is not None:
                primitive = candidate
    return IpcLayerPad(
        layer_ref=_require_attr(layer_pad, "layerRef"),
        xform=xform,
        location=location,
        primitive=primitive,
        pin_ref=pin_ref,
    )


def _read_component(component: Element) -> IpcComponent:
    """Read one placed component."""
    xform: IpcXform | None = None
    location: IpcPoint | None = None
    for child in component:
        tag = local_name(child.tag)
        if tag == "Xform":
            xform = _read_xform(child)
        elif tag == "Location":
            location = IpcPoint(_require_float(child, "x"), _require_float(child, "y"))
    return IpcComponent(
        ref_des=_require_attr(component, "refDes"),
        package_ref=component.get("packageRef"),
        layer_ref=component.get("layerRef"),
        part=component.get("part"),
        mount_type=component.get("mountType"),
        xform=xform,
        location=location,
    )


def _read_logical_net(logical_net: Element) -> IpcLogicalNet:
    """Read one logical net declaration."""
    pins = tuple(
        IpcPin(
            component_ref=_require_attr(child, "componentRef"),
            pin=_require_attr(child, "pin"),
        )
        for child in logical_net
        if local_name(child.tag) == "PinRef"
    )
    return IpcLogicalNet(name=_require_attr(logical_net, "name"), pins=pins)


def _read_layer_feature(layer_feature: Element, unknown: _UnknownConstructs) -> IpcLayerFeature:
    """Read all sets of one layer's artwork."""
    sets: list[IpcSet] = []
    for child in layer_feature:
        if local_name(child.tag) != "Set":
            unknown.note(f"LayerFeature:{local_name(child.tag)}")
            continue
        sets.append(_read_set(child, unknown))
    return IpcLayerFeature(layer_ref=_require_attr(layer_feature, "layerRef"), sets=tuple(sets))


def _read_set(set_element: Element, unknown: _UnknownConstructs) -> IpcSet:
    """Read one net's features on one layer."""
    features: list[IpcFeature] = []
    holes: list[IpcHole] = []
    for child in set_element:
        tag = local_name(child.tag)
        if tag == "Features":
            features.append(_read_features(child, unknown))
        elif tag == "Hole":
            holes.append(
                IpcHole(
                    name=child.get("name") or "",
                    diameter=_require_float(child, "diameter"),
                    plating_status=(child.get("platingStatus") or "").upper(),
                    x=_require_float(child, "x"),
                    y=_require_float(child, "y"),
                )
            )
        elif tag in ("Pad", "Fiducial", "SlotCavity"):
            unknown.note(f"Set:{tag}")
        elif tag in _IGNORED_TAGS:
            continue
        else:
            unknown.note(f"Set:{tag}")
    return IpcSet(
        net=set_element.get("net"),
        polarity=set_element.get("polarity"),
        features=tuple(features),
        holes=tuple(holes),
    )


def _read_features(features: Element, unknown: _UnknownConstructs) -> IpcFeature:
    """Read one `Features` element: optional placement, then primitives."""
    xform: IpcXform | None = None
    location: IpcPoint | None = None
    primitives: list[IpcPrimitive] = []
    for child in features:
        tag = local_name(child.tag)
        if tag == "Xform":
            xform = _read_xform(child)
        elif tag == "Location":
            location = IpcPoint(_require_float(child, "x"), _require_float(child, "y"))
        elif tag == "UserSpecial":
            for sub in child:
                primitive = _read_primitive(sub, unknown, where="UserSpecial")
                if primitive is not None:
                    primitives.append(primitive)
        elif tag in _IGNORED_TAGS:
            continue
        else:
            primitive = _read_primitive(child, unknown, where="Features")
            if primitive is not None:
                primitives.append(primitive)
    return IpcFeature(xform=xform, location=location, primitives=tuple(primitives))


# --- primitive readers --------------------------------------------------------
def _read_single_primitive(
    parent: Element, unknown: _UnknownConstructs, where: str
) -> IpcPrimitive | None:
    """Read the one primitive child of `parent` (dictionary entries)."""
    for child in parent:
        primitive = _read_primitive(child, unknown, where=where)
        if primitive is not None:
            return primitive
    return None


def _read_primitive(
    element: Element, unknown: _UnknownConstructs, where: str
) -> IpcPrimitive | None:
    """Read one geometry primitive, or record it as unknown."""
    tag = local_name(element.tag)
    if tag == "Line":
        return IpcLine(
            start_x=_require_float(element, "startX"),
            start_y=_require_float(element, "startY"),
            end_x=_require_float(element, "endX"),
            end_y=_require_float(element, "endY"),
            line_desc=_read_line_desc(element),
        )
    if tag == "Arc":
        return IpcArc(
            start_x=_require_float(element, "startX"),
            start_y=_require_float(element, "startY"),
            end_x=_require_float(element, "endX"),
            end_y=_require_float(element, "endY"),
            center_x=_require_float(element, "centerX"),
            center_y=_require_float(element, "centerY"),
            clockwise=_require_bool(element, "clockwise"),
            line_desc=_read_line_desc(element),
        )
    if tag == "Polyline":
        begin, steps = _read_poly_steps(element, where=f"{where}/Polyline")
        return IpcPolyline(begin=begin, steps=steps, line_desc=_read_line_desc(element))
    if tag == "Contour":
        polygon: IpcPolygon | None = None
        cutouts: list[IpcPolygon] = []
        for child in element:
            child_tag = local_name(child.tag)
            if child_tag == "Polygon":
                polygon = _read_polygon(child, where=f"{where}/Contour")
            elif child_tag == "Cutout":
                cutouts.append(_read_polygon(child, where=f"{where}/Contour/Cutout"))
            else:
                unknown.note(f"Contour:{child_tag}")
        if polygon is None:
            raise MalformedSourceError(f"A Contour in {where} has no boundary Polygon")
        return IpcContour(polygon=polygon, cutouts=tuple(cutouts))
    if tag == "Polygon":
        # A bare filled polygon outside a Contour is legal in the standard.
        return IpcContour(polygon=_read_polygon(element, where=where), cutouts=())
    if tag == "Circle":
        return IpcCircle(diameter=_require_float(element, "diameter"))
    if tag == "RectCenter":
        return IpcRectCenter(
            width=_require_float(element, "width"), height=_require_float(element, "height")
        )
    if tag == "StandardPrimitiveRef":
        return IpcStandardPrimitiveRef(entry_id=_require_attr(element, "id"))
    if tag in _PLACEMENT_TAGS or tag in _IGNORED_TAGS or tag == "LineDesc":
        return None
    unknown.note(f"{where}:{tag}")
    return None


def _read_polygon(element: Element, where: str) -> IpcPolygon:
    """Read a `Polygon`/`Cutout` boundary."""
    begin, steps = _read_poly_steps(element, where=where)
    return IpcPolygon(begin=begin, steps=steps)


def _read_poly_steps(
    element: Element, where: str
) -> tuple[IpcPoint, tuple[IpcSegmentStep | IpcCurveStep, ...]]:
    """Read a `PolyBegin` and its following poly-steps."""
    begin: IpcPoint | None = None
    steps: list[IpcSegmentStep | IpcCurveStep] = []
    for child in element:
        tag = local_name(child.tag)
        if tag == "PolyBegin":
            begin = IpcPoint(_require_float(child, "x"), _require_float(child, "y"))
        elif tag == "PolyStepSegment":
            steps.append(IpcSegmentStep(_require_float(child, "x"), _require_float(child, "y")))
        elif tag == "PolyStepCurve":
            steps.append(
                IpcCurveStep(
                    x=_require_float(child, "x"),
                    y=_require_float(child, "y"),
                    center_x=_require_float(child, "centerX"),
                    center_y=_require_float(child, "centerY"),
                    clockwise=_require_bool(child, "clockwise"),
                )
            )
        elif tag == "LineDesc":
            continue
    if begin is None:
        raise MalformedSourceError(f"A polygon in {where} has no PolyBegin")
    return begin, tuple(steps)


def _read_line_desc(element: Element) -> IpcLineDesc | None:
    """Read the stroke description child, if any."""
    for child in element:
        if local_name(child.tag) == "LineDesc":
            width = _optional_float(child, "lineWidth")
            if width is None:
                return None
            return IpcLineDesc(line_width=width, line_end=(child.get("lineEnd") or "").upper())
    return None


def _read_xform(element: Element) -> IpcXform:
    """Read an instance transform."""
    return IpcXform(
        rotation_deg=_optional_float(element, "rotation") or 0.0,
        mirror=(element.get("mirror") or "").strip().lower() == "true",
        x_offset=_optional_float(element, "xOffset") or 0.0,
        y_offset=_optional_float(element, "yOffset") or 0.0,
    )


# --- attribute helpers ---------------------------------------------------------
def _require_attr(element: Element, name: str) -> str:
    """Return a required attribute or fail naming the element, not its content."""
    value = element.get(name)
    if value is None or not value.strip():
        raise MalformedSourceError(
            f"<{local_name(element.tag)}> is missing required attribute {name!r}"
        )
    return value


def _require_float(element: Element, name: str) -> float:
    """Return a required numeric attribute, refusing NaN and infinities."""
    raw = _require_attr(element, name)
    try:
        value = float(raw)
    except ValueError as exc:
        raise MalformedSourceError(
            f"<{local_name(element.tag)}> attribute {name!r} is not a number"
        ) from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise MalformedSourceError(
            f"<{local_name(element.tag)}> attribute {name!r} is not a finite number"
        )
    return value


def _optional_float(element: Element, name: str) -> float | None:
    """Return an optional numeric attribute, refusing non-finite values."""
    if element.get(name) is None:
        return None
    return _require_float(element, name)


def _optional_int(element: Element, name: str) -> int | None:
    """Return an optional integer attribute."""
    raw = element.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise MalformedSourceError(
            f"<{local_name(element.tag)}> attribute {name!r} is not an integer"
        ) from exc


def _require_bool(element: Element, name: str) -> bool:
    """Return a required boolean attribute."""
    raw = _require_attr(element, name).strip().lower()
    if raw in ("true", "1"):
        return True
    if raw in ("false", "0"):
        return False
    raise MalformedSourceError(f"<{local_name(element.tag)}> attribute {name!r} is not a boolean")
