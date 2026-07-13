"""Tests for Windows path handling in project file parsers.

Altium .PrjPcb and KiCad .kicad_sch files may store relative paths with
Windows backslash separators. The parsers must normalize these to work
correctly on all platforms, and warn when referenced files are missing.
"""

import textwrap
from pathlib import Path

import pytest

from phosphor_eda.formats.altium.project import parse_prjpcb
from phosphor_eda.formats.altium.to_schematic import altium_to_design
from phosphor_eda.formats.kicad.to_schematic import kicad_to_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"


# ---------------------------------------------------------------------------
# Altium: backslash normalization in project paths
# ---------------------------------------------------------------------------


def test_prjpcb_preserves_raw_paths():
    """parse_prjpcb stores paths as-is — normalization happens at load time."""
    content = textwrap.dedent("""\
        [Design]
        HierarchyMode=1

        [Document1]
        DocumentPath=sheets\\Main.SchDoc

        [Document2]
        DocumentPath=sheets\\Power.SchDoc
    """)
    project = parse_prjpcb(content)
    assert project.schematic_paths == ["sheets\\Main.SchDoc", "sheets\\Power.SchDoc"]


def test_altium_loads_schdoc_with_backslash_paths(tmp_path: Path):
    """altium_to_design resolves backslash paths from .PrjPcb to real files."""
    sub = tmp_path / "sheets"
    sub.mkdir()

    # Minimal valid OLE SchDoc — use a real fixture's bytes.
    real_schdoc = UPSTREAM_FIXTURES / "qfsae-pcb/Debugger/TOP.SchDoc"
    schdoc_bytes = real_schdoc.read_bytes()

    (sub / "Main.SchDoc").write_bytes(schdoc_bytes)

    prjpcb = tmp_path / "Test.PrjPcb"
    prjpcb.write_text(
        "[Design]\nHierarchyMode=1\n\n[Document1]\nDocumentPath=sheets\\Main.SchDoc\n"
    )

    design = altium_to_design(prjpcb)
    assert len(design.pages) == 1


def test_altium_warns_on_missing_schdoc(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """A missing sheet records a ParseContext warning (not a stdout/stderr print)."""
    from phosphor_eda.formats.altium.to_schematic import load_project_sheets
    from phosphor_eda.formats.common.diagnostics import ParseContext

    prjpcb = tmp_path / "Test.PrjPcb"
    prjpcb.write_text("[Design]\nHierarchyMode=1\n\n[Document1]\nDocumentPath=Missing.SchDoc\n")

    ctx = ParseContext()
    sheets = load_project_sheets(prjpcb, ctx=ctx)
    assert sheets == {}
    assert any("Missing.SchDoc" in issue.message for issue in ctx.issues)

    # Library code must not print; only the CLI surfaces warnings.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    # The count is surfaced in design metadata for the CLI to report.
    design = altium_to_design(prjpcb)
    assert len(design.pages) == 0
    assert design.metadata.get("parse_issue_count") == "1"


def test_altium_warns_on_missing_backslash_schdoc(tmp_path: Path) -> None:
    """The recorded warning includes the original path when a backslash path is missing."""
    from phosphor_eda.formats.altium.to_schematic import load_project_sheets
    from phosphor_eda.formats.common.diagnostics import ParseContext

    prjpcb = tmp_path / "Test.PrjPcb"
    prjpcb.write_text(
        "[Design]\nHierarchyMode=1\n\n[Document1]\nDocumentPath=sub\\Missing.SchDoc\n"
    )

    ctx = ParseContext()
    sheets = load_project_sheets(prjpcb, ctx=ctx)
    assert sheets == {}
    assert any("Missing.SchDoc" in issue.message for issue in ctx.issues)


# ---------------------------------------------------------------------------
# KiCad: backslash normalization in sheet file references
# ---------------------------------------------------------------------------


def test_kicad_loads_child_sheet_with_backslash_path(tmp_path: Path):
    """kicad_to_design resolves Sheetfile values that use backslashes."""
    sub = tmp_path / "sheets"
    sub.mkdir()

    # Minimal child .kicad_sch with a single resistor.
    # Pin S-expressions are long but must stay on one line for sexpdata.
    pin1 = (  # noqa: E501
        "(pin passive (at 0 3.81 270) (length 1.27)"
        ' (name "~" (effects (font (size 1.27 1.27))))'
        ' (number "1" (effects (font (size 1.27 1.27)))))'
    )
    pin2 = (
        "(pin passive (at 0 -3.81 90) (length 1.27)"
        ' (name "~" (effects (font (size 1.27 1.27))))'
        ' (number "2" (effects (font (size 1.27 1.27)))))'
    )
    child_content = textwrap.dedent(f"""\
        (kicad_sch (version 20230121) (generator eeschema)
          (lib_symbols
            (symbol "Device:R"
              (pin_names (offset 0)) (in_bom yes) (on_board yes)
              (symbol "R_0_1"
                (rectangle (start -1.016 -2.54) (end 1.016 2.54)
                  (stroke (width 0) (type default))
                  (fill (type none))))
              (symbol "R_1_1"
                {pin1}
                {pin2})))
          (symbol (lib_id "Device:R") (at 100 100 0) (unit 1)
            (in_bom yes) (on_board yes)
            (property "Reference" "R1" (at 101 99 0)
              (effects (font (size 1.27 1.27))))
            (property "Value" "10k" (at 101 101 0)
              (effects (font (size 1.27 1.27))))
            (pin "1" (uuid "pin1-uuid"))
            (pin "2" (uuid "pin2-uuid"))))
    """)
    (sub / "child.kicad_sch").write_text(child_content)

    # Root references child via backslash path
    root_content = textwrap.dedent("""\
        (kicad_sch (version 20230121) (generator eeschema)
          (lib_symbols)
          (sheet (at 50 50) (size 20 20)
            (property "Sheetname" "ChildSheet")
            (property "Sheetfile" "sheets\\child.kicad_sch")))
    """)
    root_file = tmp_path / "root.kicad_sch"
    root_file.write_text(root_content)

    design = kicad_to_design(root_file)
    page_names = {p.name for p in design.pages}
    assert "ChildSheet" in page_names


def test_kicad_warns_on_missing_child_sheet(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """A missing child sheet surfaces a parse-issue count, with no library print."""
    root_content = textwrap.dedent("""\
        (kicad_sch (version 20230121) (generator eeschema)
          (lib_symbols)
          (sheet (at 50 50) (size 20 20)
            (property "Sheetname" "Missing")
            (property "Sheetfile" "nonexistent.kicad_sch")))
    """)
    root_file = tmp_path / "root.kicad_sch"
    root_file.write_text(root_content)

    design = kicad_to_design(root_file)
    # The root page still exists, but no child page
    assert len(design.pages) == 1
    assert design.metadata.get("parse_issue_count") == "1"

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_kicad_warns_on_missing_backslash_sheet(tmp_path: Path) -> None:
    """A missing backslash sheet ref still surfaces a parse-issue count."""
    root_content = textwrap.dedent("""\
        (kicad_sch (version 20230121) (generator eeschema)
          (lib_symbols)
          (sheet (at 50 50) (size 20 20)
            (property "Sheetname" "Missing")
            (property "Sheetfile" "sub\\gone.kicad_sch")))
    """)
    root_file = tmp_path / "root.kicad_sch"
    root_file.write_text(root_content)

    design = kicad_to_design(root_file)
    assert len(design.pages) == 1
    assert design.metadata.get("parse_issue_count") == "1"
