"""Conductor materials.

Physical constants are named and sourced here rather than appearing as literals
inside a resistance calculation. A bare `5.8e7` in solver code is a defect; see
`.agents/skills/development-conventions/SKILL.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from openpdn.domain.errors import InvalidQuantityError

#: Electrical conductivity of annealed copper at 20 degC, i.e. 100 % IACS.
#: Reference: IEC 60028 (International Annealed Copper Standard).
COPPER_CONDUCTIVITY_S_PER_M: Final = 5.8001e7

#: Temperature coefficient of resistance of annealed copper near 20 degC, 1/K.
COPPER_TEMPERATURE_COEFFICIENT_PER_K: Final = 3.93e-3

#: Reference temperature for the constants above, in kelvin (20 degC).
STANDARD_REFERENCE_TEMPERATURE_K: Final = 293.15


@dataclass(frozen=True, slots=True)
class Material:
    """An isotropic conductor characterised for DC conduction.

    Attributes:
        name: Human-readable identifier shown in the UI.
        conductivity_s_per_m: Conductivity at `reference_temperature_k`.
        temperature_coefficient_per_k: Linear TCR; `None` disables temperature
            correction rather than silently assuming zero drift.
        reference_temperature_k: Temperature at which the conductivity holds.
    """

    name: str
    conductivity_s_per_m: float
    temperature_coefficient_per_k: float | None = None
    reference_temperature_k: float = STANDARD_REFERENCE_TEMPERATURE_K

    def __post_init__(self) -> None:
        """Reject non-physical material parameters."""
        if self.conductivity_s_per_m <= 0.0:
            raise InvalidQuantityError(
                f"Conductivity must be positive, got {self.conductivity_s_per_m!r} S/m"
            )
        if self.reference_temperature_k <= 0.0:
            raise InvalidQuantityError("Reference temperature must be above absolute zero")

    @property
    def resistivity_ohm_m(self) -> float:
        """Resistivity at the reference temperature."""
        return 1.0 / self.conductivity_s_per_m

    def conductivity_at_s_per_m(self, temperature_k: float) -> float:
        """Return conductivity at `temperature_k` using the linear TCR model.

        Falls back to the reference conductivity when no temperature
        coefficient is known -- an explicit, documented no-op rather than a
        guessed coefficient.
        """
        if self.temperature_coefficient_per_k is None:
            return self.conductivity_s_per_m
        delta_k = temperature_k - self.reference_temperature_k
        scale = 1.0 + self.temperature_coefficient_per_k * delta_k
        if scale <= 0.0:
            raise InvalidQuantityError(
                f"Linear TCR model is invalid at {temperature_k} K for material {self.name!r}"
            )
        return self.conductivity_s_per_m / scale


#: Default PCB conductor. Real boards use plated/rolled copper whose
#: conductivity is typically 95-100 % IACS; treat deviations as a study input.
COPPER_ANNEALED: Final = Material(
    name="Copper (annealed, 100 % IACS)",
    conductivity_s_per_m=COPPER_CONDUCTIVITY_S_PER_M,
    temperature_coefficient_per_k=COPPER_TEMPERATURE_COEFFICIENT_PER_K,
    reference_temperature_k=STANDARD_REFERENCE_TEMPERATURE_K,
)
