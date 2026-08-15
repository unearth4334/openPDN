"""Conversion between the canonical `Board` model and plain JSON documents.

Pure functions over already-parsed dictionaries: no file access here, so the
mapping can be unit-tested without a filesystem, and so file-level concerns
(size limits, untrusted paths) stay in `importer.py`.

All lengths in the document are metres, matching the domain. The format is
versioned by `CANONICAL_FORMAT_VERSION`; readers reject unknown versions rather
than guessing.
"""

from __future__ import annotations

from typing import Any, Final

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
from openpdn.domain.materials import Material
from openpdn.domain.provenance import Provenance, Quantity
from openpdn.pcb_import.api import MalformedSourceError

CANONICAL_FORMAT_VERSION: Final = 1

_DOCUMENT_KEY: Final = "openpdn_canonical_board"


# --- decoding ---------------------------------------------------------------
def board_from_document(document: Any, *, source_name: str, digest: str | None = None) -> Board:
    """Build a `Board` from a parsed canonical-JSON document.

    Args:
        document: The parsed JSON value; validated here, not trusted.
        source_name: Name recorded in the board's import provenance.
        digest: Optional content hash recorded for reproducibility.

    Raises:
        MalformedSourceError: If the document is not a well-formed canonical
            board, or if it violates a domain invariant.
    """
    root = _require_mapping(document, "document")
    version = root.get(_DOCUMENT_KEY)
    if version != CANONICAL_FORMAT_VERSION:
        raise MalformedSourceError(
            f"Expected {_DOCUMENT_KEY}={CANONICAL_FORMAT_VERSION}, got {version!r}"
        )
    body = _require_mapping(root.get("board"), "board")

    try:
        stackup = Stackup(
            tuple(
                _layer_from(_require_mapping(item, "layer"), index)
                for index, item in enumerate(_require_list(body.get("stackup"), "stackup"))
            )
        )
        board = Board(
            id=BoardId(_require_str(body.get("id"), "board.id")),
            name=_require_str(body.get("name"), "board.name"),
            stackup=stackup,
            profile=_profile_from(body.get("profile")),
            nets=tuple(
                Net(
                    id=NetId(_require_str(item.get("id"), "net.id")),
                    name=_require_str(item.get("name"), "net.name"),
                )
                for item in _mappings(body.get("nets"), "nets")
            ),
            copper_regions=tuple(
                _copper_region_from(item)
                for item in _mappings(body.get("copper_regions"), "copper_regions")
            ),
            vias=tuple(_via_from(item) for item in _mappings(body.get("vias"), "vias")),
            pads=tuple(_pad_from(item) for item in _mappings(body.get("pads"), "pads")),
            terminals=tuple(
                _terminal_from(item) for item in _mappings(body.get("terminals"), "terminals")
            ),
            components=tuple(
                _component_from(item) for item in _mappings(body.get("components"), "components")
            ),
            provenance=ImportProvenance(
                importer="canonical-json",
                source_format="openPDN canonical JSON",
                source_name=source_name,
                source_digest=digest,
            ),
            notes=tuple(_require_str(note, "note") for note in _optional_list(body.get("notes"))),
        )
    except DomainError as exc:
        # A structurally valid document can still describe an impossible board;
        # that is a source defect, not an internal error.
        raise MalformedSourceError(f"Inconsistent board document: {exc}") from exc
    return board


def _layer_from(item: dict[str, Any], fallback_index: int) -> Layer:
    """Decode one stackup layer."""
    function = _require_str(item.get("function"), "layer.function")
    try:
        layer_function = LayerFunction(function)
    except ValueError as exc:
        raise MalformedSourceError(f"Unknown layer function {function!r}") from exc
    return Layer(
        id=LayerId(_require_str(item.get("id"), "layer.id")),
        name=_require_str(item.get("name"), "layer.name"),
        function=layer_function,
        index=int(item.get("index", fallback_index)),
        thickness=_optional_quantity(item.get("thickness"), "layer.thickness"),
        material=_optional_material(item.get("material")),
    )


