"""The 2.5-D sheet-conduction FEM solver.

`FemSheetSolver` implements the `ElectricalSolver` contract (and the staged
variant) on the in-house pipeline: normalised copper -> feature-aware mesh ->
P1 sheet assembly -> SuperLU direct solve -> conservation-checked results.

Fidelity is reported as `SHEET_2P5D` and every physical assumption the solver
makes -- assumed plating, degraded point terminals, dangling vias -- arrives
as a `Diagnostic` on the result (fem-solver skill).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np

from openpdn.domain.results import (
    Diagnostic,
    DiagnosticSeverity,
    ElectricalAnalysisResult,
    NetIRDropResult,
    ResistanceProbeResult,
    ResultFidelity,
    SolverIdentity,
    SolverRunStats,
    TerminalResult,
)
from openpdn.domain.study import ViaModel
from openpdn.domain.units import AMPERE, VOLT
from openpdn.solver.api import (
    SolverCapabilities,
    SolverConfigurationError,
    SolverDescriptor,
)
from openpdn.solver.fem.controls import MeshControls
from openpdn.solver.fem.post import (
    ConservationReport,
    ElementFields,
    conservation_report,
    current_density_stats,
    element_fields,
    via_currents_a,
)
from openpdn.solver.fem.problem import SheetProblem, build_problem
from openpdn.solver.fem.solve import Excitation, Solution, solve_excitation

if TYPE_CHECKING:
    import numpy.typing as npt

    from openpdn.domain.board import Board, TerminalId
    from openpdn.domain.study import AnalysisStudy
    from openpdn.geometry.api import GeometryNormalizer

SOLVER_NAME: Final = "fem-2p5d"
SOLVER_VERSION: Final = "0.1.0"

#: Default maximum element size when the study supplies no mesh settings:
#: the board diagonal divided by this. Eighty elements across the board is a
#: usable engineering default; profiles set explicit sizes.
DEFAULT_DIAGONAL_DIVISIONS: Final = 80.0

#: Current-imbalance / power-mismatch fractions above the first threshold
#: warn; above the second the result carries an ERROR diagnostic and must not
#: present as clean. A healthy direct solve sits many orders below both.
CONSERVATION_WARN_FRACTION: Final = 1e-6
CONSERVATION_ERROR_FRACTION: Final = 1e-3


@dataclass(frozen=True)
class FemFieldData:
    """Field-level solution data for artifact generation and overlays.

    This never crosses the solver contract -- `ElectricalAnalysisResult`
    stays scalar -- but the job worker persists it into the result artifact.
    """

    points: npt.NDArray[np.float64]
    triangles: npt.NDArray[np.int32]
    node_voltage_v: npt.NDArray[np.float64]
    tri_j_vol_a_per_m2: npt.NDArray[np.float64]
    tri_power_w: npt.NDArray[np.float64]
    tri_region_index: npt.NDArray[np.int32]
    region_layer_ids: tuple[str, ...]
    region_net_ids: tuple[str, ...]
    via_currents_a: dict[str, float]
    conservation: ConservationReport
    matrix_nonzeros: int


class FemSheetSolver:
    """2.5-D sheet conduction with lumped via conductances."""

    def __init__(self, normalizer: GeometryNormalizer) -> None:
        """Store the geometry-normalisation engine used to obtain copper."""
        self._normalizer = normalizer

    def describe(self) -> SolverDescriptor:
        """Identity and honest capabilities."""
        return SolverDescriptor(
            name=SOLVER_NAME,
            version=SOLVER_VERSION,
            summary="2.5-D sheet-conduction FEM, direct sparse solve (SuperLU)",
            capabilities=SolverCapabilities(
                fidelity=ResultFidelity.SHEET_2P5D,
                via_models=frozenset({ViaModel.LUMPED_CONDUCTANCE}),
                supports_resistance_probes=True,
                supports_current_density=True,
                supports_power_loss=True,
                supports_thermal_coupling=False,
            ),
            tags=("fem", "direct-solver"),
        )

    def solve(self, board: Board, study: AnalysisStudy) -> ElectricalAnalysisResult:
        """Solve `study` on `board`, returning the common result model."""
        result, _ = self.solve_with_fields(board, study)
        return result

    def solve_with_fields(
        self, board: Board, study: AnalysisStudy
    ) -> tuple[ElectricalAnalysisResult, FemFieldData]:
        """Solve and additionally return field data for artifact storage."""
        study.validate_against(board)
        if study.via_model is not ViaModel.LUMPED_CONDUCTANCE:
            raise SolverConfigurationError(
                f"{SOLVER_NAME} supports only the lumped-conductance via model"
            )

        assembly_started = time.perf_counter()
        problem = self._build(board, study)
        assembly_seconds = time.perf_counter() - assembly_started

        return _solve_prepared(problem, board, study, assembly_seconds, cache_hit=False)

    def prepare(self, board: Board, study: AnalysisStudy) -> PreparedFemProblem:
        """Mesh and assemble without applying source or load magnitudes."""
        study.validate_against(board)
        problem = self._build(board, study)
        return PreparedFemProblem(
            problem=problem,
            board=board,
            cache_key_value=_cache_key(board, study),
        )

    def _build(self, board: Board, study: AnalysisStudy) -> SheetProblem:
        normalized = self._normalizer.normalize(board)
        controls = _controls_for(board, study)
        return build_problem(board, study, normalized, controls)


@dataclass(frozen=True)
class PreparedFemProblem:
    """An assembled problem reusable across excitations of the same geometry."""

    problem: SheetProblem
    board: Board
    cache_key_value: str

    @property
    def cache_key(self) -> str:
        """Stable key over geometry, materials and mesh settings."""
        return self.cache_key_value

    def solve_with(self, study: AnalysisStudy) -> ElectricalAnalysisResult:
        """Apply `study`'s boundary conditions to the assembled system."""
        result, _ = _solve_prepared(
            self.problem, self.board, study, assembly_seconds=0.0, cache_hit=True
        )
        return result


