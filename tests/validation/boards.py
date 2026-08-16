"""Synthetic boards with analytically known electrical behaviour.

Builders here construct minimal canonical boards -- traces, plates, vias --
whose resistances are computable by hand from `analytical.py`. They exist so
FEM validation tests read as physics statements, not as board-construction
boilerplate.

Conventions:

* terminals get full-width rectangular pads so the equipotential terminal
  region coincides with an equipotential line of the exact 1-D solution --
  the analytical trace formula then applies between pad inner edges exactly;
* all dimensions in metres, SI throughout (ADR-0004).
"""

from __future__ import annotations

from openpdn.domain.board import (
    Board,
    BoardId,
    CopperRegion,
    CopperRegionId,
    Layer,
    LayerFunction,
    LayerId,
    Net,
    NetId,
    Pad,
    PadId,
    Stackup,
    Terminal,
    TerminalId,
    Via,
    ViaId,
)
from openpdn.domain.geometry import Point2D, Polygon2D
from openpdn.domain.materials import COPPER_ANNEALED
from openpdn.domain.provenance import Quantity
from openpdn.domain.units import METRE

NET = NetId("net-dut")

#: Standard 1 oz-ish copper thickness used across the validation boards.
COPPER_T_M = 35e-6

#: Standard dielectric separation between adjacent copper layers.
DIELECTRIC_T_M = 0.5e-3


def conductive_layer(layer_id: str, index: int, thickness_m: float = COPPER_T_M) -> Layer:
    """A copper layer with imported thickness and standard copper."""
    return Layer(
        id=LayerId(layer_id),
        name=layer_id,
        function=LayerFunction.SIGNAL,
        index=index,
        thickness=Quantity.imported(thickness_m, METRE),
        material=COPPER_ANNEALED,
    )


def dielectric_layer(layer_id: str, index: int, thickness_m: float = DIELECTRIC_T_M) -> Layer:
    """A dielectric layer with imported thickness."""
    return Layer(
        id=LayerId(layer_id),
        name=layer_id,
        function=LayerFunction.DIELECTRIC,
        index=index,
        thickness=Quantity.imported(thickness_m, METRE),
    )


def rect_region(
    region_id: str, layer_id: str, x0: float, y0: float, width: float, height: float
) -> CopperRegion:
    """An axis-aligned rectangular copper region on `layer_id`."""
    return CopperRegion(
        id=CopperRegionId(region_id),
        net_id=NET,
        layer_id=LayerId(layer_id),
        outline=Polygon2D.rectangle(Point2D(x0, y0), width, height),
    )


