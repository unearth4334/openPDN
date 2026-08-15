"""The common result model.

Every solver -- 2.5-D sheet, Elmer, or the mock -- returns this shape, so the
API, CLI and UI never branch on which backend ran (ADR-0003).

Two fields exist specifically to keep openPDN honest about what it computed:

* `fidelity` records the physics actually applied. `ResultFidelity.MOCK` means
  no conduction problem was solved; such a result must never be rendered as a
  simulation.
* `diagnostics` carries assumptions, warnings and numerical caveats -- including
  the current-density singularity that appears at any mathematical point
  source, which is an artefact of the boundary condition, not a hot spot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openpdn.domain.board import NetId, TerminalId
    from openpdn.domain.study import ProbeId, StudyId


class ResultFidelity(StrEnum):
    """The physics actually applied to produce a result."""

    #: No conduction problem was solved. Placeholder values only.
    MOCK = "mock"
    #: 2.5-D sheet conduction with lumped interlayer via conductance.
    SHEET_2P5D = "sheet_2p5d"
    #: Full volumetric conduction.
    VOLUME_3D = "volume_3d"

    @property
    def is_physical(self) -> bool:
        """True when a conduction problem was actually solved."""
        return self is not ResultFidelity.MOCK


class DiagnosticSeverity(StrEnum):
    """How much attention a diagnostic deserves."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A machine-readable note attached to a result.

    Attributes:
        code: Stable dotted identifier, e.g. `"assumption.layer_thickness"`.
            UIs key off this, never off `message`.
        severity: Attention level.
        message: Human-readable explanation.
        context: Structured detail (`layer`, `net`, `terminal`, ...).
    """

    code: str
    severity: DiagnosticSeverity
    message: str
    context: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SolverIdentity:
    """Which solver produced a result, and at what version."""

    name: str
    version: str
    backend: str | None = None


@dataclass(frozen=True, slots=True)
class SolverRunStats:
    """Numerical and performance detail about one solve.

    Kept alongside results so that convergence can be reviewed without digging
    through logs, and so cache behaviour is visible.
    """

    mesh_nodes: int | None = None
    mesh_elements: int | None = None
    assembly_seconds: float | None = None
    solve_seconds: float | None = None
    iterations: int | None = None
    residual: float | None = None
    converged: bool | None = None
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """Potential and current at one terminal."""

    terminal_id: TerminalId
    voltage_v: float
    current_a: float


@dataclass(frozen=True, slots=True)
class NetIRDropResult:
    """Extreme potentials on one net, and the resulting IR drop."""

    net_id: NetId
    max_voltage_v: float
    min_voltage_v: float
    max_current_density_a_per_m2: float | None = None
    resistive_loss_w: float | None = None

    @property
    def ir_drop_v(self) -> float:
        """Potential difference between the highest and lowest node on the net."""
        return self.max_voltage_v - self.min_voltage_v


@dataclass(frozen=True, slots=True)
class ResistanceProbeResult:
    """Effective terminal-to-terminal resistance for one probe."""

    probe_id: ProbeId
    resistance_ohm: float


@dataclass(frozen=True)
class ElectricalAnalysisResult:
    """The complete outcome of running one study against one board."""

    study_id: StudyId
    board_id: str
    solver: SolverIdentity
    fidelity: ResultFidelity
    terminals: tuple[TerminalResult, ...] = ()
    nets: tuple[NetIRDropResult, ...] = ()
    probes: tuple[ResistanceProbeResult, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    stats: SolverRunStats = field(default_factory=SolverRunStats)

    @property
    def is_physical(self) -> bool:
        """False for placeholder results that no engineering decision may rest on."""
        return self.fidelity.is_physical

    @cached_property
    def terminals_by_id(self) -> Mapping[TerminalId, TerminalResult]:
        """Terminal results keyed by terminal id."""
        return {result.terminal_id: result for result in self.terminals}

    @property
    def has_errors(self) -> bool:
        """True when any diagnostic is an error."""
        return any(item.severity is DiagnosticSeverity.ERROR for item in self.diagnostics)

    @property
    def worst_ir_drop_v(self) -> float | None:
        """Largest IR drop across all analysed nets, or `None` without net results."""
        if not self.nets:
            return None
        return max(net.ir_drop_v for net in self.nets)
