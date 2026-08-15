"""Resolved meshing controls for the 2.5-D sheet FEM.

`MeshSettings` on a study is the user-facing, solver-independent contract.
This module resolves it into the concrete numbers the mesher consumes, with
every default named and justified rather than buried in mesher code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from openpdn.domain.units import METRE

if TYPE_CHECKING:
    from openpdn.domain.study import MeshSettings

#: Fraction of the target element size used when a study supplies no explicit
#: minimum. Two orders of magnitude of grading is enough for pad-scale
#: refinement inside plane-scale copper without unbounded point counts.
DEFAULT_MINIMUM_FRACTION: Final = 0.01

#: Boundary pilot samples are spaced at this fraction of the maximum element
#: size. Pilots only *measure* local width/clearance; the real boundary points
#: are placed afterwards at the graded spacing.
PILOT_SPACING_FRACTION: Final = 0.5

#: A ray cast to measure local width or clearance stops after this many target
#: element sizes: beyond that, width no longer influences sizing.
RAY_REACH_IN_TARGET_SIZES: Final = 40.0

#: Interior lattice points keep at least this much clearance (in units of
#: their own lattice spacing) from the boundary, so boundary points stay the
#: nearest neighbours of boundary triangles.
INTERIOR_BOUNDARY_CLEARANCE: Final = 0.55

#: Mandatory points (via centres, pad vertices) suppress generated points
#: within this fraction of the local element size, so the triangulation keeps
#: them as clean vertices instead of creating slivers beside them.
MANDATORY_SUPPRESSION_FRACTION: Final = 0.35

#: Triangles with an area below this fraction of the square of the local
#: element size are numerical slivers and are discarded before assembly.
SLIVER_AREA_FRACTION: Final = 1e-6

#: Per-region cap on generated mesh points. A region exceeding it fails with a
#: diagnostic naming the region rather than exhausting memory silently; the
#: server-side resource budget is enforced above the solver as well.
MAX_POINTS_PER_REGION: Final = 2_000_000


@dataclass(frozen=True, slots=True)
class MeshControls:
    """Concrete sizing numbers derived from a study's `MeshSettings`.

    Attributes:
        max_size_m: Upper bound on element edge length (wide copper).
        min_size_m: Lower bound refinement may not go below.
        elements_across_feature: Elements across a narrow conductor's width.
        growth_rate: Element growth per unit distance from refined boundary.
        refine_terminals: Whether terminal pads force local refinement.
    """

    max_size_m: float
    min_size_m: float
    elements_across_feature: int
    growth_rate: float
    refine_terminals: bool

    @classmethod
    def from_settings(cls, settings: MeshSettings) -> MeshControls:
        """Resolve user-facing settings into mesher numbers."""
        max_size_m = settings.target_element_size.require_unit(METRE)
        if settings.minimum_element_size is not None:
            min_size_m = settings.minimum_element_size.require_unit(METRE)
        else:
            min_size_m = max_size_m * DEFAULT_MINIMUM_FRACTION
        return cls(
            max_size_m=max_size_m,
            min_size_m=min_size_m,
            elements_across_feature=settings.elements_across_feature,
            growth_rate=settings.growth_rate,
            refine_terminals=settings.refine_around_terminals,
        )
