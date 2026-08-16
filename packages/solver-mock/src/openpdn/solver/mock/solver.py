"""The mock backend implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from openpdn.domain.board import TerminalId
from openpdn.domain.results import (
    Diagnostic,
    DiagnosticSeverity,
    ElectricalAnalysisResult,
    ResultFidelity,
    SolverIdentity,
    SolverRunStats,
    TerminalResult,
)
from openpdn.domain.study import ViaModel
from openpdn.domain.units import AMPERE, VOLT
from openpdn.solver.api import SolverCapabilities, SolverDescriptor

if TYPE_CHECKING:
    from openpdn.domain.board import Board
    from openpdn.domain.study import AnalysisStudy

_SOLVER_NAME: Final = "mock"
_SOLVER_VERSION: Final = "0.0.1"


class MockSolver:
    """Echoes a study's boundary conditions back as clearly-flagged placeholders.

    The terminal potentials it reports are the *applied* source voltages and
    the *requested* load currents -- the inputs, not a solution. There is no
    IR drop, no current density and no resistance, because no conduction
    problem was solved.
    """

    def describe(self) -> SolverDescriptor:
        """Return identity and (deliberately minimal) capabilities."""
        return SolverDescriptor(
            name=_SOLVER_NAME,
            version=_SOLVER_VERSION,
            summary="Pipeline test double: returns boundary conditions, solves nothing",
            capabilities=SolverCapabilities(
                fidelity=ResultFidelity.MOCK,
                via_models=frozenset({ViaModel.LUMPED_CONDUCTANCE}),
                supports_resistance_probes=False,
                supports_current_density=False,
                supports_power_loss=False,
            ),
            tags=("test-double",),
        )

    def solve(self, board: Board, study: AnalysisStudy) -> ElectricalAnalysisResult:
        """Return placeholder results for `study`."""
        study.validate_against(board)

        applied_voltage_v: dict[TerminalId, float] = {}
        for source in study.sources:
            voltage = source.voltage.require_unit(VOLT)
            for terminal_id in source.attachment.terminal_ids:
                applied_voltage_v[terminal_id] = voltage
            for via_id in source.attachment.via_ids:
                applied_voltage_v[TerminalId(f"via:{via_id}")] = voltage

        drawn_current_a: dict[TerminalId, float] = {}
        for load in study.loads:
            current = load.current.require_unit(AMPERE)
            for terminal_id in load.attachment.terminal_ids:
                drawn_current_a[terminal_id] = drawn_current_a.get(terminal_id, 0.0) + current
            for via_id in load.attachment.via_ids:
                key = TerminalId(f"via:{via_id}")
                drawn_current_a[key] = drawn_current_a.get(key, 0.0) + current

        terminals = tuple(
            TerminalResult(
                terminal_id=terminal_id,
                voltage_v=applied_voltage_v.get(terminal_id, 0.0),
                current_a=drawn_current_a.get(terminal_id, 0.0),
            )
            for terminal_id in sorted(set(applied_voltage_v) | set(drawn_current_a))
        )

        diagnostics = [
            Diagnostic(
                code="mock.no_physics",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    "No conduction problem was solved. These values are the study's own "
                    "boundary conditions and must not be used for engineering decisions."
                ),
                context={"solver": _SOLVER_NAME, "study": str(study.id)},
            )
        ]
        if study.probes:
            diagnostics.append(
                Diagnostic(
                    code="mock.probes_unsupported",
                    severity=DiagnosticSeverity.INFO,
                    message=(
                        f"{len(study.probes)} resistance probe(s) were ignored: the mock "
                        "solver cannot compute terminal-to-terminal resistance."
                    ),
                    context={"probe_count": str(len(study.probes))},
                )
            )

        return ElectricalAnalysisResult(
            study_id=study.id,
            board_id=study.board_id,
            solver=SolverIdentity(name=_SOLVER_NAME, version=_SOLVER_VERSION),
            fidelity=ResultFidelity.MOCK,
            terminals=terminals,
            nets=(),
            probes=(),
            diagnostics=tuple(diagnostics),
            stats=SolverRunStats(converged=None),
        )
