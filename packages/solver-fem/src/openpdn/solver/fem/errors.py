"""FEM-specific error types, all rooted in the solver contract's taxonomy."""

from __future__ import annotations

from openpdn.solver.api import SolverConfigurationError, SolverError


class MeshGenerationError(SolverError):
    """The copper geometry could not be triangulated.

    Messages name the region (layer, net, id) and the reason, because "mesh
    failed" alone gives the user nothing to act on.
    """


class DisconnectedTerminalError(SolverConfigurationError):
    """A source and a load/probe terminal lie on disconnected copper.

    The study is electrically impossible as posed; solving would produce a
    singular system. The message names both terminals and the net.
    """
