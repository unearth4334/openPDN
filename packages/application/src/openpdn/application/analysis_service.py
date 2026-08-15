"""Use case: run an electrical study against a board."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from openpdn.application import events
from openpdn.application.errors import AnalysisRequestError
from openpdn.solver.api import (
    SolverNotAvailableError,
    SolverUnsupportedFeatureError,
)

if TYPE_CHECKING:
    from openpdn.domain.board import Board
    from openpdn.domain.results import ElectricalAnalysisResult
    from openpdn.domain.study import AnalysisStudy
    from openpdn.solver.api import SolverRegistry

_logger = logging.getLogger(__name__)


class AnalysisService:
    """Validates a study, dispatches it to a solver and returns results.

    This service never imports a solver implementation -- it resolves one by
    name through `SolverRegistry`. That indirection is what lets padne, a
    native FEM and Elmer coexist without touching any caller (ADR-0003).
    """

    def __init__(self, solvers: SolverRegistry, default_solver: str) -> None:
        """Store the solver registry and the deployment's default backend."""
        self._solvers = solvers
        self._default_solver = default_solver

    def run(
        self,
        board: Board,
        study: AnalysisStudy,
        solver_name: str | None = None,
    ) -> ElectricalAnalysisResult:
        """Solve `study` on `board`.

        Args:
            board: Canonical board model, treated as immutable.
            study: Boundary conditions and settings, treated as immutable.
            solver_name: Registry key of the backend; defaults to the
                deployment's configured solver.

        Raises:
            InvalidStudyError: If the study does not resolve against the board.
            AnalysisRequestError: If the requested solver is unknown, or cannot
                represent the physics the study asks for.
            SolverError: If the backend fails.
        """
        # Fail on an inconsistent study before any expensive work starts.
        study.validate_against(board)

        name = solver_name or self._default_solver
        try:
            solver = self._solvers.get(name)
        except SolverNotAvailableError as exc:
            raise AnalysisRequestError(f"Solver {name!r} is not available: {exc}") from exc

        descriptor = solver.describe()
        if study.via_model not in descriptor.capabilities.via_models:
            # Never silently substitute physics the user did not ask for.
            raise AnalysisRequestError(
                f"Solver {descriptor.name!r} cannot represent via model {study.via_model.value!r}"
            )

        started = time.perf_counter()
        _logger.info(
            events.SOLVER_STARTED,
            extra={
                "event": events.SOLVER_STARTED,
                "solver": descriptor.name,
                "board_id": str(board.id),
                "study_id": str(study.id),
                "net_count": len(study.net_ids),
            },
        )
        try:
            result = solver.solve(board, study)
        except SolverUnsupportedFeatureError as exc:
            _logger.warning(
                events.SOLVER_FAILED,
                extra={"event": events.SOLVER_FAILED, "solver": descriptor.name},
            )
            raise AnalysisRequestError(str(exc)) from exc
        except Exception:
            _logger.exception(
                events.SOLVER_FAILED,
                extra={"event": events.SOLVER_FAILED, "solver": descriptor.name},
            )
            raise

        duration_seconds = time.perf_counter() - started
        _logger.info(
            events.RESULTS_GENERATED,
            extra={
                "event": events.RESULTS_GENERATED,
                "solver": descriptor.name,
                "board_id": str(board.id),
                "study_id": str(study.id),
                "fidelity": str(result.fidelity),
                "mesh_nodes": result.stats.mesh_nodes,
                "mesh_elements": result.stats.mesh_elements,
                "cache_hit": result.stats.cache_hit,
                "duration_seconds": round(duration_seconds, 6),
            },
        )
        return result
