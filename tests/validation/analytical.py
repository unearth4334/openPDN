"""Closed-form references for numerical validation.

These functions are the *ground truth* a solver is measured against. They are
deliberately trivial and independently checked in
`test_analytical_references.py`, because a validation suite whose reference is
wrong is worse than no validation suite.

Every geometry here is chosen so an analytical answer exists exactly:

* a straight uniform trace -- R = rho L / A;
* conductors in parallel and in series;
* a plated via barrel treated as a uniform annular conductor.

Nothing in this module knows about meshing, and nothing here may import a
solver: the reference must stay independent of the thing it judges.
"""

from __future__ import annotations

import math

from openpdn.domain.materials import COPPER_CONDUCTIVITY_S_PER_M


def trace_resistance_ohm(
    length_m: float,
    width_m: float,
    thickness_m: float,
    conductivity_s_per_m: float = COPPER_CONDUCTIVITY_S_PER_M,
) -> float:
    """DC resistance of a straight rectangular conductor.

    R = L / (sigma * w * t). Valid where current flow is uniform and along the
    length -- i.e. away from terminals, bends and constrictions.
    """
    if min(length_m, width_m, thickness_m, conductivity_s_per_m) <= 0.0:
        raise ValueError("Trace dimensions and conductivity must be positive")
    return length_m / (conductivity_s_per_m * width_m * thickness_m)


def sheet_resistance_ohm_per_square(
    thickness_m: float,
    conductivity_s_per_m: float = COPPER_CONDUCTIVITY_S_PER_M,
) -> float:
    """Sheet resistance of a copper layer, in ohms per square.

    The 2.5-D formulation reduces each layer to this quantity, so it is the
    natural unit in which to check a sheet solver's material handling.
    """
    if min(thickness_m, conductivity_s_per_m) <= 0.0:
        raise ValueError("Thickness and conductivity must be positive")
    return 1.0 / (conductivity_s_per_m * thickness_m)


def parallel_resistance_ohm(*resistances_ohm: float) -> float:
    """Equivalent resistance of conductors in parallel."""
    if not resistances_ohm:
        raise ValueError("Need at least one resistance")
    if any(value <= 0.0 for value in resistances_ohm):
        raise ValueError("Resistances must be positive")
    return 1.0 / sum(1.0 / value for value in resistances_ohm)


def series_resistance_ohm(*resistances_ohm: float) -> float:
    """Equivalent resistance of conductors in series."""
    if any(value < 0.0 for value in resistances_ohm):
        raise ValueError("Resistances must not be negative")
    return sum(resistances_ohm)


def via_barrel_resistance_ohm(
    length_m: float,
    finished_hole_diameter_m: float,
    plating_thickness_m: float,
    conductivity_s_per_m: float = COPPER_CONDUCTIVITY_S_PER_M,
) -> float:
    """Resistance of a plated via barrel, treated as a uniform annulus.

    Only the plating conducts: the hole itself is empty (or filled with a
    non-conductor). This ignores the pad, the fillet and any spreading
    resistance where the barrel meets the sheet -- which is exactly why a
    2.5-D solver's via model is compared against it with a stated tolerance
    rather than expected to match to machine precision.
    """
    if min(length_m, finished_hole_diameter_m, plating_thickness_m) <= 0.0:
        raise ValueError("Via dimensions must be positive")
    inner_radius_m = 0.5 * finished_hole_diameter_m
    outer_radius_m = inner_radius_m + plating_thickness_m
    area_m2 = math.pi * (outer_radius_m**2 - inner_radius_m**2)
    return length_m / (conductivity_s_per_m * area_m2)


def voltage_divider_v(
    source_voltage_v: float, upper_resistance_ohm: float, lower_resistance_ohm: float
) -> float:
    """Potential at the midpoint of two series resistances.

    Used to check that a solver reproduces voltage division along a trace, the
    simplest non-trivial statement about an IR-drop result.
    """
    total = upper_resistance_ohm + lower_resistance_ohm
    if total <= 0.0:
        raise ValueError("Total resistance must be positive")
    return source_voltage_v * lower_resistance_ohm / total


def ir_drop_v(current_a: float, resistance_ohm: float) -> float:
    """Potential drop across a resistance carrying `current_a`."""
    return current_a * resistance_ohm


def resistive_power_w(current_a: float, resistance_ohm: float) -> float:
    """Ohmic loss in a conductor carrying `current_a`."""
    return current_a * current_a * resistance_ohm


def current_density_a_per_m2(current_a: float, width_m: float, thickness_m: float) -> float:
    """Uniform current density in a rectangular cross-section."""
    if min(width_m, thickness_m) <= 0.0:
        raise ValueError("Cross-section dimensions must be positive")
    return current_a / (width_m * thickness_m)
