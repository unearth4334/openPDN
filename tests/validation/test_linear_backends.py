"""Direct versus iterative, on real boards. ADR-0014 §8's release gate.

A scalable backend that has not been checked against the direct one is not
trusted, so this file exists to do the checking: same board, same excitation,
two backends, and the engineering answers must agree.

Measured on `plane_neck_plane_board` (terminal resistance, agreement against
the direct solve, and what each backend cost):

    DOFs      CG iterations   agreement    direct     iterative
    287             112        1.5e-14      0.001 s     0.002 s
    737             180        3.1e-14      0.001 s     0.002 s
    2,465           295        1.3e-14      0.004 s     0.006 s
    9,200           506        3.6e-13      0.017 s     0.027 s
    35,976          940        2.2e-14      0.100 s     1.513 s

Two things worth stating plainly, because they shape when the iterative path
should be chosen at all:

* **Agreement is excellent** -- fourteen digits, everywhere. The backends
  are interchangeable as far as the physics is concerned.
* **Jacobi-preconditioned CG does not scale.** Iteration count tracks
  `sqrt(DOFs)` (112 -> 940 as DOFs go 287 -> 35,976), which is exactly the
  behaviour algebraic multigrid exists to remove, and direct solving is
  faster at every size measurable here. Until a scalable preconditioner is
  available, the iterative backend is a correctness-validated *memory*
  fallback, not a performance win -- which is why `AUTO` keeps preferring
  direct well past the sizes above.
"""

from __future__ import annotations

import pytest

from openpdn.domain.provenance import Quantity
from openpdn.domain.study import (
    AnalysisStudy,
    AttachmentGroup,
    CurrentLoad,
    LoadId,
    MeshSettings,
    SourceId,
    StudyId,
    VoltageSource,
)
from openpdn.domain.units import AMPERE, METRE, VOLT
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer
from openpdn.solver.fem.controls import MeshControls
from openpdn.solver.fem.linear import DIRECT, ITERATIVE, LinearPolicy
from openpdn.solver.fem.post import conservation_report, element_fields
from openpdn.solver.fem.problem import build_problem
from openpdn.solver.fem.solve import Excitation, solve_excitation
from tests.validation.boards import NET, plane_neck_plane_board

_SOURCE = SourceId("src")
_LOAD = LoadId("load")


def _problem_and_excitation(element_size_m: float):
    board = plane_neck_plane_board()
    settings = MeshSettings(
        target_element_size=Quantity.configured(element_size_m, METRE),
        elements_across_feature=4,
        growth_rate=0.7,
    )
    study = AnalysisStudy(
        id=StudyId("backends"),
        name="backends",
        board_id=str(board.id),
        net_ids=(NET,),
        sources=(
            VoltageSource(
                id=_SOURCE,
                attachment=AttachmentGroup(terminal_ids=("term-a",)),  # type: ignore[arg-type]
                voltage=Quantity.configured(0.0, VOLT),
            ),
        ),
        loads=(
            CurrentLoad(
                id=_LOAD,
                attachment=AttachmentGroup(terminal_ids=("term-b",)),  # type: ignore[arg-type]
                current=Quantity.configured(1.0, AMPERE),
            ),
        ),
        mesh=settings,
    )
    problem = build_problem(
        board,
        study,
        ShapelyGeometryNormalizer().normalize(board),
        MeshControls.from_settings(settings),
    )
    excitation = Excitation(
        {problem.source_dofs[_SOURCE]: 0.0},
        {problem.load_dofs[_LOAD]: -1.0},
    )
    return problem, excitation


def _resistance(problem, solution) -> float:
    return abs(
        solution.voltage_v[problem.source_dofs[_SOURCE]]
        - solution.voltage_v[problem.load_dofs[_LOAD]]
    )


@pytest.fixture(scope="module")
def solved_both():
    problem, excitation = _problem_and_excitation(0.25e-3)
    direct = solve_excitation(problem, excitation, LinearPolicy(method=DIRECT))
    iterative = solve_excitation(
        problem,
        excitation,
        LinearPolicy(method=ITERATIVE, target_discretisation_error=1e-8),
    )
    return problem, direct, iterative


class TestCrossValidation:
    def test_terminal_resistance_agrees(self, solved_both):
        problem, direct, iterative = solved_both
        reference = _resistance(problem, direct)
        assert _resistance(problem, iterative) == pytest.approx(reference, rel=1e-9)

    def test_the_whole_potential_field_agrees(self, solved_both):
        # Not just the reported scalar: if the fields differed anywhere, a
        # current-density map would differ even where resistance did not.
        problem, direct, iterative = solved_both
        del problem
        finite = ~(
            (direct.voltage_v != direct.voltage_v)
            | (iterative.voltage_v != iterative.voltage_v)
        )
        spread = max(abs(direct.voltage_v[finite]).max(), 1e-30)
        assert abs(direct.voltage_v[finite] - iterative.voltage_v[finite]).max() < 1e-9 * spread

    def test_integrated_power_and_conservation_agree(self, solved_both):
        problem, direct, iterative = solved_both
        drawn = {problem.load_dofs[_LOAD]: 1.0}
        reports = [
            conservation_report(
                problem, solution, element_fields(problem, solution), drawn
            )
            for solution in (direct, iterative)
        ]
        assert reports[1].dissipated_power_w == pytest.approx(
            reports[0].dissipated_power_w, rel=1e-8
        )
        for report in reports:
            assert report.imbalance_fraction < 1e-6
            assert report.power_mismatch_fraction < 1e-6


class TestReportedDiagnostics:
    def test_the_iterative_solve_reports_a_finite_iteration_count(self, solved_both):
        _, _, iterative = solved_both
        assert iterative.linear is not None
        assert iterative.linear.backend == ITERATIVE
        assert iterative.linear.iterations is not None
        assert 0 < iterative.linear.iterations < 5_000

    def test_the_direct_solve_reports_its_backend(self, solved_both):
        _, direct, _ = solved_both
        assert direct.linear is not None
        assert direct.linear.backend == DIRECT

    def test_both_reach_a_residual_far_below_discretisation_error(self, solved_both):
        # The residual is linear-algebra health, never accuracy -- but it
        # does have to sit well below the discretisation error so that the
        # linear solve is not what limits the answer.
        _, direct, iterative = solved_both
        assert direct.linear.relative_residual < 1e-9
        assert iterative.linear.relative_residual < 1e-8


class TestIterationScaling:
    def test_iteration_count_grows_with_problem_size(self):
        # Documents the measured non-scalability of Jacobi preconditioning:
        # iterations track sqrt(DOFs). This is the empirical case for an
        # algebraic-multigrid preconditioner, and it is why the iterative
        # backend is not yet a performance win. If a future preconditioner
        # makes this flat, that is a success and this test should be
        # revisited deliberately.
        counts = []
        for element_size_m in (1.0e-3, 0.25e-3):
            problem, excitation = _problem_and_excitation(element_size_m)
            solution = solve_excitation(
                problem,
                excitation,
                LinearPolicy(method=ITERATIVE, target_discretisation_error=1e-8),
            )
            counts.append((problem.n_dofs, solution.linear.iterations))
        (small_dofs, small_iters), (large_dofs, large_iters) = counts
        assert large_dofs > small_dofs
        assert large_iters > small_iters
