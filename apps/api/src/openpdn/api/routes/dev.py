"""Development-only conveniences.

Mounted exclusively when `environment=development` *and* a local fixture is
configured (`OPENPDN_DEV_FIXTURE`). Production builds never see these routes,
and no fixture path is baked into code -- the normal application accepts
arbitrary uploads through `/api/boards`.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from openpdn.api.board_schemas import BoardReviewResponse
from openpdn.api.dependencies import ContainerDep
from openpdn.application.errors import ImportRequestError

router = APIRouter(prefix="/api/dev", tags=["dev"])


class DevFixtureResponse(BaseModel):
    """Whether a local development fixture is configured."""

    name: str


@router.get("/fixture", response_model=DevFixtureResponse, summary="Configured dev fixture")
def fixture(container: ContainerDep) -> DevFixtureResponse:
    """Name the configured local fixture, if any."""
    path = container.settings.dev_fixture
    if path is None or not path.is_file():
        raise ImportRequestError("No development fixture is configured or it does not exist")
    return DevFixtureResponse(name=path.name)


@router.post(
    "/fixture/import",
    response_model=BoardReviewResponse,
    status_code=201,
    summary="Import the configured dev fixture",
)
async def import_fixture(container: ContainerDep) -> BoardReviewResponse:
    """Import the configured local fixture without an upload round trip."""
    path = container.settings.dev_fixture
    if path is None or not path.is_file():
        raise ImportRequestError("No development fixture is configured or it does not exist")
    review = await run_in_threadpool(container.review_service.import_and_review, path)
    return BoardReviewResponse.from_dto(review)
