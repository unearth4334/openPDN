"""Board import and review endpoints.

Uploads are untrusted input: the file is staged into an isolated workspace
under a sanitised name and size-capped while streaming, before any parser sees
a byte. Import and normalisation run in the threadpool -- they are CPU-bound
and must not stall the event loop's health checks.
"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from openpdn.api.board_schemas import BoardListResponse, BoardReviewResponse, GeometryResponse
from openpdn.api.dependencies import ContainerDep
from openpdn.api.schemas import ErrorResponse
from openpdn.application.review_models import GeometryView
from openpdn.infrastructure.workspace import TemporaryWorkspace, sanitise_label

router = APIRouter(prefix="/api/boards", tags=["boards"])

#: Streaming chunk size for uploads; 1 MiB keeps memory flat on large boards.
_UPLOAD_CHUNK_BYTES = 1 << 20


@router.post(
    "",
    response_model=BoardReviewResponse,
    status_code=201,
    summary="Import a PCB source file",
    responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def import_board(
    file: UploadFile, container: ContainerDep
) -> BoardReviewResponse | JSONResponse:
    """Import an uploaded PCB source and return its review.

    The format is detected from the document content; re-uploading identical
    content returns the already-imported board without re-parsing.
    """
    settings = container.settings
    label = sanitise_label(file.filename or "board")
    with TemporaryWorkspace(settings.cache_dir / "uploads", label="upload") as workspace:
        staged = workspace.path / label
        received = 0
        with staged.open("wb") as handle:
            while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                received += len(chunk)
                if received > settings.max_upload_bytes:
                    return JSONResponse(
                        status_code=413,
                        content=ErrorResponse(
                            error="UploadTooLarge",
                            detail=(f"Upload exceeds the {settings.max_upload_bytes} byte limit"),
                        ).model_dump(),
                    )
                handle.write(chunk)
        review = await run_in_threadpool(container.review_service.import_and_review, staged)
    return BoardReviewResponse.from_dto(review)


@router.get("", response_model=BoardListResponse, summary="List imported boards")
def list_boards(container: ContainerDep) -> BoardListResponse:
    """List every board currently held by this deployment."""
    return BoardListResponse.from_dto(container.review_service.list_boards())


@router.get(
    "/{board_id}",
    response_model=BoardReviewResponse,
    summary="Review one imported board",
    responses={404: {"model": ErrorResponse}},
)
def get_board(board_id: str, container: ContainerDep) -> BoardReviewResponse:
    """Return the full review of one stored board."""
    return BoardReviewResponse.from_dto(container.review_service.review(board_id))


@router.get(
    "/{board_id}/geometry",
    response_model=GeometryResponse,
    summary="Fetch renderable copper geometry",
    responses={404: {"model": ErrorResponse}},
)
async def get_geometry(
    board_id: str, container: ContainerDep, view: GeometryView = GeometryView.NORMALIZED
) -> GeometryResponse:
    """Return one geometry view; large, so clients fetch it once per view."""
    geometry = await run_in_threadpool(container.review_service.geometry, board_id, view)
    return GeometryResponse.from_dto(geometry)
