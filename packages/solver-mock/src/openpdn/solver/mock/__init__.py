"""A deliberately non-physical solver.

`MockSolver` exists to exercise the pipeline -- registry lookup, study
validation, result model, API and CLI plumbing -- before a real backend exists.

It solves nothing. It applies no conduction physics, performs no meshing, and
must never be presented as a simulation: every result it returns is tagged
`ResultFidelity.MOCK` and carries an explicit diagnostic saying so. Code that
consumes results is expected to check `result.is_physical`.
"""

from openpdn.solver.mock.solver import MockSolver

__all__ = ["MockSolver"]