def _controls_for(board: Board, study: AnalysisStudy) -> MeshControls:
    """Resolve mesh controls, deriving a default target from the board size."""
    if study.mesh is not None:
        return MeshControls.from_settings(study.mesh)
    box = board.bounding_box
    if box is None:
        raise SolverConfigurationError("Board has no geometry; nothing to mesh")
    diagonal_m = float(np.hypot(box.max_x_m - box.min_x_m, box.max_y_m - box.min_y_m))
    max_size_m = diagonal_m / DEFAULT_DIAGONAL_DIVISIONS
    return MeshControls(
        max_size_m=max_size_m,
        min_size_m=max_size_m * 0.01,
        elements_across_feature=4,
        growth_rate=0.7,
        refine_terminals=True,
    )


def _cache_key(board: Board, study: AnalysisStudy) -> str:
    """Hash of every input that affects meshing and assembly."""
    digest = hashlib.sha256()
    provenance = board.provenance
    source_digest = provenance.source_digest if provenance is not None else None
    digest.update((source_digest or str(board.id)).encode())
    digest.update(",".join(sorted(str(n) for n in study.net_ids)).encode())
    controls = _controls_for(board, study)
    digest.update(repr(controls).encode())
    digest.update(repr(sorted(study.thickness_override_by_layer.items())).encode())
    if study.conductor_material is not None:
        digest.update(repr(study.conductor_material).encode())
    if study.temperature is not None:
        digest.update(repr(study.temperature).encode())
    if study.via_plating_thickness is not None:
        digest.update(repr(study.via_plating_thickness).encode())
    digest.update(SOLVER_VERSION.encode())
    return digest.hexdigest()


