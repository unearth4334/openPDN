"""Solver-facing error types.

Adapters translate backend failures -- a padne exception, a non-zero Elmer exit
code, a singular matrix -- into these. Application code catches these and never
a backend's own exception type.
"""


class SolverError(Exception):
    """Base class for every failure originating in a solver adapter."""


class SolverNotAvailableError(SolverError):
    """The requested solver is not installed, licensed or reachable."""


class SolverConfigurationError(SolverError):
    """The board or study cannot be turned into a well-posed problem.

    Typical causes: no voltage source, unknown copper thickness, a load on a
    net that is not under analysis.
    """


class SolverUnsupportedFeatureError(SolverError):
    """The study requests physics this backend cannot represent.

    Example: `ViaModel.RESOLVED_3D` on a 2.5-D sheet solver. Failing here is
    mandatory -- silently downgrading the physics would misreport fidelity.
    """


class SolverConvergenceError(SolverError):
    """The linear or nonlinear solve did not reach its tolerance.

    Carries the achieved residual so the caller can report *how far off* the
    run was rather than only that it failed.
    """

    def __init__(self, message: str, residual: float | None = None) -> None:
        """Store the message and the achieved residual."""
        super().__init__(message)
        self.residual = residual
