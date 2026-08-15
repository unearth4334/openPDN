"""Minimal planar geometry value objects.

Deliberately small. The domain needs enough geometry to *describe* copper, not
to perform boolean operations or meshing -- those live behind adapter
boundaries where Shapely (or another library) may be used. Keeping the domain
library-free is what lets it be unit-tested with nothing installed (ADR-0001).

All coordinates are in metres in the board coordinate system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openpdn.domain.errors import InvalidGeometryError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class Point2D:
    """A point in the board plane, in metres."""

    x_m: float
    y_m: float

    def __post_init__(self) -> None:
        """Reject non-finite coordinates."""
        if not (math.isfinite(self.x_m) and math.isfinite(self.y_m)):
            raise InvalidGeometryError(f"Non-finite point ({self.x_m!r}, {self.y_m!r})")

    def distance_to_m(self, other: Point2D) -> float:
        """Euclidean distance to `other`, in metres."""
        return math.hypot(self.x_m - other.x_m, self.y_m - other.y_m)


@dataclass(frozen=True, slots=True)
class BoundingBox2D:
    """An axis-aligned bounding box in metres."""

    min_x_m: float
    min_y_m: float
    max_x_m: float
    max_y_m: float

    def __post_init__(self) -> None:
        """Reject inverted boxes."""
        if self.max_x_m < self.min_x_m or self.max_y_m < self.min_y_m:
            raise InvalidGeometryError("Bounding box maxima must not be below its minima")

    @classmethod
    def enclosing(cls, points: Iterable[Point2D]) -> BoundingBox2D:
        """Return the smallest box containing every point in `points`."""
        xs: list[float] = []
        ys: list[float] = []
        for point in points:
            xs.append(point.x_m)
            ys.append(point.y_m)
        if not xs:
            raise InvalidGeometryError("Cannot build a bounding box from no points")
        return cls(min(xs), min(ys), max(xs), max(ys))

    @property
    def width_m(self) -> float:
        """Extent along x."""
        return self.max_x_m - self.min_x_m

    @property
    def height_m(self) -> float:
        """Extent along y."""
        return self.max_y_m - self.min_y_m

    def merged_with(self, other: BoundingBox2D) -> BoundingBox2D:
        """Return the smallest box containing both boxes."""
        return BoundingBox2D(
            min(self.min_x_m, other.min_x_m),
            min(self.min_y_m, other.min_y_m),
            max(self.max_x_m, other.max_x_m),
            max(self.max_y_m, other.max_y_m),
        )


@dataclass(frozen=True, slots=True)
class Polygon2D:
    """A simple polygon with optional holes, in metres.

    Rings are stored without a repeated closing vertex. No self-intersection
    check is performed here: validating and repairing imported outlines is the
    importer's job, and it reports what it repaired as a diagnostic.
    """

    exterior: tuple[Point2D, ...]
    holes: tuple[tuple[Point2D, ...], ...] = ()

    def __post_init__(self) -> None:
        """Reject rings that cannot bound an area."""
        if len(self.exterior) < 3:
            raise InvalidGeometryError("A polygon exterior needs at least three vertices")
        for hole in self.holes:
            if len(hole) < 3:
                raise InvalidGeometryError("A polygon hole needs at least three vertices")

    @classmethod
    def from_coordinates(
        cls,
        exterior: Sequence[tuple[float, float]],
        holes: Sequence[Sequence[tuple[float, float]]] = (),
    ) -> Polygon2D:
        """Build a polygon from raw (x_m, y_m) coordinate pairs."""
        return cls(
            tuple(Point2D(x, y) for x, y in exterior),
            tuple(tuple(Point2D(x, y) for x, y in hole) for hole in holes),
        )

    @classmethod
    def rectangle(cls, origin: Point2D, width_m: float, height_m: float) -> Polygon2D:
        """Build an axis-aligned rectangle -- the common case for test fixtures."""
        if width_m <= 0.0 or height_m <= 0.0:
            raise InvalidGeometryError("Rectangle dimensions must be positive")
        x0, y0 = origin.x_m, origin.y_m
        return cls.from_coordinates(
            [(x0, y0), (x0 + width_m, y0), (x0 + width_m, y0 + height_m), (x0, y0 + height_m)]
        )

    @property
    def area_m2(self) -> float:
        """Signed-area magnitude of the exterior minus the holes, in m^2."""
        return abs(_ring_signed_area_m2(self.exterior)) - sum(
            abs(_ring_signed_area_m2(hole)) for hole in self.holes
        )

    @property
    def bounding_box(self) -> BoundingBox2D:
        """Axis-aligned bounds of the exterior ring."""
        return BoundingBox2D.enclosing(self.exterior)


def _ring_signed_area_m2(ring: tuple[Point2D, ...]) -> float:
    """Shoelace signed area of a closed ring given without its repeated vertex."""
    total = 0.0
    for index, current in enumerate(ring):
        following = ring[(index + 1) % len(ring)]
        total += current.x_m * following.y_m - following.x_m * current.y_m
    return 0.5 * total
