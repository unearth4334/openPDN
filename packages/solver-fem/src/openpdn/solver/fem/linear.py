"""Linear-solver backends for the reduced conduction system (ADR-0014).

The reduced system `K_ff x = b` is **symmetric positive definite**. That is
not an assumption inherited from theory -- it follows from how Dirichlet
conditions are imposed. `solve.py` eliminates the fixed rows and columns
rather than applying a penalty or a Lagrange multiplier, and elimination
preserves symmetry; the conduction operator is positive definite on a
connected, driven component. Measured on a 737-DOF board: symmetric to
1e-18 relative, smallest eigenvalue `0.46`, largest `4.4e5`.

**If anyone ever changes to a penalty or multiplier scheme, conjugate
gradients stops being applicable and this module's central assumption dies
with it.**

Two backends live here behind one contract:

* `direct` -- SuperLU factorisation. Deterministic, reusable across
  right-hand sides, and the reference every other backend is checked against.
* `iterative` -- preconditioned conjugate gradients, for problems whose
  factorisation no longer fits. A direct factorisation of a 2-D problem fills
  in badly and its memory grows far faster than the DOF count, which is the
  entire reason this module exists.

The iterative path is honest about its own limits: **max iterations reached
is a failure, not a result** (ADR-0014 §6), and its tolerance is derived from
the discretisation error being targeted so that linear algebra is never the
accuracy-limiting step.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, cg, splu

from openpdn.solver.api import SolverConvergenceError

if TYPE_CHECKING:
    import numpy.typing as npt

#: Backend selection, exposed as an advanced setting and resolved
#: server-side. `AUTO` picks by problem size; the others are explicit.
AUTO: Final = "auto"
DIRECT: Final = "direct"
ITERATIVE: Final = "iterative"

#: Relative residual above which a *direct* solve is treated as failed.
DIRECT_RESIDUAL_LIMIT: Final = 1e-6

#: Iterative-refinement rounds attempted when a direct solve misses the
#: limit. Each round reuses the factorisation, so it costs one triangular
#: solve and typically recovers several digits.
MAX_REFINEMENT_ROUNDS: Final = 3

#: DOF count above which `AUTO` prefers the iterative backend -- **when an
#: AMG preconditioner is available** (`resolve` checks; without one, `AUTO`
#: always means direct, because Jacobi-CG was measured *unable* to converge
#: at 2.24M DOFs, exhausting 5,000 iterations at residual 4.2e-3).
#:
#: The crossover is measured, on `plane_neck_plane_board` with
#: smoothed-aggregation AMG (setup + solve wall time, matched answers to
#: better than 1e-9 relative):
#:
#:     DOFs        direct     AMG-CG    AMG iterations
#:     35,976      0.10 s     0.91 s          13
#:     143,237     1.53 s     2.10 s          29
#:     571,557    19.02 s     3.02 s          39
#:
#: Direct wall time grows superlinearly (fill-in), AMG's roughly linearly
#: with a near-mesh-independent iteration count -- so the curves cross
#: between the last two rows, at roughly this many DOFs. Below it, direct
#: additionally buys determinism and factorisation reuse across
#: excitations.
AUTO_DIRECT_MAX_DOFS: Final = 200_000

#: Fraction of the target discretisation error the linear solve is allowed to
#: contribute. ADR-0014 §6: the linear algebra must never be the
#: accuracy-limiting step.
DEFAULT_TOLERANCE_FRACTION: Final = 0.05

#: Relative tolerance used when no discretisation target is supplied.
DEFAULT_RELATIVE_TOLERANCE: Final = 1e-12


@dataclass(frozen=True, slots=True)
class LinearPolicy:
    """How to solve the reduced system, and how well."""

    method: str = AUTO
    #: Discretisation error this run is aiming at. The linear tolerance is
    #: derived from it rather than being a fixed constant.
    target_discretisation_error: float | None = None
    tolerance_fraction: float = DEFAULT_TOLERANCE_FRACTION
    max_iterations: int = 5_000
    auto_direct_max_dofs: int = AUTO_DIRECT_MAX_DOFS

    def relative_tolerance(self) -> float:
        """Relative residual the iterative backend must reach."""
        if self.target_discretisation_error is None:
            return DEFAULT_RELATIVE_TOLERANCE
        return max(
            self.target_discretisation_error * self.tolerance_fraction,
            np.finfo(np.float64).eps * 10.0,
        )

    def resolve(self, n_free: int) -> str:
        """Which backend `AUTO` selects for a problem of this size.

        The crossover only exists when an AMG preconditioner does: with the
        Jacobi fallback, iterative CG cannot reach tight tolerances on large
        systems at all, and routing there turns feasible jobs into
        guaranteed failures. An explicit `iterative` request is honoured
        either way -- and refuses honestly if it cannot converge.
        """
        if self.method != AUTO:
            return self.method
        if not amg_available():
            return DIRECT
        return DIRECT if n_free <= self.auto_direct_max_dofs else ITERATIVE


@dataclass(frozen=True, slots=True)
class LinearReport:
    """What a solve actually did -- ADR-0014 §7 requires all of it."""

    backend: str
    preconditioner: str
    relative_residual: float
    iterations: int | None
    converged_reason: str
    factor_seconds: float
    solve_seconds: float


def solve_reduced(
    matrix: sp.csc_matrix,
    rhs: npt.NDArray[np.float64],
    policy: LinearPolicy | None = None,
) -> tuple[npt.NDArray[np.float64], LinearReport]:
    """Solve `matrix x = rhs`, choosing a backend by policy.

    Raises:
        SolverConvergenceError: The system could not be solved to tolerance.
            An iterative solve that exhausts its iteration budget lands here
            too -- it is not a result.
    """
    policy = policy or LinearPolicy()
    backend = policy.resolve(matrix.shape[0])
    if backend == DIRECT:
        return _solve_direct(matrix, rhs)
    if backend == ITERATIVE:
        return _solve_iterative(matrix, rhs, policy)
    raise ValueError(f"Unknown linear-solver backend {backend!r}")


def _relative_residual(
    matrix: sp.csc_matrix,
    solution: npt.NDArray[np.float64],
    rhs: npt.NDArray[np.float64],
) -> float:
    scale = max(float(np.linalg.norm(rhs)), 1e-30)
    return float(np.linalg.norm(matrix @ solution - rhs)) / scale


def _solve_direct(
    matrix: sp.csc_matrix,
    rhs: npt.NDArray[np.float64],
) -> tuple[npt.NDArray[np.float64], LinearReport]:
    """SuperLU factorisation with iterative refinement."""
    started = time.perf_counter()
    try:
        factor = splu(matrix)
    except RuntimeError as exc:
        raise SolverConvergenceError(
            f"Sparse factorisation failed: {exc}. This usually means a "
            "degenerate mesh element or a zero-conductance region."
        ) from exc
    factor_seconds = time.perf_counter() - started

    started = time.perf_counter()
    solution = factor.solve(rhs)
    residual = _relative_residual(matrix, solution, rhs)
    rounds = 0
    while (
        np.isfinite(residual)
        and residual > DIRECT_RESIDUAL_LIMIT
        and rounds < MAX_REFINEMENT_ROUNDS
    ):
        solution = solution + factor.solve(rhs - matrix @ solution)
        improved = _relative_residual(matrix, solution, rhs)
        rounds += 1
        if improved >= residual:
            break  # No progress: the system is genuinely sick.
        residual = improved
    solve_seconds = time.perf_counter() - started

    if not np.isfinite(residual) or residual > DIRECT_RESIDUAL_LIMIT:
        raise SolverConvergenceError(
            f"Direct solve residual {residual:.3e} exceeds {DIRECT_RESIDUAL_LIMIT:.0e} "
            f"after {rounds} refinement rounds; the system is severely ill-conditioned",
            residual=residual,
        )
    return solution, LinearReport(
        backend=DIRECT,
        preconditioner="none",
        relative_residual=residual,
        iterations=None,
        converged_reason=f"factorised, {rounds} refinement round(s)",
        factor_seconds=factor_seconds,
        solve_seconds=solve_seconds,
    )


def amg_available() -> bool:
    """Whether the smoothed-aggregation AMG preconditioner can be built."""
    try:
        import pyamg  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        return False
    return True


def _build_preconditioner(matrix: sp.csc_matrix) -> tuple[LinearOperator, str]:
    """Best available preconditioner for the SPD conduction operator.

    Preference order, and why it is an order rather than a choice:

    * **Smoothed-aggregation AMG** (pyamg, MIT-licensed, wheel-only). The
      operator is a scalar elliptic diffusion problem on an unstructured
      mesh -- the class AMG was built for, where its iteration count is
      near mesh-independent. ADR-0014 chose AMG via PETSc+hypre; pyamg is
      the interim that needs no system libraries, and the report records
      which one actually ran.
    * **Jacobi** (diagonal), dependency-free fallback. Measured to *not*
      scale: iterations grow as `sqrt(kappa)`, and at 2.24M DOFs it
      exhausted its budget at residual 4.2e-3. A solve that falls back here
      still refuses honestly on non-convergence; it just refuses sooner.
    """
    if amg_available():
        import pyamg

        hierarchy = pyamg.smoothed_aggregation_solver(sp.csr_matrix(matrix), max_coarse=300)
        return hierarchy.aspreconditioner(cycle="V"), "pyamg-smoothed-aggregation"
    diagonal = matrix.diagonal().astype(np.float64)
    safe = np.where(np.abs(diagonal) > 0.0, diagonal, 1.0)
    inverse = 1.0 / safe
    return (
        LinearOperator(matrix.shape, matvec=lambda v: inverse * v, dtype=np.float64),
        "jacobi",
    )


def _solve_iterative(
    matrix: sp.csc_matrix,
    rhs: npt.NDArray[np.float64],
    policy: LinearPolicy,
) -> tuple[npt.NDArray[np.float64], LinearReport]:
    """Preconditioned conjugate gradients on the SPD reduced system."""
    started = time.perf_counter()
    preconditioner, name = _build_preconditioner(matrix)
    factor_seconds = time.perf_counter() - started

    tolerance = policy.relative_tolerance()
    iterations = 0

    def count(_: npt.NDArray[np.float64]) -> None:
        nonlocal iterations
        iterations += 1

    started = time.perf_counter()
    solution, info = cg(
        matrix,
        rhs,
        rtol=tolerance,
        atol=0.0,
        maxiter=policy.max_iterations,
        M=preconditioner,
        callback=count,
    )
    solve_seconds = time.perf_counter() - started
    residual = _relative_residual(matrix, solution, rhs)

    if info != 0 or not np.isfinite(residual):
        # ADR-0014 §6: exhausting the iteration budget is a failure. Returning
        # the current iterate would hand back a number that looks like an
        # answer and is not one.
        raise SolverConvergenceError(
            f"Conjugate gradients did not converge (info={info}) after {iterations} "
            f"iterations; relative residual {residual:.3e} against a target of "
            f"{tolerance:.3e}",
            residual=residual,
        )
    return solution, LinearReport(
        backend=ITERATIVE,
        preconditioner=name,
        relative_residual=residual,
        iterations=iterations,
        converged_reason=f"converged to rtol {tolerance:.2e}",
        factor_seconds=factor_seconds,
        solve_seconds=solve_seconds,
    )
