"""Solver lookup.

The registry is the only place that knows which backends exist. Application
services take a registry, ask it for a solver by name, and stay unaware of the
concrete classes -- which is what keeps `import elmer` out of the application
layer.

Concrete registries are wired in the composition root
(`openpdn.infrastructure.container`), not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openpdn.solver.api.protocol import ElectricalSolver, SolverDescriptor


class SolverRegistry(Protocol):
    """Read-only lookup of the solvers this deployment offers."""

    def available(self) -> Sequence[SolverDescriptor]:
        """Describe every registered solver, including unavailable ones."""
        ...

    def get(self, name: str) -> ElectricalSolver:
        """Return the solver registered under `name`.

        Raises:
            SolverNotAvailableError: If no such solver is registered, or its
                external dependency is missing.
        """
        ...