def _profile_from(value: Any) -> BoardProfile | None:
    """Decode the optional board profile."""
    if value is None:
        return None
    item = _require_mapping(value, "profile")
    outlines = tuple(
        _polygon_from(_require_mapping(outline, "profile.outline"))
        for outline in _require_list(item.get("outlines"), "profile.outlines")
    )
    return BoardProfile(outlines=outlines)


def _copper_region_from(item: dict[str, Any]) -> CopperRegion:
    """Decode one copper region."""
    net_id = item.get("net_id")
    source_ref = item.get("source_ref")
    return CopperRegion(
        id=CopperRegionId(_require_str(item.get("id"), "copper_region.id")),
        net_id=NetId(str(net_id)) if net_id is not None else None,
        layer_id=LayerId(_require_str(item.get("layer_id"), "copper_region.layer_id")),
        outline=_polygon_from(_require_mapping(item.get("outline"), "copper_region.outline")),
        thickness=_optional_quantity(item.get("thickness"), "copper_region.thickness"),
        source_ref=str(source_ref) if source_ref is not None else None,
    )


def _via_from(item: dict[str, Any]) -> Via:
    """Decode one via."""
    net_id = item.get("net_id")
    padstack_name = item.get("padstack_name")
    return Via(
        id=ViaId(_require_str(item.get("id"), "via.id")),
        net_id=NetId(str(net_id)) if net_id is not None else None,
        from_layer_id=LayerId(_require_str(item.get("from_layer_id"), "via.from_layer_id")),
        to_layer_id=LayerId(_require_str(item.get("to_layer_id"), "via.to_layer_id")),
        position=_point_from(item.get("position"), "via.position"),
        drill_diameter=_optional_quantity(item.get("drill_diameter"), "via.drill_diameter"),
        finished_hole_diameter=_optional_quantity(
            item.get("finished_hole_diameter"), "via.finished_hole_diameter"
        ),
        plating_thickness=_optional_quantity(
            item.get("plating_thickness"), "via.plating_thickness"
        ),
        padstack_name=str(padstack_name) if padstack_name is not None else None,
    )


def _pad_from(item: dict[str, Any]) -> Pad:
    """Decode one pad."""
    net_id = item.get("net_id")
    outline = item.get("outline")
    return Pad(
        id=PadId(_require_str(item.get("id"), "pad.id")),
        layer_id=LayerId(_require_str(item.get("layer_id"), "pad.layer_id")),
        position=_point_from(item.get("position"), "pad.position"),
        net_id=NetId(str(net_id)) if net_id is not None else None,
        outline=_polygon_from(_require_mapping(outline, "pad.outline")) if outline else None,
    )


def _terminal_from(item: dict[str, Any]) -> Terminal:
    """Decode one terminal."""
    component_id = item.get("component_id")
    return Terminal(
        id=TerminalId(_require_str(item.get("id"), "terminal.id")),
        name=_require_str(item.get("name"), "terminal.name"),
        net_id=NetId(_require_str(item.get("net_id"), "terminal.net_id")),
        pad_ids=tuple(PadId(str(value)) for value in _optional_list(item.get("pad_ids"))),
        component_id=ComponentId(str(component_id)) if component_id is not None else None,
    )


def _component_from(item: dict[str, Any]) -> PhysicalComponent:
    """Decode one placed component."""
    part_number = item.get("part_number")
    return PhysicalComponent(
        id=ComponentId(_require_str(item.get("id"), "component.id")),
        reference_designator=_require_str(
            item.get("reference_designator"), "component.reference_designator"
        ),
        terminal_ids=tuple(
            TerminalId(str(value)) for value in _optional_list(item.get("terminal_ids"))
        ),
        part_number=str(part_number) if part_number is not None else None,
    )