def rect_pad_terminal(
    name: str,
    layer_id: str,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> tuple[Pad, Terminal]:
    """A rectangular pad plus the terminal that owns it."""
    pad = Pad(
        id=PadId(f"pad-{name}"),
        layer_id=LayerId(layer_id),
        position=Point2D(x0 + width / 2.0, y0 + height / 2.0),
        net_id=NET,
        outline=Polygon2D.rectangle(Point2D(x0, y0), width, height),
    )
    terminal = Terminal(
        id=TerminalId(f"term-{name}"),
        name=name,
        net_id=NET,
        pad_ids=(pad.id,),
    )
    return pad, terminal


def straight_trace_board(
    *,
    length_between_pads_m: float,
    width_m: float,
    pad_length_m: float = 1e-3,
    thickness_m: float = COPPER_T_M,
) -> Board:
    """One straight trace with full-width terminal pads at each end.

    The conduction length between the equipotential pad inner edges is
    exactly `length_between_pads_m`, so `R = L / (sigma w t)` applies with no
    geometric ambiguity.
    """
    total = length_between_pads_m + 2.0 * pad_length_m
    pad_a, term_a = rect_pad_terminal("a", "L1", 0.0, 0.0, pad_length_m, width_m)
    pad_b, term_b = rect_pad_terminal("b", "L1", total - pad_length_m, 0.0, pad_length_m, width_m)
    return Board(
        id=BoardId("val-trace"),
        name="validation straight trace",
        stackup=Stackup((conductive_layer("L1", 0, thickness_m),)),
        nets=(Net(id=NET, name="DUT"),),
        copper_regions=(rect_region("trace", "L1", 0.0, 0.0, total, width_m),),
        pads=(pad_a, pad_b),
        terminals=(term_a, term_b),
    )


def parallel_traces_board(
    *,
    length_between_pads_m: float,
    width_1_m: float,
    width_2_m: float,
    pad_length_m: float = 1e-3,
) -> Board:
    """Two disjoint traces joined only through shared multi-pad terminals.

    Terminal A owns one full-width pad on each trace (and likewise B), so the
    terminals behave as ideal equipotential bus bars and the network is two
    resistances exactly in parallel.
    """
    total = length_between_pads_m + 2.0 * pad_length_m
    gap = 2e-3  # lateral separation; only terminals connect the traces
    y2 = width_1_m + gap

    pad_a1 = Pad(
        id=PadId("pad-a1"),
        layer_id=LayerId("L1"),
        position=Point2D(pad_length_m / 2, width_1_m / 2),
        net_id=NET,
        outline=Polygon2D.rectangle(Point2D(0.0, 0.0), pad_length_m, width_1_m),
    )
    pad_a2 = Pad(
        id=PadId("pad-a2"),
        layer_id=LayerId("L1"),
        position=Point2D(pad_length_m / 2, y2 + width_2_m / 2),
        net_id=NET,
        outline=Polygon2D.rectangle(Point2D(0.0, y2), pad_length_m, width_2_m),
    )
    pad_b1 = Pad(
        id=PadId("pad-b1"),
        layer_id=LayerId("L1"),
        position=Point2D(total - pad_length_m / 2, width_1_m / 2),
        net_id=NET,
        outline=Polygon2D.rectangle(Point2D(total - pad_length_m, 0.0), pad_length_m, width_1_m),
    )
    pad_b2 = Pad(
        id=PadId("pad-b2"),
        layer_id=LayerId("L1"),
        position=Point2D(total - pad_length_m / 2, y2 + width_2_m / 2),
        net_id=NET,
        outline=Polygon2D.rectangle(Point2D(total - pad_length_m, y2), pad_length_m, width_2_m),
    )
    term_a = Terminal(id=TerminalId("term-a"), name="A", net_id=NET, pad_ids=(pad_a1.id, pad_a2.id))
    term_b = Terminal(id=TerminalId("term-b"), name="B", net_id=NET, pad_ids=(pad_b1.id, pad_b2.id))
    return Board(
        id=BoardId("val-parallel"),
        name="validation parallel traces",
        stackup=Stackup((conductive_layer("L1", 0),)),
        nets=(Net(id=NET, name="DUT"),),
        copper_regions=(
            rect_region("trace-1", "L1", 0.0, 0.0, total, width_1_m),
            rect_region("trace-2", "L1", 0.0, y2, total, width_2_m),
        ),
        pads=(pad_a1, pad_a2, pad_b1, pad_b2),
        terminals=(term_a, term_b),
    )


def split_terminal_parallel_traces_board(
    *,
    length_between_pads_m: float,
    width_1_m: float,
    width_2_m: float,
    pad_length_m: float = 1e-3,
) -> Board:
    """Two disjoint traces, each pad its own single-pad terminal.

    Identical geometry to `parallel_traces_board`, but where that board wires
    each side's two pads into one multi-pad `Terminal` (exercising the
    existing per-terminal pad union), this board keeps all four pads as four
    independent terminals -- so a source/load attachment *group* naming two
    of them is the only thing that can put them on one equipotential DOF.
    """
    total = length_between_pads_m + 2.0 * pad_length_m
    gap = 2e-3
    y2 = width_1_m + gap

    pad_a1, term_a1 = rect_pad_terminal("a1", "L1", 0.0, 0.0, pad_length_m, width_1_m)
    pad_a2, term_a2 = rect_pad_terminal("a2", "L1", 0.0, y2, pad_length_m, width_2_m)
    pad_b1, term_b1 = rect_pad_terminal(
        "b1", "L1", total - pad_length_m, 0.0, pad_length_m, width_1_m
    )
    pad_b2, term_b2 = rect_pad_terminal(
        "b2", "L1", total - pad_length_m, y2, pad_length_m, width_2_m
    )
    return Board(
        id=BoardId("val-split-parallel"),
        name="validation split-terminal parallel traces",
        stackup=Stackup((conductive_layer("L1", 0),)),
        nets=(Net(id=NET, name="DUT"),),
        copper_regions=(
            rect_region("trace-1", "L1", 0.0, 0.0, total, width_1_m),
            rect_region("trace-2", "L1", 0.0, y2, total, width_2_m),
        ),
        pads=(pad_a1, pad_a2, pad_b1, pad_b2),
        terminals=(term_a1, term_a2, term_b1, term_b2),
    )


def series_widths_board(
    *,
    length_each_m: float,
    width_wide_m: float,
    width_narrow_m: float,
    pad_length_m: float = 1e-3,
) -> Board:
    """A wide segment and a narrow segment in series, sharing a bottom edge.

    The 1-D analytical series sum ignores the constriction where the width
    steps; the FEM includes it, so the computed resistance must exceed the
    1-D sum slightly. Tests assert both the closeness and the sign.
    """
    total = 2.0 * length_each_m + 2.0 * pad_length_m
    x_step = pad_length_m + length_each_m
    pad_a, term_a = rect_pad_terminal("a", "L1", 0.0, 0.0, pad_length_m, width_wide_m)
    pad_b, term_b = rect_pad_terminal(
        "b", "L1", total - pad_length_m, 0.0, pad_length_m, width_narrow_m
    )
    return Board(
        id=BoardId("val-series"),
        name="validation series widths",
        stackup=Stackup((conductive_layer("L1", 0),)),
        nets=(Net(id=NET, name="DUT"),),
        copper_regions=(
            rect_region("wide", "L1", 0.0, 0.0, x_step, width_wide_m),
            rect_region("narrow", "L1", x_step, 0.0, total - x_step, width_narrow_m),
        ),
        pads=(pad_a, pad_b),
        terminals=(term_a, term_b),
    )


def via_stack_board(
    *,
    layer_count: int,
    finished_hole_m: float = 0.3e-3,
    plating_m: float = 25e-6,
    plate_side_m: float = 0.2e-3,
) -> Board:
    """`layer_count` copper plates joined by one through-via.

    Each plate is a square small enough to lie entirely within the via's
    contact disc, so every plate is a single equipotential and the measured
    terminal-to-terminal resistance equals the series barrel resistance of
    the spanned segments *exactly* -- no sheet-spreading ambiguity.
    """
    layers: list[Layer] = []
    index = 0
    copper_ids: list[str] = []
    for i in range(layer_count):
        if i > 0:
            layers.append(dielectric_layer(f"D{i}", index))
            index += 1
        layer_id = f"L{i + 1}"
        layers.append(conductive_layer(layer_id, index))
        copper_ids.append(layer_id)
        index += 1

    cx = cy = 1e-3
    half = plate_side_m / 2.0
    regions = tuple(
        rect_region(f"plate-{lid}", lid, cx - half, cy - half, plate_side_m, plate_side_m)
        for lid in copper_ids
    )
    pad_top = Pad(
        id=PadId("pad-top"),
        layer_id=LayerId(copper_ids[0]),
        position=Point2D(cx, cy),
        net_id=NET,
        outline=Polygon2D.rectangle(Point2D(cx - half, cy - half), plate_side_m, plate_side_m),
    )
    pad_bottom = Pad(
        id=PadId("pad-bottom"),
        layer_id=LayerId(copper_ids[-1]),
        position=Point2D(cx, cy),
        net_id=NET,
        outline=Polygon2D.rectangle(Point2D(cx - half, cy - half), plate_side_m, plate_side_m),
    )
    via = Via(
        id=ViaId("via-1"),
        net_id=NET,
        from_layer_id=LayerId(copper_ids[0]),
        to_layer_id=LayerId(copper_ids[-1]),
        position=Point2D(cx, cy),
        finished_hole_diameter=Quantity.imported(finished_hole_m, METRE),
        plating_thickness=Quantity.imported(plating_m, METRE),
    )
    return Board(
        id=BoardId("val-via"),
        name="validation via stack",
        stackup=Stackup(tuple(layers)),
        nets=(Net(id=NET, name="DUT"),),
        copper_regions=regions,
        vias=(via,),
        pads=(pad_top, pad_bottom),
        terminals=(
            Terminal(id=TerminalId("term-a"), name="TOP", net_id=NET, pad_ids=(pad_top.id,)),
            Terminal(id=TerminalId("term-b"), name="BOTTOM", net_id=NET, pad_ids=(pad_bottom.id,)),
        ),
    )


def disconnected_islands_board() -> Board:
    """Two copper islands of the same net with no conductive path between."""
    pad_a, term_a = rect_pad_terminal("a", "L1", 0.0, 0.0, 1e-3, 1e-3)
    pad_b, term_b = rect_pad_terminal("b", "L1", 10e-3, 0.0, 1e-3, 1e-3)
    return Board(
        id=BoardId("val-islands"),
        name="validation disconnected islands",
        stackup=Stackup((conductive_layer("L1", 0),)),
        nets=(Net(id=NET, name="DUT"),),
        copper_regions=(
            rect_region("island-a", "L1", 0.0, 0.0, 3e-3, 1e-3),
            rect_region("island-b", "L1", 8e-3, 0.0, 3e-3, 1e-3),
        ),
        pads=(pad_a, pad_b),
        terminals=(term_a, term_b),
    )


def disconnected_islands_with_via_board() -> Board:
    """Two same-net islands on L1, plus a via sitting only on island A.

    `find_disconnection` must treat the via as an endpoint candidate in its
    own right (it has no pad, so a terminal-only connectivity walk cannot
    see it) and still report island A and island B as disconnected when a
    via on A is checked against a terminal on B.
    """
    pad_a, term_a = rect_pad_terminal("a", "L1", 0.0, 0.0, 1e-3, 1e-3)
    pad_b, term_b = rect_pad_terminal("b", "L1", 10e-3, 0.0, 1e-3, 1e-3)
    via = Via(
        id=ViaId("via-island-a"),
        net_id=NET,
        from_layer_id=LayerId("L1"),
        to_layer_id=LayerId("L2"),
        position=Point2D(1.5e-3, 0.5e-3),
        finished_hole_diameter=Quantity.imported(0.3e-3, METRE),
        plating_thickness=Quantity.imported(25e-6, METRE),
    )
    return Board(
        id=BoardId("val-islands-via"),
        name="validation disconnected islands with via",
        stackup=Stackup((conductive_layer("L1", 0), conductive_layer("L2", 1))),
        nets=(Net(id=NET, name="DUT"),),
        copper_regions=(
            rect_region("island-a", "L1", 0.0, 0.0, 3e-3, 1e-3),
            rect_region("island-b", "L1", 8e-3, 0.0, 3e-3, 1e-3),
        ),
        vias=(via,),
        pads=(pad_a, pad_b),
        terminals=(term_a, term_b),
    )


def midplane_barrel_length_m(board: Board, upper: str, lower: str) -> float:
    """Distance between two layers' thickness midplanes, from the stackup.

    This is the barrel length the solver stamps for a via segment between the
    two layers; validation compares against the same definition.
    """
    z = 0.0
    midplanes: dict[str, float] = {}
    for layer in sorted(board.stackup.layers, key=lambda item: item.index):
        t = layer.thickness.require_unit(METRE) if layer.thickness else 0.0
        midplanes[str(layer.id)] = z + t / 2.0
        z += t
    return abs(midplanes[lower] - midplanes[upper])
