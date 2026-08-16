"""Analysis studies: how a board is electrically exercised.

A `Board` describes the manufactured PCB. An `AnalysisStudy` describes one
electrical experiment performed on it -- which terminals are driven, which draw
current, what copper conductivity and temperature to assume, how finely to
mesh. Studies reference the board by id and never modify it (ADR-0002).

The same board therefore supports many studies (nominal load, worst-case load,
single-rail sweep) without re-importing fabrication data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING, NewType

from openpdn.domain.errors import InvalidStudyError
from openpdn.domain.materials import Material
from openpdn.domain.provenance import Quantity
from openpdn.domain.units import AMPERE, KELVIN, METRE, VOLT

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openpdn.domain.board import Board, LayerId, NetId, TerminalId, ViaId

StudyId = NewType("StudyId", str)
SourceId = NewType("SourceId", str)
LoadId = NewType("LoadId", str)
ProbeId = NewType("ProbeId", str)


class ViaModel(StrEnum):
    """How vias are represented electrically.

    `LUMPED_CONDUCTANCE` is the 2.5-D default: a via becomes a conductance
    between two sheet layers. `RESOLVED_3D` requires a volumetric backend.
    """

    LUMPED_CONDUCTANCE = "lumped_conductance"
    RESOLVED_3D = "resolved_3d"


class ElementOrder(StrEnum):
    """Polynomial order of the finite-element basis (ADR-0012).

    `P1` is the linear triangle every profile below Reference uses. `P2` is
    the six-node quadratic triangle: more accurate per element and per DOF on
    smooth solutions, roughly four times the DOFs on the same triangulation.
    Order is a study input, not a separate solver -- a backend that cannot
    serve the requested order declares so and is refused before dispatch.
    """

    P1 = "p1"
    P2 = "p2"


@dataclass(frozen=True, slots=True)
class AttachmentGroup:
    """Terminals and/or vias forming one equipotential/current group.

    A source or load may drive more than one physical pad at once -- a BGA
    power rail's dozens of pins, or a pin plus a nearby via barrel -- and this
    is what makes that a single explicit modelling choice rather than an
    accident of geometry. Every member is preserved; the solver never
    collapses a group to one artificial coordinate.

    A via member attaches at the via's contact node on its topmost connected
    conductive layer, not across the whole barrel -- forcing every layer of
    the via to the same potential would silently short out the barrel's own
    resistance, which is exactly the quantity a via-current result reports.
    """

    terminal_ids: tuple[TerminalId, ...] = ()
    via_ids: tuple[ViaId, ...] = ()

    def __post_init__(self) -> None:
        """Reject an attachment with nothing to attach to."""
        if not self.terminal_ids and not self.via_ids:
            raise InvalidStudyError("An attachment group needs at least one terminal or via")


@dataclass(frozen=True, slots=True)
class VoltageSource:
    """An attachment held at a fixed potential -- a Dirichlet boundary condition.

    `internal_resistance` models a non-ideal supply; `None` means ideal, which
    is a modelling choice the result should carry forward to the user.
    """

    id: SourceId
    attachment: AttachmentGroup
    voltage: Quantity
    internal_resistance: Quantity | None = None

    def __post_init__(self) -> None:
        """Validate source units."""
        self.voltage.require_unit(VOLT)
        if (
            self.internal_resistance is not None
            and self.internal_resistance.require_unit("ohm") < 0.0
        ):
            raise InvalidStudyError(f"Source {self.id!r} has negative internal resistance")


@dataclass(frozen=True, slots=True)
class CurrentLoad:
    """An attachment sinking a fixed current -- a Neumann boundary condition.

    Positive `current` means current leaving the board into the load, shared
    across every member of the attachment group (an equipotential-terminal
    current-sharing model, per the terminal-groups design note; the exact
    per-pad split within the group is not resolved, only the group total).
    """

    id: LoadId
    attachment: AttachmentGroup
    current: Quantity

    def __post_init__(self) -> None:
        """Validate load units."""
        self.current.require_unit(AMPERE)


@dataclass(frozen=True, slots=True)
class ResistanceProbe:
    """A request for the effective resistance between two terminals."""

    id: ProbeId
    from_terminal_id: TerminalId
    to_terminal_id: TerminalId

    def __post_init__(self) -> None:
        """Reject degenerate probes."""
        if self.from_terminal_id == self.to_terminal_id:
            raise InvalidStudyError(f"Probe {self.id!r} measures a terminal against itself")


@dataclass(frozen=True, slots=True)
class MeshSettings:
    """Discretisation controls, independent of any particular solver.

    Solver adapters translate these into backend-specific options; backend
    options never appear here (ADR-0003).

    Attributes:
        target_element_size: Upper bound on element edge length in wide copper.
        minimum_element_size: Lower bound the mesher may not refine below;
            `None` lets the mesher derive one from the target.
        refine_around_terminals: Whether terminal pads receive local
            refinement beyond the width-based sizing.
        elements_across_feature: How many elements a narrow conductor should
            receive across its local width. This -- not raw element size -- is
            what controls discretisation error in neck-downs and traces.
        growth_rate: How fast element size may grow per unit distance from a
            refined boundary, dimensionless. Lower is smoother and denser.
        element_order: Polynomial order of the basis. Independent of element
            *size*: raising the order refines the approximation without
            changing the triangulation.
    """

    target_element_size: Quantity
    minimum_element_size: Quantity | None = None
    refine_around_terminals: bool = True
    elements_across_feature: int = 4
    growth_rate: float = 0.7
    element_order: ElementOrder = ElementOrder.P1

    def __post_init__(self) -> None:
        """Validate element sizing."""
        target_m = self.target_element_size.require_unit(METRE)
        if target_m <= 0.0:
            raise InvalidStudyError("Target element size must be positive")
        if self.minimum_element_size is not None:
            minimum_m = self.minimum_element_size.require_unit(METRE)
            if minimum_m <= 0.0 or minimum_m > target_m:
                raise InvalidStudyError(
                    "Minimum element size must be positive and no larger than the target"
                )
        if self.elements_across_feature < 1:
            raise InvalidStudyError("Elements-across-feature must be at least 1")
        if not 0.0 < self.growth_rate <= 2.0:
            raise InvalidStudyError("Mesh growth rate must be in (0, 2]")


@dataclass(frozen=True, slots=True)
class LayerThicknessOverride:
    """A study-supplied copper thickness for a layer whose value is unknown.

    Overrides live in the study, never written back into the board: the board
    records what was imported, the study records what the engineer decided.
    """

    layer_id: LayerId
    thickness: Quantity

    def __post_init__(self) -> None:
        """Validate the override."""
        if self.thickness.require_unit(METRE) <= 0.0:
            raise InvalidStudyError(f"Thickness override for {self.layer_id!r} is not positive")


@dataclass(frozen=True)
class AnalysisStudy:
    """One DC conduction experiment on one board."""

    id: StudyId
    name: str
    board_id: str
    net_ids: tuple[NetId, ...]
    sources: tuple[VoltageSource, ...]
    loads: tuple[CurrentLoad, ...] = ()
    probes: tuple[ResistanceProbe, ...] = ()
    conductor_material: Material | None = None
    temperature: Quantity | None = None
    via_model: ViaModel = ViaModel.LUMPED_CONDUCTANCE
    mesh: MeshSettings | None = None
    thickness_overrides: tuple[LayerThicknessOverride, ...] = field(default_factory=tuple)
    #: Barrel plating thickness to use for vias whose fabrication data does not
    #: state one. Lives on the study -- the board records only what was
    #: imported -- and solvers must report its use as an assumption.
    via_plating_thickness: Quantity | None = None

    def __post_init__(self) -> None:
        """Validate internal consistency of the study."""
        if not self.net_ids:
            raise InvalidStudyError(f"Study {self.id!r} analyses no nets")
        if not self.sources:
            raise InvalidStudyError(
                f"Study {self.id!r} has no voltage source; a DC conduction problem "
                "without a Dirichlet condition has no unique solution"
            )
        _require_unique_ids("source", [source.id for source in self.sources])
        _require_unique_ids("load", [load.id for load in self.loads])
        _require_unique_ids("probe", [probe.id for probe in self.probes])
        driven_terminals = [
            terminal_id for source in self.sources for terminal_id in source.attachment.terminal_ids
        ]
        if len(set(driven_terminals)) != len(driven_terminals):
            raise InvalidStudyError(f"Study {self.id!r} drives a terminal from two sources")
        driven_vias = [via_id for source in self.sources for via_id in source.attachment.via_ids]
        if len(set(driven_vias)) != len(driven_vias):
            raise InvalidStudyError(f"Study {self.id!r} drives a via from two sources")
        source_members = {
            member
            for source in self.sources
            for member in (*source.attachment.terminal_ids, *source.attachment.via_ids)
        }
        load_members = {
            member
            for load in self.loads
            for member in (*load.attachment.terminal_ids, *load.attachment.via_ids)
        }
        overlap = source_members & load_members
        if overlap:
            # A source pins a node's potential (Dirichlet); a load draws a
            # fixed current there (Neumann). The same node cannot honour
            # both -- the solver would silently ignore the load's current
            # instead of drawing it, which conservation only catches after
            # a full solve. Refuse before meshing, where the mistake is
            # cheap and the message points at the actual overlap.
            raise InvalidStudyError(
                f"Study {self.id!r}: a source and a load both attach to {sorted(overlap)!r}"
            )
        if self.temperature is not None and self.temperature.require_unit(KELVIN) <= 0.0:
            raise InvalidStudyError("Study temperature must be above absolute zero")
        if (
            self.via_plating_thickness is not None
            and self.via_plating_thickness.require_unit(METRE) <= 0.0
        ):
            raise InvalidStudyError("Via plating thickness must be positive")

    @cached_property
    def net_id_set(self) -> frozenset[NetId]:
        """Nets under analysis, as a set."""
        return frozenset(self.net_ids)

    @cached_property
    def thickness_override_by_layer(self) -> dict[LayerId, Quantity]:
        """Thickness overrides keyed by layer id."""
        return {override.layer_id: override.thickness for override in self.thickness_overrides}

    @property
    def total_load_current_a(self) -> float:
        """Sum of all load currents, in amperes."""
        return sum(load.current.require_unit(AMPERE) for load in self.loads)

    def validate_against(self, board: Board) -> None:
        """Check that every study reference resolves on `board`.

        Cross-aggregate validation lives here rather than in `Board` so that a
        board stays valid on its own, and so the application layer has a single
        call to make before handing work to a solver.

        Raises:
            InvalidStudyError: On the first unresolvable reference.
        """
        if self.board_id != board.id:
            raise InvalidStudyError(
                f"Study {self.id!r} targets board {self.board_id!r}, got {board.id!r}"
            )
        known_nets = {net.id for net in board.nets}
        for net_id in self.net_ids:
            if net_id not in known_nets:
                raise InvalidStudyError(f"Study {self.id!r} references unknown net {net_id!r}")

        known_terminals = board.terminals_by_id
        known_vias = board.vias_by_id
        referenced_terminals: list[tuple[str, TerminalId]] = [
            *(
                (f"source {source.id!r}", terminal_id)
                for source in self.sources
                for terminal_id in source.attachment.terminal_ids
            ),
            *(
                (f"load {load.id!r}", terminal_id)
                for load in self.loads
                for terminal_id in load.attachment.terminal_ids
            ),
            *(
                (f"probe {probe.id!r}", terminal_id)
                for probe in self.probes
                for terminal_id in (probe.from_terminal_id, probe.to_terminal_id)
            ),
        ]
        for owner, terminal_id in referenced_terminals:
            terminal = known_terminals.get(terminal_id)
            if terminal is None:
                raise InvalidStudyError(
                    f"Study {self.id!r}: {owner} references unknown terminal {terminal_id!r}"
                )
            if terminal.net_id not in self.net_id_set:
                raise InvalidStudyError(
                    f"Study {self.id!r}: {owner} sits on net {terminal.net_id!r}, "
                    "which is not under analysis"
                )

        referenced_vias: list[tuple[str, ViaId]] = [
            *(
                (f"source {source.id!r}", via_id)
                for source in self.sources
                for via_id in source.attachment.via_ids
            ),
            *(
                (f"load {load.id!r}", via_id)
                for load in self.loads
                for via_id in load.attachment.via_ids
            ),
        ]
        for owner, via_id in referenced_vias:
            via = known_vias.get(via_id)
            if via is None:
                raise InvalidStudyError(
                    f"Study {self.id!r}: {owner} references unknown via {via_id!r}"
                )
            if via.net_id not in self.net_id_set:
                raise InvalidStudyError(
                    f"Study {self.id!r}: {owner} sits on net {via.net_id!r}, "
                    "which is not under analysis"
                )

        known_layers = {layer.id for layer in board.stackup.layers}
        for override in self.thickness_overrides:
            if override.layer_id not in known_layers:
                raise InvalidStudyError(
                    f"Study {self.id!r} overrides thickness of unknown layer {override.layer_id!r}"
                )


def _require_unique_ids(label: str, values: Sequence[str]) -> None:
    """Raise if `values` contains duplicates."""
    if len(set(values)) != len(values):
        raise InvalidStudyError(f"Duplicate {label} ids in study")
