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

#: DOF count above which `AUTO` prefers the iterative backend.
#:
#: This is a **memory** guard, not a speed optimisation, and the measurements
#: say so plainly. On `plane_neck_plane_board` the direct factorisation fills
#: in badly as the problem grows -- L+U non-zeros against matrix non-zeros
#: rise from 2.6x at 287 DOFs to 22.5x at 143,213, which is 189 bytes per DOF
#: climbing to 1,880 and still climbing. Iterative memory is flat in
#: comparison, since CG stores a handful of vectors and never factorises.
#:
#: Speed points the other way at every size that can be measured here: direct
#: took 0.10 s at 35,976 DOFs where Jacobi-preconditioned CG took 1.51 s.
#: Switching earlier than this would trade a fast, deterministic solve for a
#: slow one to solve a memory problem that does not exist yet.
#:
#: Provisional, because the crossover that matters depends on the available
#: worker memory and on a scalable preconditioner that is not installed here
#: (see `_jacobi_preconditioner`). Extrapolating the fill trend, a direct
#: solve near this size wants roughly a gigabyte -- comfortable -- while
#: several million DOFs would not be.
AUTO_DIRECT_MAX_DOFS: Final = 500_000

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
        """Which backend `AUTO` selects for a problem of this size."""
        if self.method != AUTO:
            return self.method
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


def _jacobi_preconditioner(matrix: sp.csc_matrix) -> tuple[LinearOperator, str]:
    """Diagonal (Jacobi) preconditioning.

    Chosen because it is dependency-free and always available. It is
    emphatically **not** the preconditioner ADR-0014 selected: that is
    algebraic multigrid via hypre, whose iteration count is near
    mesh-independent. Jacobi's is not -- unpreconditioned or diagonally
    preconditioned CG needs iterations growing like `sqrt(kappa)`, and
    `kappa` for this operator grows as the mesh refines (measured: `9.5e5`
    at only 737 DOFs). Jacobi therefore makes the iterative path *usable and
    testable* today without making it *scalable*; AMG is what would.
    """
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
    preconditioner, name = _jacobi_preconditioner(matrix)
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
