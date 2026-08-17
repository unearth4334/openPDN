"""How current enters the copper: distributed contacts versus a single node.

ADR-0010 chose equipotential *contact regions* -- a pad's copper for a
terminal, a disc of the barrel's outer radius for a via -- and asserted that a
single-node coupling "would accrue a logarithmically mesh-dependent spreading
resistance". That assertion had never been measured. It is now, and it holds
quantitatively.

Injecting current into a 2-D sheet at a single point has no finite potential:
the continuum solution is `-(I / 2 pi Gs) ln(r)`. Halving the element size
therefore adds a *constant* `ln(2) / (2 pi Gs)` to the apparent resistance,
forever. Measured on `centre_contact_sheet_board`:

    h (mm)     DOFs      contact disc      point contact
    0.400        723      0.403011 mOhm     0.482040 mOhm
    0.200      2,811      0.415155          0.534873
    0.100     11,127      0.423298          0.580872
    0.050     44,094      0.427280          0.639541
    0.025    175,927      0.427238          0.698235

The disc converges to about `0.4272 mOhm`. The point contact adds a steady
`+0.0587 mOhm` per halving with no sign of stopping -- against
`ln(2) / (2 pi Gs) = 0.0543 mOhm` from the analytical spreading formula, an
8 % match on a discrete mesh.

So `numerics.point_source_singularity` is not a politeness. A point-contact
result is not merely less accurate than a distributed one: it has no limit to
be accurate *about*, and refining the mesh makes it worse rather than better.
"""

from __future__ import annotations

import itertools
import math

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
from openpdn.solver.fem import FemSheetSolver
from tests.validation.boards import COPPER_T_M, NET, centre_contact_sheet_board

#: Annealed copper conductivity, S/m -- the value the material table carries.
_SIGMA_S_PER_M = 5.8e7

#: Element sizes used for the refinement sweeps. Each step halves, so a
#: logarithmic divergence shows up as a constant increment.
_SIZES_M = (0.4e-3, 0.2e-3, 0.1e-3, 0.05e-3)


def _sweep(*, point_contact: bool) -> list[float]:
    """Terminal-to-terminal resistance across a halving mesh sequence."""
    board = centre_contact_sheet_board(point_contact=point_contact)
    solver = FemSheetSolver(normalizer=ShapelyGeometryNormalizer())
    resistances: list[float] = []
    for element_size_m in _SIZES_M:
        study = AnalysisStudy(
            id=StudyId("contact"),
            name="contact",
            board_id=str(board.id),
            net_ids=(NET,),
            sources=(
                VoltageSource(
                    id=SourceId("src"),
                    attachment=AttachmentGroup(terminal_ids=("term-b",)),  # type: ignore[arg-type]
                    voltage=Quantity.configured(0.0, VOLT),
                ),
            ),
            loads=(
                CurrentLoad(
                    id=LoadId("load"),
                    attachment=AttachmentGroup(terminal_ids=("term-a",)),  # type: ignore[arg-type]
                    current=Quantity.configured(1.0, AMPERE),
                ),
            ),
            mesh=MeshSettings(
                target_element_size=Quantity.configured(element_size_m, METRE),
                elements_across_feature=4,
                growth_rate=0.7,
            ),
        )
        result = solver.solve(board, study)
        voltages = [terminal.voltage_v for terminal in result.terminals]
        resistances.append(abs(voltages[1] - voltages[0]))
    return resistances


@pytest.fixture(scope="module")
def disc_sweep() -> list[float]:
    return _sweep(point_contact=False)


@pytest.fixture(scope="module")
def point_sweep() -> list[float]:
    return _sweep(point_contact=True)


class TestDistributedContactConverges:
    def test_successive_refinements_change_the_answer_less_and_less(self, disc_sweep):
        steps = [abs(later - earlier) for earlier, later in itertools.pairwise(disc_sweep)]
        assert steps[-1] < steps[0] / 3.0

    def test_the_finest_two_meshes_agree_closely(self, disc_sweep):
        assert disc_sweep[-1] == pytest.approx(disc_sweep[-2], rel=0.02)


