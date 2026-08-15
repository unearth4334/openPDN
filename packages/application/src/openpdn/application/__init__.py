"""Application services: the use cases openPDN offers.

Everything the UI, the HTTP API and the CLI can do is a method on a service in
this package -- which is what keeps the three surfaces in agreement instead of
each growing its own logic.

Services orchestrate the domain and talk to the outside world only through
contracts (`openpdn.pcb_import.api`, `openpdn.solver.api`). They must not
import a concrete importer or solver, FastAPI, Pydantic, or anything that
reads the environment (ADR-0001).
"""

from openpdn.application.analysis_service import AnalysisService
from openpdn.application.dto import (
    ApplicationInfo,
    CapabilityInfo,
    CapabilityStatus,
    ImporterInfo,
    SolverInfo,
)
from openpdn.application.errors import ApplicationError, ImportRequestError
from openpdn.application.import_service import BoardImportService
from openpdn.application.info_service import ApplicationInfoService
from openpdn.application.version import API_VERSION, APPLICATION_NAME, get_version

__all__ = [
    "API_VERSION",
    "APPLICATION_NAME",
    "AnalysisService",
    "ApplicationError",
    "ApplicationInfo",
    "ApplicationInfoService",
    "BoardImportService",
    "CapabilityInfo",
    "CapabilityStatus",
    "ImportRequestError",
    "ImporterInfo",
    "SolverInfo",
    "get_version",
]
