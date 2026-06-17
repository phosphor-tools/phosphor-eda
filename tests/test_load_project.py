"""Tests for load_project() unified project assembly."""

from pathlib import Path

import pytest

from phosphor_eda.domain.pcb import Board
from phosphor_eda.domain.project import Project
from phosphor_eda.formats.altium.pcb_project import AltiumEnrichment
from phosphor_eda.query.convert import load_project

FIXTURES = Path(__file__).parent / "fixtures"

JETSON_ORIN_PRO = FIXTURES / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pro"
PI_MX8_PRJPCB = FIXTURES / "altium" / "pi-mx8" / "PiMX8MP_r0.3_release.PrjPcb"


# ---------------------------------------------------------------------------
# KiCad project
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kicad_project() -> Project:
    if not JETSON_ORIN_PRO.exists():
        pytest.skip("Fixture not available")
    return load_project(JETSON_ORIN_PRO)


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
    root = min(kicad_project.schematic.pages, key=lambda page: len(page.scope_id.path))
    assert root.title_block is not None
    assert kicad_project.metadata.name == JETSON_ORIN_PRO.stem
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
    assert len(altium_project.net_classes) == 64


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

    monkeypatch.setattr("phosphor_eda.query.convert.parse_altium_pcb", fake_parse_altium_pcb)
    monkeypatch.setattr(
        "phosphor_eda.query.convert.load_altium_enrichment",
        fake_load_altium_enrichment,
    )

    project = load_project(prjpcb)

    assert [board.name for board in project.boards] == ["First", "Second"]
    assert project.board is project.boards[0]


def test_unsupported_extension() -> None:
    with pytest.raises(ValueError, match="project file required"):
        load_project(Path("foo.txt"))