def _solve_prepared(
    problem: SheetProblem,
    board: Board,
    study: AnalysisStudy,
    assembly_seconds: float,
    cache_hit: bool,
) -> tuple[ElectricalAnalysisResult, FemFieldData]:
    """Apply the study's excitation to an assembled problem and post-process."""
    diagnostics: list[Diagnostic] = list(problem.diagnostics)

    dirichlet: dict[int, float] = {}
    for source in study.sources:
        binding = problem.terminals[source.terminal_id]
        voltage = source.voltage.require_unit(VOLT)
        existing = dirichlet.get(binding.dof)
        if existing is not None and existing != voltage:
            raise SolverConfigurationError(
                f"Two sources drive the same copper at different potentials "
                f"({existing} V and {voltage} V); their pads share a terminal region"
            )
        dirichlet[binding.dof] = voltage

    injected: dict[int, float] = {}
    load_drawn_by_dof: dict[int, float] = {}
    for load in study.loads:
        binding = problem.terminals[load.terminal_id]
        drawn = load.current.require_unit(AMPERE)
        injected[binding.dof] = injected.get(binding.dof, 0.0) - drawn
        load_drawn_by_dof[binding.dof] = load_drawn_by_dof.get(binding.dof, 0.0) + drawn

    solution = solve_excitation(problem, Excitation(dirichlet, injected))
    fields = element_fields(problem, solution)
    conservation = conservation_report(problem, solution, fields, load_drawn_by_dof)
    _conservation_diagnostics(conservation, diagnostics)

    j_stats = current_density_stats(fields)
    via_current = via_currents_a(problem, solution)

    terminals = _terminal_results(problem, study, solution, load_drawn_by_dof)
    nets = _net_results(problem, study, solution, fields)
    probes = _probe_results(problem, study)

    stats = SolverRunStats(
        mesh_nodes=problem.node_count,
        mesh_elements=problem.element_count,
        assembly_seconds=assembly_seconds,
        solve_seconds=solution.factor_seconds + solution.solve_seconds,
        iterations=1,
        residual=solution.residual,
        converged=True,
        cache_hit=cache_hit,
    )

    diagnostics.append(
        Diagnostic(
            code="fidelity.sheet_2p5d",
            severity=DiagnosticSeverity.INFO,
            message=(
                "2.5-D sheet model: in-plane conduction with lumped via barrels. "
                "Current crowding within a barrel and vertical field detail near "
                "pads are outside this model."
            ),
            context={
                "j_peak_a_per_m2": f"{j_stats.peak:.6e}",
                "j_p99_a_per_m2": f"{j_stats.p99:.6e}",
                "j_p999_a_per_m2": f"{j_stats.p999:.6e}",
            },
        )
    )

    result = ElectricalAnalysisResult(
        study_id=study.id,
        board_id=str(board.id),
        solver=SolverIdentity(name=SOLVER_NAME, version=SOLVER_VERSION, backend="scipy-superlu"),
        fidelity=ResultFidelity.SHEET_2P5D,
        terminals=terminals,
        nets=nets,
        probes=probes,
        diagnostics=tuple(diagnostics),
        stats=stats,
    )
    field_data = FemFieldData(
        points=problem.points,
        triangles=problem.triangles,
        node_voltage_v=solution.voltage_v[problem.dof_of_node],
        tri_j_vol_a_per_m2=fields.j_vol_a_per_m2,
        tri_power_w=fields.power_w,
        tri_region_index=problem.tri_region_index,
        region_layer_ids=tuple(str(ref.layer_id) for ref in problem.regions),
        region_net_ids=tuple(str(ref.net_id or "") for ref in problem.regions),
        via_currents_a=via_current,
        conservation=conservation,
        matrix_nonzeros=int(problem.matrix.nnz),
    )
    return result, field_data


def _conservation_diagnostics(
    conservation: ConservationReport, diagnostics: list[Diagnostic]
) -> None:
    """Turn conservation failures into result diagnostics."""
    checks = (
        ("numerics.current_imbalance", conservation.imbalance_fraction, "Current balance"),
        ("numerics.power_mismatch", conservation.power_mismatch_fraction, "Power balance"),
    )
    for code, fraction, label in checks:
        if fraction > CONSERVATION_ERROR_FRACTION:
            severity = DiagnosticSeverity.ERROR
        elif fraction > CONSERVATION_WARN_FRACTION:
            severity = DiagnosticSeverity.WARNING
        else:
            continue
        diagnostics.append(
            Diagnostic(
                code=code,
                severity=severity,
                message=f"{label} error of {fraction:.3e} exceeds the expected tolerance.",
                context={"fraction": f"{fraction:.6e}"},
            )
        )


