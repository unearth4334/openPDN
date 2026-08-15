"""System endpoints: health and deployment description.

`/api/health` is what the container HEALTHCHECK, an orchestrator and any load
balancer probe. It must stay cheap, dependency-free and free of side effects --
no solver call, no filesystem scan, no database query.
"""

from __future__ import annotations

from fastapi import APIRouter

from openpdn.api.dependencies import InfoServiceDep
from openpdn.api.schemas import HealthResponse, InfoResponse

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(info_service: InfoServiceDep) -> HealthResponse:
    """Report that the process is up, and which build it is."""
    info = info_service.describe()
    return HealthResponse(
        status="ok",
        name=info.name,
        version=info.version,
        api_version=info.api_version,
        environment=info.environment,
    )


@router.get("/info", response_model=InfoResponse, summary="Deployment description")
def info(info_service: InfoServiceDep) -> InfoResponse:
    """Describe available solvers, importers and honest capability statuses."""
    return InfoResponse.from_dto(info_service.describe())
