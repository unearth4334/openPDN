"""Element-order validation against exact solutions on a bare square.

Per ADR-0012, P2 is not trusted because a formula was typed in correctly --
it is trusted because it reproduces solutions it should reproduce exactly and
converges at the rate theory predicts on ones it should not. Both are checked
here on a structured unit square, deliberately away from PCB geometry: a
reentrant corner or a conductance jump depresses the observed rate locally, so
a board is the wrong place to measure whether the *basis functions* are right
(spec: method of manufactured solutions).

The manufactured solutions are all **harmonic**, so they satisfy
`div(grad V) = 0` with no body source -- which is exactly the equation the
solver assembles. That keeps these tests honest about the operator actually
shipped rather than requiring a forcing term the production code has no way
to apply.
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from openpdn.domain.study import ElementOrder
from openpdn.solver.fem.elements import (
    build_edges,
    element_stiffness,
    nodes_per_element,
    observed_order,
    p2_nodes,
)


def _structured_square(n: int, jitter: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """A unit square cut into `2 n^2` triangles on an `(n+1)^2` grid.

    `jitter` displaces interior vertices by that fraction of the grid spacing,
    from a fixed seed. It exists because a *uniform* mesh is a special case,
    not a neutral one: on it the P1 stiffness reduces to the five-point
    finite-difference Laplacian, whose truncation error involves only fourth
    derivatives, so P1 reproduces harmonic quadratics *exactly at the nodes*.
    That superconvergence is a property of the grid, not of the basis, and it
    hides the very difference between P1 and P2 that these tests exist to
    measure. An irregular mesh -- which is what the real mesher produces --
    removes it.
    """
    xs = np.linspace(0.0, 1.0, n + 1)
    grid_x, grid_y = np.meshgrid(xs, xs, indexing="ij")
    points = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    if jitter:
        rng = np.random.default_rng(20260816)
        interior = (
            (points[:, 0] > 1e-12)
            & (points[:, 0] < 1.0 - 1e-12)
            & (points[:, 1] > 1e-12)
            & (points[:, 1] < 1.0 - 1e-12)
        )
        shift = rng.uniform(-jitter, jitter, size=(int(interior.sum()), 2)) / n
        points = points.copy()
        points[interior] += shift

    def index(i: int, j: int) -> int:
        return i * (n + 1) + j

    triangles = []
    for i in range(n):
        for j in range(n):
            a, b, c, d = index(i, j), index(i + 1, j), index(i + 1, j + 1), index(i, j + 1)
            triangles.append([a, b, c])
            triangles.append([a, c, d])
    return points, np.asarray(triangles, dtype=np.int32)


def _solve_dirichlet(
    points: np.ndarray,
    triangles: np.ndarray,
    order: ElementOrder,
    exact,
) -> tuple[np.ndarray, np.ndarray]:
    """Solve with every boundary node pinned to the exact solution.

    Returns `(nodes, solution)`. Unit sheet conductance throughout: this is a
    test of the basis, not of material handling.
    """
    if order is ElementOrder.P2:
        nodes, tri_nodes = p2_nodes(points, triangles)
    else:
        nodes, tri_nodes = points, triangles

    conductance = np.ones(len(triangles), dtype=np.float64)
    local = element_stiffness(nodes, tri_nodes, conductance, order)

    k = nodes_per_element(order)
    rows = np.repeat(tri_nodes, k, axis=1).ravel()
    cols = np.tile(tri_nodes, (1, k)).ravel()
    matrix = sp.coo_matrix(
        (local.reshape(-1), (rows, cols)), shape=(len(nodes), len(nodes))
    ).tocsr()

    on_boundary = (
        np.isclose(nodes[:, 0], 0.0)
        | np.isclose(nodes[:, 0], 1.0)
        | np.isclose(nodes[:, 1], 0.0)
        | np.isclose(nodes[:, 1], 1.0)
    )
    fixed = np.flatnonzero(on_boundary)
    free = np.flatnonzero(~on_boundary)

    values = exact(nodes[:, 0], nodes[:, 1])
    solution = values.copy()
    if len(free):
        rhs = -matrix[free][:, fixed] @ values[fixed]
        solution[free] = spla.spsolve(matrix[free][:, free].tocsc(), rhs)
    return nodes, solution


def _l2_node_error(nodes: np.ndarray, solution: np.ndarray, exact) -> float:
    """Root-mean-square nodal error -- a discrete L2 norm."""
    diff = solution - exact(nodes[:, 0], nodes[:, 1])
    return float(np.sqrt(np.mean(diff**2)))


# Harmonic (source-free) manufactured solutions.
def _linear(x, y):
    return 1.0 + 2.0 * x - 3.0 * y


def _quadratic_harmonic(x, y):
    return x**2 - y**2


def _quartic_harmonic(x, y):
    # Re((x + iy)^4): harmonic, and outside both the P1 and P2 spaces, so it
    # is what actually exercises the convergence *rate* of each order.
    return x**4 - 6.0 * x**2 * y**2 + y**4


def _transcendental_harmonic(x, y):
    return np.exp(x) * np.sin(y)


class TestStiffnessInvariants:
    """Properties that must hold for any correct element, at any order."""

    @pytest.mark.parametrize("order", [ElementOrder.P1, ElementOrder.P2])
    def test_constant_field_is_in_the_kernel(self, order: ElementOrder):
        # Partition of unity: the basis functions sum to 1 everywhere, so a
        # constant potential must drive no current. If row sums are not zero
        # the gradients are wrong, and every solve would leak current.
        points, triangles = _structured_square(4)
        nodes, tri_nodes = (
            p2_nodes(points, triangles) if order is ElementOrder.P2 else (points, triangles)
        )
        local = element_stiffness(nodes, tri_nodes, np.ones(len(triangles)), order)
        assert np.abs(local.sum(axis=2)).max() < 1e-12

    @pytest.mark.parametrize("order", [ElementOrder.P1, ElementOrder.P2])
    def test_local_matrices_are_symmetric(self, order: ElementOrder):
        points, triangles = _structured_square(4)
        nodes, tri_nodes = (
            p2_nodes(points, triangles) if order is ElementOrder.P2 else (points, triangles)
        )
        local = element_stiffness(nodes, tri_nodes, np.ones(len(triangles)), order)
        assert np.abs(local - local.transpose(0, 2, 1)).max() < 1e-12

    @pytest.mark.parametrize("order", [ElementOrder.P1, ElementOrder.P2])
    def test_a_linear_field_is_reproduced_exactly(self, order: ElementOrder):
        # The patch test: a linear potential lies in both bases, so both
        # orders must return it to machine precision on any mesh.
        points, triangles = _structured_square(6)
        nodes, solution = _solve_dirichlet(points, triangles, order, _linear)
        assert _l2_node_error(nodes, solution, _linear) < 1e-12


class TestEdgeEnumeration:
    def test_edge_count_matches_eulers_formula(self):
        # A triangulated disc satisfies V - E + F = 1 counting only interior
        # faces, so E = V + T - 1. Getting this wrong means duplicated or
        # dropped midpoint nodes, which would corrupt every P2 solve.
        points, triangles = _structured_square(5)
        edges, tri_edges = build_edges(triangles)
        assert len(edges) == len(points) + len(triangles) - 1
        assert tri_edges.shape == (len(triangles), 3)

    def test_edges_are_shared_between_neighbouring_triangles(self):
        _, triangles = _structured_square(4)
        _, tri_edges = build_edges(triangles)
        counts = np.bincount(tri_edges.ravel())
        # Every edge belongs to one triangle (boundary) or two (interior).
        assert set(np.unique(counts)).issubset({1, 2})

    def test_numbering_is_deterministic(self):
        # Adaptive refinement depends on the whole pipeline being
        # reproducible (ADR-0013); an edge numbering that varied run to run
        # would break that silently.
        _, triangles = _structured_square(4)
        first, _ = build_edges(triangles)
        second, _ = build_edges(triangles)
        assert np.array_equal(first, second)

    def test_p2_keeps_the_vertex_block_as_a_prefix(self):
        # ADR-0012 §3: midpoints are appended, never interleaved, so every
        # existing vertex-indexed routine keeps working untouched.
        points, triangles = _structured_square(3)
        nodes, tri_nodes = p2_nodes(points, triangles)
        assert np.array_equal(nodes[: len(points)], points)
        assert np.array_equal(tri_nodes[:, :3], triangles)
        assert tri_nodes[:, 3:].min() >= len(points)


class TestQuadraticExactness:
    """P2 contains the quadratics; P1 does not. The gap must be visible.

    Both cases run on an *irregular* mesh -- see `_structured_square` -- since
    a uniform grid makes P1 nodally exact here too and would make the contrast
    vanish for reasons that have nothing to do with the basis.
    """

    def test_p2_reproduces_a_harmonic_quadratic_exactly(self):
        # x^2 - y^2 is both harmonic and inside the P2 space, so the exact
        # solution *is* the discrete solution -- error should be roundoff, on
        # any mesh. This is the sharpest single check that the quadratic
        # basis and the quadrature are right.
        points, triangles = _structured_square(6, jitter=0.3)
        nodes, solution = _solve_dirichlet(points, triangles, ElementOrder.P2, _quadratic_harmonic)
        assert _l2_node_error(nodes, solution, _quadratic_harmonic) < 1e-11

    def test_p1_does_not_reproduce_it(self):
        # The contrast matters: if P1 also passed, the test above would be
        # measuring the mesh, not the basis.
        points, triangles = _structured_square(6, jitter=0.3)
        nodes, solution = _solve_dirichlet(points, triangles, ElementOrder.P1, _quadratic_harmonic)
        assert _l2_node_error(nodes, solution, _quadratic_harmonic) > 1e-6


@pytest.mark.parametrize(
    ("exact", "name"),
    [(_quartic_harmonic, "quartic"), (_transcendental_harmonic, "exp-sin")],
)
class TestConvergenceOrder:
    """Observed rate, not just "the error got smaller"."""

    def test_p1_converges_at_second_order(self, exact, name: str):
        del name
        sizes, errors = _sweep(ElementOrder.P1, exact)
        assert observed_order(errors, sizes) == pytest.approx(2.0, abs=0.25)

    def test_p2_converges_faster_than_p1(self, exact, name: str):
        del name
        _, p1_errors = _sweep(ElementOrder.P1, exact)
        _, p2_errors = _sweep(ElementOrder.P2, exact)
        # On the same triangulation the quadratic basis must be strictly
        # better everywhere, not merely better asymptotically.
        assert all(q < p for q, p in zip(p2_errors, p1_errors, strict=True))

    def test_p2_converges_at_third_order_or_better(self, exact, name: str):
        del name
        sizes, errors = _sweep(ElementOrder.P2, exact)
        # Nodal L2 for P2 is cubic in h. Asserting ">= 2.75" rather than
        # "== 3" keeps the test honest about a fitted slope on four points
        # while still failing loudly if P2 quietly degraded to P1's rate.
        assert observed_order(errors, sizes) > 2.75


class TestAccuracyPerDegreeOfFreedom:
    """The claim that justifies P2 at all: better answer for the same cost.

    DOFs are what a solve is billed in -- matrix size, factorisation memory,
    time. "P2 is better on the same triangulation" is not interesting on its
    own, because that triangulation costs four times as much at P2. The
    comparison that matters holds the DOF count fixed.
    """

    def test_p2_beats_p1_at_an_equal_dof_count(self):
        # A 16x16 grid at P2 and a 32x32 grid at P1 both give 1089 nodes.
        coarse_points, coarse_triangles = _structured_square(16)
        fine_points, fine_triangles = _structured_square(32)

        p2_nodes_, _ = p2_nodes(coarse_points, coarse_triangles)
        assert len(p2_nodes_) == len(fine_points), "the DOF counts must match to compare fairly"

        nodes_p2, solution_p2 = _solve_dirichlet(
            coarse_points, coarse_triangles, ElementOrder.P2, _quartic_harmonic
        )
        nodes_p1, solution_p1 = _solve_dirichlet(
            fine_points, fine_triangles, ElementOrder.P1, _quartic_harmonic
        )
        error_p2 = _l2_node_error(nodes_p2, solution_p2, _quartic_harmonic)
        error_p1 = _l2_node_error(nodes_p1, solution_p1, _quartic_harmonic)

        # Measured at 1089 DOFs: P1 1.56e-4, P2 1.44e-6 -- about 100x. The
        # assertion is deliberately looser than the measurement so it tests
        # the claim rather than pinning a machine-specific number.
        assert error_p2 < error_p1 / 20.0


def _sweep(order: ElementOrder, exact) -> tuple[list[float], list[float]]:
    """Nodal error across a refinement sequence."""
    sizes: list[float] = []
    errors: list[float] = []
    for n in (4, 8, 16, 32):
        points, triangles = _structured_square(n)
        nodes, solution = _solve_dirichlet(points, triangles, order, exact)
        sizes.append(1.0 / n)
        errors.append(_l2_node_error(nodes, solution, exact))
    return sizes, errors
