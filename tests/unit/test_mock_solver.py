"""The mock solver must never look like a simulation."""

from __future__ import annotations

import dataclasses

import pytest

from openpdn.domain.board import Board, TerminalId
from openpdn.domain.results import DiagnosticSeverity, ResultFidelity
from openpdn.domain.study import AnalysisStudy, ProbeId, ResistanceProbe
from openpdn.solver.mock import MockSolver


@pytest.fixture
def solver() -> MockSolver:
    return MockSolver()


class TestMockSolverHonesty:
    def test_results_are_tagged_as_non_physical(
        self, solver: MockSolver, simple_board: Board, simple_study: AnalysisStudy
    ):
        result = solver.solve(simple_board, simple_study)
        assert result.fidelity is ResultFidelity.MOCK
        assert not result.is_physical

    def test_a_warning_says_no_physics_was_applied(
        self, solver: MockSolver, simple_board: Board, simple_study: AnalysisStudy
    ):
        result = solver.solve(simple_board, simple_study)
        codes = {diagnostic.code: diagnostic for diagnostic in result.diagnostics}
        assert "mock.no_physics" in codes
        assert codes["mock.no_physics"].severity is DiagnosticSeverity.WARNING

    def test_no_ir_drop_or_resistance_is_reported(
        self, solver: MockSolver, simple_board: Board, simple_study: AnalysisStudy
    ):
        # Nothing was solved, so no derived quantity may be published.
        result = solver.solve(simple_board, simple_study)
        assert result.nets == ()
        assert result.probes == ()
        assert result.worst_ir_drop_v is None

    def test_ignored_probes_are_reported(
        self, solver: MockSolver, simple_board: Board, simple_study: AnalysisStudy
    ):
        study = dataclasses.replace(
            simple_study,
            probes=(ResistanceProbe(ProbeId("P1"), TerminalId("T_SRC"), TerminalId("T_LOAD")),),
        )
        result = solver.solve(simple_board, study)
        assert any(item.code == "mock.probes_unsupported" for item in result.diagnostics)


class TestMockSolverContract:
    def test_it_echoes_the_boundary_conditions(
        self, solver: MockSolver, simple_board: Board, simple_study: AnalysisStudy
    ):
        result = solver.solve(simple_board, simple_study)
        by_terminal = result.terminals_by_id
        assert by_terminal[TerminalId("T_SRC")].voltage_v == pytest.approx(0.85)
        assert by_terminal[TerminalId("T_LOAD")].current_a == pytest.approx(4.0)

    def test_it_declares_only_what_it_supports(self, solver: MockSolver):
        capabilities = solver.describe().capabilities
        assert capabilities.fidelity is ResultFidelity.MOCK
        assert not capabilities.supports_current_density
        assert not capabilities.supports_resistance_probes

    def test_it_validates_the_study_before_answering(
        self, solver: MockSolver, simple_board: Board, simple_study: AnalysisStudy
    ):
        from openpdn.domain.errors import InvalidStudyError

        broken = dataclasses.replace(simple_study, board_id="brd-other")
        with pytest.raises(InvalidStudyError):
            solver.solve(simple_board, broken)
