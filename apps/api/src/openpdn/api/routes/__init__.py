"""HTTP routers, one module per resource group."""

from openpdn.api.routes.boards import router as boards_router
from openpdn.api.routes.dev import router as dev_router
from openpdn.api.routes.system import router as system_router

__all__ = ["boards_router", "dev_router", "system_router"]
