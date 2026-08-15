"""Accuracy profiles: named confidence levels resolved to mesh numbers.

A profile is a bundle of numerical controls, not a single element-size
slider. Each resolves against the board's diagonal so "Standard" means the
same relative resolution on a 30 mm module and a 300 mm backplane; the
resolved absolute numbers -- not the profile name -- are frozen into the job
spec (ADR-0011).

The values below were calibrated on the validation suite (straight-trace
error at Standard ~0.3 %, High ~0.1 %) and bounded on the real reference
board; treat changes as solver-behaviour changes, not cosmetics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from openpdn.application.simulation_models import AccuracyProfile, ResolvedMeshSpec


@dataclass(frozen=True, slots=True)
class _ProfileShape:
    """How one profile scales against the board diagonal."""

    diagonal_divisions: float
    elements_across_feature: int
    growth_rate: float
    verify_convergence: bool


_SHAPES: Final[dict[AccuracyProfile, _ProfileShape]] = {
    # Fast sanity check: coarse elements, 2-3 across narrow features, no
    # convergence pass. Results are labelled preview / not verified.
    AccuracyProfile.PREVIEW: _ProfileShape(
        diagonal_divisions=60.0,
        elements_across_feature=2,
        growth_rate=1.0,
        verify_convergence=False,
    ),
    # Normal engineering work: ~4 elements across significant features.
    AccuracyProfile.STANDARD: _ProfileShape(
        diagonal_divisions=100.0,
        elements_across_feature=4,
        growth_rate=0.7,
        verify_convergence=False,
    ),
    # High confidence: ~6 across, tighter grading.
    AccuracyProfile.HIGH: _ProfileShape(
        diagonal_divisions=140.0,
        elements_across_feature=6,
        growth_rate=0.5,
        verify_convergence=False,
    ),
    # Verification: ~8 across plus an automatic refined-mesh comparison whose
    # deltas are reported as discretisation sensitivity -- evidence, not just
    # smaller triangles.
    AccuracyProfile.VERIFICATION: _ProfileShape(
        diagonal_divisions=180.0,
        elements_across_feature=8,
        growth_rate=0.5,
        verify_convergence=True,
    ),
}

#: Minimum element size as a fraction of the maximum: two decades of grading
#: covers pad-scale refinement inside plane-scale copper.
MIN_SIZE_FRACTION: Final = 0.01

#: Refinement factor for the Verification comparison mesh. sqrt(2) doubles
#: the element count -- enough to expose discretisation sensitivity without
#: quadrupling the cost.
VERIFICATION_REFINEMENT_FACTOR: Final = 1.4142135623730951


def resolve_profile(
    profile: AccuracyProfile, board_diagonal_m: float
) -> tuple[ResolvedMeshSpec, bool]:
    """Resolve a named profile into concrete sizing for one board.

    Returns the mesh spec and whether a convergence-verification pass runs.
    """
    shape = _SHAPES[profile]
    max_element_m = board_diagonal_m / shape.diagonal_divisions
    return (
        ResolvedMeshSpec(
            max_element_m=max_element_m,
            min_element_m=max_element_m * MIN_SIZE_FRACTION,
            elements_across_feature=shape.elements_across_feature,
            growth_rate=shape.growth_rate,
        ),
        shape.verify_convergence,
    )
