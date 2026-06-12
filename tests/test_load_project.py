"""Tests for load_project() unified project assembly."""

from pathlib import Path

import pytest

from phosphor_eda.domain.project import Project
from phosphor_eda.query.convert import load_project

FIXTURES = Path(__file__).parent / "fixtures"

JETSON_ORIN_PCB = FIXTURES / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pcb"
JETSON_ORIN_PRO = FIXTURES / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pro"
PI_MX8_PCB = FIXTURES / "altium" / "pi-mx8" / "PCB" / "PiMX8MP_r0.3.PcbDoc"
SWD_SWITCH_PCB = FIXTURES / "swd_switch.kicad_pcb"


# ---------------------------------------------------------------------------
# KiCad project (from .kicad_pcb)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kicad_project() -> Project:
    if not JETSON_ORIN_PCB.exists():
        pytest.skip("Fixture not available")
    return load_project(JETSON_ORIN_PCB)


def test_kicad_project_has_board(kicad_project: Project) -> None:
    assert kicad_project.board is not None


def test_kicad_project_has_stackup(kicad_project: Project) -> None:
    assert kicad_project.board is not None
    stackup = kicad_project.board.stackup
    assert stackup is not None
    # 8-layer board → copper + dielectric layers
    copper = [ly for ly in stackup.layers if ly.layer_type == "copper"]
    assert len(copper) == 8


def test_kicad_project_has_net_classes(kicad_project: Project) -> None:
    assert len(kicad_project.net_classes) == 8


def test_kicad_project_has_design_rules(kicad_project: Project) -> None:
    assert len(kicad_project.design_rules) >= 15


def test_kicad_project_has_schematic(kicad_project: Project) -> None:
    assert kicad_project.schematic is not None
    assert len(kicad_project.schematic.components) > 0


def test_kicad_project_metadata_from_title_block(kicad_project: Project) -> None:
    """Root page title block fills empty ProjectMetadata fields."""
    assert kicad_project.schematic is not None
    root = kicad_project.schematic.pages[0]
    assert root.title_block is not None
    assert kicad_project.metadata.name == root.title_block.title
    assert kicad_project.metadata.revision == root.title_block.revision
    assert kicad_project.metadata.date == root.title_block.date


# ---------------------------------------------------------------------------
# KiCad project (from .kicad_pro entry point)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kicad_project_from_pro() -> Project:
    if not JETSON_ORIN_PRO.exists():
        pytest.skip("Fixture not available")
    return load_project(JETSON_ORIN_PRO)


def test_kicad_pro_same_result(kicad_project_from_pro: Project) -> None:
    """Loading from .kicad_pro gives same data as loading from .kicad_pcb."""
    p = kicad_project_from_pro
    assert p.board is not None
    assert p.board.stackup is not None
    assert len(p.net_classes) == 8
    assert len(p.design_rules) >= 15


# ---------------------------------------------------------------------------
# Altium project (from .PcbDoc)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def altium_project() -> Project:
    if not PI_MX8_PCB.exists():
        pytest.skip("Fixture not available")
    return load_project(PI_MX8_PCB)


def test_altium_project_has_board(altium_project: Project) -> None:
    assert altium_project.board is not None


def test_altium_project_has_stackup(altium_project: Project) -> None:
    assert altium_project.board is not None
    stackup = altium_project.board.stackup
    assert stackup is not None
    copper = [ly for ly in stackup.layers if ly.layer_type == "copper"]
    assert len(copper) == 10


def test_altium_project_has_net_classes(altium_project: Project) -> None:
    assert len(altium_project.net_classes) == 64


def test_altium_project_has_design_rules(altium_project: Project) -> None:
    assert len(altium_project.design_rules) >= 100


def test_altium_project_has_diff_pairs(altium_project: Project) -> None:
    assert len(altium_project.diff_pairs) == 55


# ---------------------------------------------------------------------------
# Simple KiCad project (no .kicad_pro / .kicad_dru)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def simple_project() -> Project:
    if not SWD_SWITCH_PCB.exists():
        pytest.skip("Fixture not available")
    return load_project(SWD_SWITCH_PCB)


def test_simple_project_has_board(simple_project: Project) -> None:
    assert simple_project.board is not None


def test_simple_project_empty_rules(simple_project: Project) -> None:
    """No .kicad_dru → empty design rules."""
    assert simple_project.design_rules == []


def test_simple_project_empty_classes(simple_project: Project) -> None:
    """No .kicad_pro → empty net classes."""
    assert simple_project.net_classes == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unsupported_extension() -> None:
    with pytest.raises(ValueError, match="Unsupported project entry point"):
        load_project(Path("foo.txt"))
