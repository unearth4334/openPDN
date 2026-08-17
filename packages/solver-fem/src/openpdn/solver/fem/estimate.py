"""A posteriori error estimation for the sheet-conduction problem.

The estimator is the residual-based **edge flux jump** (ADR-0013 §2):

    eta_K^2 = (1/2) sum over edges e of K  of  |e|^2 * [ Gs grad(V) . n ]_e^2

The interior residual term of the standard explicit estimator is absent for
a reason, not an omission: the source term is zero and sheet conductance is
constant within an element, so `div(Gs grad V)` vanishes identically on every
element. What is left is the jump in normal sheet current across element
edges -- the physical continuity condition the discretisation violates, and
the right quantity to measure even where `Gs` genuinely jumps between regions
or layers.

Deliberately *not* a recovered-gradient (Zienkiewicz-Zhu) estimator: patch
recovery averages flux over the elements around a node, and at a real sheet
conductance discontinuity the true flux is discontinuous, so recovery would
report large error exactly where the answer is correct and drive refinement
into every region boundary on the board.

Boundary edges count too. Copper edges are insulated, so the exact solution
carries no normal current through them; whatever the discrete solution does
carry there is discretisation error. Current enters through terminal contact
*regions*, which are interior collapsed nodes, not mesh boundary edges, so
this does not mistake a driven terminal for an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from openpdn.solver.fem.elements import (
    barycentric_gradients,
    build_edges,
    shape_gradients,
)

if TYPE_CHECKING:
    import numpy.typing as npt

    from openpdn.solver.fem.problem import SheetProblem


def element_gradients(
    problem: SheetProblem,
    node_values: npt.NDArray[np.float64],
    barycentric: npt.NDArray[np.float64] | None = None,
) -> npt.NDArray[np.float64]:
    """Potential gradient per element at one point, `(m, 2)`.

    At P1 the gradient is constant, so `barycentric` is irrelevant. At P2 it
    varies linearly across the element and the evaluation point matters --
    which is why the flux jump has to be integrated along an edge rather than
    sampled once.
    """
    grad_l, _ = barycentric_gradients(problem.nodes, problem.tri_nodes)
    where = np.full(3, 1.0 / 3.0) if barycentric is None else np.asarray(barycentric)
    shapes = shape_gradients(grad_l, where, problem.element_order)
    values = np.nan_to_num(node_values[problem.tri_nodes], nan=0.0)
    gradients: npt.NDArray[np.float64] = np.einsum("mn,mna->ma", values, shapes)
    return gradients


def flux_jump_indicators(
    problem: SheetProblem,
    node_values: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Per-element error indicator `eta_K`, `(m,)`, in amperes.

    Each triangle contributes its own **outward** normal flux to each of its
    edges. Summing those contributions per edge gives the jump directly: for
    an interior edge the two outward normals are opposite, so the sum is
    `(Gs_K grad_K - Gs_K' grad_K') . n`; for a boundary edge the single
    contribution is the leaked normal current, which should be zero. One
    accumulation handles both cases without special-casing.

    The jump is evaluated at **both endpoints** of each edge and integrated
    exactly along it. At P1 the two values coincide and this reduces to the
    familiar `|e|^2 J^2`; at P2 the flux varies linearly along the edge, and
    sampling it once would misreport the error by an amount that grows with
    the very gradients adaptivity is chasing.
    """
    triangles = problem.triangles
    if len(triangles) == 0:
        return np.zeros(0, dtype=np.float64)

    edges, tri_edges = build_edges(triangles)
    corners = problem.points[triangles]

    # Orientation-independent outward normals: for a counter-clockwise
    # triangle the outward normal of edge (k, k+1) is (dy, -dx); a
    # clockwise triangle flips it, and the signed area supplies the sign.
    signed_area2 = (
        (corners[:, 1, 0] - corners[:, 0, 0]) * (corners[:, 2, 1] - corners[:, 0, 1])
        - (corners[:, 2, 0] - corners[:, 0, 0]) * (corners[:, 1, 1] - corners[:, 0, 1])
    )
    winding = np.where(signed_area2 >= 0.0, 1.0, -1.0)

    # Jump at each edge's two *global* endpoints, kept in the edge's own
    # vertex order so contributions from the two adjacent triangles line up.
    jump_at = np.zeros((len(edges), 2), dtype=np.float64)
    edge_length = np.zeros(len(edges), dtype=np.float64)
    for k in range(3):
        following = (k + 1) % 3
        start = corners[:, k, :]
        end = corners[:, following, :]
        delta = end - start
        length = np.hypot(delta[:, 0], delta[:, 1])
        safe = np.maximum(length, 1e-300)
        normal = np.stack([delta[:, 1], -delta[:, 0]], axis=1) / safe[:, None]
        normal *= winding[:, None]

        at_start = _normal_flux(problem, node_values, normal, _vertex_barycentric(k))
        at_end = _normal_flux(problem, node_values, normal, _vertex_barycentric(following))

        edge_index = tri_edges[:, k]
        forward = triangles[:, k] == edges[edge_index, 0]
        np.add.at(jump_at[:, 0], edge_index, np.where(forward, at_start, at_end))
        np.add.at(jump_at[:, 1], edge_index, np.where(forward, at_end, at_start))
        edge_length[edge_index] = length

    # Exact integral of a linear function's square along the edge:
    # int_e J^2 = |e| (J0^2 + J0 J1 + J1^2) / 3.
    first, second = jump_at[:, 0], jump_at[:, 1]
    integral = edge_length * (first**2 + first * second + second**2) / 3.0

    squared = np.zeros(len(triangles), dtype=np.float64)
    for k in range(3):
        index = tri_edges[:, k]
        squared += 0.5 * edge_length[index] * integral[index]
    return np.sqrt(squared)


