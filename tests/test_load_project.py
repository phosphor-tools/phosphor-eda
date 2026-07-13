"""Tests for load_project() unified project assembly."""

from pathlib import Path

import pytest
from fixture_paths import FIXTURES, UPSTREAM_FIXTURES

from phosphor_eda.domain.pcb import Board
from phosphor_eda.domain.project import Project
from phosphor_eda.formats.altium.pcb_project import AltiumEnrichment
from phosphor_eda.query.project_loader import load_project

JETSON_ORIN_PRO = UPSTREAM_FIXTURES / "jetson-orin" / "jetson-orin-baseboard.kicad_pro"
ORANGECRAB_PRO = FIXTURES / "kicad-orangecrab" / "OrangeCrab.kicad_pro"
SWD_SWITCH_PCB = UPSTREAM_FIXTURES / "debugotron/hw/swd_switch/swd_switch.kicad_pcb"
PI_MX8_PRJPCB = (
    UPSTREAM_FIXTURES / "pi-mx8/01_Electronics/PiMX8MP_r0.3_release" / "PiMX8MP_r0.3_release.PrjPcb"
)


# ---------------------------------------------------------------------------
# KiCad project
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kicad_project() -> Project:
    if not ORANGECRAB_PRO.exists():
        pytest.skip("Fixture not available")
    return load_project(ORANGECRAB_PRO)


@pytest.fixture(scope="module")
def kicad_stackup_project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    if not SWD_SWITCH_PCB.exists():
        pytest.skip("Fixture not available")
    project_dir = tmp_path_factory.mktemp("swd-project")
    project_file = project_dir / "swd_switch.kicad_pro"
    board_file = project_dir / "swd_switch.kicad_pcb"
    board_file.write_bytes(SWD_SWITCH_PCB.read_bytes())
    project_file.write_text("{}", encoding="utf-8")
    return load_project(project_file)


@pytest.fixture(scope="module")
def jetson_project() -> Project:
    if not JETSON_ORIN_PRO.exists():
        pytest.skip("Fixture not available")
    return load_project(JETSON_ORIN_PRO)


def test_kicad_project_has_board(kicad_project: Project) -> None:
    assert kicad_project.board is not None


def test_kicad_project_has_stackup(kicad_stackup_project: Project) -> None:
    assert kicad_stackup_project.board is not None
    stackup = kicad_stackup_project.board.stackup
    assert stackup is not None
    copper = [ly for ly in stackup.layers if ly.layer_type == "copper"]
    assert len(copper) == 4


def test_kicad_project_has_net_classes(kicad_project: Project) -> None:
    assert len(kicad_project.net_classes) == 1


@pytest.mark.behavior_lock
def test_kicad_project_has_design_rules(jetson_project: Project) -> None:
    assert len(jetson_project.design_rules) >= 15


def test_kicad_project_has_schematic(kicad_project: Project) -> None:
    assert kicad_project.schematic is not None
    assert len(kicad_project.schematic.components) > 0


def test_kicad_project_metadata_from_title_block(kicad_project: Project) -> None:
    """Root page title block fills empty ProjectMetadata fields."""
    assert kicad_project.schematic is not None
    root = min(kicad_project.schematic.pages, key=lambda page: len(page.scope_id.path))
    assert root.title_block is not None
    assert kicad_project.metadata.name == ORANGECRAB_PRO.stem
    assert kicad_project.metadata.revision == root.title_block.revision
    assert kicad_project.metadata.date == root.title_block.date


# ---------------------------------------------------------------------------
# Altium project
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def altium_project() -> Project:
    if not PI_MX8_PRJPCB.exists():
        pytest.skip("Fixture not available")
    return load_project(PI_MX8_PRJPCB)


def test_altium_project_has_board(altium_project: Project) -> None:
    assert altium_project.board is not None


def test_altium_project_has_stackup(altium_project: Project) -> None:
    assert altium_project.board is not None
    stackup = altium_project.board.stackup
    assert stackup is not None
    copper = [ly for ly in stackup.layers if ly.layer_type == "copper"]
    assert len(copper) == 10


def test_altium_project_has_net_classes(altium_project: Project) -> None:
    assert len(altium_project.net_classes) == 78


def test_altium_project_has_design_rules(altium_project: Project) -> None:
    assert len(altium_project.design_rules) >= 100


def test_altium_project_has_diff_pairs(altium_project: Project) -> None:
    assert len(altium_project.diff_pairs) == 55


def test_altium_prjpcb_loads_all_existing_boards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "First.PcbDoc"
    second = tmp_path / "Second.PcbDoc"
    first.write_text("")
    second.write_text("")
    prjpcb = tmp_path / "Project.PrjPcb"
    prjpcb.write_text(
        "[Design]\n"
        "HierarchyMode=1\n\n"
        "[Document1]\n"
        "DocumentPath=First.PcbDoc\n\n"
        "[Document2]\n"
        "DocumentPath=Second.PcbDoc\n"
    )

    def board(name: str, path: Path) -> Board:
        return Board(
            name=name,
            layers=[],
            nets={},
            footprints=[],
            pads=[],
            vias=[],
            drills=[],
            conductors=[],
            artwork=[],
            pours=[],
            keepouts=[],
            source_path=str(path),
        )

    def fake_parse_altium_pcb(path: Path, _ctx: object) -> Board:
        return board(path.stem, path)

    def fake_load_altium_enrichment(_path: Path, _ctx: object) -> AltiumEnrichment:
        return AltiumEnrichment(design_rules=[], net_classes=[], diff_pairs=[])

    monkeypatch.setattr(
        "phosphor_eda.formats.altium.project_loader.parse_altium_pcb",
        fake_parse_altium_pcb,
    )
    monkeypatch.setattr(
        "phosphor_eda.formats.altium.project_loader.load_altium_enrichment",
        fake_load_altium_enrichment,
    )

    project = load_project(prjpcb)

    assert [board.name for board in project.boards] == ["First", "Second"]
    assert project.board is project.boards[0]


def test_unsupported_extension() -> None:
    with pytest.raises(ValueError, match="project file required"):
        load_project(Path("foo.txt"))
