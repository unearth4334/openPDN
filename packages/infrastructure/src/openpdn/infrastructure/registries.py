"""Concrete registries of solvers and importers.

Plain dictionaries behind the registry protocols. They are the *only* objects
that hold references to concrete adapters, which is what keeps `import
MockSolver` out of the application layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openpdn.pcb_import.api import UnsupportedFormatError
from openpdn.solver.api import SolverNotAvailableError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openpdn.pcb_import.api import ImporterDescriptor, PCBImporter
    from openpdn.solver.api import ElectricalSolver, SolverDescriptor


class InMemorySolverRegistry:
    """A registry populated at start-up by the composition root."""

    def __init__(self, solvers: Sequence[ElectricalSolver] = ()) -> None:
        """Index `solvers` by the name each one reports."""
        self._solvers: dict[str, ElectricalSolver] = {}
        for solver in solvers:
            self.register(solver)

    def register(self, solver: ElectricalSolver) -> None:
        """Add `solver`, rejecting a duplicate name."""
        name = solver.describe().name
        if name in self._solvers:
            raise ValueError(f"Solver {name!r} is already registered")
        self._solvers[name] = solver

    def available(self) -> Sequence[SolverDescriptor]:
        """Describe every registered solver, sorted by name."""
        return sorted(
            (solver.describe() for solver in self._solvers.values()),
            key=lambda descriptor: descriptor.name,
        )

    def get(self, name: str) -> ElectricalSolver:
        """Return the solver registered under `name`."""
        solver = self._solvers.get(name)
        if solver is None:
            known = ", ".join(sorted(self._solvers)) or "none"
            raise SolverNotAvailableError(f"Unknown solver {name!r}; registered: {known}")
        descriptor = solver.describe()
        if not descriptor.available:
            raise SolverNotAvailableError(
                f"Solver {name!r} is unavailable: {descriptor.unavailable_reason}"
            )
        return solver


class InMemoryImporterRegistry:
    """A registry of PCB importers, populated at start-up."""

    def __init__(self, importers: Sequence[PCBImporter] = ()) -> None:
        """Index `importers` by the name each one reports."""
        self._importers: dict[str, PCBImporter] = {}
        for importer in importers:
            self.register(importer)

    def register(self, importer: PCBImporter) -> None:
        """Add `importer`, rejecting a duplicate name."""
        name = importer.describe().name
        if name in self._importers:
            raise ValueError(f"Importer {name!r} is already registered")
        self._importers[name] = importer

    def available(self) -> Sequence[ImporterDescriptor]:
        """Describe every registered importer, sorted by name."""
        return sorted(
            (importer.describe() for importer in self._importers.values()),
            key=lambda descriptor: descriptor.name,
        )

    def get(self, name: str) -> PCBImporter:
        """Return the importer registered under `name`."""
        importer = self._importers.get(name)
        if importer is None:
            known = ", ".join(sorted(self._importers)) or "none"
            raise UnsupportedFormatError(f"Unknown importer {name!r}; registered: {known}")
        return importer
