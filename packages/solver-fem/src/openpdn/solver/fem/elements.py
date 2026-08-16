"""Element formulations: linear (P1) and quadratic (P2) triangles.

This module is deliberately pure geometry and algebra -- points, triangles and
sheet conductances in, element stiffness matrices out. It knows nothing about
boards, nets, terminals or vias, which is what lets the basis functions be
validated against analytical solutions on a bare square (ADR-0012) instead of
only through a full PCB solve.

The two orders share one contract: `element_stiffness` returns `(m, k, k)`
local matrices over `k = nodes_per_element(order)` nodes, and the caller
scatters them with whatever DOF map it has.

P2 node layout (ADR-0012 §3): the three vertex nodes keep the indices the
mesher already assigned, and edge-midpoint nodes are *appended* after the
whole vertex block. The P1 node set is therefore exactly the P2 vertex
prefix, so every vertex-indexed routine upstream keeps working unchanged.
Local node `3 + k` is the midpoint of local edge `k`, which joins local
vertices `k` and `k + 1 (mod 3)`.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Final

import numpy as np

from openpdn.domain.study import ElementOrder

if TYPE_CHECKING:
    import numpy.typing as npt

#: Symmetric three-point rule on the reference triangle, barycentric
#: permutations of (2/3, 1/6, 1/6) with equal weights. Exact to degree 2 --
#: and degree 2 is exactly what the P2 stiffness integrand is: on a
#: straight-sided triangle the Jacobian is constant, P2 gradients are linear,
#: so grad(phi_i).grad(phi_j) is quadratic and this rule integrates it with no
#: quadrature error at all. A higher-order rule would buy nothing and would
#: mask mistakes (ADR-0012 §2).
_QUADRATURE_BARYCENTRIC: Final = np.array(
    [
        [2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0],
        [1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0],
        [1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0],
    ],
    dtype=np.float64,
)

#: Weights normalised to sum to one, so an integral is `area * sum(w * f)`.
_QUADRATURE_WEIGHTS: Final = np.full(3, 1.0 / 3.0, dtype=np.float64)

#: Guards division by a degenerate element area. A triangle this small has
#: already been rejected as a sliver by the mesher; the floor exists so a
#: pathological input produces a huge-but-finite conductance rather than a
#: NaN that would silently poison the whole matrix.
_MIN_ABS_AREA2: Final = 1e-300


def quadrature_rule() -> list[tuple[float, npt.NDArray[np.float64]]]:
    """The degree-2 rule as `(weight, barycentric)` pairs, weights summing to 1.

    Exposed so post-processing integrates with exactly the rule the stiffness
    was built with; two different rules for the same integrand is how energy
    balance drifts.
    """
    return [
        (float(w), lam) for w, lam in zip(_QUADRATURE_WEIGHTS, _QUADRATURE_BARYCENTRIC, strict=True)
    ]


def nodes_per_element(order: ElementOrder) -> int:
    """Number of nodes one element carries at `order`."""
    return 3 if order is ElementOrder.P1 else 6


def build_edges(
    triangles: npt.NDArray[np.int32],
) -> tuple[npt.NDArray[np.int32], npt.NDArray[np.int32]]:
    """Enumerate undirected mesh edges and each triangle's three edge indices.

    Local edge `k` joins local vertices `k` and `k + 1 (mod 3)`, so the
    returned `tri_edges[:, k]` is the global index of that edge.

    Edges are produced in sorted order, which makes the numbering a pure
    function of the triangle array -- adaptive refinement depends on the whole
    pipeline being deterministic (ADR-0013 §9), and an edge numbering that
    varied with dictionary or set iteration order would break that quietly.

    Returns:
        `(edges, tri_edges)` with shapes `(n_edges, 2)` and `(m, 3)`.
    """
    if len(triangles) == 0:
        return (
            np.zeros((0, 2), dtype=np.int32),
            np.zeros((0, 3), dtype=np.int32),
        )
    pairs = np.stack(
        [triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]], axis=1
    )  # (m, 3, 2)
    undirected = np.sort(pairs, axis=2)
    edges, inverse = np.unique(undirected.reshape(-1, 2), axis=0, return_inverse=True)
    return edges.astype(np.int32), inverse.reshape(-1, 3).astype(np.int32)


def p2_nodes(
    points: npt.NDArray[np.float64],
    triangles: npt.NDArray[np.int32],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int32]]:
    """Build the P2 node set: vertices unchanged, edge midpoints appended.

    Returns:
        `(nodes, tri_nodes)` where `nodes[: len(points)] is` the original
        vertex block and `tri_nodes` is `(m, 6)`.
    """
    edges, tri_edges = build_edges(triangles)
    midpoints = points[edges].mean(axis=1) if len(edges) else np.zeros((0, 2), dtype=np.float64)
    nodes = np.vstack([points, midpoints])
    tri_nodes = np.hstack([triangles, tri_edges + len(points)]).astype(np.int32)
    return nodes, tri_nodes


def barycentric_gradients(
    points: npt.NDArray[np.float64],
    triangles: npt.NDArray[np.int32],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Gradients of the three barycentric coordinates, and signed twice-area.

    `grad(L_i) = (b_i, c_i) / area2` with `b_i = y_j - y_k`, `c_i = x_k - x_j`
    cyclic -- the same `b`/`c` the P1 stiffness has always used.

    Returns:
        `(grad_l, area2)` with shapes `(m, 3, 2)` and `(m,)`. `area2` is
        signed: positive for a counter-clockwise triangle.
    """
    p = points[triangles[:, :3]]
    x = p[:, :, 0]
    y = p[:, :, 1]
    b = np.stack([y[:, 1] - y[:, 2], y[:, 2] - y[:, 0], y[:, 0] - y[:, 1]], axis=1)
    c = np.stack([x[:, 2] - x[:, 1], x[:, 0] - x[:, 2], x[:, 1] - x[:, 0]], axis=1)
    area2 = x[:, 0] * b[:, 0] + x[:, 1] * b[:, 1] + x[:, 2] * b[:, 2]
    safe = np.where(np.abs(area2) < _MIN_ABS_AREA2, _MIN_ABS_AREA2, area2)
    grad_l = np.stack([b, c], axis=2) / safe[:, None, None]
    return grad_l, area2


