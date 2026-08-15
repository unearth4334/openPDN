"""Board import HTTP surface: upload, review, geometry, and failure shapes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openpdn.api.app import create_app
from openpdn.infrastructure.config import Environment, Settings

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ipc2581"


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _upload(client: TestClient, path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        response = client.post(
            "/api/boards", files={"file": (path.name, handle, "application/xml")}
        )
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


class TestImport:
    def test_uploading_a_fixture_returns_its_review(self, client: TestClient):
        payload = _upload(client, FIXTURES / "four-layer-stackup" / "board.xml")
        assert payload["source_format"] == "IPC-2581"
        assert payload["format_revision"] == "IPC-2581B"
        assert payload["readiness"] == "ready_with_assumptions"
        conductive = [layer for layer in payload["layers"] if layer["is_conductive"]]
        assert len(conductive) == 4
        # Provenance travels the wire: imported thickness must say so.
        assert conductive[0]["thickness"]["provenance"] == "imported"

    def test_reimporting_identical_content_is_one_stored_board(self, client: TestClient):
        first = _upload(client, FIXTURES / "minimal-two-layer" / "board.xml")
        second = _upload(client, FIXTURES / "minimal-two-layer" / "board.xml")
        assert first["board_id"] == second["board_id"]
        boards = client.get("/api/boards").json()["boards"]
        assert len(boards) == 1

    def test_garbage_is_a_client_error_not_a_500(self, client: TestClient):
        response = client.post(
            "/api/boards", files={"file": ("junk.xml", b"<not-a-board/>", "application/xml")}
        )
        assert response.status_code == 400
        assert "error" in response.json()

    def test_an_oversized_upload_is_refused(self, tmp_path: Path):
        settings = Settings(
            environment=Environment.DEVELOPMENT,
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
            static_dir=None,
            max_upload_bytes=64,
        )
        with TestClient(create_app(settings)) as client:
            response = client.post(
                "/api/boards", files={"file": ("big.xml", b"x" * 1000, "application/xml")}
            )
        assert response.status_code == 413


class TestReviewAndGeometry:
    def test_an_unknown_board_is_a_404(self, client: TestClient):
        response = client.get("/api/boards/nope")
        assert response.status_code == 404
        assert response.json()["error"] == "BoardNotFoundError"

    def test_normalized_geometry_covers_every_conductive_layer(self, client: TestClient):
        board_id = _upload(client, FIXTURES / "four-layer-stackup" / "board.xml")["board_id"]
        payload = client.get(f"/api/boards/{board_id}/geometry?view=normalized").json()
        assert payload["view"] == "normalized"
        assert len(payload["layers"]) == 4
        assert all(layer["regions"] for layer in payload["layers"])
        assert payload["profile"]

    def test_the_imported_view_keeps_per_feature_regions(self, client: TestClient):
        board_id = _upload(client, FIXTURES / "plane-and-trace" / "board.xml")["board_id"]
        imported = client.get(f"/api/boards/{board_id}/geometry?view=imported").json()
        normalized = client.get(f"/api/boards/{board_id}/geometry?view=normalized").json()
        imported_count = sum(len(layer["regions"]) for layer in imported["layers"])
        normalized_count = sum(len(layer["regions"]) for layer in normalized["layers"])
        # Pre-union features are at least as numerous as the unioned result,
        # and every region traces back to source artwork.
        assert imported_count >= normalized_count
        assert all(
            region["source_refs"] for layer in imported["layers"] for region in layer["regions"]
        )

    def test_via_review_data_is_served(self, client: TestClient):
        payload = _upload(client, FIXTURES / "via-through-board" / "board.xml")
        kinds = {via["span_kind"] for via in payload["vias"]}
        assert kinds == {"through", "blind", "buried"}
        assert all(group["count"] >= 1 for group in payload["via_groups"])
        assert payload["timings"]["normalize_seconds"] is not None
