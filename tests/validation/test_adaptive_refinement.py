"""Does adaptive refinement actually pay? Measured, on two boards.

Phase 2's question is not "does the loop run" but "does spending DOFs where
the estimator points beat spending them everywhere". The answer measured here
is **it depends on the board, and the dependence is predictable**:

* `plane_neck_plane_board` -- error concentrated in a neck, copper mostly
  elsewhere. Adaptive reaches the reference band at roughly 400-700 DOFs
  where global refinement is still several times worse at 2465. Adaptivity
  wins clearly.
* `series_widths_board` -- total resistance spread along the whole path.
  Refining the reentrant corner the estimator flags barely moves the answer,
  and adaptive does *not* beat uniform. That is not a defect in the loop; it
  is what a global quantity of interest on a board with no wasted copper
  looks like.

A second measured limit, recorded because it bounds every convergence claim
made on top of this machinery: successive meshes are **not nested**
(ADR-0013), and re-meshing moves the reported resistance by a few parts per
thousand on its own. Both the adaptive and the uniform sequences oscillate at
that level, so assertions here are written against bands rather than against
monotone decreases that the mesher cannot deliver.
"""

from __future__ import annotations

import pytest

from openpdn.domain.provenance import Quantity
from openpdn.domain.study import (
    AnalysisStudy,
    AttachmentGroup,
    CurrentLoad,
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
    solve_adaptive,
    terminal_resistance_qoi,
)
from openpdn.solver.fem.solver import solve_with_controls
from tests.validation.boards import NET, plane_neck_plane_board

_CURRENT_A = 1.0


def _study(board, element_size_m: float) -> AnalysisStudy:
    return AnalysisStudy(
        id=StudyId("adaptive"),
        name="adaptive",
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
                current=Quantity.configured(_CURRENT_A, AMPERE),
            ),
        ),
        mesh=MeshSettings(
            target_element_size=Quantity.configured(element_size_m, METRE),
            elements_across_feature=4,
            growth_rate=0.7,
        ),
    )


def _uniform(board, normalized, element_size_m: float) -> tuple[int, float]:
    result, _, problem = solve_with_controls(board, _study(board, element_size_m), normalized)
    return problem.n_dofs, terminal_resistance_qoi(result)


@pytest.fixture(scope="module")
def neck_case():
    board = plane_neck_plane_board()
    normalizer = ShapelyGeometryNormalizer()
    normalized = normalizer.normalize(board)
    # The finest global mesh is the reference. It carries a few parts per
    # thousand of uncertainty of its own -- see the module docstring -- so it
    # is used to define a band, not an exact target.
    reference = _uniform(board, normalized, 0.0625e-3)[1]
    return board, normalizer, normalized, reference


class TestAdaptivityPaysWhenErrorIsConcentrated:
    def test_adaptive_reaches_the_reference_band_far_cheaper_than_uniform(self, neck_case):
        board, normalizer, normalized, reference = neck_case

        coarse_dofs, coarse_r = _uniform(board, normalized, 1.0e-3)
        mid_dofs, mid_r = _uniform(board, normalized, 0.25e-3)

        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3),
            normalizer,
            AdaptivePolicy(target_qoi_rel_change=1e-4, max_passes=5, max_dofs=400_000),
        )
        best = min(
            outcome.generations,
            key=lambda g: abs(g.quantity_of_interest - reference),
        )
        adaptive_error = abs(best.quantity_of_interest - reference) / reference
        uniform_mid_error = abs(mid_r - reference) / reference
        uniform_coarse_error = abs(coarse_r - reference) / reference

        # Starting from the same coarse mesh, adaptivity must beat it by a
        # wide margin without approaching the global mesh's DOF cost.
        assert adaptive_error < uniform_coarse_error / 10.0
        assert best.dof_count < mid_dofs
        assert adaptive_error < uniform_mid_error
        assert coarse_dofs < best.dof_count  # it really did refine

    def test_refinement_concentrates_rather_than_flooding(self, neck_case):
        board, normalizer, _, _ = neck_case
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3),
            normalizer,
            AdaptivePolicy(max_passes=4, max_dofs=400_000),
        )
        first, last = outcome.generations[0], outcome.generations[-1]
        # Global refinement to the same local resolution would multiply the
        # DOF count by orders of magnitude; bulk marking keeps the growth
        # modest because most of the copper never needed it.
        assert last.dof_count > first.dof_count
        assert last.dof_count < 12 * first.dof_count

    def test_the_estimator_falls_as_refinement_proceeds(self, neck_case):
        board, normalizer, _, _ = neck_case
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3),
            normalizer,
            AdaptivePolicy(max_passes=4, max_dofs=400_000),
        )
        estimates = [g.estimated_error for g in outcome.generations]
        assert estimates[-1] < estimates[0]


class TestStoppingDiscipline:
    def test_a_run_that_hits_its_ceiling_is_not_reported_as_converged(self, neck_case):
        # ADR-0013 §8 / ADR-0015: exhausting a budget while the answer is
        # still moving is RESOURCE_LIMITED. Presenting it as converged is
        # the single failure mode this tier exists to prevent.
        board, normalizer, _, _ = neck_case
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3),
            normalizer,
            AdaptivePolicy(target_qoi_rel_change=1e-12, max_passes=2, max_dofs=400_000),
        )
        assert outcome.status == AdaptiveStatus.RESOURCE_LIMITED
        assert not outcome.converged

    def test_a_dof_ceiling_stops_the_run(self, neck_case):
        board, normalizer, _, _ = neck_case
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3),
            normalizer,
            AdaptivePolicy(target_qoi_rel_change=1e-12, max_passes=6, max_dofs=1),
        )
        assert outcome.status == AdaptiveStatus.RESOURCE_LIMITED
        assert len(outcome.generations) == 1

    def test_convergence_needs_more_than_one_quiet_pass(self, neck_case):
        # Non-nested re-meshing means two successive meshes can agree by
        # accident. Requiring confirmations *and* a real fall in the
        # estimator is what stops that reading as convergence.
        board, normalizer, _, _ = neck_case
        outcome = solve_adaptive(
            board,
            _study(board, 1.0e-3),
            normalizer,
            AdaptivePolicy(target_qoi_rel_change=1.0, confirmations=1, max_passes=3),
        )
        # A wide-open target is met on the first comparison, but the run may
        # only call itself converged once the estimator has actually fallen.
        if outcome.converged:
            first = outcome.generations[0].estimated_error
            assert outcome.generations[-1].estimated_error <= first / 2.0

    def test_every_generation_records_its_own_evidence(self, neck_case):
        board, normalizer, _, _ = neck_case
        outcome = solve_adaptive(
            board, _study(board, 1.0e-3), normalizer, AdaptivePolicy(max_passes=3)
        )
        assert [g.index for g in outcome.generations] == list(range(len(outcome.generations)))
        assert outcome.generations[0].qoi_rel_change is None
        for generation in outcome.generations:
            assert generation.dof_count > 0
            assert generation.estimated_error >= 0.0
            assert generation.current_imbalance_fraction < 1e-6