def shape_gradients(
    grad_l: npt.NDArray[np.float64],
    barycentric: npt.NDArray[np.float64],
    order: ElementOrder,
) -> npt.NDArray[np.float64]:
    """Basis-function gradients at one point, given barycentric coordinates.

    For P1 the gradients are the barycentric gradients themselves and do not
    depend on where in the element they are evaluated. For P2, with
    `L = (L0, L1, L2)`:

        vertex i          phi_i = L_i (2 L_i - 1)   grad = (4 L_i - 1) grad(L_i)
        midpoint of (i,j) phi   = 4 L_i L_j         grad = 4 (L_i grad(L_j)
                                                             + L_j grad(L_i))

    Args:
        grad_l: `(m, 3, 2)` barycentric gradients.
        barycentric: The three barycentric coordinates of the evaluation point.
        order: Element order.

    Returns:
        `(m, k, 2)` gradients over the element's `k` nodes.
    """
    if order is ElementOrder.P1:
        return grad_l
    lam = np.asarray(barycentric, dtype=np.float64)
    out = np.empty((len(grad_l), 6, 2), dtype=np.float64)
    for i in range(3):
        out[:, i, :] = (4.0 * lam[i] - 1.0) * grad_l[:, i, :]
    for k in range(3):
        j = (k + 1) % 3
        out[:, 3 + k, :] = 4.0 * (lam[k] * grad_l[:, j, :] + lam[j] * grad_l[:, k, :])
    return out


def element_stiffness(
    points: npt.NDArray[np.float64],
    tri_nodes: npt.NDArray[np.int32],
    sheet_conductance: npt.NDArray[np.float64],
    order: ElementOrder,
) -> npt.NDArray[np.float64]:
    """Local stiffness of `div(Gs grad V) = 0` for every element.

    Args:
        points: `(n, 2)` node coordinates. For P2 this is the full node set
            from `p2_nodes`; only the vertex columns of `tri_nodes` are used
            for the geometry, since the elements are straight-sided.
        tri_nodes: `(m, 3)` for P1 or `(m, 6)` for P2.
        sheet_conductance: `(m,)` `sigma * t` per element, constant within it.
        order: Element order.

    Returns:
        `(m, k, k)` symmetric local matrices.
    """
    grad_l, area2 = barycentric_gradients(points, tri_nodes)
    area = np.abs(area2) / 2.0
    scale = sheet_conductance * area

    if order is ElementOrder.P1:
        # P1 gradients are constant per element, so the single-point rule is
        # exact and the quadrature loop below would be three identical terms.
        grad = grad_l
        p1_local: npt.NDArray[np.float64] = np.einsum("mia,mja,m->mij", grad, grad, scale)
        return p1_local

    k = nodes_per_element(order)
    local = np.zeros((len(tri_nodes), k, k), dtype=np.float64)
    for weight, lam in zip(_QUADRATURE_WEIGHTS, _QUADRATURE_BARYCENTRIC, strict=True):
        grad = shape_gradients(grad_l, lam, order)
        local += np.einsum("mia,mja,m->mij", grad, grad, scale * weight)
    return local


def interpolate_at_nodes(
    values_at_vertices: npt.NDArray[np.float64],
    points: npt.NDArray[np.float64],
    triangles: npt.NDArray[np.int32],
) -> npt.NDArray[np.float64]:
    """Lift a vertex-valued field onto the P2 node set by edge averaging.

    Only exact for fields that are linear along each edge; used for initial
    guesses and for comparing a P1 field against a P2 one, never for
    reporting a P2 solution.
    """
    edges, _ = build_edges(triangles)
    del points
    midpoint_values = (
        values_at_vertices[edges].mean(axis=1) if len(edges) else np.zeros(0, dtype=np.float64)
    )
    return np.concatenate([values_at_vertices, midpoint_values])


def observed_order(errors: list[float], sizes: list[float]) -> float:
    """Least-squares convergence order from an error-versus-size sequence.

    Fits `log(error) = p log(h) + c` and returns `p`. Reporting the *observed*
    order is the point: a wrong basis function still converges, just at the
    wrong rate, so "the error got smaller" proves nothing on its own
    (ADR-0012 consequences).
    """
    if len(errors) != len(sizes) or len(errors) < 2:
        raise ValueError("Need at least two matching (error, size) samples")
    if any(e <= 0.0 for e in errors) or any(h <= 0.0 for h in sizes):
        raise ValueError("Errors and sizes must be positive to fit a log-log slope")
    log_h = np.log(np.asarray(sizes, dtype=np.float64))
    log_e = np.log(np.asarray(errors, dtype=np.float64))
    slope, _ = np.polyfit(log_h, log_e, 1)
    return float(slope)


def is_smooth_enough_for_order(errors: list[float]) -> bool:
    """True when an error sequence decreases monotonically.

    A non-monotonic sequence is not in an asymptotic regime, and fitting an
    order to it -- or extrapolating from it -- reports a number that means
    nothing (ADR-0013, ADR-0015 §6).
    """
    return all(later < earlier for earlier, later in itertools.pairwise(errors))
