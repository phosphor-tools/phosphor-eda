from __future__ import annotations

from typing import TYPE_CHECKING

from fixture_paths import FIXTURES, UPSTREAM_FIXTURES

from phosphor_eda.domain.project import DocumentKind
from phosphor_eda.query.project_loader import load_project

if TYPE_CHECKING:
    from pathlib import Path

DSN_FILE = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
OPENCELLULAR_BREAKOUT = UPSTREAM_FIXTURES / "opencellular/electronics/breakout"
OPENCELLULAR_BREAKOUT_BOARD = OPENCELLULAR_BREAKOUT / "board/OC_CONNECT-1_BREAKOUT_LIFE-3.brd"


def test_orcad_project_loads_resolved_allegro_board_document(tmp_path: Path) -> None:
    """Proves OPJ loading populates Project.boards from resolved .brd documents.

    The OPJ manifest proves board document parsing once the path resolves
    locally. It cannot prove later package symbol sidecar enrichment.
    """
    opj = _write_opj(tmp_path / "resolved-board.opj", board_path=str(OPENCELLULAR_BREAKOUT_BOARD))

    project = load_project(opj)

    assert len(project.boards) == 1
    board = project.boards[0]
    assert board.name == OPENCELLULAR_BREAKOUT_BOARD.stem
    assert board.metadata.source_format == "allegro"
    assert board.board_profile is not None
    assert board.footprints

    pcb_docs = [doc for doc in project.documents if doc.kind is DocumentKind.PCB]
    assert len(pcb_docs) == 1
    assert pcb_docs[0].exists
    assert pcb_docs[0].parsed
    assert pcb_docs[0].metadata["resolved_path"] == str(OPENCELLULAR_BREAKOUT_BOARD)
    assert "parse_error" not in pcb_docs[0].metadata


def test_orcad_project_preserves_schematic_when_board_file_is_missing(tmp_path: Path) -> None:
    """Proves missing board files stay manifest diagnostics, not fatal errors."""
    opj = _write_opj(tmp_path / "missing-board.opj", board_path="allegro\\missing.brd")

    project = load_project(opj)

    assert project.schematic is not None
    assert project.boards == []
    pcb_doc = next(doc for doc in project.documents if doc.kind is DocumentKind.PCB)
    assert not pcb_doc.exists
    assert not pcb_doc.parsed
    assert "missing" in pcb_doc.metadata["parse_error"].lower()


def test_orcad_project_preserves_schematic_when_board_path_is_non_local(
    tmp_path: Path,
) -> None:
    """Proves Windows-absolute board paths stay manifest diagnostics."""
    opj = _write_opj(tmp_path / "non-local-board.opj", board_path=r"C:\boards\missing.brd")

    project = load_project(opj)

    assert project.schematic is not None
    assert project.boards == []
    pcb_doc = next(doc for doc in project.documents if doc.kind is DocumentKind.PCB)
    assert not pcb_doc.exists
    assert not pcb_doc.parsed
    assert pcb_doc.metadata["parse_error"] == "board path is not local to the OPJ project"


def test_orcad_project_preserves_schematic_when_board_parse_fails(tmp_path: Path) -> None:
    """Proves corrupt/unsupported boards do not discard the schematic project."""
    board = tmp_path / "bad.brd"
    board.write_bytes(b"not an allegro board")
    opj = _write_opj(tmp_path / "bad-board.opj", board_path=board.name)

    project = load_project(opj)

    assert project.schematic is not None
    assert project.boards == []
    pcb_doc = next(doc for doc in project.documents if doc.kind is DocumentKind.PCB)
    assert pcb_doc.exists
    assert not pcb_doc.parsed
    assert pcb_doc.metadata["parse_error"]


def test_orcad_project_records_schematic_parse_failure_as_issue_metadata(tmp_path: Path) -> None:
    """Proves malformed DSN loads surface structured parse issue metadata."""
    dsn = tmp_path / "bad.DSN"
    dsn.write_bytes(b"not a dsn")
    opj = _write_opj_with_schematic(tmp_path / "bad-dsn.opj", schematic_path=dsn.name)

    project = load_project(opj)

    assert project.schematic is None
    schematic_doc = next(doc for doc in project.documents if doc.kind is DocumentKind.SCHEMATIC)
    assert schematic_doc.exists
    assert not schematic_doc.parsed
    assert schematic_doc.metadata["parse_error"]
    assert schematic_doc.metadata["parse_error_category"] == "dsn_format"
    assert schematic_doc.metadata["parse_issue_count"] == "1"


def test_orcad_project_deduplicates_duplicate_resolved_board_documents(tmp_path: Path) -> None:
    """Proves duplicate board manifest entries do not duplicate Project.boards."""
    opj = _write_opj_with_duplicate_board_files(tmp_path / "duplicate-board.opj")

    project = load_project(opj)

    pcb_docs = [doc for doc in project.documents if doc.kind is DocumentKind.PCB]
    assert len(pcb_docs) == 2
    assert len(project.boards) == 1
    assert len(project.net_classes) == 8
    assert len(project.design_rules) == 4
    assert len(project.diff_pairs) == 1
    assert all(doc.parsed for doc in pcb_docs)
    assert all(
        doc.metadata["resolved_path"] == str(OPENCELLULAR_BREAKOUT_BOARD) for doc in pcb_docs
    )


def _write_opj(path: Path, *, board_path: str) -> Path:
    dsn_path = DSN_FILE
    relative_dsn = (
        dsn_path.relative_to(path.parent).as_posix()
        if dsn_path.is_relative_to(path.parent)
        else dsn_path.as_posix()
    )
    path.write_text(
        f"""(ExpressProject "Board Project"
  (ProjectVersion "19981106")
  (ProjectType "PCB")
  (Folder "Design Resources"
    (File "{relative_dsn}"
      (Type "Schematic Design"))
    ("Allegro Netlist Output Board File" "{board_path}")))
""",
        encoding="utf-8",
    )
    return path


def _write_opj_with_schematic(path: Path, *, schematic_path: str) -> Path:
    path.write_text(
        f"""(ExpressProject "Board Project"
  (ProjectVersion "19981106")
  (ProjectType "PCB")
  (Folder "Design Resources"
    (File "{schematic_path}"
      (Type "Schematic Design")))
)
""",
        encoding="utf-8",
    )
    return path


def _write_opj_with_duplicate_board_files(path: Path) -> Path:
    dsn_path = DSN_FILE
    relative_dsn = (
        dsn_path.relative_to(path.parent).as_posix()
        if dsn_path.is_relative_to(path.parent)
        else dsn_path.as_posix()
    )
    path.write_text(
        f"""(ExpressProject "Duplicate Board Project"
  (ProjectVersion "19981106")
  (ProjectType "PCB")
  (Folder "Design Resources"
    (File "{relative_dsn}"
      (Type "Schematic Design"))
    (File "{OPENCELLULAR_BREAKOUT_BOARD}"
      (Type "PCB Design"))
    (File "{OPENCELLULAR_BREAKOUT_BOARD}"
      (Type "PCB Design"))))
""",
        encoding="utf-8",
    )
    return path
