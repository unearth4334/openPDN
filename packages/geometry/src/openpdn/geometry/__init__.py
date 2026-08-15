"""Geometry normalisation -- the boundary between imported and solver-ready copper.

The contract lives in `openpdn.geometry.api`; the Shapely engine beside it is
named only by the composition root (`infrastructure/container.py`). Application
code depends on the contract alone, exactly as it does for importers and
solvers (ADR-0007).
"""

from openpdn.geometry.api import (
    GeometryNormalizationError,
    GeometryNormalizer,
    NormalizationStats,
    NormalizedGeometry,
    NormalizedRegion,
)

__all__ = [
    "GeometryNormalizationError",
    "GeometryNormalizer",
    "NormalizationStats",
    "NormalizedGeometry",
    "NormalizedRegion",
]
