"""Application-layer simulation spec validation and JSON schema migration.

`SimulationJobSpec.from_json` must keep loading schema-1 rows already
persisted in production (simulation-jobs skill) -- the multi-attachment
change must never brick `/api/jobs` for a store that predates it.
"""

from __future__ import annotations

import json

import pytest

from openpdn.application.simulation_models import (
    AccuracyProfile,
    LayerThicknessOverrideSpec,
    LoadSpec,
    ResolvedMeshSpec,
    SimulationDraft,
    SimulationJobSpec,
    SimulationKind,
    SimulationRequestError,
    analysis_signature,
)


def _mesh() -> ResolvedMeshSpec:
    return ResolvedMeshSpec(
        max_element_m=1e-3, min_element_m=1e-5, elements_across_feature=4, growth_rate=0.7
    )


def _schema1_json() -> str:
    """A literal pre-multi-attachment job row, as persisted in production."""
    return json.dumps(
        {
            "schema": 1,
            "job_id": "job-old",
            "name": "legacy job",
            "kind": "ir_drop",
            "board_id": "board-1",
            "board_digest": "digest-1",
            "board_name": "board",
            "net_id": "net-1",
            "net_name": "NET1",
            "source_terminal_id": "t-a",
            "source_voltage_v": 0.85,
            "loads": [{"terminal_id": "t-b", "current_a": 2.0}],
            "to_terminal_id": None,
            "accuracy": "standard",
            "mesh": {
                "max_element_m": 1e-3,
                "min_element_m": 1e-5,
                "elements_across_feature": 4,
                "growth_rate": 0.7,
            },
            "verify_convergence": False,
            "via_plating_m": None,
            "solver_name": "fem-2p5d",
            "created_at_epoch_s": 1000.0,
            "signature": "sig-old",
        }
    )


def _schema3_json() -> str:
    """A literal pre-conductor-override job row, as persisted in production."""
    return json.dumps(
        {
            "schema": 3,
            "job_id": "job-schema3",
            "name": "schema 3 job",
            "kind": "ir_drop",
            "board_id": "board-1",
            "board_digest": "digest-1",
            "board_name": "board",
            "net_id": "net-1",
            "net_name": "NET1",
            "source_terminal_ids": ["t-a"],
            "source_via_ids": [],
            "source_voltage_v": 0.85,
            "loads": [{"terminal_ids": ["t-b"], "via_ids": [], "current_a": 2.0}],
            "to_terminal_ids": [],
            "to_via_ids": [],
            "accuracy": "standard",
            "estimated_dofs": 0,
            "reference_policy": None,
            "mesh": {
                "max_element_m": 1e-3,
                "min_element_m": 1e-5,
                "elements_across_feature": 4,
                "growth_rate": 0.7,
            },
            "verify_convergence": False,
            "via_plating_m": None,
            "solver_name": "fem-2p5d",
            "created_at_epoch_s": 1000.0,
            "signature": "sig-schema3",
        }
    )


