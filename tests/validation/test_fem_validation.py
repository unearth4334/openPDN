"""Numerical validation of the 2.5-D sheet FEM against closed-form answers.

Every case here has an analytical ground truth in `analytical.py` and a
tolerance justified by a named physical or discretisation reason. Widening a
tolerance to make a test pass is forbidden (testing skill); refine the mesh
or explain the physics instead.
"""

from __future__ import annotations

import pytest

from openpdn.domain.provenance import Quantity
from openpdn.domain.results import ElectricalAnalysisResult
from openpdn.domain.study import (
    AnalysisStudy,
    CurrentLoad,
    LoadId,
    MeshSettings,
    ProbeId,
    ResistanceProbe,
    SourceId,
    StudyId,
    VoltageSource,
)
from openpdn.domain.units import AMPERE, METRE, VOLT
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer
from openpdn.solver.fem import FemFieldData, FemSheetSolver
from openpdn.solver.fem.errors import DisconnectedTerminalError
from tests.validation import analytical
from tests.validation.boards import (
    COPPER_T_M,
    NET,
    disconnected_islands_board,
    midplane_barrel_length_m,
    parallel_traces_board,
    series_widths_board,
    straight_trace_board,
    via_stack_board,
)

pytestmark = pytest.mark.validation


def _solver() -> FemSheetSolver:
    return FemSheetSolver(normalizer=ShapelyGeometryNormalizer())


def _mesh(target_m: float) -> MeshSettings:
    return MeshSettings(target_element_size=Quantity.configured(target_m, METRE))


def _resistance_study(mesh_target_m: float, name: str = "resistance") -> AnalysisStudy:
    return AnalysisStudy(
        id=StudyId(f"study-{name}"),
        name=name,
        board_id="ignored",  # overwritten per board below
        net_ids=(NET,),
        sources=(
            VoltageSource(
                id=SourceId("src"),
                terminal_id="term-a",  # type: ignore[arg-type]
                voltage=Quantity.configured(0.0, VOLT),
            ),
        ),
        probes=(
            ResistanceProbe(
                id=ProbeId("p"),
                from_terminal_id="term-a",  # type: ignore[arg-type]
                to_terminal_id="term-b",  # type: ignore[arg-type]
            ),
        ),
        mesh=_mesh(mesh_target_m),
    )


def _probe_resistance(board, mesh_target_m: float) -> float:
    study = _resistance_study(mesh_target_m)
    object.__setattr__(study, "board_id", str(board.id))
    result = _solver().solve(board, study)
    return result.probes[0].resistance_ohm


class TestStraightTrace:
    """63.1: R = L / (sigma w t) for a uniform trace."""

    LENGTH = 20e-3
    WIDTH = 1e-3

    def analytical_ohm(self) -> float:
        return analytical.trace_resistance_ohm(self.LENGTH, self.WIDTH, COPPER_T_M)

    def test_matches_analytical_within_discretisation_error(self):
        board = straight_trace_board(length_between_pads_m=self.LENGTH, width_m=self.WIDTH)
        r = _probe_resistance(board, 0.25e-3)
        # 0.5 %: pure P1 discretisation error at 4 elements across the width;
        # the convergence test below shows it falling with refinement.
        assert r == pytest.approx(self.analytical_ohm(), rel=5e-3)

    def test_error_falls_monotonically_under_refinement(self):
        """Show the discretisation error falling under refinement.

        A single passing number can be a coincidence; a converging sequence
        cannot (testing skill).
        """
        board = straight_trace_board(length_between_pads_m=self.LENGTH, width_m=self.WIDTH)
        exact = self.analytical_ohm()
        errors = [
            abs(_probe_resistance(board, target) / exact - 1.0)
            for target in (1e-3, 0.25e-3, 0.0625e-3)
        ]
        assert errors[0] > errors[1] > errors[2]
        # 0.1 %: remaining discretisation error at ~16 elements across.
        assert errors[2] < 1e-3


