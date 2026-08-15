"""The canonical board model.

This is openPDN's own description of a manufactured PCB. It is deliberately
*not* a rendering of any interchange format: IPC-2581, ODB++, Gerber and vendor
formats are all converted into this model by importers, and every solver
consumes this model. Adding an import format must never require touching solver
code (ADR-0002, ADR-0006).

The model describes the board *as manufactured*. How the board is electrically
exercised -- sources, loads, probes, mesh settings -- lives in `study.py` and is
never written back into these objects (ADR-0002, principle 1.4).

Unknown physical properties are represented as `Quantity` objects carrying
`Provenance.ASSUMED`, or as `None`; they are never silently defaulted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING, NewType

from openpdn.domain.errors import InvalidBoardError, MissingPhysicalPropertyError
from openpdn.domain.geometry import BoundingBox2D, Point2D, Polygon2D
from openpdn.domain.materials import Material
from openpdn.domain.provenance import Quantity
from openpdn.domain.units import METRE

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

BoardId = NewType("BoardId", str)
LayerId = NewType("LayerId", str)
NetId = NewType("NetId", str)
CopperRegionId = NewType("CopperRegionId", str)
ViaId = NewType("ViaId", str)
PadId = NewType("PadId", str)
ComponentId = NewType("ComponentId", str)
TerminalId = NewType("TerminalId", str)


class LayerFunction(StrEnum):
    """The electrical role of a stackup layer.

    Only `SIGNAL`, `PLANE` and `MIXED` layers conduct; dielectric and mask
    layers are retained because stackup order and thickness matter for future
    3-D and electrothermal analysis.
    """

    SIGNAL = "signal"
    PLANE = "plane"
    MIXED = "mixed"
    DIELECTRIC = "dielectric"
    SOLDER_MASK = "solder_mask"
    SILKSCREEN = "silkscreen"

    @property
    def is_conductive(self) -> bool:
        """True for layers that carry current."""
        return self in {LayerFunction.SIGNAL, LayerFunction.PLANE, LayerFunction.MIXED}


@dataclass(frozen=True, slots=True)
class Layer:
    """One physical layer of the stackup, ordered from the top of the board.

    Attributes:
        id: Stable identifier used by copper regions, pads and vias.
        name: Fabrication name, e.g. `"TOP"`, `"L2_GND"`.
        function: Electrical role of the layer.
        index: Position in the stackup, 0 at the top, increasing downwards.
        thickness: Finished thickness in metres. Frequently unknown in
            fabrication data, in which case it is an assumed quantity.
        material: Conductor material; `None` for non-conductive layers.
    """

    id: LayerId
    name: str
    function: LayerFunction
    index: int
    thickness: Quantity | None = None
    material: Material | None = None

    def __post_init__(self) -> None:
        """Validate layer consistency."""
        if self.index < 0:
            raise InvalidBoardError(f"Layer {self.name!r} has a negative stackup index")
        if self.thickness is not None:
            thickness_m = self.thickness.require_unit(METRE)
            if thickness_m <= 0.0:
                raise InvalidBoardError(f"Layer {self.name!r} has non-positive thickness")
        if self.function.is_conductive and self.material is None:
            raise InvalidBoardError(
                f"Conductive layer {self.name!r} has no material; "
                "importers must supply one explicitly, marking it assumed if unknown"
            )

    def require_thickness_m(self) -> float:
        """Return the layer thickness in metres, or fail loudly.

        Raises:
            MissingPhysicalPropertyError: If thickness is unknown. Solvers must
                surface this to the user rather than substituting a default.
        """
        if self.thickness is None:
            raise MissingPhysicalPropertyError(
                f"Layer {self.name!r} has no copper thickness; "
                "supply one in the study before solving"
            )
        return self.thickness.require_unit(METRE)


@dataclass(frozen=True)
class Stackup:
    """The ordered set of layers making up the board."""

    layers: tuple[Layer, ...]

    def __post_init__(self) -> None:
        """Validate layer identity and ordering."""
        if not self.layers:
            raise InvalidBoardError("A stackup needs at least one layer")
        ids = [layer.id for layer in self.layers]
        if len(set(ids)) != len(ids):
            raise InvalidBoardError("Stackup layer ids must be unique")
        indices = [layer.index for layer in self.layers]
        if indices != sorted(indices):
            raise InvalidBoardError("Stackup layers must be ordered top to bottom by index")
        if len(set(indices)) != len(indices):
            raise InvalidBoardError("Stackup layer indices must be unique")

    @cached_property
    def by_id(self) -> Mapping[LayerId, Layer]:
        """Layers keyed by id."""
        return {layer.id: layer for layer in self.layers}

    @cached_property
    def conductive_layers(self) -> tuple[Layer, ...]:
        """Layers that carry current, in stackup order."""
        return tuple(layer for layer in self.layers if layer.function.is_conductive)

    def layer(self, layer_id: LayerId) -> Layer:
        """Return the layer with `layer_id`."""
        try:
            return self.by_id[layer_id]
        except KeyError as exc:
            raise InvalidBoardError(f"Unknown layer id {layer_id!r}") from exc


@dataclass(frozen=True, slots=True)
class Net:
    """An electrical net as named in the fabrication data."""

    id: NetId
    name: str


@dataclass(frozen=True, slots=True)
class BoardProfile:
    """The manufactured outline of the board.

    `outlines` supports panelised or multi-piece boards; holes on an outline
    polygon are routed cutouts and non-plated through-holes. The profile bounds
    the meshing domain and gives the viewer its extents, so it is topology, not
    decoration.
    """

    outlines: tuple[Polygon2D, ...]

    def __post_init__(self) -> None:
        """Reject an empty profile; `Board.profile = None` says "unknown"."""
        if not self.outlines:
            raise InvalidBoardError("A board profile needs at least one outline polygon")

    @property
    def bounding_box(self) -> BoundingBox2D:
        """Extent of every outline."""
        merged = self.outlines[0].bounding_box
        for outline in self.outlines[1:]:
            merged = merged.merged_with(outline.bounding_box)
        return merged


@dataclass(frozen=True, slots=True)
class CopperRegion:
    """A contiguous piece of copper on one layer, belonging to one net.

    Regions are the raw conductive geometry. Before meshing they are normalised
    and grouped by `(net, layer)` -- that grouping is a solver-side concern, not
    a property of the imported board (see the `pcb-domain-model` skill).

    `net_id` is `None` for copper the fabrication data leaves unassigned --
    real boards carry netless copper (fiducials, text, orphaned fills), and
    recording it as belonging to some invented net would be fabrication.

    `source_ref` is an importer-chosen identifier of the source feature this
    region was resolved from (any importer can fill it in its own vocabulary).
    It exists so a reviewer can ask "why is this copper here?" and get an
    answer that points back into the source document.
    """

    id: CopperRegionId
    net_id: NetId | None
    layer_id: LayerId
    outline: Polygon2D
    thickness: Quantity | None = None
    source_ref: str | None = None

    def __post_init__(self) -> None:
        """Validate the optional per-region thickness override."""
        if self.thickness is not None and self.thickness.require_unit(METRE) <= 0.0:
            raise InvalidBoardError(f"Copper region {self.id!r} has non-positive thickness")

    @property
    def area_m2(self) -> float:
        """Copper area of the region, in m^2."""
        return self.outline.area_m2


@dataclass(frozen=True, slots=True)
class Via:
    """A plated through-hole or microvia connecting two layers.

    Attributes:
        id: Stable identifier.
        net_id: Net the via belongs to; `None` for a via the fabrication data
            leaves unassigned.
        from_layer_id: Upper connected conductive layer.
        to_layer_id: Lower connected conductive layer.
        position: Centre of the barrel in board coordinates.
        drill_diameter: Tool diameter the hole was drilled with, in metres.
        finished_hole_diameter: Hole diameter after plating, in metres.
        plating_thickness: Barrel copper wall thickness in metres. Almost never
            present in fabrication data -- expect an assumed quantity here.
        padstack_name: Fabrication-data padstack this via instantiates, kept
            verbatim so vias can be grouped and cross-referenced for review.
    """

    id: ViaId
    net_id: NetId | None
    from_layer_id: LayerId
    to_layer_id: LayerId
    position: Point2D
    drill_diameter: Quantity | None = None
    finished_hole_diameter: Quantity | None = None
    plating_thickness: Quantity | None = None
    padstack_name: str | None = None

    def __post_init__(self) -> None:
        """Validate via geometry."""
        if self.from_layer_id == self.to_layer_id:
            raise InvalidBoardError(f"Via {self.id!r} connects a layer to itself")
        for quantity, label in (
            (self.drill_diameter, "drill diameter"),
            (self.finished_hole_diameter, "finished hole diameter"),
            (self.plating_thickness, "plating thickness"),
        ):
            if quantity is not None and quantity.require_unit(METRE) <= 0.0:
                raise InvalidBoardError(f"Via {self.id!r} has non-positive {label}")

    def require_barrel_cross_section_m2(self) -> float:
        """Return the annular copper cross-section of the barrel, in m^2.

        Raises:
            MissingPhysicalPropertyError: If hole diameter or plating thickness
                is unknown.
        """
        if self.finished_hole_diameter is None or self.plating_thickness is None:
            raise MissingPhysicalPropertyError(
                f"Via {self.id!r} lacks hole diameter or plating thickness; "
                "supply both in the study before solving"
            )
        inner_radius_m = 0.5 * self.finished_hole_diameter.require_unit(METRE)
        outer_radius_m = inner_radius_m + self.plating_thickness.require_unit(METRE)
        # Annulus area; the barrel wall is the only conductor in a plated hole.
        return math.pi * (outer_radius_m**2 - inner_radius_m**2)


@dataclass(frozen=True, slots=True)
class Pad:
    """A soldered land on one layer, optionally attached to a net."""

    id: PadId
    layer_id: LayerId
    position: Point2D
    net_id: NetId | None = None
    outline: Polygon2D | None = None


@dataclass(frozen=True, slots=True)
class Terminal:
    """An electrical connection point where a study may apply a source or load.

    A terminal is where the board meets the outside world: a connector contact,
    a regulator output pin, a load pin. It is part of the *board* -- what is
    driven through it is part of the *study*.
    """

    id: TerminalId
    name: str
    net_id: NetId
    pad_ids: tuple[PadId, ...] = ()
    component_id: ComponentId | None = None


@dataclass(frozen=True, slots=True)
class PhysicalComponent:
    """A placed component. Named `PhysicalComponent` to avoid UI "component" clashes."""

    id: ComponentId
    reference_designator: str
    terminal_ids: tuple[TerminalId, ...] = ()
    part_number: str | None = None


@dataclass(frozen=True, slots=True)
class ImportProvenance:
    """Where a board came from, recorded for reproducibility and diagnostics."""

    importer: str
    source_format: str
    source_name: str
    source_digest: str | None = None


@dataclass(frozen=True)
class Board:
    """The canonical, solver-independent description of one manufactured PCB."""

    id: BoardId
    name: str
    stackup: Stackup
    profile: BoardProfile | None = None
    nets: tuple[Net, ...] = ()
    copper_regions: tuple[CopperRegion, ...] = ()
    vias: tuple[Via, ...] = ()
    pads: tuple[Pad, ...] = ()
    terminals: tuple[Terminal, ...] = ()
    components: tuple[PhysicalComponent, ...] = ()
    provenance: ImportProvenance | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate referential integrity across the board."""
        _require_unique("net", [net.id for net in self.nets])
        _require_unique("copper region", [region.id for region in self.copper_regions])
        _require_unique("via", [via.id for via in self.vias])
        _require_unique("pad", [pad.id for pad in self.pads])
        _require_unique("terminal", [terminal.id for terminal in self.terminals])
        _require_unique("component", [component.id for component in self.components])

        net_ids = {net.id for net in self.nets}
        layer_ids = {layer.id for layer in self.stackup.layers}
        pad_ids = {pad.id for pad in self.pads}

        for region in self.copper_regions:
            if region.net_id is not None:
                _require_member(region.net_id, net_ids, f"copper region {region.id!r}", "net")
            _require_member(region.layer_id, layer_ids, f"copper region {region.id!r}", "layer")
        for via in self.vias:
            if via.net_id is not None:
                _require_member(via.net_id, net_ids, f"via {via.id!r}", "net")
            _require_member(via.from_layer_id, layer_ids, f"via {via.id!r}", "layer")
            _require_member(via.to_layer_id, layer_ids, f"via {via.id!r}", "layer")
        for pad in self.pads:
            _require_member(pad.layer_id, layer_ids, f"pad {pad.id!r}", "layer")
            if pad.net_id is not None:
                _require_member(pad.net_id, net_ids, f"pad {pad.id!r}", "net")
        for terminal in self.terminals:
            _require_member(terminal.net_id, net_ids, f"terminal {terminal.id!r}", "net")
            for pad_id in terminal.pad_ids:
                _require_member(pad_id, pad_ids, f"terminal {terminal.id!r}", "pad")

    @cached_property
    def nets_by_id(self) -> Mapping[NetId, Net]:
        """Nets keyed by id."""
        return {net.id: net for net in self.nets}

    @cached_property
    def terminals_by_id(self) -> Mapping[TerminalId, Terminal]:
        """Terminals keyed by id."""
        return {terminal.id: terminal for terminal in self.terminals}

    @cached_property
    def bounding_box(self) -> BoundingBox2D | None:
        """Extent of the board.

        The manufactured profile is authoritative when known; otherwise the
        union of all copper stands in. `None` for a board with neither.
        """
        if self.profile is not None:
            return self.profile.bounding_box
        boxes = [region.outline.bounding_box for region in self.copper_regions]
        if not boxes:
            return None
        merged = boxes[0]
        for box in boxes[1:]:
            merged = merged.merged_with(box)
        return merged

    def net(self, net_id: NetId) -> Net:
        """Return the net with `net_id`."""
        try:
            return self.nets_by_id[net_id]
        except KeyError as exc:
            raise InvalidBoardError(f"Unknown net id {net_id!r}") from exc

    def terminal(self, terminal_id: TerminalId) -> Terminal:
        """Return the terminal with `terminal_id`."""
        try:
            return self.terminals_by_id[terminal_id]
        except KeyError as exc:
            raise InvalidBoardError(f"Unknown terminal id {terminal_id!r}") from exc

    def copper_regions_on(
        self, net_id: NetId | None, layer_id: LayerId
    ) -> tuple[CopperRegion, ...]:
        """Return the copper of one net on one layer.

        `(net, layer)` is the grouping a 2.5-D sheet solver meshes over.
        `net_id=None` selects the copper the fabrication data left unassigned.
        """
        return tuple(
            region
            for region in self.copper_regions
            if region.net_id == net_id and region.layer_id == layer_id
        )

    def vias_on_net(self, net_id: NetId | None) -> tuple[Via, ...]:
        """Return every via belonging to `net_id`."""
        return tuple(via for via in self.vias if via.net_id == net_id)


def _require_unique(label: str, values: Sequence[str]) -> None:
    """Raise if `values` contains duplicates."""
    if len(set(values)) != len(values):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        raise InvalidBoardError(f"Duplicate {label} ids: {duplicates}")


def _require_member(value: str, allowed: Iterable[str], owner: str, kind: str) -> None:
    """Raise if `value` is not present in `allowed`."""
    if value not in set(allowed):
        raise InvalidBoardError(f"{owner} references unknown {kind} id {value!r}")