def _vertex_barycentric(local_vertex: int) -> npt.NDArray[np.float64]:
    """Barycentric coordinates of one of a triangle's three vertices."""
    lam = np.zeros(3, dtype=np.float64)
    lam[local_vertex] = 1.0
    return lam


def _normal_flux(
    problem: SheetProblem,
    node_values: npt.NDArray[np.float64],
    normal: npt.NDArray[np.float64],
    barycentric: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Outward normal sheet current at one point of each element."""
    gradients = element_gradients(problem, node_values, barycentric)
    flux: npt.NDArray[np.float64] = problem.tri_sheet_conductance * (gradients * normal).sum(axis=1)
    return flux


def global_error_estimate(indicators: npt.NDArray[np.float64]) -> float:
    """Root-sum-square of the element indicators."""
    return float(np.sqrt(float((indicators**2).sum()))) if len(indicators) else 0.0


def dorfler_mark(
    indicators: npt.NDArray[np.float64],
    theta: float,
) -> npt.NDArray[np.int64]:
    """Smallest element set carrying `theta` of the total squared error.

    Bulk marking (ADR-0013 §6): refining the whole board because two per cent
    of the copper needs resolution is exactly what adaptivity exists to
    avoid, and refining only the single worst element converges far too
    slowly to be worth the re-mesh.
    """
    if len(indicators) == 0:
        return np.zeros(0, dtype=np.int64)
    if not 0.0 < theta <= 1.0:
        raise ValueError(f"Dorfler theta must be in (0, 1], got {theta!r}")
    squared = indicators**2
    total = float(squared.sum())
    if total <= 0.0:
        return np.zeros(0, dtype=np.int64)
    # Descending by error, with index as a deterministic tie-break: the
    # adaptive loop's reproducibility depends on this ordering (ADR-0013 §9).
    order = np.lexsort((np.arange(len(squared)), -squared))
    cumulative = np.cumsum(squared[order])
    needed = int(np.searchsorted(cumulative, theta * total)) + 1
    return np.sort(order[: min(needed, len(order))]).astype(np.int64)
