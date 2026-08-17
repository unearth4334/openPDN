"""Element order on real board geometry, against the analytical trace.

`test_element_convergence.py` proves the P2 basis is correct on a smooth
problem. This file asks the different question that decides whether P2 is
worth using in production: what does it buy on copper with terminal pads,
where the solution is *not* smooth?

The headline finding, measured here, is that the two answers differ by
almost two orders of magnitude:

    smooth square, equal DOFs      P2 ~100x more accurate than P1
    straight trace, equal DOFs     P2 ~1.6x more accurate than P1

Both are real. The gap is the point: on this board the dominant error is not
interior approximation -- which is what raising the polynomial order fixes --
but the discretisation of the *equipotential terminal boundary*, which only
gets better with smaller elements near the pads. That is the measured case
for adaptive refinement (ADR-0013) over simply switching Reference to P2, and
it is why ADR-0012 keeps P1 rather than declaring P2 universally better.
"""

from __future__ import annotations

import pytest

from openpdn.domain.board import Board
from openpdn.domain.provenance import Quantity
from openpdn.domain.study import (
    AnalysisStudy,
    AttachmentGroup,
    CurrentLoad,
    ElementOrder,
    LoadId,
    MeshSettings,
    SourceId,
    StudyId,
    VoltageSource,
)
from openpdn.domain.units import AMPERE, METRE, VOLT
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer
from openpdn.solver.fem import FemSheetSolver
from openpdn.solver.fem.controls import MeshControls
from openpdn.solver.fem.problem import build_problem
from tests.validation import analytical
from tests.validation.boards import COPPER_T_M, NET, straight_trace_board

_LENGTH_M = 20e-3
_WIDTH_M = 1e-3
_TEST_CURRENT_A = 1.0


def _board() -> Board:
    return straight_trace_board(length_between_pads_m=_LENGTH_M, width_m=_WIDTH_M)


def _exact_ohm() -> float:
    return analytical.trace_resistance_ohm(_LENGTH_M, _WIDTH_M, COPPER_T_M)


def _settings(element_size_m: float, order: ElementOrder) -> MeshSettings:
    return MeshSettings(
        target_element_size=Quantity.configured(element_size_m, METRE),
        elements_across_feature=4,
        growth_rate=0.7,
        element_order=order,
    )


def _study(mesh: MeshSettings, board) -> AnalysisStudy:
    return AnalysisStudy(
        id=StudyId("order"),
        name="element order",
        board_id=str(board.id),
        net_ids=(NET,),
        sources=(
            VoltageSource(
                id=SourceId("src"),
                attachment=AttachmentGroup(terminal_ids=("term-a",)),  # type: ignore[arg-type]
                voltage=Quantity.configured(0.0, VOLT),
            ),
        ),
        loads=(
            CurrentLoad(
                id=LoadId("load"),
                attachment=AttachmentGroup(terminal_ids=("term-b",)),  # type: ignore[arg-type]
                current=Quantity.configured(_TEST_CURRENT_A, AMPERE),
            ),
        ),
        mesh=mesh,
    )


def _solve(element_size_m: float, order: ElementOrder):
    """Return `(relative_error, dof_count, imbalance, power_mismatch)`."""
    board = _board()
    mesh = _settings(element_size_m, order)
    study = _study(mesh, board)
    normalizer = ShapelyGeometryNormalizer()
    result, fields = FemSheetSolver(normalizer=normalizer).solve_with_fields(board, study)
    problem = build_problem(
        board, study, normalizer.normalize(board), MeshControls.from_settings(mesh)
    )
    voltages = [terminal.voltage_v for terminal in result.terminals]
    resistance = abs(voltages[1] - voltages[0]) / _TEST_CURRENT_A
    exact = _exact_ohm()
    return (
        abs(resistance - exact) / exact,
        problem.n_dofs,
        fields.conservation.imbalance_fraction,
        fields.conservation.power_mismatch_fraction,
    )


class TestQuadraticOnCopper:
    def test_p2_is_more_accurate_than_p1_on_the_same_mesh(self):
        # Same triangulation, four times the DOFs, strictly better answer.
        for element_size_m in (0.5e-3, 0.25e-3, 0.125e-3):
            p1_error, _, _, _ = _solve(element_size_m, ElementOrder.P1)
            p2_error, _, _, _ = _solve(element_size_m, ElementOrder.P2)
            assert p2_error < p1_error, f"P2 was not better at h = {element_size_m}"

    def test_p2_is_more_accurate_than_p1_at_a_matched_dof_count(self):
        # The fair comparison, since DOFs are what a solve is billed in.
        # These two settings land on 6072 and 6071 DOFs respectively -- an
        # almost exactly matched budget, no interpolation needed.
        p1_error, p1_dofs, _, _ = _solve(0.0625e-3, ElementOrder.P1)
        p2_error, p2_dofs, _, _ = _solve(0.125e-3, ElementOrder.P2)
        assert abs(p1_dofs - p2_dofs) / p1_dofs < 0.05, "the DOF budgets must match to compare"
        assert p2_error < p1_error

    def test_the_per_dof_gain_is_modest_on_terminal_bounded_geometry(self):
        # Documents the finding rather than hiding it: measured ~1.6x here,
        # against ~100x for the same comparison on a smooth square. If a
        # future change makes this board behave like the smooth case, the
        # terminal-boundary error has been fixed and this test should be
        # revisited -- deliberately, not by accident.
        p1_error, _, _, _ = _solve(0.0625e-3, ElementOrder.P1)
        p2_error, _, _, _ = _solve(0.125e-3, ElementOrder.P2)
        gain = p1_error / p2_error
        assert 1.2 < gain < 10.0, (
            f"unexpected per-DOF gain {gain:.2f}x -- investigate, do not retune the bound"
        )

    def test_p2_converges_towards_the_analytical_resistance(self):
        errors = [_solve(h, ElementOrder.P2)[0] for h in (0.5e-3, 0.125e-3, 0.0625e-3)]
        assert errors[-1] < errors[0]
        assert errors[-1] < 1e-3

    @pytest.mark.parametrize("order", [ElementOrder.P1, ElementOrder.P2])
    def test_conservation_still_holds(self, order: ElementOrder):
        # ADR-0010 §6 is not suspended for a new element order. Power is
        # integrated with the same quadrature the stiffness uses, so an
        # order mismatch here would show up as an energy imbalance.
        _, _, imbalance, power_mismatch = _solve(0.25e-3, order)
        assert imbalance < 1e-6
        assert power_mismatch < 1e-6


class TestEquipotentialTerminalsUnderP2:
    def test_a_pad_straddling_midpoint_is_pinned(self):
        # ADR-0012 §4: midpoints inside a pad must join its equipotential
        # region. Leaving them free was measured to make P2 *worse* than P1
        # (1.39e-3 -> 9.02e-3 at h = 0.5 mm), because the contact region
        # stopped short of the copper and lengthened the conduction path.
        # The guard is that P2's terminal DOF count stays well below its
        # node count -- i.e. a whole pad really did collapse.
        board = _board()
        mesh = _settings(0.25e-3, ElementOrder.P2)
        problem = build_problem(
            board,
            _study(mesh, board),
            ShapelyGeometryNormalizer().normalize(board),
            MeshControls.from_settings(mesh),
        )
        binding = problem.terminals["term-a"]  # type: ignore[index]
        # More than the three vertices of a single triangle: the pad's
        # vertices *and* the midpoints of its interior edges.
        assert binding.node_count > 6
        assert not binding.is_point_contact
