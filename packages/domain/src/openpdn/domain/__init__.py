"""openPDN domain model.

The innermost layer. It knows about printed circuit boards, electrical studies
and analysis results -- and about nothing else. In particular it must never
import IPC-2581, ODB++, XML libraries, padne, Elmer, FastAPI, Pydantic, NumPy,
or any other third-party package. See `.agents/skills/architecture/SKILL.md`
and ADR-0001.
"""

from openpdn.domain.board import (
    Board,
    BoardId,
    BoardProfile,
    ComponentId,
    CopperRegion,
    CopperRegionId,
    Layer,
    LayerFunction,
    LayerId,
    Net,
    NetId,
    Pad,
    PadId,
    PhysicalComponent,
    Stackup,
    Terminal,
    TerminalId,
    Via,
    ViaId,
)
from openpdn.domain.errors import (
    DomainError,
    InvalidBoardError,
    InvalidStudyError,
    MissingPhysicalPropertyError,
)
from openpdn.domain.geometry import BoundingBox2D, Point2D, Polygon2D
from openpdn.domain.materials import COPPER_ANNEALED, Material
from openpdn.domain.provenance import Provenance, Quantity
from openpdn.domain.results import (
    Diagnostic,
    DiagnosticSeverity,
    ElectricalAnalysisResult,
    NetIRDropResult,
    ResistanceProbeResult,
    ResultFidelity,
    SolverIdentity,
    SolverRunStats,
    TerminalResult,
)
from openpdn.domain.study import (
    AnalysisStudy,
    CurrentLoad,
    MeshSettings,
    ResistanceProbe,
    StudyId,
    ViaModel,
    VoltageSource,
)

__all__ = [
    "COPPER_ANNEALED",
    "AnalysisStudy",
    "Board",
    "BoardId",
    "BoardProfile",
    "BoundingBox2D",
    "ComponentId",
    "CopperRegion",
    "CopperRegionId",
    "CurrentLoad",
    "Diagnostic",
    "DiagnosticSeverity",
    "DomainError",
    "ElectricalAnalysisResult",
    "InvalidBoardError",
    "InvalidStudyError",
    "Layer",
    "LayerFunction",
    "LayerId",
    "Material",
    "MeshSettings",
    "MissingPhysicalPropertyError",
    "Net",
    "NetIRDropResult",
    "NetId",
    "Pad",
    "PadId",
    "PhysicalComponent",
    "Point2D",
    "Polygon2D",
    "Provenance",
    "Quantity",
    "ResistanceProbe",
    "ResistanceProbeResult",
    "ResultFidelity",
    "SolverIdentity",
    "SolverRunStats",
    "Stackup",
    "StudyId",
    "Terminal",
    "TerminalId",
    "TerminalResult",
    "Via",
    "ViaId",
    "ViaModel",
    "VoltageSource",
]
