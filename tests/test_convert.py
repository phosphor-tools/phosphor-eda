from pathlib import Path

import pytest

from phosphor_eda.convert import (
    SUPPORTED_EXTENSIONS,
    convert,
    convert_directory,
    find_project_root,
)

DSN_FILE = Path("raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN")
PDF_FILE = Path("raspberry-pi-pico/RPI-PICO-R3-PUBLIC-SCHEMATIC.pdf")


def test_convert_dsn():
    text = convert(DSN_FILE)
    assert "DESIGN SUMMARY" in text
    assert "COMPONENTS" in text
    assert "NETS" in text


def test_convert_pdf():
    text = convert(PDF_FILE)
    assert "PAGE 1" in text


def test_convert_unsupported(tmp_path):
    bad = tmp_path / "test.xyz"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="Unsupported file type"):
        convert(bad)


def test_supported_extensions():
    assert ".dsn" in SUPPORTED_EXTENSIONS
    assert ".pdf" in SUPPORTED_EXTENSIONS
    assert ".sch" in SUPPORTED_EXTENSIONS
    assert ".schdoc" in SUPPORTED_EXTENSIONS
    assert ".kicad_sch" in SUPPORTED_EXTENSIONS


def test_convert_directory(tmp_path):
    # Create a directory with a PDF to convert
    pdf_src = PDF_FILE.resolve()
    if not pdf_src.exists():
        pytest.skip("PDF fixture not available")
    link = tmp_path / "test.pdf"
    link.symlink_to(pdf_src)

    results = convert_directory(tmp_path)
    assert len(results) == 1
    path, text = next(iter(results.items()))
    assert path.suffix == ".pdf"
    assert "PAGE 1" in text


def test_convert_directory_empty(tmp_path):
    results = convert_directory(tmp_path)
    assert results == {}


def test_find_project_root_case_insensitive_prjpcb(tmp_path):
    """find_project_root detects .PRJPCB regardless of case."""
    # Create a .PRJPCB (uppercase) that references a .SchDoc
    prjpcb = tmp_path / "Board.PRJPCB"
    prjpcb.write_text(
        "[Design]\n"
        "HierarchyMode=1\n"
        "[Document1]\n"
        "DocumentPath=Sheet1.SchDoc\n"
    )
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
