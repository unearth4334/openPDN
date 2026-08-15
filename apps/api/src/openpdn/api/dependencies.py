"""Request-scoped access to the wired application.

The container is built once during application start-up and stored on the
FastAPI app state; routes reach it through these dependencies rather than
importing adapters or building services of their own.

Types here are imported at runtime, not under `TYPE_CHECKING`: FastAPI resolves
dependency annotations at import time, and an unresolvable forward reference is
silently treated as a query parameter.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from openpdn.application.info_service import ApplicationInfoService
from openpdn.infrastructure.container import Container


def get_container(request: Request) -> Container:
    """Return the container built at start-up."""
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_info_service(container: ContainerDep) -> ApplicationInfoService:
    """Return the deployment-description service."""
    return container.info_service


InfoServiceDep = Annotated[ApplicationInfoService, Depends(get_info_service)]
