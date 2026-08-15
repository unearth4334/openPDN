"""The composition root.

Exactly one place in openPDN knows which concrete adapters exist and wires them
into application services: this module. The HTTP API and the CLI each build a
container at start-up and then talk only to services.

Adding a solver or an importer means editing `build_container` -- and nothing
else. If a change requires touching an application service to add a backend,
the abstraction is wrong (ADR-0003).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openpdn.application.analysis_service import AnalysisService
from openpdn.application.board_review_service import BoardReviewService
from openpdn.application.import_service import BoardImportService
from openpdn.application.info_service import ApplicationInfoService
from openpdn.application.simulation_models import WorkerLimits
from openpdn.application.simulation_service import SimulationService
from openpdn.geometry.shapely_engine import ShapelyGeometryNormalizer
from openpdn.infrastructure.board_store import InMemoryBoardStore
from openpdn.infrastructure.config import AUTO_DETECT_IMPORTER, Settings, get_settings
from openpdn.infrastructure.fem_planner import FemSimulationPlanner
from openpdn.infrastructure.job_store_sqlite import SqliteJobStore
from openpdn.infrastructure.registries import InMemoryImporterRegistry, InMemorySolverRegistry
from openpdn.infrastructure.simulation_artifacts import FilesystemArtifactStore
from openpdn.pcb_import.canonical_json import CanonicalJsonImporter
from openpdn.pcb_import.canonical_json.codec import board_from_document, board_to_document
from openpdn.pcb_import.ipc2581 import IPC2581Importer
from openpdn.solver.fem import SOLVER_NAME as FEM_SOLVER_NAME
from openpdn.solver.fem import SOLVER_VERSION as FEM_SOLVER_VERSION
from openpdn.solver.fem import FemSheetSolver
from openpdn.solver.mock import MockSolver

if TYPE_CHECKING:
    from openpdn.domain.board import Board
    from openpdn.geometry.api import GeometryNormalizer
    from openpdn.pcb_import.api import PCBImporter
    from openpdn.solver.api import ElectricalSolver


@dataclass(frozen=True, slots=True)
class Container:
    """The wired application, handed to whichever surface is running."""

    settings: Settings
    solvers: InMemorySolverRegistry
    importers: InMemoryImporterRegistry
    info_service: ApplicationInfoService
    import_service: BoardImportService
    review_service: BoardReviewService
    analysis_service: AnalysisService
    simulation_service: SimulationService
    job_store: SqliteJobStore
    artifact_store: FilesystemArtifactStore
    worker_limits: WorkerLimits
    #: Geometry engine instance for worker/inline execution paths.
    geometry_normalizer: GeometryNormalizer
    #: Decodes a persisted canonical board document (json, name, digest).
    board_decoder: object


def build_container(settings: Settings | None = None) -> Container:
    """Wire adapters into application services.

    Args:
        settings: Configuration to use; defaults to the process-wide settings.
    """
    resolved = settings or get_settings()

    solvers = InMemorySolverRegistry(_build_solvers())
    importers = InMemoryImporterRegistry(_build_importers(resolved))
    import_service = BoardImportService(
        importers=importers,
        default_importer=_default_importer(resolved),
    )

    board_store = InMemoryBoardStore()
    normalizer = ShapelyGeometryNormalizer()
    job_store = SqliteJobStore(resolved.data_dir / "jobs.sqlite3")
    artifact_store = FilesystemArtifactStore(resolved.data_dir)
    worker_limits = WorkerLimits(
        max_dofs=resolved.worker_max_dofs,
        max_concurrent_jobs=resolved.worker_max_concurrent_jobs,
        max_job_seconds=resolved.worker_max_job_seconds,
        lease_seconds=resolved.worker_lease_seconds,
        max_attempts=resolved.worker_max_attempts,
    )

    return Container(
        settings=resolved,
        solvers=solvers,
        importers=importers,
        info_service=ApplicationInfoService(
            environment=str(resolved.environment),
            solvers=solvers,
            importers=importers,
        ),
        import_service=import_service,
        review_service=BoardReviewService(
            import_service=import_service,
            importers=importers,
            normalizer=normalizer,
            store=board_store,
        ),
        analysis_service=AnalysisService(solvers=solvers, default_solver=resolved.solver),
        simulation_service=SimulationService(
            boards=board_store,
            jobs=job_store,
            artifacts=artifact_store,
            planner=FemSimulationPlanner(),
            limits=worker_limits,
            solver_name=FEM_SOLVER_NAME,
            solver_version=FEM_SOLVER_VERSION,
            board_to_document_json=_board_document_json,
        ),
        job_store=job_store,
        artifact_store=artifact_store,
        worker_limits=worker_limits,
        geometry_normalizer=normalizer,
        board_decoder=_board_from_document_json,
    )


def _board_document_json(board: Board) -> str:
    """Serialise a board to its canonical JSON document for worker processes."""
    return json.dumps(board_to_document(board), sort_keys=True)


def _board_from_document_json(document_json: str, source_name: str, digest: str) -> Board:
    """Decode a persisted canonical board document."""
    return board_from_document(json.loads(document_json), source_name=source_name, digest=digest)


def _build_solvers() -> list[ElectricalSolver]:
    """Instantiate every solver adapter this build ships.

    Backends whose external dependency is missing should still be listed, with
    `SolverDescriptor.available=False` and a reason, so the UI can explain the
    gap instead of hiding it.
    """
    return [FemSheetSolver(normalizer=ShapelyGeometryNormalizer()), MockSolver()]


def _build_importers(settings: Settings) -> list[PCBImporter]:
    """Instantiate every importer adapter this build ships.

    All of them, always. Which one runs is decided per document, not per
    deployment: an importer that cannot handle a source declines it, and one
    that is still under construction stays listed with a reason so the UI can
    explain the gap.

    IPC-2581 is the reference adapter (ADR-0006); ODB++ joins this list when it
    is written, and nothing else changes.
    """
    del settings  # Selection happens per document; see `_default_importer`.
    return [IPC2581Importer(), CanonicalJsonImporter()]


def _default_importer(settings: Settings) -> str | None:
    """Return the importer to force, or `None` to detect from the document."""
    if settings.importer.strip().lower() == AUTO_DETECT_IMPORTER:
        return None
    return settings.importer