def _polygon_from(item: dict[str, Any]) -> Polygon2D:
    """Decode a polygon given as coordinate rings."""
    exterior = [_coordinate_pair(pair) for pair in _require_list(item.get("exterior"), "exterior")]
    holes = [
        [_coordinate_pair(pair) for pair in _require_list(hole, "hole")]
        for hole in _optional_list(item.get("holes"))
    ]
    try:
        return Polygon2D.from_coordinates(exterior, holes)
    except DomainError as exc:
        raise MalformedSourceError(f"Invalid polygon: {exc}") from exc


def _point_from(value: Any, field_name: str) -> Point2D:
    """Decode a point given as a two-element coordinate list."""
    x_m, y_m = _coordinate_pair(_require_list(value, field_name))
    return Point2D(x_m, y_m)


def _coordinate_pair(value: Any) -> tuple[float, float]:
    """Decode a `[x, y]` pair in metres."""
    pair = _require_list(value, "coordinate")
    if len(pair) != 2:
        raise MalformedSourceError(f"Expected a coordinate pair, got {len(pair)} values")
    return _require_float(pair[0], "x_m"), _require_float(pair[1], "y_m")


def _optional_quantity(value: Any, field_name: str) -> Quantity | None:
    """Decode an optional provenance-tagged quantity."""
    if value is None:
        return None
    item = _require_mapping(value, field_name)
    provenance = _require_str(item.get("provenance"), f"{field_name}.provenance")
    try:
        return Quantity(
            value=_require_float(item.get("value"), f"{field_name}.value"),
            unit=_require_str(item.get("unit"), f"{field_name}.unit"),
            provenance=Provenance(provenance),
            note=None if item.get("note") is None else str(item["note"]),
        )
    except ValueError as exc:
        raise MalformedSourceError(f"Unknown provenance {provenance!r}") from exc
    except DomainError as exc:
        raise MalformedSourceError(f"Invalid quantity at {field_name}: {exc}") from exc


def _optional_material(value: Any) -> Material | None:
    """Decode an optional conductor material."""
    if value is None:
        return None
    item = _require_mapping(value, "material")
    coefficient = item.get("temperature_coefficient_per_k")
    try:
        return Material(
            name=_require_str(item.get("name"), "material.name"),
            conductivity_s_per_m=_require_float(
                item.get("conductivity_s_per_m"), "material.conductivity_s_per_m"
            ),
            temperature_coefficient_per_k=(
                None if coefficient is None else _require_float(coefficient, "material.tcr")
            ),
            reference_temperature_k=_require_float(
                item.get("reference_temperature_k", 293.15), "material.reference_temperature_k"
            ),
        )
    except DomainError as exc:
        raise MalformedSourceError(f"Invalid material: {exc}") from exc


