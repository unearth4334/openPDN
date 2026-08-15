"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from openpdn.api.routes import boards_router, dev_router, simulation_router, system_router
from openpdn.api.schemas import ErrorResponse
from openpdn.application.errors import ApplicationError, BoardNotFoundError
from openpdn.application.version import APPLICATION_NAME, get_version
from openpdn.infrastructure.config import get_settings
from openpdn.infrastructure.container import build_container
from openpdn.infrastructure.logging import configure_logging
from openpdn.pcb_import.api import PCBImportError
from openpdn.solver.api import SolverError

if TYPE_CHECKING:
    from openpdn.infrastructure.config import Settings

_logger = logging.getLogger(__name__)

_DESCRIPTION = """\
DC conduction analysis for printed circuit boards.

This build imports IPC-2581 documents into the canonical board model, derives
solver-ready conductive geometry, and serves the review data the UI renders.
Electrical analysis endpoints arrive with the 2.5-D solver; `/api/info`
reports exactly which capabilities are implemented, in development, or planned.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Args:
        settings: Configuration to run with; defaults to the process-wide
            settings. Tests pass their own rather than mutating the
            environment.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Wire the container at start-up and log a clean shutdown."""
        configure_logging(resolved.log_level, resolved.log_format)
        resolved.ensure_directories()
        app.state.container = build_container(resolved)
        _logger.info(
            "api.started",
            extra={
                "event": "api.started",
                "version": get_version(),
                "environment": str(resolved.environment),
                "solver": resolved.solver,
            },
        )
        yield
        # Nothing to release yet; the hook exists so shutdown stays orderly
        # once solver processes and caches are held open.
        _logger.info("api.stopped", extra={"event": "api.stopped"})

    app = FastAPI(
        title=f"{APPLICATION_NAME} API",
        version=get_version(),
        description=_DESCRIPTION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.include_router(system_router)
    app.include_router(boards_router)
    app.include_router(simulation_router)
    if resolved.environment.value == "development" and resolved.dev_fixture is not None:
        app.include_router(dev_router)
    _register_exception_handlers(app)
    _mount_frontend(app, resolved)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Map layer errors onto HTTP status codes in one place."""

    @app.exception_handler(BoardNotFoundError)
    async def _board_not_found(_: Request, exc: BoardNotFoundError) -> JSONResponse:
        # Well-formed request, absent resource.
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error=type(exc).__name__, detail=str(exc)).model_dump(),
        )

    @app.exception_handler(ApplicationError)
    async def _application_error(_: Request, exc: ApplicationError) -> JSONResponse:
        # A use case the caller asked for incorrectly: their problem, not ours.
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error=type(exc).__name__, detail=str(exc)).model_dump(),
        )

    @app.exception_handler(PCBImportError)
    async def _import_error(_: Request, exc: PCBImportError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(error=type(exc).__name__, detail=str(exc)).model_dump(),
        )

    @app.exception_handler(SolverError)
    async def _solver_error(_: Request, exc: SolverError) -> JSONResponse:
        _logger.warning("api.solver_error", extra={"event": "api.solver_error"})
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=type(exc).__name__, detail=str(exc)).model_dump(),
        )


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """Serve the built frontend when one is present.

    Backend and frontend ship as a single deployment unit for now; splitting
    them is a deployment decision, not an architectural one, and this is the
    only code that would change.
    """
    static_dir = settings.static_dir
    if static_dir is None or not static_dir.is_dir():
        return
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
