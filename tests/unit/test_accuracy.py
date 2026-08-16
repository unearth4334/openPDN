"""Accuracy profile → mesh-number resolution.

Pins the profile ladder measured end-to-end (real mesh, real solve) on the
150x100 mm reference board (see `accuracy.py`'s module docstring for the
measured DOFs/wall-clock table) so a future retune can't silently drift
without updating both the numbers and that record.
"""

from __future__ import annotations

import pytest

from openpdn.application.accuracy import (
    _SHAPES,
    MIN_SIZE_FRACTION,
    VERIFICATION_REFINEMENT_FACTOR,
    refine_mesh_spec,
    resolve_profile,
)
from openpdn.application.simulation_models import AccuracyProfile, ResolvedMeshSpec

_PROFILES_IN_ORDER = (
    AccuracyProfile.PREVIEW,
    AccuracyProfile.STANDARD,
    AccuracyProfile.HIGH,
    AccuracyProfile.VERIFICATION,
)


class TestProfileShapes:
    """Pin the measured ladder itself, not just its ordering."""

    def test_preview_sits_where_verification_used_to(self):
        # The deliberate shift: Preview is now what Verification was before
        # this retune (180 divisions, 8 across, 0.5 growth) -- the old
        # Preview/Standard/High all solved in well under a second and gave
        # no meaningful accuracy/cost spread.
        shape = _SHAPES[AccuracyProfile.PREVIEW]
        assert shape.diagonal_divisions == 180.0
        assert shape.elements_across_feature == 8
        assert shape.growth_rate == 0.5
        assert shape.verify_convergence is False

    def test_verification_shape_is_the_measured_hundred_x_tier(self):
        shape = _SHAPES[AccuracyProfile.VERIFICATION]
        assert shape.diagonal_divisions == 800.0
        assert shape.elements_across_feature == 18
        assert shape.growth_rate == 0.4
        assert shape.verify_convergence is True

    def test_only_verification_runs_the_convergence_pass(self):
        for profile in (AccuracyProfile.PREVIEW, AccuracyProfile.STANDARD, AccuracyProfile.HIGH):
            assert _SHAPES[profile].verify_convergence is False
        assert _SHAPES[AccuracyProfile.VERIFICATION].verify_convergence is True

    def test_divisions_and_across_feature_increase_monotonically(self):
        # Cost (and, per the measured table, wall-clock) climbs with the
        # profile ladder; a retune that breaks this ordering breaks the
        # naming ("High" cheaper than "Standard" would be a bug, not a
        # tuning choice).
        divisions = [_SHAPES[p].diagonal_divisions for p in _PROFILES_IN_ORDER]
        across = [_SHAPES[p].elements_across_feature for p in _PROFILES_IN_ORDER]
        assert divisions == sorted(divisions)
        assert len(set(divisions)) == len(divisions)
        assert across == sorted(across)
        assert len(set(across)) == len(across)


class TestResolveProfile:
    def test_max_element_scales_with_board_diagonal(self):
        mesh, verify = resolve_profile(AccuracyProfile.PREVIEW, board_diagonal_m=0.18)
        assert mesh.max_element_m == pytest.approx(0.18 / 180.0)
        assert verify is False

    def test_min_element_is_the_configured_fraction_of_max(self):
        mesh, _ = resolve_profile(AccuracyProfile.HIGH, board_diagonal_m=1.0)
        assert mesh.min_element_m == pytest.approx(mesh.max_element_m * MIN_SIZE_FRACTION)

    def test_same_relative_resolution_regardless_of_board_size(self):
        # ADR-0011: "Standard" means the same relative resolution on a 30 mm
        # module and a 300 mm backplane.
        small, _ = resolve_profile(AccuracyProfile.STANDARD, board_diagonal_m=0.03)
        large, _ = resolve_profile(AccuracyProfile.STANDARD, board_diagonal_m=0.3)
        assert large.max_element_m / small.max_element_m == pytest.approx(10.0)
        assert small.elements_across_feature == large.elements_across_feature
        assert small.growth_rate == large.growth_rate


class TestRefineMeshSpec:
    def test_scales_element_bounds_down_by_the_factor(self):
        mesh = ResolvedMeshSpec(
            max_element_m=1e-3, min_element_m=1e-5, elements_across_feature=8, growth_rate=0.5
        )
        refined = refine_mesh_spec(mesh, VERIFICATION_REFINEMENT_FACTOR)
        expected_max = mesh.max_element_m / VERIFICATION_REFINEMENT_FACTOR
        expected_min = mesh.min_element_m / VERIFICATION_REFINEMENT_FACTOR
        assert refined.max_element_m == pytest.approx(expected_max)
        assert refined.min_element_m == pytest.approx(expected_min)

    def test_scales_elements_across_feature_up_and_rounds_up(self):
        mesh = ResolvedMeshSpec(
            max_element_m=1e-3, min_element_m=1e-5, elements_across_feature=18, growth_rate=0.4
        )
        # 18 * sqrt(2) = 25.46 -- a genuine refinement must round up, never
        # down, or the "refined" feature-width sizing could end up coarser.
        refined = refine_mesh_spec(mesh, VERIFICATION_REFINEMENT_FACTOR)
        assert refined.elements_across_feature == 26

    def test_growth_rate_is_unchanged(self):
        mesh = ResolvedMeshSpec(
            max_element_m=1e-3, min_element_m=1e-5, elements_across_feature=8, growth_rate=0.5
        )
        refined = refine_mesh_spec(mesh, VERIFICATION_REFINEMENT_FACTOR)
        assert refined.growth_rate == mesh.growth_rate
