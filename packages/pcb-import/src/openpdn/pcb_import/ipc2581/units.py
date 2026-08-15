"""IPC-2581 unit normalisation.

An IPC-2581 document declares its units once and then gives every coordinate,
diameter and thickness as a bare number in them. openPDN is SI internally
(ADR-0004), so conversion happens here, at the importer boundary, exactly once
per value.

    IPC-2581 source          Canonical model
    0.035 (MILLIMETER)  ──►  3.5e-5 m

The source unit is worth keeping as diagnostic metadata -- "0.035 mm" is what
the engineer will look for in their CAD tool -- but it never travels with the
value into the domain. Nothing downstream converts units.
"""

from __future__ import annotations

from typing import Final

from openpdn.pcb_import.api import MalformedSourceError

#: IPC-2581 `unit` values, mapped to metres. The standard's unit vocabulary is
#: small and closed; an unlisted value is refused rather than assumed to be
#: millimetres, because a silent factor-of-25.4 error produces a board that
#: looks right and solves wrong.
IPC2581_UNIT_SCALE_TO_M: Final[dict[str, float]] = {
    "MILLIMETER": 1e-3,
    "MILLIMETRE": 1e-3,
    "MICRON": 1e-6,
    "MICROMETER": 1e-6,
    "CENTIMETER": 1e-2,
    "METER": 1.0,
    "INCH": 25.4e-3,
    "MIL": 25.4e-6,
    "MICROINCH": 25.4e-9,
}


def unit_scale_to_m(unit_name: str | None) -> float:
    """Return the factor converting `unit_name` values into metres.

    Raises:
        MalformedSourceError: If the document declares no units, or declares a
            unit this adapter does not know. Both are refusals, not defaults.
    """
    if unit_name is None or not unit_name.strip():
        raise MalformedSourceError(
            "IPC-2581 document declares no units; every dimension in it would be ambiguous"
        )
    key = unit_name.strip().upper()
    try:
        return IPC2581_UNIT_SCALE_TO_M[key]
    except KeyError as exc:
        known = ", ".join(sorted(IPC2581_UNIT_SCALE_TO_M))
        raise MalformedSourceError(
            f"Unknown IPC-2581 unit {unit_name!r}; known units: {known}"
        ) from exc


def to_metres(value: float, unit_name: str) -> float:
    """Convert a document value in `unit_name` to metres."""
    return value * unit_scale_to_m(unit_name)
