"""Backend selection, tolerance derivation, and refusal discipline.

The numerical agreement between backends is measured on real boards in
`tests/validation/test_linear_backends.py`; this file covers the policy and
the failure paths, where the rules matter more than the physics.
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.sparse as sp

from openpdn.solver.api import SolverConvergenceError
from openpdn.solver.fem.linear import (
    AUTO,
    DEFAULT_RELATIVE_TOLERANCE,
    DIRECT,
    ITERATIVE,
    LinearPolicy,
    solve_reduced,
)


def _spd_matrix(n: int = 40) -> sp.csc_matrix:
    """A 1-D Laplacian: symmetric positive definite, like the real operator."""
    main = 2.0 * np.ones(n)
    off = -1.0 * np.ones(n - 1)
    return sp.diags([off, main, off], [-1, 0, 1], format="csc")


class TestBackendSelection:
    def test_auto_prefers_direct_for_small_problems(self):
        assert LinearPolicy(method=AUTO).resolve(1_000) == DIRECT

    def test_auto_switches_to_iterative_above_the_threshold(self):
        # A memory guard, not a speed optimisation -- see the constant's note.
        policy = LinearPolicy(method=AUTO, auto_direct_max_dofs=1_000)
        assert policy.resolve(1_001) == ITERATIVE

    def test_an_explicit_choice_overrides_size(self):
        assert LinearPolicy(method=ITERATIVE).resolve(1) == ITERATIVE
        assert LinearPolicy(method=DIRECT).resolve(10**9) == DIRECT

    def test_an_unknown_backend_is_refused(self):
        with pytest.raises(ValueError, match="Unknown linear-solver backend"):
            solve_reduced(_spd_matrix(), np.ones(40), LinearPolicy(method="magic"))


class TestToleranceDerivation:
    def test_tolerance_is_a_fraction_of_the_discretisation_target(self):
        # ADR-0014 §6: the linear solve must never be the accuracy-limiting
        # step, so its tolerance follows the discretisation target rather
        # than being a fixed constant.
        policy = LinearPolicy(target_discretisation_error=1e-4, tolerance_fraction=0.05)
        assert policy.relative_tolerance() == pytest.approx(5e-6)

    def test_a_tighter_target_gives_a_tighter_tolerance(self):
        loose = LinearPolicy(target_discretisation_error=1e-3).relative_tolerance()
        tight = LinearPolicy(target_discretisation_error=1e-6).relative_tolerance()
        assert tight < loose

    def test_without_a_target_a_strict_default_applies(self):
        assert LinearPolicy().relative_tolerance() == DEFAULT_RELATIVE_TOLERANCE

    def test_the_tolerance_never_goes_below_machine_precision(self):
        # Asking for an impossible tolerance must not make every solve fail.
        policy = LinearPolicy(target_discretisation_error=1e-300)
        assert policy.relative_tolerance() > 0.0


class TestSolveAndRefusal:
    @pytest.mark.parametrize("method", [DIRECT, ITERATIVE])
    def test_both_backends_solve_an_spd_system(self, method: str):
        matrix = _spd_matrix()
        expected = np.arange(matrix.shape[0], dtype=np.float64)
        rhs = matrix @ expected
        solution, report = solve_reduced(matrix, rhs, LinearPolicy(method=method))
        assert np.allclose(solution, expected)
        assert report.backend == method
        assert report.relative_residual < 1e-8

    def test_the_iterative_backend_reports_its_iterations(self):
        # ADR-0014 §7: iteration count and preconditioner are part of the
        # result, not debug output.
        matrix = _spd_matrix()
        _, report = solve_reduced(matrix, np.ones(40), LinearPolicy(method=ITERATIVE))
        assert report.iterations is not None
        assert report.iterations > 0
        # Best-available preconditioner: AMG when pyamg is installed,
        # Jacobi otherwise. Either way the report names what actually ran.
        assert report.preconditioner in {"pyamg-smoothed-aggregation", "jacobi"}

    def test_the_direct_backend_reports_no_iterations(self):
        _, report = solve_reduced(_spd_matrix(), np.ones(40), LinearPolicy(method=DIRECT))
        assert report.iterations is None
        assert report.preconditioner == "none"

    def test_exhausting_the_iteration_budget_is_a_failure_not_a_result(self):
        # The rule this tier turns on: a solver that stopped because it ran
        # out of iterations has not produced an answer, and must not hand
        # back its current iterate as though it had.
        matrix = _spd_matrix(200)
        rhs = np.random.default_rng(0).normal(size=200)
        with pytest.raises(SolverConvergenceError, match="did not converge"):
            solve_reduced(
                matrix,
                rhs,
                LinearPolicy(method=ITERATIVE, max_iterations=1),
            )

    def test_a_singular_system_is_refused_by_the_direct_backend(self):
        # A floating component: no Dirichlet condition anywhere, so the
        # matrix is singular. Refusing beats returning arbitrary potentials.
        matrix = sp.csc_matrix(np.array([[1.0, -1.0], [-1.0, 1.0]]))
        with pytest.raises(SolverConvergenceError):
            solve_reduced(matrix, np.array([1.0, -1.0]), LinearPolicy(method=DIRECT))


class TestReportContents:
    @pytest.mark.parametrize("method", [DIRECT, ITERATIVE])
    def test_timings_and_reason_are_always_populated(self, method: str):
        _, report = solve_reduced(_spd_matrix(), np.ones(40), LinearPolicy(method=method))
        assert report.factor_seconds >= 0.0
        assert report.solve_seconds >= 0.0
        assert report.converged_reason
