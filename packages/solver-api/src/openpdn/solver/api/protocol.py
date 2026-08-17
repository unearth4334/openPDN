"""Protocols implemented by solver adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from openpdn.domain.study import ElementOrder

if TYPE_CHECKING:
    from openpdn.domain.board import Board
    from openpdn.domain.results import ElectricalAnalysisResult, ResultFidelity
    from openpdn.domain.study import AnalysisStudy, ViaModel


@dataclass(frozen=True, slots=True)
class SolverCapabilities:
    """What a backend can actually do.

    The application asks before dispatching, so an unsupported study fails with
    a clear message instead of being quietly solved with different physics.
    """

    fidelity: ResultFidelity
    via_models: frozenset[ViaModel]
    #: Basis orders this backend can serve. Defaults to linear only, so a
    #: backend that predates quadratic support keeps declaring the truth
    #: without being edited (ADR-0012 §5).
    element_orders: frozenset[ElementOrder] = frozenset({ElementOrder.P1})
    supports_resistance_probes: bool = False
    supports_current_density: bool = False
    supports_power_loss: bool = False
    supports_thermal_coupling: bool = False


@dataclass(frozen=True, slots=True)
class SolverDescriptor:
    """Identity and capabilities of one registered solver.

    Attributes:
        name: Stable registry key, e.g. `"mock"`, `"padne"`, `"elmer"`.
        version: Adapter version, reported in results and logs.
        summary: One line shown in the UI's solver picker.
        capabilities: What the backend supports.
        available: False when the backend's external dependency is missing --
            it stays listed, so the UI can explain *why* it cannot be selected.
        unavailable_reason: Populated when `available` is False.
    """

    name: str
    version: str
    summary: str
    capabilities: SolverCapabilities
    available: bool = True
    unavailable_reason: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class ElectricalSolver(Protocol):
    """A DC conduction backend.

    Implementations must:

    * be constructible without touching the network or a licence server;
    * treat `board` and `study` as immutable inputs;
    * report `ResultFidelity` truthfully;
    * raise `SolverUnsupportedFeatureError` rather than silently approximating;
    * attach a `Diagnostic` for every assumption they had to make.
    """

    def describe(self) -> SolverDescriptor:
        """Return identity and capabilities. Must not run a solve."""
        ...

    def solve(self, board: Board, study: AnalysisStudy) -> ElectricalAnalysisResult:
        """Solve `study` on `board` and return results in the common model."""
        ...


class PreparedProblem(Protocol):
    """An assembled problem that can be re-solved with new excitations.

    Meshing and matrix assembly depend only on geometry, materials and mesh
    settings; source and load *magnitudes* only enter the right-hand side.
    Separating them is what makes "change a load current and re-solve" fast,
    and it is why re-import and re-mesh must not be triggered by a load edit
    (see the `solver-development` skill).
    """

    @property
    def cache_key(self) -> str:
        """Stable key over geometry, materials and mesh settings."""
        ...

    def solve_with(self, study: AnalysisStudy) -> ElectricalAnalysisResult:
        """Apply `study`'s boundary conditions to the assembled system."""
        ...


@runtime_checkable
class StagedElectricalSolver(ElectricalSolver, Protocol):
    """A solver that exposes its mesh/assembly stage for reuse.

    Optional: a backend that cannot separate the stages implements
    `ElectricalSolver` alone, and callers fall back to `solve`.
    """

    def prepare(self, board: Board, study: AnalysisStudy) -> PreparedProblem:
        """Mesh and assemble, without applying source or load magnitudes."""
        ...
