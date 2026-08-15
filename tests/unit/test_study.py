"""Study invariants and the study/board separation."""

from __future__ import annotations

import dataclasses

import pytest

from openpdn.domain.board import Board, NetId, TerminalId
from openpdn.domain.errors import InvalidStudyError
from openpdn.domain.provenance import Quantity
from openpdn.domain.study import (
    AnalysisStudy,
    CurrentLoad,
    LayerThicknessOverride,
    LoadId,
    MeshSettings,
    ProbeId,
    ResistanceProbe,
    SourceId,
    StudyId,
    VoltageSource,
)
from openpdn.domain.units import AMPERE, METRE, VOLT


class TestStudyConstruction:
    def test_a_study_without_a_source_is_rejected(self, simple_board: Board):
        # Pure Neumann problems have no unique solution; refuse early.
        with pytest.raises(InvalidStudyError, match="no voltage source"):
            AnalysisStudy(
                id=StudyId("s"),
                name="No source",
                board_id=str(simple_board.id),
                net_ids=(NetId("NET_VCC"),),
                sources=(),
            )

    def test_two_sources_on_one_terminal_are_rejected(self, simple_study: AnalysisStudy):
        duplicate = VoltageSource(
            id=SourceId("SRC2"),
            terminal_id=TerminalId("T_SRC"),
            voltage=Quantity.configured(1.0, VOLT),
        )
        with pytest.raises(InvalidStudyError, match="two sources"):
            dataclasses.replace(simple_study, sources=(*simple_study.sources, duplicate))

    def test_a_source_voltage_must_be_in_volts(self):
        with pytest.raises(Exception, match="V"):
            VoltageSource(
                id=SourceId("SRC1"),
                terminal_id=TerminalId("T"),
                voltage=Quantity.configured(0.85, AMPERE),
            )

    def test_a_probe_cannot_measure_a_terminal_against_itself(self):
        with pytest.raises(InvalidStudyError):
            ResistanceProbe(ProbeId("P1"), TerminalId("T1"), TerminalId("T1"))

    def test_mesh_minimum_cannot_exceed_the_target(self):
        with pytest.raises(InvalidStudyError):
            MeshSettings(
                target_element_size=Quantity.configured(1e-4, METRE),
                minimum_element_size=Quantity.configured(1e-3, METRE),
            )


class TestStudyAgainstBoard:
    def test_a_valid_study_resolves(self, simple_board: Board, simple_study: AnalysisStudy):
        simple_study.validate_against(simple_board)

    def test_a_load_on_an_unknown_terminal_is_rejected(
        self, simple_board: Board, simple_study: AnalysisStudy
    ):
        broken = dataclasses.replace(
            simple_study,
            loads=(
                CurrentLoad(
                    id=LoadId("L2"),
                    terminal_id=TerminalId("T_GHOST"),
                    current=Quantity.configured(1.0, AMPERE),
                ),
            ),
        )
        with pytest.raises(InvalidStudyError, match="unknown terminal"):
            broken.validate_against(simple_board)

    def test_a_study_for_another_board_is_rejected(
        self, simple_board: Board, simple_study: AnalysisStudy
    ):
        broken = dataclasses.replace(simple_study, board_id="brd-other")
        with pytest.raises(InvalidStudyError, match="targets board"):
            broken.validate_against(simple_board)

    def test_an_override_for_an_unknown_layer_is_rejected(
        self, simple_board: Board, simple_study: AnalysisStudy
    ):
        broken = dataclasses.replace(
            simple_study,
            thickness_overrides=(
                LayerThicknessOverride(
                    layer_id="L_GHOST",  # type: ignore[arg-type]
                    thickness=Quantity.configured(35e-6, METRE),
                ),
            ),
        )
        with pytest.raises(InvalidStudyError, match="unknown layer"):
            broken.validate_against(simple_board)


class TestStudyDoesNotMutateTheBoard:
    def test_thickness_overrides_live_in_the_study(
        self, simple_board: Board, simple_study: AnalysisStudy
    ):
        # The board records what was imported; the study records what the
        # engineer decided. Overriding must not rewrite fabrication data.
        before = simple_board.stackup.layer("L1").thickness  # type: ignore[arg-type]
        study = dataclasses.replace(
            simple_study,
            thickness_overrides=(
                LayerThicknessOverride(
                    layer_id=simple_board.stackup.layers[0].id,
                    thickness=Quantity.configured(70e-6, METRE),
                ),
            ),
        )
        study.validate_against(simple_board)
        after = simple_board.stackup.layer("L1").thickness  # type: ignore[arg-type]
        assert before == after
        assert study.thickness_override_by_layer[simple_board.stackup.layers[0].id].value == (70e-6)

    def test_total_load_current(self, simple_study: AnalysisStudy):
        assert simple_study.total_load_current_a == pytest.approx(4.0)
