"""Unit handling.

openPDN is SI internally: metres, seconds, amperes, volts, ohms, kelvin.
Millimetres, mils, ounces of copper, milliohms and A/mm^2 exist only at
boundaries -- importers, the HTTP API, the CLI and the UI. Conversion happens
there, never in the middle of a computation.

Naming rule: any variable holding a bare float carries its unit in its name
(`thickness_m`, `current_a`, `resistance_ohm`). See
`.agents/skills/development-conventions/SKILL.md`.
"""

from typing import Final

# --- Unit symbols used by `Quantity.unit` -----------------------------------
METRE: Final = "m"
SQUARE_METRE: Final = "m^2"
VOLT: Final = "V"
AMPERE: Final = "A"
OHM: Final = "ohm"
SIEMENS_PER_METRE: Final = "S/m"
AMPERE_PER_SQUARE_METRE: Final = "A/m^2"
WATT_PER_CUBIC_METRE: Final = "W/m^3"
KELVIN: Final = "K"
SECOND: Final = "s"

# --- Scale factors ----------------------------------------------------------
MILLIMETRE_IN_METRES: Final = 1e-3
MICROMETRE_IN_METRES: Final = 1e-6
MIL_IN_METRES: Final = 25.4e-6
INCH_IN_METRES: Final = 25.4e-3

# Nominal finished copper thickness per ounce of copper foil weight.
# 1 oz/ft^2 of copper is conventionally 1.37 mil = 34.8 um of plated thickness.
# This is a *nominal manufacturing convention*, not a measured value: quantities
# derived from it must be marked `Provenance.ASSUMED`.
OUNCE_COPPER_IN_METRES: Final = 34.8e-6

ABSOLUTE_ZERO_IN_CELSIUS: Final = -273.15


def mm_to_m(value_mm: float) -> float:
    """Convert millimetres to metres."""
    return value_mm * MILLIMETRE_IN_METRES


def m_to_mm(value_m: float) -> float:
    """Convert metres to millimetres."""
    return value_m / MILLIMETRE_IN_METRES


def um_to_m(value_um: float) -> float:
    """Convert micrometres to metres."""
    return value_um * MICROMETRE_IN_METRES


def m_to_um(value_m: float) -> float:
    """Convert metres to micrometres."""
    return value_m / MICROMETRE_IN_METRES


def mil_to_m(value_mil: float) -> float:
    """Convert mils (thousandths of an inch) to metres."""
    return value_mil * MIL_IN_METRES


def m_to_mil(value_m: float) -> float:
    """Convert metres to mils."""
    return value_m / MIL_IN_METRES


def oz_copper_to_m(weight_oz: float) -> float:
    """Convert a copper foil weight in oz/ft^2 to a nominal thickness in metres.

    The result is a manufacturing convention, not a measurement. Wrap it in a
    `Quantity` with `Provenance.ASSUMED` when it stands in for an unknown.
    """
    return weight_oz * OUNCE_COPPER_IN_METRES


def celsius_to_kelvin(value_c: float) -> float:
    """Convert degrees Celsius to kelvin."""
    return value_c - ABSOLUTE_ZERO_IN_CELSIUS


def kelvin_to_celsius(value_k: float) -> float:
    """Convert kelvin to degrees Celsius."""
    return value_k + ABSOLUTE_ZERO_IN_CELSIUS


def a_per_m2_to_a_per_mm2(value_a_per_m2: float) -> float:
    """Convert current density from A/m^2 to the A/mm^2 used in UI displays."""
    return value_a_per_m2 * 1e-6


def a_per_mm2_to_a_per_m2(value_a_per_mm2: float) -> float:
    """Convert current density from A/mm^2 to SI A/m^2."""
    return value_a_per_mm2 * 1e6
