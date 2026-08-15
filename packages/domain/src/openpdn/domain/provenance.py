"""Provenance-tagged physical quantities.

openPDN never silently invents a physical property. Copper thickness, plating
thickness, conductivity and finished hole diameter are frequently absent or
untrustworthy in fabrication data, and an IR-drop number computed from a
guessed thickness is an engineering hazard if it is presented like a measured
one.

Every physical quantity therefore records *where its value came from*, so the
API and the UI can distinguish:

    IMPORTED   read from the fabrication data
    CONFIGURED entered by the engineer for this study
    ASSUMED    a default filling an unknown -- results inherit its uncertainty
    DERIVED    computed from other quantities
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum

from openpdn.domain.errors import InvalidQuantityError


class Provenance(StrEnum):
    """Where the value of a physical quantity came from."""

    IMPORTED = "imported"
    CONFIGURED = "configured"
    ASSUMED = "assumed"
    DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class Quantity:
    """A scalar physical value in SI units, with its unit and provenance.

    Attributes:
        value: Magnitude, always in the SI unit named by `unit`.
        unit: SI unit symbol, e.g. `"m"`, `"A"`, `"S/m"` (see `units.py`).
        provenance: How the value was obtained.
        note: Short human-readable justification. Required for
            `Provenance.ASSUMED` so that a UI can explain the assumption.
    """

    value: float
    unit: str
    provenance: Provenance
    note: str | None = None

    def __post_init__(self) -> None:
        """Reject values that cannot represent a physical measurement."""
        if not math.isfinite(self.value):
            raise InvalidQuantityError(f"Quantity value must be finite, got {self.value!r}")
        if not self.unit:
            raise InvalidQuantityError("Quantity requires a unit symbol")
        if self.provenance is Provenance.ASSUMED and not self.note:
            raise InvalidQuantityError(
                "An assumed quantity must carry a note explaining the assumption"
            )

    @classmethod
    def imported(cls, value: float, unit: str, note: str | None = None) -> Quantity:
        """Build a quantity read directly from fabrication data."""
        return cls(value, unit, Provenance.IMPORTED, note)

    @classmethod
    def configured(cls, value: float, unit: str, note: str | None = None) -> Quantity:
        """Build a quantity supplied by the engineer."""
        return cls(value, unit, Provenance.CONFIGURED, note)

    @classmethod
    def assumed(cls, value: float, unit: str, note: str) -> Quantity:
        """Build a quantity standing in for an unknown; `note` is mandatory."""
        return cls(value, unit, Provenance.ASSUMED, note)

    @classmethod
    def derived(cls, value: float, unit: str, note: str | None = None) -> Quantity:
        """Build a quantity computed from other quantities."""
        return cls(value, unit, Provenance.DERIVED, note)

    @property
    def is_assumed(self) -> bool:
        """True when the value is a stand-in for missing data."""
        return self.provenance is Provenance.ASSUMED

    def with_value(self, value: float) -> Quantity:
        """Return a copy holding `value`, keeping unit and provenance."""
        return replace(self, value=value)

    def require_unit(self, unit: str) -> float:
        """Return the magnitude, asserting the expected unit.

        Guards against a caller passing, say, a thickness in the slot meant for
        a conductivity -- the single most likely source of silent engineering
        error in a numeric pipeline.
        """
        if self.unit != unit:
            raise InvalidQuantityError(f"Expected a quantity in {unit!r}, got {self.unit!r}")
        return self.value


def weakest_provenance(*quantities: Quantity | None) -> Provenance:
    """Return the least trustworthy provenance among `quantities`.

    A derived result is only as trustworthy as its weakest input: if any input
    was assumed, the result is assumed too.
    """
    order = (Provenance.ASSUMED, Provenance.DERIVED, Provenance.CONFIGURED, Provenance.IMPORTED)
    present = [q.provenance for q in quantities if q is not None]
    if not present:
        return Provenance.DERIVED
    for provenance in order:
        if provenance in present:
            return provenance
    return Provenance.DERIVED
