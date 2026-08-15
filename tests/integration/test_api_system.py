"""HTTP surface: the vertical slice from a request to an application service."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from openpdn.api.app import create_app
from openpdn.application.version import get_version
from openpdn.infrastructure.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


class TestHealth:
    def test_health_reports_ok_and_identifies_the_build(self, client: TestClient):
        response = client.get("/api/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["version"] == get_version()
        assert payload["api_version"] == "v0"

    def test_health_does_not_require_a_solver_run(self, client: TestClient):
        # Probes must stay cheap: this is what the container HEALTHCHECK calls.
        assert client.get("/api/health").elapsed.total_seconds() < 2.0


class TestInfo:
    def test_info_lists_registered_adapters(self, client: TestClient):
        payload = client.get("/api/info").json()
        assert [solver["name"] for solver in payload["solvers"]] == ["mock"]
        assert [importer["name"] for importer in payload["importers"]] == [
            "canonical-json",
            "ipc2581",
        ]

    def test_the_mock_solver_is_advertised_as_non_physical(self, client: TestClient):
        payload = client.get("/api/info").json()
        mock = next(solver for solver in payload["solvers"] if solver["name"] == "mock")
        assert mock["fidelity"] == "mock"

    def test_unimplemented_capabilities_are_not_claimed(self, client: TestClient):
        # The API is the contract the UI renders; it must not overstate.
        statuses = {
            capability["name"]: capability["status"]
            for capability in client.get("/api/info").json()["capabilities"]
        }
        assert statuses["IPC-2581 import"] == "implemented"
        assert statuses["ODB++ import"] == "planned"
        assert statuses["IR-drop analysis"] == "planned"
        assert statuses["Canonical board model"] == "implemented"
        assert statuses["Geometry normalisation"] == "implemented"


class TestApiSurface:
    def test_openapi_schema_is_served(self, client: TestClient):
        assert client.get("/api/openapi.json").status_code == 200

    def test_unknown_routes_are_404(self, client: TestClient):
        assert client.get("/api/nonexistent").status_code == 404
