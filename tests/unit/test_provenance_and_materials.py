"""Provenance, units and material behaviour -- the honesty machinery."""

from __future__ import annotations

import math

import pytest

from openpdn.domain.errors import InvalidQuantityError
from openpdn.domain.materials import (
    COPPER_ANNEALED,
    COPPER_CONDUCTIVITY_S_PER_M,
    Material,
)
from openpdn.domain.provenance import Provenance, Quantity, weakest_provenance
from openpdn.domain.units import METRE, mil_to_m, mm_to_m, oz_copper_to_m, um_to_m


class TestQuantity:
    def test_assumed_quantity_requires_a_note(self):
        # An assumption a user cannot see is indistinguishable from a lie.
        with pytest.raises(InvalidQuantityError, match="note"):
            Quantity(35e-6, METRE, Provenance.ASSUMED)

    def test_assumed_quantity_with_a_note_is_accepted(self):
        quantity = Quantity.assumed(35e-6, METRE, "1 oz nominal; absent from the source")
        assert quantity.is_assumed
        assert quantity.note

    def test_non_finite_values_are_rejected(self):
        with pytest.raises(InvalidQuantityError):
            Quantity.imported(math.inf, METRE)
        with pytest.raises(InvalidQuantityError):
            Quantity.imported(math.nan, METRE)

    def test_require_unit_catches_a_swapped_argument(self):
        thickness = Quantity.imported(35e-6, METRE)
        with pytest.raises(InvalidQuantityError, match="S/m"):
            thickness.require_unit("S/m")

    def test_with_value_keeps_unit_and_provenance(self):
        original = Quantity.configured(0.85, "V", "rail setpoint")
        updated = original.with_value(1.0)
        assert updated.unit == "V"
        assert updated.provenance is Provenance.CONFIGURED
        assert updated.note == "rail setpoint"


class TestWeakestProvenance:
    def test_an_assumption_anywhere_taints_the_result(self):
        assert (
            weakest_provenance(
                Quantity.imported(1.0, METRE),
                Quantity.assumed(2.0, METRE, "unknown"),
                Quantity.configured(3.0, METRE),
            )
            is Provenance.ASSUMED
        )

    def test_all_imported_inputs_stay_imported(self):
        assert (
            weakest_provenance(Quantity.imported(1.0, METRE), Quantity.imported(2.0, METRE))
            is Provenance.IMPORTED
        )

    def test_no_inputs_is_derived(self):
        assert weakest_provenance() is Provenance.DERIVED


class TestUnits:
    @pytest.mark.parametrize(
        ("converted", "expected_m"),
        [
            (mm_to_m(1.0), 1e-3),
            (um_to_m(35.0), 35e-6),
            (mil_to_m(1.0), 25.4e-6),
            (oz_copper_to_m(1.0), 34.8e-6),
            (oz_copper_to_m(2.0), 69.6e-6),
        ],
    )
    def test_conversions_land_on_si(self, converted: float, expected_m: float):
        assert converted == pytest.approx(expected_m, rel=1e-12)


class TestMaterial:
    def test_copper_matches_the_iacs_standard(self):
        # 100 % IACS at 20 degC. Drift here would silently rescale every result.
        assert COPPER_ANNEALED.conductivity_s_per_m == COPPER_CONDUCTIVITY_S_PER_M
        assert COPPER_ANNEALED.resistivity_ohm_m == pytest.approx(1.724e-8, rel=1e-3)

    def test_conductivity_falls_with_temperature(self):
        hot = COPPER_ANNEALED.conductivity_at_s_per_m(373.15)
        assert hot < COPPER_ANNEALED.conductivity_s_per_m

    def test_conductivity_is_unchanged_at_the_reference_temperature(self):
        at_reference = COPPER_ANNEALED.conductivity_at_s_per_m(
            COPPER_ANNEALED.reference_temperature_k
        )
        assert at_reference == pytest.approx(COPPER_ANNEALED.conductivity_s_per_m)

    def test_a_material_without_a_coefficient_does_not_guess_one(self):
        material = Material("Unknown alloy", 1.0e7, temperature_coefficient_per_k=None)
        assert material.conductivity_at_s_per_m(400.0) == material.conductivity_s_per_m

    def test_non_physical_conductivity_is_rejected(self):
        with pytest.raises(InvalidQuantityError):
            Material("Broken", 0.0)
