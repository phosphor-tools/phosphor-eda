from pathlib import Path
from shutil import copy2

import pytest

from phosphor_eda.formats.altium.project_loader import resolve_prjpcb_pcbdoc
from phosphor_eda.query.format import serialize_design
from phosphor_eda.query.project_loader import SCHEMATIC_EXTENSIONS, load_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
DSN_FILE = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PIMX8_PRJPCB = (
    UPSTREAM_FIXTURES / "pi-mx8/01_Electronics/PiMX8MP_r0.3_release/PiMX8MP_r0.3_release.PrjPcb"
)


def test_load_design_dsn_can_be_serialized():
    text = serialize_design(load_design(DSN_FILE))
    assert "DESIGN SUMMARY" in text
    assert "COMPONENTS" in text
    assert "NETS" in text


def test_load_design_unsupported(tmp_path):
    bad = tmp_path / "test.xyz"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="Unsupported schematic format"):
        load_design(bad)


def test_schematic_extensions():
    assert ".dsn" in SCHEMATIC_EXTENSIONS
    assert ".sch" in SCHEMATIC_EXTENSIONS
    assert ".schdoc" in SCHEMATIC_EXTENSIONS
    assert ".kicad_sch" in SCHEMATIC_EXTENSIONS
    assert ".prjpcb" in SCHEMATIC_EXTENSIONS


def test_resolve_prjpcb_pcbdoc_subfolder_prefix(tmp_path: Path) -> None:
    """A PcbDoc listed under a PCB/ subfolder resolves relative to the project."""
    project = tmp_path / PIMX8_PRJPCB.name
    copy2(PIMX8_PRJPCB, project)
    pcb_dir = tmp_path / "PCB"
    pcb_dir.mkdir()
    copy2(PIMX8_PRJPCB.parent / "PCB/PiMX8MP_r0.3.PcbDoc", pcb_dir)

    pcbdoc = resolve_prjpcb_pcbdoc(project)
    assert pcbdoc.parent.name == "PCB"
    assert pcbdoc.name == "PiMX8MP_r0.3.PcbDoc"
    assert pcbdoc.is_file()


def test_real_pimx8_project_preserves_multiple_board_documents() -> None:
    with pytest.raises(ValueError, match="references multiple existing .PcbDoc files"):
        resolve_prjpcb_pcbdoc(PIMX8_PRJPCB)


def test_resolve_prjpcb_pcbdoc_windows_separators(tmp_path):
    """A DocumentPath with backslash subfolder separators resolves correctly."""
    prjpcb = tmp_path / "Board.PrjPcb"
    prjpcb.write_text("[Design]\nHierarchyMode=1\n[Document1]\nDocumentPath=PCB\\Board.PcbDoc\n")
    pcb_dir = tmp_path / "PCB"
    pcb_dir.mkdir()
    (pcb_dir / "Board.PcbDoc").write_text("")

    resolved = resolve_prjpcb_pcbdoc(prjpcb)
    assert resolved == pcb_dir / "Board.PcbDoc"