class TestSchemaMigration:
    """A schema-1 row must upgrade to one-member plural groups, not crash."""

    def test_schema_1_source_terminal_upgrades_to_a_singleton_group(self):
        spec = SimulationJobSpec.from_json(_schema1_json())
        assert spec.source_terminal_ids == ("t-a",)
        assert spec.source_via_ids == ()

    def test_schema_1_load_terminal_upgrades_to_a_singleton_group(self):
        spec = SimulationJobSpec.from_json(_schema1_json())
        assert len(spec.loads) == 1
        assert spec.loads[0].terminal_ids == ("t-b",)
        assert spec.loads[0].via_ids == ()
        assert spec.loads[0].current_a == 2.0

    def test_schema_1_absent_to_terminal_upgrades_to_an_empty_group(self):
        spec = SimulationJobSpec.from_json(_schema1_json())
        assert spec.to_terminal_ids == ()
        assert spec.to_via_ids == ()

    def test_schema_1_empty_string_to_terminal_upgrades_to_an_empty_group(self):
        # A schema-1 IR-drop row wrote `to_terminal_id` as `str | None`; an
        # empty string was reachable if a draft ever carried one. It must
        # upgrade the same way `None` does, not become a one-member group
        # containing "".
        data = json.loads(_schema1_json())
        data["to_terminal_id"] = ""
        spec = SimulationJobSpec.from_json(json.dumps(data))
        assert spec.to_terminal_ids == ()

    def test_round_trip_through_to_json_preserves_the_upgraded_shape(self):
        spec = SimulationJobSpec.from_json(_schema1_json())
        reloaded = SimulationJobSpec.from_json(spec.to_json())
        assert reloaded.source_terminal_ids == spec.source_terminal_ids
        assert reloaded.loads == spec.loads

    def test_current_schema_round_trips_a_multi_member_group(self):
        spec = SimulationJobSpec(
            job_id="job-new",
            name="multi",
            kind=SimulationKind.IR_DROP,
            board_id="board-1",
            board_digest="digest-1",
            board_name="board",
            net_id="net-1",
            net_name="NET1",
            source_terminal_ids=("t-a", "t-c"),
            source_via_ids=("v-1",),
            source_voltage_v=1.0,
            loads=(LoadSpec(current_a=2.0, terminal_ids=("t-b",), via_ids=("v-2",)),),
            to_terminal_ids=(),
            to_via_ids=(),
            accuracy=AccuracyProfile.STANDARD,
            mesh=_mesh(),
            verify_convergence=False,
            via_plating_m=None,
            solver_name="fem-2p5d",
            created_at_epoch_s=1000.0,
            signature="sig-new",
        )
        reloaded = SimulationJobSpec.from_json(spec.to_json())
        assert reloaded.source_terminal_ids == ("t-a", "t-c")
        assert reloaded.source_via_ids == ("v-1",)
        assert reloaded.loads[0].terminal_ids == ("t-b",)
        assert reloaded.loads[0].via_ids == ("v-2",)

    def test_schema_3_row_loads_with_no_conductor_overrides(self):
        # Rows queued before conductor overrides existed were queued against
        # each layer's imported material and thickness and must re-run that
        # way, not silently acquire an override.
        spec = SimulationJobSpec.from_json(_schema3_json())
        assert spec.conductor_conductivity_s_per_m is None
        assert spec.conductor_material_name is None
        assert spec.thickness_overrides == ()

    def test_conductor_overrides_round_trip(self):
        spec = SimulationJobSpec(
            job_id="job-conductor",
            name="conductor",
            kind=SimulationKind.IR_DROP,
            board_id="board-1",
            board_digest="digest-1",
            board_name="board",
            net_id="net-1",
            net_name="NET1",
            source_terminal_ids=("t-a",),
            source_via_ids=(),
            source_voltage_v=1.0,
            loads=(LoadSpec(current_a=2.0, terminal_ids=("t-b",)),),
            to_terminal_ids=(),
            to_via_ids=(),
            accuracy=AccuracyProfile.STANDARD,
            mesh=_mesh(),
            verify_convergence=False,
            via_plating_m=None,
            solver_name="fem-2p5d",
            created_at_epoch_s=1000.0,
            signature="sig-conductor",
            conductor_conductivity_s_per_m=4.5e7,
            conductor_material_name="Custom",
            thickness_overrides=(LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=3.5e-5),),
        )
        reloaded = SimulationJobSpec.from_json(spec.to_json())
        assert reloaded.conductor_conductivity_s_per_m == 4.5e7
        assert reloaded.conductor_material_name == "Custom"
        assert reloaded.thickness_overrides == (
            LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=3.5e-5),
        )


class TestLayerThicknessOverrideSpecValidation:
    def test_a_zero_thickness_is_rejected(self):
        with pytest.raises(SimulationRequestError, match="positive thickness"):
            LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=0.0)

    def test_a_thickness_at_or_above_one_millimetre_is_rejected(self):
        with pytest.raises(SimulationRequestError, match="positive thickness"):
            LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=1e-3)


class TestLoadSpecValidation:
    def test_a_load_with_neither_terminal_nor_via_is_rejected(self):
        with pytest.raises(SimulationRequestError, match="at least one terminal or via"):
            LoadSpec(current_a=1.0)

    def test_a_via_only_load_is_accepted(self):
        load = LoadSpec(current_a=1.0, via_ids=("v-1",))
        assert load.terminal_ids == ()
        assert load.via_ids == ("v-1",)


