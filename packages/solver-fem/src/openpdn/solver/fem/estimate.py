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

from openpdn.solver.fem.elements import barycentric_gradients, build_edges

if TYPE_CHECKING:
    import numpy.typing as npt

    from openpdn.solver.fem.problem import SheetProblem


def element_gradients(
    problem: SheetProblem,
    node_voltage_v: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Potential gradient per element from vertex potentials, `(m, 2)`.

    P1 only: the gradient is constant per element, which is what makes the
    jump across an edge a single well-defined number.
    """
    triangles = problem.triangles
    grad_l, _ = barycentric_gradients(problem.points, triangles)
    values = np.nan_to_num(node_voltage_v[triangles], nan=0.0)
    gradients: npt.NDArray[np.float64] = np.einsum("mn,mna->ma", values, grad_l)
    return gradients


def flux_jump_indicators(
    problem: SheetProblem,
    node_voltage_v: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Per-element error indicator `eta_K`, `(m,)`, in amperes.

    Each triangle contributes its own **outward** normal flux to each of its
    edges. Summing those contributions per edge gives the jump directly: for
    an interior edge the two outward normals are opposite, so the sum is
    `(Gs_K grad_K - Gs_K' grad_K') . n`; for a boundary edge the single
    contribution is the leaked normal current, which should be zero. One
    accumulation handles both cases without special-casing.
    """
    triangles = problem.triangles
    if len(triangles) == 0:
        return np.zeros(0, dtype=np.float64)

    edges, tri_edges = build_edges(triangles)
    gradients = element_gradients(problem, node_voltage_v)
    corners = problem.points[triangles]

    # Orientation-independent outward normals: for a counter-clockwise
    # triangle the outward normal of edge (k, k+1) is (dy, -dx); a
    # clockwise triangle flips it, and the signed area supplies the sign.
    signed_area2 = (
        (corners[:, 1, 0] - corners[:, 0, 0]) * (corners[:, 2, 1] - corners[:, 0, 1])
        - (corners[:, 2, 0] - corners[:, 0, 0]) * (corners[:, 1, 1] - corners[:, 0, 1])
    )
    winding = np.where(signed_area2 >= 0.0, 1.0, -1.0)

    jump_per_edge = np.zeros(len(edges), dtype=np.float64)
    edge_length = np.zeros(len(edges), dtype=np.float64)
    for k in range(3):
        start = corners[:, k, :]
        end = corners[:, (k + 1) % 3, :]
        delta = end - start
        length = np.hypot(delta[:, 0], delta[:, 1])
        safe = np.maximum(length, 1e-300)
        normal = np.stack([delta[:, 1], -delta[:, 0]], axis=1) / safe[:, None]
        normal *= winding[:, None]
        flux = problem.tri_sheet_conductance * (gradients * normal).sum(axis=1)
        np.add.at(jump_per_edge, tri_edges[:, k], flux)
        edge_length[tri_edges[:, k]] = length

    squared = np.zeros(len(triangles), dtype=np.float64)
    for k in range(3):
        index = tri_edges[:, k]
        squared += 0.5 * (edge_length[index] ** 2) * (jump_per_edge[index] ** 2)
    return np.sqrt(squared)


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
