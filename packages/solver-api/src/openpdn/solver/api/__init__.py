"""The solver contract.

Everything user-facing depends on this module; nothing user-facing depends on a
particular solver. Adding a backend (padne, a native 2.5-D FEM, Elmer) means
adding an implementation of `ElectricalSolver`, not editing application
services, the API or the UI (ADR-0003).

This package contains *no* numerical code and *no* third-party imports.
"""

from openpdn.solver.api.errors import (
    SolverConfigurationError,
    SolverConvergenceError,
    SolverError,
    SolverNotAvailableError,
    SolverUnsupportedFeatureError,
)
from openpdn.solver.api.protocol import (
    ElectricalSolver,
    PreparedProblem,
    SolverCapabilities,
    SolverDescriptor,
    StagedElectricalSolver,
)
from openpdn.solver.api.registry import SolverRegistry

__all__ = [
    "ElectricalSolver",
    "PreparedProblem",
    "SolverCapabilities",
    "SolverConfigurationError",
    "SolverConvergenceError",
    "SolverDescriptor",
    "SolverError",
    "SolverNotAvailableError",
    "SolverRegistry",
    "SolverUnsupportedFeatureError",
    "StagedElectricalSolver",
]
