"""Check the analytical references themselves.

The references in `analytical.py` decide whether a future solver passes or
fails, so they are pinned here against hand-computed values and against
identities that must hold regardless of the numbers.

Tolerances are deliberate. `rel=1e-12` means "these should agree to floating
point"; anything looser needs a stated physical reason. Widening a tolerance to
make a test pass is prohibited -- see `.agents/skills/testing/SKILL.md`.
"""

from __future__ import annotations

import math

import pytest

from openpdn.domain.materials import COPPER_CONDUCTIVITY_S_PER_M
from openpdn.domain.units import oz_copper_to_m

from .analytical import (
    current_density_a_per_m2,
    ir_drop_v,
    parallel_resistance_ohm,
    resistive_power_w,
    series_resistance_ohm,
    sheet_resistance_ohm_per_square,
    trace_resistance_ohm,
    via_barrel_resistance_ohm,
    voltage_divider_v,
)

pytestmark = pytest.mark.validation


class TestStraightTrace:
    def test_matches_a_hand_computed_case(self):
        # 100 mm long, 1 mm wide, 1 oz (34.8 um) copper at 100 % IACS:
        #   R = 0.1 / (5.8001e7 * 1e-3 * 34.8e-6) = 49.53 mohm
        resistance = trace_resistance_ohm(0.100, 1e-3, oz_copper_to_m(1.0))
        assert resistance == pytest.approx(0.04953, rel=1e-3)

    def test_resistance_is_linear_in_length(self):
        single = trace_resistance_ohm(0.010, 1e-3, 35e-6)
        double = trace_resistance_ohm(0.020, 1e-3, 35e-6)
        assert double == pytest.approx(2.0 * single, rel=1e-12)

    def test_resistance_is_inverse_in_width_and_thickness(self):
        base = trace_resistance_ohm(0.010, 1e-3, 35e-6)
        wider = trace_resistance_ohm(0.010, 2e-3, 35e-6)
        thicker = trace_resistance_ohm(0.010, 1e-3, 70e-6)
        assert wider == pytest.approx(base / 2.0, rel=1e-12)
        assert thicker == pytest.approx(base / 2.0, rel=1e-12)

    def test_a_square_of_copper_equals_its_sheet_resistance(self):
        # The defining property of ohms-per-square: side length cancels.
        thickness_m = 35e-6
        per_square = sheet_resistance_ohm_per_square(thickness_m)
        for side_m in (1e-3, 5e-3, 50e-3):
            assert trace_resistance_ohm(side_m, side_m, thickness_m) == pytest.approx(
                per_square, rel=1e-12
            )

    def test_one_ounce_copper_is_about_half_a_milliohm_per_square(self):
        # Familiar shop-floor number: ~0.5 mohm/square for 1 oz copper.
        assert sheet_resistance_ohm_per_square(oz_copper_to_m(1.0)) == pytest.approx(
            0.495e-3, rel=1e-2
        )

    def test_non_physical_input_is_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            trace_resistance_ohm(0.1, 0.0, 35e-6)


class TestCombinations:
    def test_two_identical_traces_in_parallel_halve_the_resistance(self):
        single = trace_resistance_ohm(0.050, 1e-3, 35e-6)
        assert parallel_resistance_ohm(single, single) == pytest.approx(single / 2.0, rel=1e-12)

    def test_parallel_of_unequal_resistances(self):
        # 2 || 3 = 1.2
        assert parallel_resistance_ohm(2.0, 3.0) == pytest.approx(1.2, rel=1e-12)

    def test_series_segments_equal_one_long_trace(self):
        # Splitting a trace must not change its resistance.
        whole = trace_resistance_ohm(0.030, 1e-3, 35e-6)
        parts = series_resistance_ohm(*(trace_resistance_ohm(0.010, 1e-3, 35e-6) for _ in range(3)))
        assert parts == pytest.approx(whole, rel=1e-12)

    def test_voltage_division_is_proportional(self):
        assert voltage_divider_v(0.85, 1.0, 1.0) == pytest.approx(0.425, rel=1e-12)
        assert voltage_divider_v(0.85, 3.0, 1.0) == pytest.approx(0.2125, rel=1e-12)


class TestViaBarrel:
    def test_matches_a_hand_computed_case(self):
        # 1.6 mm board, 0.3 mm finished hole, 25 um plating:
        #   A = pi * ((0.175 mm)^2 - (0.15 mm)^2) = 2.5525e-8 m^2
        #   R = 1.6e-3 / (5.8001e7 * 2.5525e-8) = 1.081 mohm
        area_m2 = math.pi * ((0.5 * 0.0003 + 25e-6) ** 2 - (0.5 * 0.0003) ** 2)
        expected = 1.6e-3 / (COPPER_CONDUCTIVITY_S_PER_M * area_m2)
        assert via_barrel_resistance_ohm(1.6e-3, 0.0003, 25e-6) == pytest.approx(
            expected, rel=1e-12
        )
        assert expected == pytest.approx(1.081e-3, rel=1e-3)

    def test_thicker_plating_lowers_resistance(self):
        thin = via_barrel_resistance_ohm(1.6e-3, 0.0003, 18e-6)
        thick = via_barrel_resistance_ohm(1.6e-3, 0.0003, 35e-6)
        assert thick < thin

    def test_two_stitching_vias_halve_the_interlayer_resistance(self):
        single = via_barrel_resistance_ohm(1.6e-3, 0.0003, 25e-6)
        assert parallel_resistance_ohm(single, single) == pytest.approx(single / 2, rel=1e-12)


class TestDerivedQuantities:
    def test_ir_drop_on_a_realistic_rail(self):
        # R = 50 mm / (5.8001e7 * 2 mm * 34.8 um) = 12.39 mohm.
        # 4 A through it drops 49.5 mV -- 5.8 % of a 0.85 V rail, which is the
        # kind of number openPDN exists to surface.
        resistance = trace_resistance_ohm(0.050, 2e-3, oz_copper_to_m(1.0))
        assert resistance == pytest.approx(0.01239, rel=1e-3)
        drop_v = ir_drop_v(4.0, resistance)
        assert drop_v == pytest.approx(0.0495, rel=1e-2)
        assert drop_v / 0.85 == pytest.approx(0.058, rel=2e-2)

    def test_power_follows_i_squared_r(self):
        assert resistive_power_w(2.0, 0.5) == pytest.approx(2.0, rel=1e-12)
        assert resistive_power_w(4.0, 0.5) == pytest.approx(
            4.0 * resistive_power_w(2.0, 0.5), rel=1e-12
        )

    def test_current_density_in_a_narrow_trace(self):
        # 4 A in 1 mm x 35 um copper = 1.14e8 A/m^2 = 114 A/mm^2: far above any
        # sane continuous rating, and exactly what a current-density map should
        # make obvious.
        density = current_density_a_per_m2(4.0, 1e-3, 35e-6)
        assert density == pytest.approx(1.143e8, rel=1e-3)