class TestCurrentScaling:
    """63.2: the model is linear -- R invariant, voltage drop proportional."""

    def _effective_resistance(self, current_a: float) -> tuple[float, float]:
        board = straight_trace_board(length_between_pads_m=20e-3, width_m=1e-3)
        study = AnalysisStudy(
            id=StudyId(f"scale-{current_a}"),
            name="scaling",
            board_id=str(board.id),
            net_ids=(NET,),
            sources=(
                VoltageSource(
                    id=SourceId("src"),
                    terminal_id="term-a",  # type: ignore[arg-type]
                    voltage=Quantity.configured(0.85, VOLT),
                ),
            ),
            loads=(
                CurrentLoad(
                    id=LoadId("load"),
                    terminal_id="term-b",  # type: ignore[arg-type]
                    current=Quantity.configured(current_a, AMPERE),
                ),
            ),
            mesh=_mesh(0.5e-3),
        )
        result = _solver().solve(board, study)
        v_a = result.terminals_by_id["term-a"].voltage_v  # type: ignore[index]
        v_b = result.terminals_by_id["term-b"].voltage_v  # type: ignore[index]
        drop = v_a - v_b
        return drop / current_a, drop

    def test_resistance_invariant_and_drop_linear_across_decades(self):
        r_small, drop_small = self._effective_resistance(0.1)
        r_unit, drop_unit = self._effective_resistance(1.0)
        r_large, drop_large = self._effective_resistance(10.0)
        # Linearity is a property of the algebra, not the mesh: agreement to
        # floating-point precision is required, not hoped for.
        assert r_small == pytest.approx(r_unit, rel=1e-9)
        assert r_large == pytest.approx(r_unit, rel=1e-9)
        assert drop_small == pytest.approx(drop_unit / 10.0, rel=1e-9)
        assert drop_large == pytest.approx(drop_unit * 10.0, rel=1e-9)


class TestParallelConductors:
    """63.3: two disjoint traces between shared bus-bar terminals."""

    def test_parallel_combination(self):
        length = 20e-3
        w1, w2 = 1e-3, 0.5e-3
        board = parallel_traces_board(length_between_pads_m=length, width_1_m=w1, width_2_m=w2)
        r1 = analytical.trace_resistance_ohm(length, w1, COPPER_T_M)
        r2 = analytical.trace_resistance_ohm(length, w2, COPPER_T_M)
        expected = analytical.parallel_resistance_ohm(r1, r2)
        r = _probe_resistance(board, 0.25e-3)
        # 0.5 %: per-branch discretisation error, same order as the single
        # trace at this mesh density.
        assert r == pytest.approx(expected, rel=5e-3)


class TestSeriesGeometry:
    """63.4: a wide and a narrow segment in series."""

    def test_series_sum_plus_constriction(self):
        length = 10e-3
        wide, narrow = 2e-3, 0.5e-3
        board = series_widths_board(length_each_m=length, width_wide_m=wide, width_narrow_m=narrow)
        r1 = analytical.trace_resistance_ohm(length, wide, COPPER_T_M)
        r2 = analytical.trace_resistance_ohm(length, narrow, COPPER_T_M)
        expected = analytical.series_resistance_ohm(r1, r2)
        r = _probe_resistance(board, 0.125e-3)
        # The 1-D sum ignores the constriction where the width steps from
        # 2 mm to 0.5 mm; the FEM resolves it, so the computed resistance
        # must exceed the sum -- by a few percent for this aspect ratio
        # (spreading resistance ~ ln(w1/w2)/(pi sigma t) ~ 4 % of the sum).
        assert r > expected
        assert r == pytest.approx(expected, rel=6e-2)


class TestViaResistance:
    """63.5: exact annular-barrel resistance through equipotential plates."""

    def test_two_layer_barrel_matches_exact_annulus(self):
        hole, plating = 0.3e-3, 25e-6
        board = via_stack_board(layer_count=2, finished_hole_m=hole, plating_m=plating)
        length = midplane_barrel_length_m(board, "L1", "L2")
        expected = analytical.via_barrel_resistance_ohm(length, hole, plating)
        r = _probe_resistance(board, 0.1e-3)
        # Both plates lie entirely inside the equipotential contact disc, so
        # the only resistance is the lumped barrel itself: agreement to
        # floating point, no discretisation excuse.
        assert r == pytest.approx(expected, rel=1e-9)

    def test_four_layer_stack_is_three_segments_in_series(self):
        """63.6: interlayer connectivity through a multi-layer barrel."""
        hole, plating = 0.3e-3, 25e-6
        board = via_stack_board(layer_count=4, finished_hole_m=hole, plating_m=plating)
        expected = analytical.series_resistance_ohm(
            *(
                analytical.via_barrel_resistance_ohm(
                    midplane_barrel_length_m(board, f"L{i}", f"L{i + 1}"), hole, plating
                )
                for i in (1, 2, 3)
            )
        )
        r = _probe_resistance(board, 0.1e-3)
        assert r == pytest.approx(expected, rel=1e-9)


