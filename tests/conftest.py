"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from openpdn.domain.board import (
    Board,
    BoardId,
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
from openpdn.domain.geometry import Point2D, Polygon2D
from openpdn.domain.materials import COPPER_ANNEALED
from openpdn.domain.provenance import Quantity
from openpdn.domain.study import (
    AnalysisStudy,
    CurrentLoad,
    LoadId,
    SourceId,
    StudyId,
    VoltageSource,
)
from openpdn.domain.units import AMPERE, METRE, VOLT
from openpdn.infrastructure.config import Environment, Settings
from openpdn.infrastructure.container import Container, build_container

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def fixture_dir() -> Path:
    """Directory holding committed test fixtures."""
    return FIXTURE_DIR


@pytest.fixture
def two_layer_rail_path() -> Path:
    """Path to the canonical-JSON regression board."""
    return FIXTURE_DIR / "boards" / "two-layer-rail.json"


@pytest.fixture
def minimal_ipc2581_path() -> Path:
    """Path to the hand-written minimal IPC-2581B document."""
    return FIXTURE_DIR / "ipc2581" / "minimal-two-layer" / "board.xml"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pinned to a temporary directory, never the developer's own."""
    return Settings(
        environment=Environment.DEVELOPMENT,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        static_dir=None,
    )


@pytest.fixture
def container(settings: Settings) -> Container:
    """A fully wired application container."""
    return build_container(settings)


@pytest.fixture
def simple_board() -> Board:
    """A minimal, valid two-terminal board built in code.

    Built by hand rather than loaded from a file so that domain tests do not
    depend on the importer.
    """
    copper = Quantity.imported(35e-6, METRE, "1 oz finished copper")
    layer = Layer(
        id=LayerId("L1"),
        name="TOP",
        function=LayerFunction.SIGNAL,
        index=0,
        thickness=copper,
        material=COPPER_ANNEALED,
    )
    bottom = Layer(
        id=LayerId("L2"),
        name="BOTTOM",
        function=LayerFunction.PLANE,
        index=1,
        thickness=copper,
        material=COPPER_ANNEALED,
    )
    return Board(
        id=BoardId("brd-simple"),
        name="Simple rail",
        stackup=Stackup((layer, bottom)),
        nets=(Net(NetId("NET_VCC"), "VCC"),),
        copper_regions=(
            CopperRegion(
                id=CopperRegionId("CU1"),
                net_id=NetId("NET_VCC"),
                layer_id=LayerId("L1"),
                outline=Polygon2D.rectangle(Point2D(0.0, 0.0), 0.040, 0.008),
            ),
        ),
        vias=(
            Via(
                id=ViaId("V1"),
                net_id=NetId("NET_VCC"),
                from_layer_id=LayerId("L1"),
                to_layer_id=LayerId("L2"),
                position=Point2D(0.020, 0.004),
                finished_hole_diameter=Quantity.imported(0.0003, METRE),
                plating_thickness=Quantity.assumed(25e-6, METRE, "IPC-6012 Class 2 minimum"),
            ),
        ),
        pads=(
            Pad(PadId("PAD_IN"), LayerId("L1"), Point2D(0.001, 0.004), NetId("NET_VCC")),
            Pad(PadId("PAD_OUT"), LayerId("L1"), Point2D(0.039, 0.004), NetId("NET_VCC")),
        ),
        terminals=(
            Terminal(
                id=TerminalId("T_SRC"),
                name="J1.1",
                net_id=NetId("NET_VCC"),
                pad_ids=(PadId("PAD_IN"),),
                component_id=ComponentId("C_J1"),
            ),
            Terminal(
                id=TerminalId("T_LOAD"),
                name="U1.1",
                net_id=NetId("NET_VCC"),
                pad_ids=(PadId("PAD_OUT"),),
                component_id=ComponentId("C_U1"),
            ),
        ),
        components=(
            PhysicalComponent(ComponentId("C_J1"), "J1", (TerminalId("T_SRC"),)),
            PhysicalComponent(ComponentId("C_U1"), "U1", (TerminalId("T_LOAD"),)),
        ),
    )


@pytest.fixture
def simple_study(simple_board: Board) -> AnalysisStudy:
    """A one-source, one-load study on `simple_board`."""
    return AnalysisStudy(
        id=StudyId("study-nominal"),
        name="Nominal load",
        board_id=str(simple_board.id),
        net_ids=(NetId("NET_VCC"),),
        sources=(
            VoltageSource(
                id=SourceId("SRC1"),
                terminal_id=TerminalId("T_SRC"),
                voltage=Quantity.configured(0.85, VOLT),
            ),
        ),
        loads=(
            CurrentLoad(
                id=LoadId("LOAD1"),
                terminal_id=TerminalId("T_LOAD"),
                current=Quantity.configured(4.0, AMPERE),
            ),
        ),
    )
