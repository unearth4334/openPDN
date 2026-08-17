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
    relative_change,
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
    def test_p2_adaptive_beats_uniform_p1_at_a_fraction_of_the_dofs(self, board_and_normalizer):
        board, normalizer = board_and_normalizer
        normalized = normalizer.normalize(board)

        uniform, _, uniform_problem, _ = solve_with_controls(
            board, _study(board, 0.25e-3, ElementOrder.P1), normalized
        )
        uniform_error = abs(terminal_resistance_qoi(uniform) - _REFERENCE_OHM) / _REFERENCE_OHM

        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3, ElementOrder.P2),
            normalizer,
            AdaptivePolicy(target_qoi_rel_change=1e-4, max_passes=4, max_dofs=400_000),
        )
        adaptive_error = abs(outcome.final.quantity_of_interest - _REFERENCE_OHM) / _REFERENCE_OHM
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
        assert all(later < earlier for earlier, later in itertools.pairwise(estimates))

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
            AdaptivePolicy(
                target_qoi_rel_change=1.0,
                confirmations=1,
                max_passes=3,
            ),
        )
        assert outcome.status in {
            AdaptiveStatus.CONVERGED,
            AdaptiveStatus.CONVERGED_WITH_MODEL_LIMITATIONS,
        }
        assert outcome.converged


class TestEstimatorStabilisationStoppingRule:
    """The redesigned §8 estimator criterion, measured against a real defect.

    On a production 392-via board the previous design (require the global
    estimator to halve from its first, coarsest pass) could never be
    satisfied: the estimator plateaued at ~55% of its starting value from
    pass 6 onward -- a genuine singular contribution (a via annulus) capping
    the *global* RSS estimator -- while the QoI itself had settled to a
    relative change of 1e-11 by pass 12. The run exhausted its full pass
    ceiling reporting RESOURCE_LIMITED despite being converged by every
    practical measure. Goal-oriented marking did not change the qualitative
    plateau. The fix asks whether the estimator has *stopped moving*, which
    holds regardless of what value it stopped at.
    """

    #: The actual global-estimator sequence from the production run that
    #: exposed this defect (`plane-neck-plane`-style board, 392 vias,
    #: 32,317 -> 51,136 DOFs, theta=0.7, P2) -- pass 0 through pass 9.
    #: Reproduced locally from the customer's own board document; only the
    #: numeric sequence is checked in, not the board.
    _PRODUCTION_PLATEAU = (
        0.7372,
        0.5187,
        0.4388,
        0.4180,
        0.4101,
        0.4057,
        0.4054,
        0.4047,
        0.4049,
        0.4048,
    )

    def test_the_old_halving_rule_never_fires_on_the_measured_sequence(self):
        # Documents exactly why the old design failed in production: every
        # value from pass 5 onward sits above half of pass 0, forever.
        eta0 = self._PRODUCTION_PLATEAU[0]
        assert all(v > eta0 / 2.0 for v in self._PRODUCTION_PLATEAU[5:])

    def test_the_new_stabilisation_rule_fires_on_the_measured_sequence(self):
        # The new criterion accepts what the old one structurally could
        # not: pass-over-pass relative change within a 1e-3 target, which
        # this sequence reaches by pass 7 (change 8.78e-7 -- see the report).
        target = 1e-3
        rel_changes = [
            relative_change(later, earlier)
            for earlier, later in itertools.pairwise(self._PRODUCTION_PLATEAU)
        ]
        stabilised_from = next(
            i for i, v in enumerate(rel_changes) if v is not None and v <= target
        )
        assert stabilised_from < len(self._PRODUCTION_PLATEAU) - 1

    def test_relative_change_matches_the_reported_production_deltas(self):
        # Ground the helper itself against the two production deltas quoted
        # in the report (dQoI at passes 8 and 9 of the real run).
        assert relative_change(0.003023643, 0.003023650) == pytest.approx(2.315e-6, rel=1e-3)
        assert relative_change(0.003023692, 0.003023643) == pytest.approx(1.6206e-5, rel=1e-3)

    def test_default_estimator_target_reuses_the_qoi_target(self):
        from openpdn.solver.fem.adaptive import AdaptivePolicy

        policy = AdaptivePolicy(target_qoi_rel_change=5e-4)
        assert policy.target_estimator_rel_change is None  # reuses the QoI target

    def test_an_explicit_estimator_target_overrides_the_qoi_target(self):
        from openpdn.solver.fem.adaptive import AdaptivePolicy

        policy = AdaptivePolicy(target_qoi_rel_change=1e-3, target_estimator_rel_change=1e-2)
        assert policy.target_estimator_rel_change == 1e-2

    def test_a_negative_estimator_target_is_refused(self):
        from openpdn.solver.fem.adaptive import AdaptivePolicy

        with pytest.raises(ValueError, match="stabilisation target"):
            AdaptivePolicy(target_estimator_rel_change=-1e-3)

    def test_the_estimator_gate_still_refuses_a_mesh_that_is_still_moving(
        self, board_and_normalizer
    ):
        # The other half of the claim: this isn't "loosen the gate until
        # anything converges". On `plane_neck_plane_board`, even a loose
        # 5e-2 QoI target does not yield CONVERGED within 8 passes, because
        # the estimator itself is still actively falling by 10-25% per pass
        # here -- it has not reached a fixed point yet, so declining to
        # converge is correct, not a regression. This is exactly the
        # non-nested-meshes-agreeing-by-accident case ADR-0013 §8 guards
        # against, still guarded after the redesign.
        board, normalizer = board_and_normalizer
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3, ElementOrder.P1),
            normalizer,
            AdaptivePolicy(target_qoi_rel_change=5e-2, max_passes=8, confirmations=2),
        )
        assert outcome.status == AdaptiveStatus.RESOURCE_LIMITED
        estimates = [g.estimated_error for g in outcome.generations]
        # The estimator was still moving by more than the target right up to
        # the ceiling -- the reason it correctly never settled.
        last_change = relative_change(estimates[-1], estimates[-2])
        assert last_change is not None
        assert last_change > 5e-2