class TestSimulationDraftValidation:
    def test_ir_drop_source_may_be_via_only(self):
        draft = SimulationDraft(
            kind=SimulationKind.IR_DROP,
            board_id="board-1",
            net_id="net-1",
            accuracy=AccuracyProfile.STANDARD,
            source_via_ids=("v-1",),
            source_voltage_v=1.0,
            loads=(LoadSpec(current_a=1.0, terminal_ids=("t-b",)),),
        )
        assert draft.source_terminal_ids == ()
        assert draft.source_via_ids == ("v-1",)

    def test_a_load_sharing_a_source_terminal_is_rejected(self):
        with pytest.raises(SimulationRequestError, match="both attach"):
            SimulationDraft(
                kind=SimulationKind.IR_DROP,
                board_id="board-1",
                net_id="net-1",
                accuracy=AccuracyProfile.STANDARD,
                source_terminal_ids=("t-a", "t-c"),
                source_voltage_v=1.0,
                loads=(LoadSpec(current_a=1.0, terminal_ids=("t-c",)),),
            )

    def test_a_load_sharing_a_source_via_is_rejected(self):
        with pytest.raises(SimulationRequestError, match="both attach"):
            SimulationDraft(
                kind=SimulationKind.IR_DROP,
                board_id="board-1",
                net_id="net-1",
                accuracy=AccuracyProfile.STANDARD,
                source_via_ids=("v-1",),
                source_voltage_v=1.0,
                loads=(LoadSpec(current_a=1.0, via_ids=("v-1",)),),
            )

    def test_resistance_source_needs_a_real_terminal_not_just_a_via(self):
        with pytest.raises(SimulationRequestError, match="exactly one terminal"):
            SimulationDraft(
                kind=SimulationKind.RESISTANCE,
                board_id="board-1",
                net_id="net-1",
                accuracy=AccuracyProfile.STANDARD,
                source_via_ids=("v-1",),
                to_terminal_ids=("t-b",),
            )

    def test_resistance_endpoints_must_not_share_a_member(self):
        with pytest.raises(SimulationRequestError, match="must not share"):
            SimulationDraft(
                kind=SimulationKind.RESISTANCE,
                board_id="board-1",
                net_id="net-1",
                accuracy=AccuracyProfile.STANDARD,
                source_terminal_ids=("t-a",),
                to_terminal_ids=("t-a",),
            )

    def test_a_layer_overridden_twice_is_rejected(self):
        with pytest.raises(SimulationRequestError, match="given twice"):
            SimulationDraft(
                kind=SimulationKind.RESISTANCE,
                board_id="board-1",
                net_id="net-1",
                accuracy=AccuracyProfile.STANDARD,
                source_terminal_ids=("t-a",),
                to_terminal_ids=("t-b",),
                thickness_overrides=(
                    LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=3.5e-5),
                    LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=1.7e-5),
                ),
            )

    def test_a_non_finite_conductivity_is_rejected(self):
        with pytest.raises(SimulationRequestError, match="finite positive"):
            SimulationDraft(
                kind=SimulationKind.RESISTANCE,
                board_id="board-1",
                net_id="net-1",
                accuracy=AccuracyProfile.STANDARD,
                source_terminal_ids=("t-a",),
                to_terminal_ids=("t-b",),
                conductor_conductivity_s_per_m=float("nan"),
            )


class TestAnalysisSignature:
    def _signature(
        self,
        source_terminal_ids: tuple[str, ...] = ("t-a",),
        conductor_conductivity_s_per_m: float | None = None,
        conductor_material_name: str | None = None,
        thickness_overrides: tuple[LayerThicknessOverrideSpec, ...] = (),
    ) -> str:
        return analysis_signature(
            board_digest="digest-1",
            kind=SimulationKind.IR_DROP,
            net_id="net-1",
            source_terminal_ids=source_terminal_ids,
            source_via_ids=(),
            source_voltage_v=1.0,
            loads=(LoadSpec(current_a=1.0, terminal_ids=("t-b",)),),
            to_terminal_ids=(),
            to_via_ids=(),
            mesh=_mesh(),
            verify_convergence=False,
            via_plating_m=None,
            solver_name="fem-2p5d",
            solver_version="0.1.0",
            conductor_conductivity_s_per_m=conductor_conductivity_s_per_m,
            thickness_overrides=thickness_overrides,
        )

    def test_group_membership_order_does_not_change_the_signature(self):
        assert self._signature(("t-a", "t-c")) == self._signature(("t-c", "t-a"))

    def test_different_group_membership_changes_the_signature(self):
        assert self._signature(("t-a",)) != self._signature(("t-a", "t-c"))

    def test_a_different_conductivity_changes_the_signature(self):
        assert self._signature(conductor_conductivity_s_per_m=4.5e7) != self._signature(
            conductor_conductivity_s_per_m=5.8001e7
        )

    def test_material_name_alone_does_not_change_the_signature(self):
        # Display-only: two physically identical jobs must not get different
        # signatures just because one named its material and the other didn't.
        assert self._signature(
            conductor_conductivity_s_per_m=4.5e7, conductor_material_name="Custom"
        ) == self._signature(conductor_conductivity_s_per_m=4.5e7, conductor_material_name=None)

    def test_a_different_thickness_override_changes_the_signature(self):
        overrides = (LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=3.5e-5),)
        assert self._signature(thickness_overrides=overrides) != self._signature()

    def test_thickness_override_order_does_not_change_the_signature(self):
        a = (
            LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=3.5e-5),
            LayerThicknessOverrideSpec(layer_id="l-bottom", thickness_m=1.7e-5),
        )
        b = (
            LayerThicknessOverrideSpec(layer_id="l-bottom", thickness_m=1.7e-5),
            LayerThicknessOverrideSpec(layer_id="l-top", thickness_m=3.5e-5),
        )
        assert self._signature(thickness_overrides=a) == self._signature(thickness_overrides=b)
