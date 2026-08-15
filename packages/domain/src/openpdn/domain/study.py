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

    from openpdn.domain.board import Board, LayerId, NetId, TerminalId

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


@dataclass(frozen=True, slots=True)
class VoltageSource:
    """A terminal held at a fixed potential -- a Dirichlet boundary condition.

    `internal_resistance` models a non-ideal supply; `None` means ideal, which
    is a modelling choice the result should carry forward to the user.
    """

    id: SourceId
    terminal_id: TerminalId
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
    """A terminal sinking a fixed current -- a Neumann boundary condition.

    Positive `current` means current leaving the board into the load.
    """

    id: LoadId
    terminal_id: TerminalId
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
    """

    target_element_size: Quantity
    minimum_element_size: Quantity | None = None
    refine_around_terminals: bool = True

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
        driven = [source.terminal_id for source in self.sources]
        if len(set(driven)) != len(driven):
            raise InvalidStudyError(f"Study {self.id!r} drives a terminal from two sources")
        if self.temperature is not None and self.temperature.require_unit(KELVIN) <= 0.0:
            raise InvalidStudyError("Study temperature must be above absolute zero")

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
        referenced: list[tuple[str, TerminalId]] = [
            *((f"source {source.id!r}", source.terminal_id) for source in self.sources),
            *((f"load {load.id!r}", load.terminal_id) for load in self.loads),
            *(
                (f"probe {probe.id!r}", terminal_id)
                for probe in self.probes
                for terminal_id in (probe.from_terminal_id, probe.to_terminal_id)
            ),
        ]
        for owner, terminal_id in referenced:
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
