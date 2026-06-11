from pathlib import Path

import pytest

from phosphor_eda.query.convert import (
    SCHEMATIC_EXTENSIONS,
    convert,
    find_project_root,
    resolve_prjpcb_pcbdoc,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DSN_FILE = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PIMX8_PRJPCB = FIXTURES / "altium/pi-mx8/PiMX8MP_r0.3_release.PrjPcb"


def test_convert_dsn():
    text = convert(DSN_FILE)
    assert "DESIGN SUMMARY" in text
    assert "COMPONENTS" in text
    assert "NETS" in text


def test_convert_unsupported(tmp_path):
    bad = tmp_path / "test.xyz"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="Unsupported schematic format"):
        convert(bad)


def test_schematic_extensions():
    assert ".dsn" in SCHEMATIC_EXTENSIONS
    assert ".sch" in SCHEMATIC_EXTENSIONS
    assert ".schdoc" in SCHEMATIC_EXTENSIONS
    assert ".kicad_sch" in SCHEMATIC_EXTENSIONS
    assert ".prjpcb" in SCHEMATIC_EXTENSIONS


def test_resolve_prjpcb_pcbdoc_subfolder_prefix():
    """A PcbDoc listed under a PCB/ subfolder resolves relative to the project."""
    pcbdoc = resolve_prjpcb_pcbdoc(PIMX8_PRJPCB)
    assert pcbdoc.parent.name == "PCB"
    assert pcbdoc.name == "PiMX8MP_r0.3.PcbDoc"
    assert pcbdoc.is_file()


def test_resolve_prjpcb_pcbdoc_windows_separators(tmp_path):
    """A DocumentPath with backslash subfolder separators resolves correctly."""
    prjpcb = tmp_path / "Board.PrjPcb"
    prjpcb.write_text("[Design]\nHierarchyMode=1\n[Document1]\nDocumentPath=PCB\\Board.PcbDoc\n")
    pcb_dir = tmp_path / "PCB"
    pcb_dir.mkdir()
    (pcb_dir / "Board.PcbDoc").write_text("")

    resolved = resolve_prjpcb_pcbdoc(prjpcb)
    assert resolved == pcb_dir / "Board.PcbDoc"


def test_find_project_root_case_insensitive_prjpcb(tmp_path):
    """find_project_root detects .PRJPCB regardless of case."""
    prjpcb = tmp_path / "Board.PRJPCB"
    prjpcb.write_text("[Design]\nHierarchyMode=1\n[Document1]\nDocumentPath=Sheet1.SchDoc\n")
    schdoc = tmp_path / "Sheet1.SchDoc"
    schdoc.write_text("")

    root = find_project_root(schdoc)
    assert root is not None
    assert root.name == "Board.PRJPCB"


def test_find_project_root_case_insensitive_kicad(tmp_path):
    """find_project_root detects .KICAD_SCH parent regardless of case."""
    parent = tmp_path / "root.KICAD_SCH"
    parent.write_text('(kicad_sch (sheet (property "Sheetfile" "child.kicad_sch")))')
    child = tmp_path / "child.kicad_sch"
    child.write_text("(kicad_sch)")

    root = find_project_root(child)
    assert root is not None
    assert root.name == "root.KICAD_SCH"
