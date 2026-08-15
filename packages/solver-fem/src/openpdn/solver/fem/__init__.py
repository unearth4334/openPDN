"""2.5-D sheet-conduction FEM solver (ADR-0010).

The composition root registers `FemSheetSolver`; everything else in this
package is implementation detail behind the `ElectricalSolver` contract.
"""

from openpdn.solver.fem.errors import DisconnectedTerminalError, MeshGenerationError
from openpdn.solver.fem.solver import (
    SOLVER_NAME,
    SOLVER_VERSION,
    FemFieldData,
    FemSheetSolver,
)

__all__ = [
    "SOLVER_NAME",
    "SOLVER_VERSION",
    "DisconnectedTerminalError",
    "FemFieldData",
    "FemSheetSolver",
    "MeshGenerationError",
]
