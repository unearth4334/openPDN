"""Application service behaviour, exercised against test doubles only."""

from __future__ import annotations

import dataclasses

import pytest

from openpdn.application.analysis_service import AnalysisService
from openpdn.application.errors import AnalysisRequestError
from openpdn.domain.board import Board
from openpdn.domain.errors import InvalidStudyError
from openpdn.domain.results import (
    ElectricalAnalysisResult,
    ResultFidelity,
    SolverIdentity,
)
from openpdn.domain.study import AnalysisStudy, ViaModel
from openpdn.solver.api import (
    SolverCapabilities,
    SolverDescriptor,
    SolverNotAvailableError,
    SolverUnsupportedFeatureError,
)


class _StubSolver:
    """A solver double; the application must not care what is behind it."""

    def __init__(
        self,
        name: str = "stub",
        via_models: frozenset[ViaModel] = frozenset({ViaModel.LUMPED_CONDUCTANCE}),
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.via_models = via_models
        self.raises = raises
        self.calls = 0

    def describe(self) -> SolverDescriptor:
        return SolverDescriptor(
            name=self.name,
            version="0.0.0",
            summary="stub",
            capabilities=SolverCapabilities(
                fidelity=ResultFidelity.SHEET_2P5D, via_models=self.via_models
            ),
        )

    def solve(self, board: Board, study: AnalysisStudy) -> ElectricalAnalysisResult:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return ElectricalAnalysisResult(
            study_id=study.id,
            board_id=study.board_id,
            solver=SolverIdentity(self.name, "0.0.0"),
            fidelity=ResultFidelity.SHEET_2P5D,
        )


class _StubRegistry:
    def __init__(self, solvers: dict[str, _StubSolver]) -> None:
        self._solvers = solvers

    def available(self):
        return [solver.describe() for solver in self._solvers.values()]

    def get(self, name: str) -> _StubSolver:
        try:
            return self._solvers[name]
        except KeyError as exc:
            raise SolverNotAvailableError(name) from exc


@pytest.fixture
def stub() -> _StubSolver:
    return _StubSolver()


@pytest.fixture
def service(stub: _StubSolver) -> AnalysisService:
    return AnalysisService(solvers=_StubRegistry({"stub": stub}), default_solver="stub")


class TestAnalysisService:
    def test_the_default_solver_is_used_when_none_is_named(
        self,
        service: AnalysisService,
        stub: _StubSolver,
        simple_board: Board,
        simple_study: AnalysisStudy,
    ):
        result = service.run(simple_board, simple_study)
        assert stub.calls == 1
        assert result.solver.name == "stub"

    def test_an_invalid_study_never_reaches_the_solver(
        self,
        service: AnalysisService,
        stub: _StubSolver,
        simple_board: Board,
        simple_study: AnalysisStudy,
    ):
        broken = dataclasses.replace(simple_study, board_id="brd-other")
        with pytest.raises(InvalidStudyError):
            service.run(simple_board, broken)
        assert stub.calls == 0

    def test_an_unknown_solver_is_a_request_error(
        self, service: AnalysisService, simple_board: Board, simple_study: AnalysisStudy
    ):
        with pytest.raises(AnalysisRequestError, match="not available"):
            service.run(simple_board, simple_study, solver_name="elmer")

    def test_unsupported_physics_is_refused_rather_than_approximated(
        self, simple_board: Board, simple_study: AnalysisStudy
    ):
        # A 2.5-D backend asked for resolved 3-D vias must fail, not silently
        # solve a different problem and report it as the requested one.
        sheet_only = _StubSolver(via_models=frozenset({ViaModel.LUMPED_CONDUCTANCE}))
        service = AnalysisService(
            solvers=_StubRegistry({"stub": sheet_only}), default_solver="stub"
        )
        study = dataclasses.replace(simple_study, via_model=ViaModel.RESOLVED_3D)
        with pytest.raises(AnalysisRequestError, match="via model"):
            service.run(simple_board, study)
        assert sheet_only.calls == 0

    def test_backend_feature_errors_become_request_errors(
        self, simple_board: Board, simple_study: AnalysisStudy
    ):
        failing = _StubSolver(raises=SolverUnsupportedFeatureError("no probes"))
        service = AnalysisService(solvers=_StubRegistry({"stub": failing}), default_solver="stub")
        with pytest.raises(AnalysisRequestError, match="no probes"):
            service.run(simple_board, simple_study)
