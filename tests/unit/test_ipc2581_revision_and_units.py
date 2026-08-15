"""Revision handling and unit normalisation at the IPC-2581 boundary.

These are the two ways an importer silently produces a wrong board: reading a
document with another revision's semantics, and getting the unit scale wrong by
a factor of 25.4. Both fail loudly here.
"""

from __future__ import annotations

import pytest

from openpdn.pcb_import.api import MalformedSourceError, UnsupportedRevisionError
from openpdn.pcb_import.ipc2581.revision import (
    SUPPORTED_REVISIONS,
    IPC2581Revision,
    detect_revision,
    local_name,
)
from openpdn.pcb_import.ipc2581.units import IPC2581_UNIT_SCALE_TO_M, to_metres, unit_scale_to_m


class TestRevisionDetection:
    @pytest.mark.parametrize(
        "written",
        ["B", "b", " B ", "IPC-2581B", "ipc-2581b", "2581-B", "Rev B", "REV.B"],
    )
    def test_generator_spellings_all_resolve(self, written: str):
        # Generators write the revision several ways; none of them is a reason
        # to reject a document openPDN can read.
        assert detect_revision(written) is IPC2581Revision.B

    def test_revision_b_is_the_only_supported_target(self):
        assert IPC2581Revision.B in SUPPORTED_REVISIONS
        assert len(SUPPORTED_REVISIONS) == 1

    @pytest.mark.parametrize("written", ["A", "C", "IPC-2581C"])
    def test_known_but_unimplemented_revisions_are_refused(self, written: str):
        # Reading revision C with revision B semantics would produce a
        # plausible, wrong board. Refusing is the safe failure.
        with pytest.raises(UnsupportedRevisionError, match="not supported"):
            detect_revision(written)

    @pytest.mark.parametrize("written", [None, "", "   "])
    def test_a_missing_revision_is_never_assumed(self, written: str | None):
        with pytest.raises(MalformedSourceError, match="does not declare a revision"):
            detect_revision(written)

    def test_an_unrecognisable_revision_is_rejected(self):
        with pytest.raises(MalformedSourceError, match="Unrecognised"):
            detect_revision("Z9")


class TestLocalName:
    def test_strips_a_namespace(self):
        assert local_name("{http://webstds.ipc.org/2581}IPC-2581") == "IPC-2581"

    def test_leaves_a_bare_tag_alone(self):
        assert local_name("Layer") == "Layer"


class TestUnits:
    @pytest.mark.parametrize(
        ("unit", "expected_m"),
        [
            ("MILLIMETER", 1e-3),
            ("MICRON", 1e-6),
            ("INCH", 25.4e-3),
            ("MIL", 25.4e-6),
            ("METER", 1.0),
        ],
    )
    def test_scales_land_on_si(self, unit: str, expected_m: float):
        assert unit_scale_to_m(unit) == pytest.approx(expected_m, rel=1e-12)

    def test_lookup_is_case_and_space_insensitive(self):
        assert unit_scale_to_m(" millimeter ") == pytest.approx(1e-3)

    def test_a_typical_copper_thickness_converts_correctly(self):
        # 0.0348 mm in the document is 34.8 um in the canonical model.
        assert to_metres(0.0348, "MILLIMETER") == pytest.approx(34.8e-6, rel=1e-12)

    def test_an_unknown_unit_is_refused_rather_than_assumed(self):
        # Assuming millimetres here is the classic factor-of-25.4 error.
        with pytest.raises(MalformedSourceError, match="Unknown IPC-2581 unit"):
            unit_scale_to_m("FURLONG")

    @pytest.mark.parametrize("unit", [None, "", "  "])
    def test_missing_units_are_refused(self, unit: str | None):
        with pytest.raises(MalformedSourceError, match="declares no units"):
            unit_scale_to_m(unit)

    def test_every_declared_scale_is_positive(self):
        assert all(scale > 0 for scale in IPC2581_UNIT_SCALE_TO_M.values())
