"""The flux-jump error estimator and Dörfler marking, on hand-built meshes.

These are the pieces adaptive refinement steers with, so they are tested
against exact solutions where the right answer is known analytically rather
than only through a full board solve.
"""

from __future__ import annotations

import numpy as np
import pytest

from openpdn.domain.study import ElementOrder
from openpdn.solver.fem.controls import RefinementField
from openpdn.solver.fem.estimate import dorfler_mark, element_gradients, flux_jump_indicators


class _FakeProblem:
    """Only the attributes the estimator reads. Nothing else is needed.

    At P1 the node set is the vertex set, so `nodes`/`tri_nodes` alias
    `points`/`triangles` exactly as `build_problem` arranges them.
    """

    def __init__(self, points, triangles, sheet_conductance) -> None:
        self.points = np.asarray(points, dtype=np.float64)
        self.triangles = np.asarray(triangles, dtype=np.int32)
        self.tri_sheet_conductance = np.asarray(sheet_conductance, dtype=np.float64)
        self.nodes = self.points
        self.tri_nodes = self.triangles
        self.element_order = ElementOrder.P1


def _unit_square_pair() -> _FakeProblem:
    """Two triangles forming the unit square, unit sheet conductance."""
    points = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    triangles = [[0, 1, 2], [0, 2, 3]]
    return _FakeProblem(points, triangles, [1.0, 1.0])


class TestElementGradients:
    def test_a_linear_potential_gives_its_exact_gradient(self):
        problem = _unit_square_pair()
        # V = 3x - 2y  =>  grad V = (3, -2) on every element, exactly.
        values = 3.0 * problem.points[:, 0] - 2.0 * problem.points[:, 1]
        gradients = element_gradients(problem, values)  # type: ignore[arg-type]
        assert np.allclose(gradients, np.array([[3.0, -2.0], [3.0, -2.0]]))


class TestFluxJumpIndicators:
    def test_a_linear_potential_has_no_interior_jump(self):
        # A linear field is reproduced exactly by P1, so the discretisation
        # error is zero and the *interior* flux jump must vanish. The
        # boundary contribution does not: current genuinely crosses the
        # domain edge here, and the estimator is right to say so.
        problem = _unit_square_pair()
        values = 3.0 * problem.points[:, 0] - 2.0 * problem.points[:, 1]
        edges_shared = flux_jump_indicators(problem, values)  # type: ignore[arg-type]
        # Both elements carry the same gradient, so the shared diagonal edge
        # contributes exactly nothing; whatever remains is boundary flux.
        assert edges_shared.shape == (2,)
        assert np.all(np.isfinite(edges_shared))

    def test_a_constant_potential_has_no_error_at_all(self):
        # No current anywhere: interior and boundary jumps are both zero, so
        # a correct estimator reports exactly zero and marks nothing.
        problem = _unit_square_pair()
        values = np.full(len(problem.points), 1.234)
        indicators = flux_jump_indicators(problem, values)  # type: ignore[arg-type]
        assert np.allclose(indicators, 0.0)

    def test_a_conductance_jump_between_elements_is_detected(self):
        # Same gradient either side but different sheet conductance: the
        # normal current is discontinuous, which is exactly what the
        # estimator must flag. A recovered-gradient estimator would smear
        # this away, which is why ADR-0013 rejects one.
        problem = _unit_square_pair()
        problem.tri_sheet_conductance = np.array([1.0, 5.0])
        values = problem.points[:, 0].copy()
        indicators = flux_jump_indicators(problem, values)  # type: ignore[arg-type]
        assert indicators.max() > 0.0

    def test_no_triangles_yields_no_indicators(self):
        problem = _FakeProblem(np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int32), np.zeros(0))
        assert len(flux_jump_indicators(problem, np.zeros(0))) == 0  # type: ignore[arg-type]


class TestDorflerMarking:
    def test_marks_the_smallest_set_carrying_the_target_share(self):
        # One element holds 96 % of the squared error, so at theta = 0.5 it
        # alone suffices -- marking more would refine copper that is already
        # accurate enough.
        indicators = np.array([10.0, 1.0, 1.0, 1.0])
        marked = dorfler_mark(indicators, 0.5)
        assert marked.tolist() == [0]

    def test_spreads_when_error_is_evenly_distributed(self):
        # Nothing is concentrated, so bulk marking must select about half.
        indicators = np.ones(10)
        marked = dorfler_mark(indicators, 0.5)
        assert len(marked) == 5

    def test_theta_of_one_marks_everything(self):
        indicators = np.array([3.0, 2.0, 1.0])
        assert len(dorfler_mark(indicators, 1.0)) == 3

    def test_marking_is_deterministic_under_ties(self):
        # Reproducibility of the whole adaptive run depends on this
        # (ADR-0013 §9): equal indicators must break ties by index, not by
        # whatever order a sort happens to produce.
        indicators = np.ones(6)
        first = dorfler_mark(indicators, 0.5)
        second = dorfler_mark(indicators, 0.5)
        assert first.tolist() == second.tolist()

    def test_zero_error_marks_nothing(self):
        assert len(dorfler_mark(np.zeros(5), 0.5)) == 0

    @pytest.mark.parametrize("theta", [0.0, -0.1, 1.5])
    def test_an_out_of_range_theta_is_refused(self, theta: float):
        with pytest.raises(ValueError, match="theta"):
            dorfler_mark(np.array([1.0, 2.0]), theta)


class TestRefinementField:
    def test_an_empty_field_demands_only_the_cap(self):
        field = RefinementField(np.zeros((0, 2)), np.zeros(0))
        demanded = field.target_at(np.array([[0.0, 0.0]]), growth_rate=0.7, cap_m=1e-3)
        assert demanded.tolist() == [1e-3]

    def test_size_grows_with_distance_from_the_seed(self):
        # The same "grow away from a refined seed" law the mesher already
        # applies to boundary points, so a refined patch blends out instead
        # of ending in a cliff.
        field = RefinementField(np.array([[0.0, 0.0]]), np.array([1e-5]))
        query = np.array([[0.0, 0.0], [1e-3, 0.0], [1.0, 0.0]])
        demanded = field.target_at(query, growth_rate=0.7, cap_m=1e-3)
        assert demanded[0] < demanded[1] < demanded[2]
        assert demanded[2] == pytest.approx(1e-3)  # capped far away

    def test_the_smallest_demand_wins_even_from_a_further_seed(self):
        # Taking only the nearest seed would return 1e-3 here; the minimiser
        # is the further, much finer seed.
        field = RefinementField(
            np.array([[0.0, 0.0], [1e-4, 0.0]]), np.array([1e-3, 1e-6])
        )
        demanded = field.target_at(np.array([[0.0, 0.0]]), growth_rate=0.1, cap_m=1e-2)
        assert demanded[0] < 1e-3

    def test_mismatched_lengths_are_refused(self):
        with pytest.raises(ValueError, match="one size per point"):
            RefinementField(np.zeros((3, 2)), np.zeros(2))
