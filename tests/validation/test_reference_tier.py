"""Quadratic elements and adaptivity together, at the Reference tier.

Per-quantity convergence, plus extrapolation that refuses to guess.

The headline measurement, on `plane_neck_plane_board` against a converged
reference (uniform P2, 571,557 DOFs, 6.882549 mOhm):

    method                     DOFs        relative error
    uniform  P1               2,465            9.98e-3
    uniform  P1              35,976            5.28e-3
    uniform  P1             143,213            1.98e-3
    adaptive P1                 513            3.73e-3
    adaptive P2               1,388            1.07e-3

Adaptive P2 reaches a better answer than uniform P1 does with **a hundred
times** the degrees of freedom. That is the case for this tier, and it is
only visible against a properly converged reference -- an earlier reference
taken from the finest uniform P1 mesh was itself 0.5 % off.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

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
from openpdn.solver.fem.adaptive import (
    AdaptivePolicy,
    AdaptiveStatus,
    richardson_extrapolate,
    solve_adaptive,
    terminal_resistance_qoi,
)
from openpdn.solver.fem.solver import solve_with_controls
from tests.validation.boards import NET, plane_neck_plane_board
from tests.validation.test_adaptive_refinement import _REFERENCE_OHM


def _study(board, element_size_m: float, order: ElementOrder) -> AnalysisStudy:
    return AnalysisStudy(
        id=StudyId("reference"),
        name="reference",
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
                current=Quantity.configured(1.0, AMPERE),
            ),
        ),
        mesh=MeshSettings(
            target_element_size=Quantity.configured(element_size_m, METRE),
            elements_across_feature=4,
            growth_rate=0.7,
            element_order=order,
        ),
    )


@pytest.fixture(scope="module")
def board_and_normalizer():
    board = plane_neck_plane_board()
    normalizer = ShapelyGeometryNormalizer()
    return board, normalizer


class TestReferenceValueItself:
    def test_a_moderately_fine_p2_solve_agrees_with_the_stored_reference(
        self, board_and_normalizer
    ):
        # Guards the hard-coded constant the other tests measure against.
        # A 143k-DOF run takes seconds, so this checks a 36k-DOF one and
        # allows the residual gap that mesh genuinely has.
        board, normalizer = board_and_normalizer
        result, _, problem, _ = solve_with_controls(
            board, _study(board, 0.125e-3, ElementOrder.P2), normalizer.normalize(board)
        )
        resistance = terminal_resistance_qoi(result)
        assert problem.n_dofs > 30_000
        assert abs(resistance - _REFERENCE_OHM) / _REFERENCE_OHM < 3e-3


class TestQuadraticPlusAdaptive:
    def test_p2_adaptive_beats_uniform_p1_at_a_fraction_of_the_dofs(
        self, board_and_normalizer
    ):
        board, normalizer = board_and_normalizer
        normalized = normalizer.normalize(board)

        uniform, _, uniform_problem, _ = solve_with_controls(
            board, _study(board, 0.25e-3, ElementOrder.P1), normalized
        )
        uniform_error = (
            abs(terminal_resistance_qoi(uniform) - _REFERENCE_OHM) / _REFERENCE_OHM
        )

        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3, ElementOrder.P2),
            normalizer,
            AdaptivePolicy(target_qoi_rel_change=1e-4, max_passes=4, max_dofs=400_000),
        )
        adaptive_error = (
            abs(outcome.final.quantity_of_interest - _REFERENCE_OHM) / _REFERENCE_OHM
        )
        assert adaptive_error < uniform_error
        assert outcome.final.dof_count < uniform_problem.n_dofs

    def test_the_estimator_falls_monotonically_under_p2(self, board_and_normalizer):
        # The flux jump is integrated along each edge rather than sampled,
        # because at P2 it varies linearly along one. A sampled version
        # misreports the error by an amount that grows with the gradients
        # adaptivity is chasing, so this would not hold.
        board, normalizer = board_and_normalizer
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3, ElementOrder.P2),
            normalizer,
            AdaptivePolicy(max_passes=4, max_dofs=400_000),
        )
        estimates = [g.estimated_error for g in outcome.generations]
        assert all(
            later < earlier for earlier, later in itertools.pairwise(estimates)
        )

    def test_conservation_holds_at_every_generation(self, board_and_normalizer):
        board, normalizer = board_and_normalizer
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3, ElementOrder.P2),
            normalizer,
            AdaptivePolicy(max_passes=3, max_dofs=400_000),
        )
        for generation in outcome.generations:
            assert generation.current_imbalance_fraction < 1e-6
            assert generation.power_mismatch_fraction < 1e-6


@pytest.fixture(scope="module")
def per_quantity_outcome(board_and_normalizer):
    board, normalizer = board_and_normalizer
    return solve_adaptive(
        board,
        _study(board, 1.0e-3, ElementOrder.P1),
        normalizer,
        AdaptivePolicy(max_passes=4, max_dofs=400_000),
    )


class TestPerQuantityConvergence:
    def test_every_tracked_quantity_has_a_history(self, per_quantity_outcome):
        outcome = per_quantity_outcome
        for name in ("resistance_ohm", "total_loss_w", "j99_a_per_m2", "peak_j_a_per_m2"):
            quantity = outcome.quantity(name)
            assert len(quantity.values) == len(outcome.generations)

    def test_peak_current_density_is_flagged_singular_and_never_converged(
        self, per_quantity_outcome
    ):
        outcome = per_quantity_outcome
        # At a reentrant corner the continuum peak is unbounded, so it rises
        # with every refinement. Reporting it as converged would be a lie
        # however quiet it happens to look (ADR-0013 §5).
        peak = outcome.quantity("peak_j_a_per_m2")
        assert peak.singular
        assert not peak.converged
        assert peak.extrapolated is None

    def test_the_robust_percentile_is_not_flagged_singular(self, per_quantity_outcome):
        outcome = per_quantity_outcome
        # J99 is area-weighted and does have a finite limit -- it is what
        # replaces the raw peak as a convergeable current-density measure.
        assert not outcome.quantity("j99_a_per_m2").singular

    def test_a_singular_quantity_does_not_condemn_the_run(self, board_and_normalizer):
        board, normalizer = board_and_normalizer
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3, ElementOrder.P1),
            normalizer,
            # Wide target so the engineering quantities settle immediately.
            AdaptivePolicy(target_qoi_rel_change=1.0, confirmations=1,
                           required_error_reduction=1.0, max_passes=3),
        )
        assert outcome.status in {
            AdaptiveStatus.CONVERGED,
            AdaptiveStatus.CONVERGED_WITH_MODEL_LIMITATIONS,
        }
        assert outcome.converged


class TestExtrapolationRefusesToGuess:
    def test_a_clean_second_order_sequence_is_extrapolated(self):
        # f = 1 + h^2 with h halving, so DOFs quadruple in 2-D.
        limit = 1.0
        values = [limit + (0.5**k) ** 2 for k in range(4)]
        dofs = [100 * 4**k for k in range(4)]
        extrapolated, order = richardson_extrapolate(values, dofs)
        assert extrapolated == pytest.approx(limit, abs=1e-6)
        assert order == pytest.approx(2.0, abs=0.05)

    def test_an_oscillating_sequence_is_refused(self):
        # Exactly what non-nested re-meshing produces. Fitting a limit here
        # yields a confident number that means nothing (ADR-0015 §6).
        values = [1.0, 1.02, 0.99, 1.01]
        dofs = [100, 400, 1600, 6400]
        assert richardson_extrapolate(values, dofs) == (None, None)

    def test_a_sequence_whose_steps_grow_is_refused(self):
        values = [1.0, 1.01, 1.03, 1.08]
        dofs = [100, 400, 1600, 6400]
        assert richardson_extrapolate(values, dofs) == (None, None)

    def test_too_few_samples_are_refused(self):
        assert richardson_extrapolate([1.0, 1.1], [100, 400]) == (None, None)

    def test_an_implausible_rate_is_refused(self):
        # Monotone and shrinking, but at a rate no discretisation produces --
        # noise that happened to look like a trend over three samples.
        values = [1.0, 1.5, 1.5 + 1e-12]
        dofs = [100, 400, 1600]
        extrapolated, order = richardson_extrapolate(values, dofs)
        assert (extrapolated, order) == (None, None)

    def test_extrapolation_on_a_real_run_is_either_absent_or_sane(
        self, board_and_normalizer
    ):
        # Whichever way it goes, it must not produce a value far outside the
        # measured band -- refusing is a valid, expected outcome here.
        board, normalizer = board_and_normalizer
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3, ElementOrder.P1),
            normalizer,
            AdaptivePolicy(max_passes=4, max_dofs=400_000),
        )
        resistance = outcome.quantity("resistance_ohm")
        if resistance.extrapolated is not None:
            assert np.isfinite(resistance.extrapolated)
            assert 0.5 * _REFERENCE_OHM < resistance.extrapolated < 2.0 * _REFERENCE_OHM
            assert resistance.observed_order is not None