class TestPointContactDiverges:
    def test_the_answer_keeps_growing_under_refinement(self, point_sweep):
        # Not noise, and not slow convergence: every refinement adds more.
        assert all(later > earlier for earlier, later in itertools.pairwise(point_sweep))

    def test_the_increments_do_not_shrink(self, disc_sweep, point_sweep):
        # The signature of a logarithm. A converging quantity's increments
        # fall off; these do not, which is why no amount of meshing rescues
        # a point contact.
        point_steps = [abs(b - a) for a, b in itertools.pairwise(point_sweep)]
        disc_steps = [abs(b - a) for a, b in itertools.pairwise(disc_sweep)]
        assert point_steps[-1] > 0.5 * point_steps[0]
        assert disc_steps[-1] < 0.5 * disc_steps[0]

    def test_the_growth_rate_matches_the_analytical_spreading_formula(self, point_sweep):
        # dR per halving should be ln(2) / (2 pi Gs). Matching this is what
        # distinguishes "the model is singular, as predicted" from "the
        # solver has a bug near the contact".
        sheet_conductance = _SIGMA_S_PER_M * COPPER_T_M
        expected = math.log(2.0) / (2.0 * math.pi * sheet_conductance)
        measured = abs(point_sweep[-1] - point_sweep[-2])
        assert measured == pytest.approx(expected, rel=0.35)

    def test_a_point_contact_is_always_worse_than_a_distributed_one(self, disc_sweep, point_sweep):
        assert all(p > d for p, d in zip(point_sweep, disc_sweep, strict=True))


class TestTheSolverSaysSo:
    def test_a_point_contact_carries_the_singularity_diagnostic(self):
        # The result must announce this, because the number it carries has
        # no continuum limit and a reader cannot tell that from its value.
        board = centre_contact_sheet_board(point_contact=True)
        solver = FemSheetSolver(normalizer=ShapelyGeometryNormalizer())
        study = AnalysisStudy(
            id=StudyId("contact"),
            name="contact",
            board_id=str(board.id),
            net_ids=(NET,),
            sources=(
                VoltageSource(
                    id=SourceId("src"),
                    attachment=AttachmentGroup(terminal_ids=("term-b",)),  # type: ignore[arg-type]
                    voltage=Quantity.configured(0.0, VOLT),
                ),
            ),
            loads=(
                CurrentLoad(
                    id=LoadId("load"),
                    attachment=AttachmentGroup(terminal_ids=("term-a",)),  # type: ignore[arg-type]
                    current=Quantity.configured(1.0, AMPERE),
                ),
            ),
            mesh=MeshSettings(
                target_element_size=Quantity.configured(0.2e-3, METRE),
                elements_across_feature=4,
                growth_rate=0.7,
            ),
        )
        codes = {diagnostic.code for diagnostic in solver.solve(board, study).diagnostics}
        assert "numerics.point_source_singularity" in codes

    def test_a_distributed_contact_does_not(self):
        board = centre_contact_sheet_board(point_contact=False)
        solver = FemSheetSolver(normalizer=ShapelyGeometryNormalizer())
        study = AnalysisStudy(
            id=StudyId("contact"),
            name="contact",
            board_id=str(board.id),
            net_ids=(NET,),
            sources=(
                VoltageSource(
                    id=SourceId("src"),
                    attachment=AttachmentGroup(terminal_ids=("term-b",)),  # type: ignore[arg-type]
                    voltage=Quantity.configured(0.0, VOLT),
                ),
            ),
            loads=(
                CurrentLoad(
                    id=LoadId("load"),
                    attachment=AttachmentGroup(terminal_ids=("term-a",)),  # type: ignore[arg-type]
                    current=Quantity.configured(1.0, AMPERE),
                ),
            ),
            mesh=MeshSettings(
                target_element_size=Quantity.configured(0.2e-3, METRE),
                elements_across_feature=4,
                growth_rate=0.7,
            ),
        )
        codes = {diagnostic.code for diagnostic in solver.solve(board, study).diagnostics}
        assert "numerics.point_source_singularity" not in codes
