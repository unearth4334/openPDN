"""Accuracy profiles: named confidence levels resolved to mesh numbers.

A profile is a bundle of numerical controls, not a single element-size
slider. Each resolves against the board's diagonal so "Standard" means the
same relative resolution on a 30 mm module and a 300 mm backplane; the
resolved absolute numbers -- not the profile name -- are frozen into the job
spec (ADR-0011).

The ladder was re-measured (2026-08-16) end-to-end -- real mesh, real
assembly, real direct solve -- on a 150x100 mm single-layer plane board (two
corner terminals, no narrow features) because the previous numbers made even
Verification solve in well under a second, which is not a meaningful
confidence check. Measured wall-clock, one net:

    profile        divisions  across  growth   DOFs               time
    preview        180        8       0.50     17,462             0.23 s
    standard       300        10      0.50     48,348             0.60 s
    high           500        14      0.45     133,809             2.05 s
    verification   800        18      0.40     341,917 base /      24.2 s
                                                683,956 refined     (~104x preview)

Preview now sits where Verification used to (a deliberate shift -- the old
Preview/Standard/High were all "instant" on realistic boards); Verification's
total wall-clock (base solve plus its automatic refined comparison, see
`VERIFICATION_REFINEMENT_FACTOR`) lands close to 100x Preview's. Both are
measured, not derived from a formula -- re-measure with a real solve before
changing these numbers, the same way, rather than reasoning about divisions
in the abstract; treat changes as solver-behaviour changes, not cosmetics.
"""

from __future__ import annotations

import math
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
    # Fast sanity check: sits where the old Verification tier did. Results
    # are labelled preview / not verified.
    AccuracyProfile.PREVIEW: _ProfileShape(
        diagonal_divisions=180.0,
        elements_across_feature=8,
        growth_rate=0.5,
        verify_convergence=False,
    ),
    # Normal engineering work: ~0.6 s on the reference board.
    AccuracyProfile.STANDARD: _ProfileShape(
        diagonal_divisions=300.0,
        elements_across_feature=10,
        growth_rate=0.5,
        verify_convergence=False,
    ),
    # High confidence: ~2 s on the reference board.
    AccuracyProfile.HIGH: _ProfileShape(
        diagonal_divisions=500.0,
        elements_across_feature=14,
        growth_rate=0.45,
        verify_convergence=False,
    ),
    # Verification: an automatic refined-mesh comparison whose deltas are
    # reported as discretisation sensitivity -- evidence, not just smaller
    # triangles. ~100x Preview's wall-clock across the base plus refined
    # solve.
    AccuracyProfile.VERIFICATION: _ProfileShape(
        diagonal_divisions=800.0,
        elements_across_feature=18,
        growth_rate=0.4,
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


def refine_mesh_spec(mesh: ResolvedMeshSpec, factor: float) -> ResolvedMeshSpec:
    """Scale a resolved mesh spec for the Verification comparison solve.

    A genuine refinement must scale the *feature-width* sizing too: most of a
    routed net's mesh is width-graded, so shrinking only the max/min bounds
    would barely change it and the comparison would prove nothing. Shared by
    the queue-time budget check and the worker's actual refined solve so the
    two can never drift apart.
    """
    return ResolvedMeshSpec(
        max_element_m=mesh.max_element_m / factor,
        min_element_m=mesh.min_element_m / factor,
        elements_across_feature=math.ceil(mesh.elements_across_feature * factor),
        growth_rate=mesh.growth_rate,
    )