class TestConservationAndPower:
    """Current balance and energy consistency on an IR-drop study."""

    def _run(self) -> tuple[ElectricalAnalysisResult, FemFieldData]:
        board = straight_trace_board(length_between_pads_m=20e-3, width_m=1e-3)
        study = AnalysisStudy(
            id=StudyId("conservation"),
            name="conservation",
            board_id=str(board.id),
            net_ids=(NET,),
            sources=(
                VoltageSource(
                    id=SourceId("src"),
                    terminal_id="term-a",  # type: ignore[arg-type]
                    voltage=Quantity.configured(0.85, VOLT),
                ),
            ),
            loads=(
                CurrentLoad(
                    id=LoadId("load"),
                    terminal_id="term-b",  # type: ignore[arg-type]
                    current=Quantity.configured(2.0, AMPERE),
                ),
            ),
            mesh=_mesh(0.25e-3),
        )
        return _solver().solve_with_fields(board, study)

    def test_current_balance_is_exact(self):
        _, fields = self._run()
        # Direct solve of a linear system: imbalance is numerical noise.
        assert fields.conservation.imbalance_fraction < 1e-9

    def test_source_supplies_the_load_current(self):
        result, _ = self._run()
        assert result.terminals_by_id["term-a"].current_a == pytest.approx(  # type: ignore[index]
            2.0, rel=1e-9
        )

    def test_power_balance_terminal_vs_integrated(self):
        """67: net terminal power equals integrated copper dissipation."""
        _, fields = self._run()
        # 1e-8: the identity V^T K V = V^T I holds exactly in exact
        # arithmetic; in doubles the source-current recovery K[d,:] . V
        # amplifies the ~1e-15 solve residual by the matrix scale. Observed
        # ~4e-9; a genuine accounting bug would show at 1e-3 or worse.
        assert fields.conservation.power_mismatch_fraction < 1e-8

    def test_dissipation_equals_i_squared_r(self):
        result, fields = self._run()
        v_a = result.terminals_by_id["term-a"].voltage_v  # type: ignore[index]
        v_b = result.terminals_by_id["term-b"].voltage_v  # type: ignore[index]
        r_eff = (v_a - v_b) / 2.0
        expected = analytical.resistive_power_w(2.0, r_eff)
        # Same floating-point argument as the power-balance test above.
        assert fields.conservation.dissipated_power_w == pytest.approx(expected, rel=1e-8)


class TestDisconnectedCopper:
    """63.7: an electrically impossible study is refused, not approximated."""

    def test_load_on_disconnected_island_is_refused(self):
        board = disconnected_islands_board()
        study = AnalysisStudy(
            id=StudyId("islands"),
            name="islands",
            board_id=str(board.id),
            net_ids=(NET,),
            sources=(
                VoltageSource(
                    id=SourceId("src"),
                    terminal_id="term-a",  # type: ignore[arg-type]
                    voltage=Quantity.configured(1.0, VOLT),
                ),
            ),
            loads=(
                CurrentLoad(
                    id=LoadId("load"),
                    terminal_id="term-b",  # type: ignore[arg-type]
                    current=Quantity.configured(1.0, AMPERE),
                ),
            ),
            mesh=_mesh(0.5e-3),
        )
        with pytest.raises(DisconnectedTerminalError):
            _solver().solve(board, study)


class TestCurrentDensity:
    """Uniform-field current density with correct volumetric units."""

    def test_uniform_trace_current_density(self):
        board = straight_trace_board(length_between_pads_m=20e-3, width_m=1e-3)
        study = AnalysisStudy(
            id=StudyId("density"),
            name="density",
            board_id=str(board.id),
            net_ids=(NET,),
            sources=(
                VoltageSource(
                    id=SourceId("src"),
                    terminal_id="term-a",  # type: ignore[arg-type]
                    voltage=Quantity.configured(0.85, VOLT),
                ),
            ),
            loads=(
                CurrentLoad(
                    id=LoadId("load"),
                    terminal_id="term-b",  # type: ignore[arg-type]
                    current=Quantity.configured(2.0, AMPERE),
                ),
            ),
            mesh=_mesh(0.25e-3),
        )
        _, fields = _solver().solve_with_fields(board, study)
        expected = analytical.current_density_a_per_m2(2.0, 1e-3, COPPER_T_M)
        # The field is uniform away from the pads, so the area-weighted bulk
        # of elements must sit at the analytical value. The raw peak is a
        # boundary artefact and deliberately NOT asserted (testing skill).
        import numpy as np

        median = float(np.median(fields.tri_j_vol_a_per_m2))
        assert median == pytest.approx(expected, rel=1e-2)