def _terminal_results(
    problem: SheetProblem,
    study: AnalysisStudy,
    solution: Solution,
    load_drawn_by_dof: dict[int, float],
) -> tuple[TerminalResult, ...]:
    """Voltage and current at every study terminal."""
    results: list[TerminalResult] = []
    seen: set[TerminalId] = set()
    for source in study.sources:
        binding = problem.terminals[source.terminal_id]
        results.append(
            TerminalResult(
                terminal_id=source.terminal_id,
                voltage_v=float(solution.voltage_v[binding.dof]),
                current_a=float(solution.source_current_a.get(binding.dof, 0.0)),
            )
        )
        seen.add(source.terminal_id)
    for load in study.loads:
        if load.terminal_id in seen:
            continue
        binding = problem.terminals[load.terminal_id]
        results.append(
            TerminalResult(
                terminal_id=load.terminal_id,
                voltage_v=float(solution.voltage_v[binding.dof]),
                current_a=float(load_drawn_by_dof.get(binding.dof, 0.0)),
            )
        )
        seen.add(load.terminal_id)
    return tuple(results)


def _net_results(
    problem: SheetProblem,
    study: AnalysisStudy,
    solution: Solution,
    fields: ElementFields,
) -> tuple[NetIRDropResult, ...]:
    """Per-net potential extremes, peak |J| and dissipated power."""
    results: list[NetIRDropResult] = []
    for net_id in study.net_ids:
        node_mask = np.zeros(problem.node_count, dtype=bool)
        tri_mask = np.zeros(problem.element_count, dtype=bool)
        for index, ref in enumerate(problem.regions):
            if ref.net_id != net_id:
                continue
            node_mask[ref.node_start : ref.node_start + ref.node_count] = True
            tri_mask[problem.tri_region_index == index] = True
        if not node_mask.any():
            continue
        v = solution.voltage_v[problem.dof_of_node[node_mask]]
        v = v[~np.isnan(v)]
        if len(v) == 0:
            continue
        via_loss = sum(
            segment.conductance_s
            * float(
                np.nan_to_num(
                    solution.voltage_v[segment.dof_upper] - solution.voltage_v[segment.dof_lower]
                )
            )
            ** 2
            for segment in problem.via_segments
            if segment.net_id == net_id
        )
        results.append(
            NetIRDropResult(
                net_id=net_id,
                max_voltage_v=float(v.max()),
                min_voltage_v=float(v.min()),
                max_current_density_a_per_m2=float(fields.j_vol_a_per_m2[tri_mask].max())
                if tri_mask.any()
                else None,
                resistive_loss_w=float(fields.power_w[tri_mask].sum()) + via_loss,
            )
        )
    return tuple(results)


#: Normalised probe test current. One ampere makes the probe resistance
#: numerically equal to the measured voltage difference; the value is a
#: numerical convenience, not an operating current, and effective resistance
#: is invariant to it in this linear model (validated by the scaling tests).
PROBE_TEST_CURRENT_A: Final = 1.0


def _probe_results(
    problem: SheetProblem, study: AnalysisStudy
) -> tuple[ResistanceProbeResult, ...]:
    """Effective terminal-to-terminal resistance for each probe.

    Each probe is its own excitation on the already-assembled matrix: the
    `from` terminal is grounded and the test current drawn at `to`, giving
    `R = (V_from - V_to) / I_test = -V_to`.
    """
    results: list[ResistanceProbeResult] = []
    for probe in study.probes:
        from_dof = problem.terminals[probe.from_terminal_id].dof
        to_dof = problem.terminals[probe.to_terminal_id].dof
        solution = solve_excitation(
            problem,
            Excitation(
                dirichlet={from_dof: 0.0},
                injected_current_a={to_dof: -PROBE_TEST_CURRENT_A},
            ),
        )
        resistance = float(-solution.voltage_v[to_dof] / PROBE_TEST_CURRENT_A)
        results.append(ResistanceProbeResult(probe_id=probe.id, resistance_ohm=resistance))
    return (results and tuple(results)) or ()