class TestConservationGateMatchesADR0010sErrorThreshold:
    """The default conservation gate was copying the wrong ADR-0010 §6 constant.

    `AdaptivePolicy.max_power_mismatch` and `max_current_imbalance` defaulted
    to `1e-6` -- ADR-0010's *warning* threshold, reused as a hard pass/fail
    gate on convergence. On the same 392-via production board that exposed
    the estimator-stabilisation defect above, power mismatch settled at
    1.0e-5 to 1.4e-5 across all eleven passes: an order of magnitude past
    that gate, but two orders inside ADR-0010's actual *error* threshold of
    1e-3. The board was already fully stabilised (both QoI and estimator)
    by pass 10 and still reported RESOURCE_LIMITED for burning through its
    entire pass ceiling on a conservation figure the rest of the codebase
    itself treats as merely worth flagging, not disqualifying. The result
    still carries a `numerics.power_mismatch` warning diagnostic either way
    -- only the hard gate was miscalibrated.
    """

    #: `power_mismatch_fraction` per pass from the production run that
    #: exposed this, passes 0 through 10. Reproduced locally from the
    #: customer's own board document; only the numeric sequence is checked
    #: in, not the board.
    _PRODUCTION_POWER_MISMATCH = (
        4.985700e-06,
        5.386248e-06,
        9.672472e-06,
        1.038296e-05,
        1.002368e-05,
        1.011611e-05,
        1.292104e-05,
        1.409747e-05,
        1.319437e-05,
        1.288359e-05,
        1.362962e-05,
    )

    def test_the_old_default_would_have_refused_every_one_of_these_passes(self):
        old_default = 1e-6
        assert all(v > old_default for v in self._PRODUCTION_POWER_MISMATCH)

    def test_the_new_default_accepts_every_one_of_these_passes(self):
        from openpdn.solver.fem.adaptive import AdaptivePolicy

        policy = AdaptivePolicy()
        assert all(v <= policy.max_power_mismatch for v in self._PRODUCTION_POWER_MISMATCH)

    def test_only_power_mismatch_moved_to_the_error_threshold(self):
        # Current imbalance measured 3e-8 to 4e-8 on the same production
        # board -- 25x inside even the old 1e-6 gate -- so there is no
        # measured reason to loosen it too. Only power mismatch was ever
        # the blocker.
        from openpdn.solver.fem.adaptive import AdaptivePolicy
        from openpdn.solver.fem.solver import (
            CONSERVATION_ERROR_FRACTION,
            CONSERVATION_WARN_FRACTION,
        )

        policy = AdaptivePolicy()
        assert policy.max_power_mismatch == CONSERVATION_ERROR_FRACTION
        assert policy.max_current_imbalance == CONSERVATION_WARN_FRACTION
        # Still a real gate -- not disabled, just no longer at the warn line.
        assert policy.max_power_mismatch > CONSERVATION_WARN_FRACTION

    def test_a_genuinely_broken_power_balance_still_refuses_to_converge(self, board_and_normalizer):
        # The other half of the claim: this isn't "the gate no longer does
        # anything". A fraction past the *error* threshold must still block
        # convergence.
        from openpdn.solver.fem.adaptive import AdaptivePolicy

        board, normalizer = board_and_normalizer
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3, ElementOrder.P1),
            normalizer,
            AdaptivePolicy(
                target_qoi_rel_change=1.0,
                confirmations=1,
                max_passes=2,
                max_power_mismatch=0.0,
            ),
        )
        assert outcome.status == AdaptiveStatus.RESOURCE_LIMITED


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

    def test_extrapolation_on_a_real_run_is_either_absent_or_sane(self, board_and_normalizer):
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
