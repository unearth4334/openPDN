"""CLI surface, and its agreement with the HTTP API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openpdn.api.app import create_app
from openpdn.cli.main import main
from openpdn.infrastructure.config import Settings

pytestmark = pytest.mark.integration


class TestCliCommands:
    def test_info_exits_cleanly(self, capsys: pytest.CaptureFixture[str]):
        assert main(["info"]) == 0
        assert "openPDN" in capsys.readouterr().out

    def test_info_json_is_machine_readable(self, capsys: pytest.CaptureFixture[str]):
        assert main(["--json", "info"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["name"] == "openPDN"
        assert "mock" in payload["solvers"]

    def test_solvers_lists_both_backends(self, capsys: pytest.CaptureFixture[str]):
        assert main(["--json", "solvers"]) == 0
        payload = json.loads(capsys.readouterr().out)
        by_name = {entry["name"]: entry for entry in payload}
        assert by_name["mock"]["fidelity"] == "mock"
        assert by_name["fem-2p5d"]["fidelity"] == "sheet_2p5d"

    def test_importers_lists_the_canonical_json_reader(self, capsys: pytest.CaptureFixture[str]):
        assert main(["--json", "importers"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["name"] == "canonical-json"

    def test_import_summarises_a_board(
        self, capsys: pytest.CaptureFixture[str], two_layer_rail_path: Path
    ):
        assert main(["--json", "import", str(two_layer_rail_path)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["board_id"] == "brd-two-layer-rail"
        assert payload["nets"] == 2
        assert payload["vias"] == 2

    def test_import_surfaces_assumptions_as_diagnostics(
        self, capsys: pytest.CaptureFixture[str], two_layer_rail_path: Path
    ):
        main(["--json", "import", str(two_layer_rail_path)])
        codes = {item["code"] for item in json.loads(capsys.readouterr().out)["diagnostics"]}
        assert "import.assumed_layer_thickness" in codes
        assert "import.incomplete_via_geometry" in codes

    def test_a_bad_path_fails_with_exit_code_one(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ):
        assert main(["import", str(tmp_path / "missing.json")]) == 1
        assert "error:" in capsys.readouterr().err

    def test_an_unknown_command_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exit_info:
            main(["frobnicate"])
        assert exit_info.value.code == 2


class TestSurfaceParity:
    def test_cli_and_api_report_the_same_deployment(
        self, capsys: pytest.CaptureFixture[str], settings: Settings
    ):
        """Both surfaces call the same service; drift here means duplicated logic."""
        assert main(["--json", "info"]) == 0
        cli_payload = json.loads(capsys.readouterr().out)

        with TestClient(create_app(settings)) as client:
            api_payload = client.get("/api/info").json()

        assert cli_payload["name"] == api_payload["name"]
        assert cli_payload["version"] == api_payload["version"]
        assert cli_payload["api_version"] == api_payload["api_version"]
        assert cli_payload["solvers"] == [solver["name"] for solver in api_payload["solvers"]]
        assert cli_payload["capabilities"] == {
            capability["name"]: capability["status"] for capability in api_payload["capabilities"]
        }


class TestInspectAndValidate:
    def test_inspect_prints_the_structural_summary(
        self, capsys: pytest.CaptureFixture[str], minimal_ipc2581_path: Path
    ):
        assert main(["inspect", str(minimal_ipc2581_path)]) == 0
        out = capsys.readouterr().out
        assert "IPC-2581 Import Inspection" in out
        assert "Conductive         2" in out
        assert "ready with assumptions" in out

    def test_validate_import_passes_with_correct_expectations(
        self, capsys: pytest.CaptureFixture[str], minimal_ipc2581_path: Path
    ):
        assert (
            main(
                [
                    "validate-import",
                    str(minimal_ipc2581_path),
                    "--expect-conductive-layers",
                    "2",
                    "--expect-nets",
                    "2",
                ]
            )
            == 0
        )
        assert "OK" in capsys.readouterr().out

    def test_validate_import_fails_on_a_wrong_expectation(
        self, capsys: pytest.CaptureFixture[str], minimal_ipc2581_path: Path
    ):
        assert (
            main(["validate-import", str(minimal_ipc2581_path), "--expect-conductive-layers", "4"])
            == 1
        )
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "expected 4, found 2" in out
