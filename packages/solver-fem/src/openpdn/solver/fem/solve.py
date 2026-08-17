"""Direct sparse solution of the assembled sheet problem.

One excitation = one set of Dirichlet potentials plus one injected-current
vector. The reduced system

    K_ff V_f = I_f - K_fd V_d

is solved with SciPy's SuperLU factorisation -- deterministic, no iterative
tuning, and the factorisation is reusable across right-hand sides for the
same Dirichlet set (resistance probes, load sweeps).

Numerical honesty (fem-solver skill): the reported residual is a property of
the *linear algebra*, not of the discretisation. A residual of 1e-14 says the
matrix equation was solved, not that the mesh resolves the physics; mesh
convergence is judged separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import scipy.sparse as sp

from openpdn.solver.fem.errors import DisconnectedTerminalError
from openpdn.solver.fem.linear import (
    DIRECT,
    LinearPolicy,
    LinearReport,
    solve_reduced,
)

if TYPE_CHECKING:
    import numpy.typing as npt

    from openpdn.solver.fem.problem import SheetProblem


@dataclass(frozen=True)
class Excitation:
    """One set of boundary conditions on the assembled problem.

    Attributes:
        dirichlet: DOF -> fixed potential in volts.
        injected_current_a: DOF -> current injected *into* the copper in
            amperes (a load drawing current is negative).
    """

    dirichlet: dict[int, float]
    injected_current_a: dict[int, float]


@dataclass(frozen=True)
class Solution:
    """Nodal potentials and solve health for one excitation."""

    voltage_v: npt.NDArray[np.float64]
    residual: float
    source_current_a: dict[int, float]
    active_dofs: int
    factor_seconds: float
    solve_seconds: float
    #: Which backend ran, and how it behaved (ADR-0014 §7).
    linear: LinearReport | None = None


def solve_excitation(
    problem: SheetProblem,
    excitation: Excitation,
    policy: LinearPolicy | None = None,
) -> Solution:
    """Solve the conduction problem under one excitation.

    Copper components that contain no Dirichlet DOF are electrically floating
    and are excluded; their potentials are reported as NaN. A current
    injected into such a component has no return path -- that is refused, not
    approximated.

    Raises:
        DisconnectedTerminalError: A current is injected into copper with no
            conductive path to any voltage source.
        SolverConvergenceError: The direct solve produced an unacceptable
            residual (severely ill-conditioned system).
    """
    if not excitation.dirichlet:
        raise DisconnectedTerminalError("An excitation needs at least one fixed potential")

    matrix = problem.matrix
    n = problem.n_dofs
    labels = problem.component_of_dof

    driven_components = {int(labels[dof]) for dof in excitation.dirichlet}
    active = np.isin(labels, sorted(driven_components))

    for dof, current in excitation.injected_current_a.items():
        if current != 0.0 and not active[dof]:
            raise DisconnectedTerminalError(
                "A load or probe terminal lies on copper that is electrically "
                "disconnected from every voltage source in this study"
            )

    dirichlet_dofs = np.fromiter(excitation.dirichlet.keys(), dtype=np.int64)
    dirichlet_values = np.fromiter(
        (excitation.dirichlet[int(d)] for d in dirichlet_dofs), dtype=np.float64
    )

    is_dirichlet = np.zeros(n, dtype=bool)
    is_dirichlet[dirichlet_dofs] = True
    free = active & ~is_dirichlet
    free_index = np.nonzero(free)[0]

    rhs_full = np.zeros(n, dtype=np.float64)
    for dof, current in excitation.injected_current_a.items():
        rhs_full[dof] += current

    voltage = np.full(n, np.nan, dtype=np.float64)
    voltage[dirichlet_dofs] = dirichlet_values

    factor_seconds = 0.0
    solve_seconds = 0.0
    if len(free_index) > 0:
        k_ff = matrix[free_index][:, free_index].tocsc()
        k_fd = matrix[free_index][:, dirichlet_dofs]
        rhs = rhs_full[free_index] - k_fd @ dirichlet_values

        v_free, report = solve_reduced(k_ff, rhs, policy)
        factor_seconds = report.factor_seconds
        solve_seconds = report.solve_seconds
        residual = report.relative_residual
        voltage[free_index] = v_free
    else:
        residual = 0.0
        report = LinearReport(
            backend=DIRECT,
            preconditioner="none",
            relative_residual=0.0,
            iterations=None,
            converged_reason="no free degrees of freedom",
            factor_seconds=0.0,
            solve_seconds=0.0,
        )

    # Net current entering the copper at each Dirichlet DOF: I = (K V)_d minus
    # any explicitly injected current at that DOF.
    source_current: dict[int, float] = {}
    voltage_for_flux = np.where(active, np.nan_to_num(voltage, nan=0.0), 0.0)
    flux = matrix @ voltage_for_flux
    for raw_dof in dirichlet_dofs:
        dof = int(raw_dof)
        source_current[dof] = float(flux[dof] - rhs_full[dof])

    return Solution(
        voltage_v=voltage,
        residual=residual,
        source_current_a=source_current,
        active_dofs=int(active.sum()),
        factor_seconds=factor_seconds,
        linear=report,
        solve_seconds=solve_seconds,
    )


def is_connected(problem: SheetProblem, dof_a: int, dof_b: int) -> bool:
    """True when two DOFs share a connected copper component."""
    return bool(problem.component_of_dof[dof_a] == problem.component_of_dof[dof_b])


def csr_stats(matrix: sp.csr_matrix) -> tuple[int, int]:
    """Return (rows, stored nonzeros) for logging."""
    return matrix.shape[0], matrix.nnz
