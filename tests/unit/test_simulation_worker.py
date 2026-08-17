"""`_study_from_spec` actually applies conductor overrides to the `AnalysisStudy` it builds.

The application layer and the API route are tested for carrying
`conductor_conductivity_s_per_m`/`conductor_material_name`/`thickness_overrides`
into the job spec; this is the one place those fields take mechanical effect
-- the point where they get constructed into the `Material`/
`LayerThicknessOverride` the solver actually reads.
"""

from __future__ import annotations

from openpdn.application.simulation_models import (
    AccuracyProfile,
    LayerThicknessOverrideSpec,
    LoadSpec,
    ResolvedMeshSpec,
    SimulationJobSpec,
    SimulationKind,
)
from openpdn.domain.provenance import Provenance
from openpdn.domain.units import METRE
from openpdn.infrastructure.simulation_worker import _study_from_spec
from tests.validation.boards import NET, plane_neck_plane_board


def _mesh() -> ResolvedMeshSpec:
    return ResolvedMeshSpec(
        max_element_m=1e-3, min_element_m=1e-5, elements_across_feature=4, growth_rate=0.7
    )


def _spec(**overrides) -> SimulationJobSpec:
    base = {
        "job_id": "job-worker-test",
        "name": "worker test",
        "kind": SimulationKind.IR_DROP,
        "board_id": "board-1",
        "board_digest": "digest-1",
        "board_name": "board",
        "net_id": str(NET),
        "net_name": "NET1",
        "source_terminal_ids": ("term-a",),
        "source_via_ids": (),
        "source_voltage_v": 1.0,
        "loads": (LoadSpec(current_a=1.0, terminal_ids=("term-b",)),),
        "to_terminal_ids": (),
        "to_via_ids": (),
        "accuracy": AccuracyProfile.STANDARD,
        "mesh": _mesh(),
        "verify_convergence": False,
        "via_plating_m": None,
        "solver_name": "fem-2p5d",
        "created_at_epoch_s": 1000.0,
        "signature": "sig-worker-test",
    }
    return SimulationJobSpec(**{**base, **overrides})


class TestConductorMaterialConstruction:
    def test_no_override_leaves_the_study_material_unset(self):
        board = plane_neck_plane_board()
        study = _study_from_spec(_spec(), board, refine_factor=1.0)
        assert study.conductor_material is None

    def test_a_custom_conductivity_becomes_a_study_material(self):
        board = plane_neck_plane_board()
        spec = _spec(conductor_conductivity_s_per_m=4.5e7, conductor_material_name="Custom")
        study = _study_from_spec(spec, board, refine_factor=1.0)
        assert study.conductor_material is not None
        assert study.conductor_material.conductivity_s_per_m == 4.5e7
        assert study.conductor_material.name == "Custom"


class TestThicknessOverrideConstruction:
    def test_no_overrides_leaves_the_study_overrides_empty(self):
        board = plane_neck_plane_board()
        study = _study_from_spec(_spec(), board, refine_factor=1.0)
        assert study.thickness_overrides == ()

    def test_an_override_lands_on_the_study_with_configured_provenance(self):
        # CONFIGURED, not ASSUMED: the engineer entered this for the study,
        # unlike via plating's ASSUMED default-filling-an-unknown.
        board = plane_neck_plane_board()
        spec = _spec(
            thickness_overrides=(LayerThicknessOverrideSpec(layer_id="L1", thickness_m=3.5e-5),)
        )
        study = _study_from_spec(spec, board, refine_factor=1.0)
        assert len(study.thickness_overrides) == 1
        override = study.thickness_overrides[0]
        assert override.layer_id == "L1"
        assert override.thickness.require_unit(METRE) == 3.5e-5
        assert override.thickness.provenance is Provenance.CONFIGURED