# --- encoding ---------------------------------------------------------------
def board_to_document(board: Board) -> dict[str, Any]:
    """Serialise `board` into a canonical-JSON document.

    Round-trips through `board_from_document`, which is what makes the format
    usable for regression fixtures.
    """
    return {
        _DOCUMENT_KEY: CANONICAL_FORMAT_VERSION,
        "board": {
            "id": str(board.id),
            "name": board.name,
            "stackup": [
                {
                    "id": str(layer.id),
                    "name": layer.name,
                    "function": layer.function.value,
                    "index": layer.index,
                    "thickness": _quantity_to_document(layer.thickness),
                    "material": _material_to_document(layer.material),
                }
                for layer in board.stackup.layers
            ],
            "profile": (
                None
                if board.profile is None
                else {
                    "outlines": [
                        _polygon_to_document(outline) for outline in board.profile.outlines
                    ]
                }
            ),
            "nets": [{"id": str(net.id), "name": net.name} for net in board.nets],
            "copper_regions": [
                {
                    "id": str(region.id),
                    "net_id": None if region.net_id is None else str(region.net_id),
                    "layer_id": str(region.layer_id),
                    "outline": _polygon_to_document(region.outline),
                    "thickness": _quantity_to_document(region.thickness),
                    "source_ref": region.source_ref,
                }
                for region in board.copper_regions
            ],
            "vias": [
                {
                    "id": str(via.id),
                    "net_id": None if via.net_id is None else str(via.net_id),
                    "from_layer_id": str(via.from_layer_id),
                    "to_layer_id": str(via.to_layer_id),
                    "position": [via.position.x_m, via.position.y_m],
                    "drill_diameter": _quantity_to_document(via.drill_diameter),
                    "finished_hole_diameter": _quantity_to_document(via.finished_hole_diameter),
                    "plating_thickness": _quantity_to_document(via.plating_thickness),
                    "padstack_name": via.padstack_name,
                }
                for via in board.vias
            ],
            "pads": [
                {
                    "id": str(pad.id),
                    "layer_id": str(pad.layer_id),
                    "position": [pad.position.x_m, pad.position.y_m],
                    "net_id": None if pad.net_id is None else str(pad.net_id),
                    "outline": None if pad.outline is None else _polygon_to_document(pad.outline),
                }
                for pad in board.pads
            ],
            "terminals": [
                {
                    "id": str(terminal.id),
                    "name": terminal.name,
                    "net_id": str(terminal.net_id),
                    "pad_ids": [str(pad_id) for pad_id in terminal.pad_ids],
                    "component_id": (
                        None if terminal.component_id is None else str(terminal.component_id)
                    ),
                }
                for terminal in board.terminals
            ],
            "components": [
                {
                    "id": str(component.id),
                    "reference_designator": component.reference_designator,
                    "terminal_ids": [str(value) for value in component.terminal_ids],
                    "part_number": component.part_number,
                }
                for component in board.components
            ],
            "notes": list(board.notes),
        },
    }


def _quantity_to_document(quantity: Quantity | None) -> dict[str, Any] | None:
    """Serialise an optional quantity, preserving provenance."""
    if quantity is None:
        return None
    return {
        "value": quantity.value,
        "unit": quantity.unit,
        "provenance": quantity.provenance.value,
        "note": quantity.note,
    }


def _material_to_document(material: Material | None) -> dict[str, Any] | None:
    """Serialise an optional material."""
    if material is None:
        return None
    return {
        "name": material.name,
        "conductivity_s_per_m": material.conductivity_s_per_m,
        "temperature_coefficient_per_k": material.temperature_coefficient_per_k,
        "reference_temperature_k": material.reference_temperature_k,
    }


def _polygon_to_document(polygon: Polygon2D) -> dict[str, Any]:
    """Serialise a polygon as coordinate rings."""
    return {
        "exterior": [[point.x_m, point.y_m] for point in polygon.exterior],
        "holes": [[[point.x_m, point.y_m] for point in hole] for hole in polygon.holes],
    }


# --- validation helpers -----------------------------------------------------
def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    """Return `value` as a mapping or fail."""
    if not isinstance(value, dict):
        raise MalformedSourceError(f"Expected an object at {field_name}")
    return value


def _require_list(value: Any, field_name: str) -> list[Any]:
    """Return `value` as a list or fail."""
    if not isinstance(value, list):
        raise MalformedSourceError(f"Expected an array at {field_name}")
    return value


def _optional_list(value: Any) -> list[Any]:
    """Return `value` as a list, treating absence as empty."""
    if value is None:
        return []
    return _require_list(value, "list")


def _mappings(value: Any, field_name: str) -> list[dict[str, Any]]:
    """Return `value` as a list of mappings, treating absence as empty."""
    if value is None:
        return []
    return [_require_mapping(item, field_name) for item in _require_list(value, field_name)]


def _require_str(value: Any, field_name: str) -> str:
    """Return `value` as a non-empty string or fail."""
    if not isinstance(value, str) or not value:
        raise MalformedSourceError(f"Expected a non-empty string at {field_name}")
    return value


def _require_float(value: Any, field_name: str) -> float:
    """Return `value` as a float or fail."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MalformedSourceError(f"Expected a number at {field_name}")
    return float(value)
