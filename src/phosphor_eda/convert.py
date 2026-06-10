"""Schematic loading and project detection.

Dispatches on file extension to parse schematics from Altium, KiCad,
OrCAD, and Eagle into a unified Schematic model.  Also provides
load_project() for loading a full Project (PCB, stackup, rules, etc.).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from phosphor_eda.formats.altium.pcb_parser import (
    parse_altium_classes,
    parse_altium_diff_pairs,
    parse_altium_pcb,
    parse_altium_rules,
    parse_altium_stackup,
    read_text_records,
)
from phosphor_eda.formats.altium.project import parse_prjpcb_file
from phosphor_eda.formats.altium.to_schematic import altium_to_design
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design
from phosphor_eda.formats.eagle.to_schematic import eagle_to_design
from phosphor_eda.formats.kicad.dru_parser import parse_kicad_dru
from phosphor_eda.formats.kicad.pcb_parser import (
    parse_kicad_pcb_from_sexpr,
    parse_kicad_stackup,
    read_kicad_pcb_sexpr,
)
from phosphor_eda.formats.kicad.pro_parser import parse_kicad_pro
from phosphor_eda.formats.kicad.to_schematic import kicad_to_design
from phosphor_eda.domain.project import Project
from phosphor_eda.serialize import serialize_design

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from phosphor_eda.domain.schematic import Schematic

# Matches "Sheetfile" "<value>" in KiCad S-expression text.
_SHEETFILE_RE = re.compile(r'"Sheetfile"\s+"([^"]+)"')


def _load_altium(path: Path) -> Schematic:
    return altium_to_design(path, name=path.stem)


def _load_dsn(path: Path) -> Schematic:
    ctx = ParseContext()
    raw = parse_dsn(path, ctx)
    return dsn_to_design(raw, name=path.stem, ctx=ctx)


def _load_eagle(path: Path) -> Schematic:
    return eagle_to_design(path, name=path.stem)


def _load_kicad(path: Path) -> Schematic:
    return kicad_to_design(path, name=path.stem)


_DESIGN_LOADERS: dict[str, Callable[[Path], Schematic]] = {
    ".schdoc": _load_altium,
    ".prjpcb": _load_altium,
    ".dsn": _load_dsn,
    ".kicad_sch": _load_kicad,
    ".sch": _load_eagle,
}

SCHEMATIC_EXTENSIONS: frozenset[str] = frozenset(_DESIGN_LOADERS)


def find_project_root(path: Path) -> Path | None:
    """If *path* is a sub-sheet, return the project root that contains it.

    For Altium .SchDoc files: searches the same directory for .PrjPcb files
    that reference this sheet.

    For KiCad .kicad_sch files: searches sibling .kicad_sch files for
    ``(sheet ... (property "Sheetfile" "<this_filename>"))`` references.

    Returns None if *path* is already a project root or no parent is found.
    """
    ext = path.suffix.lower()

    if ext == ".schdoc":
        return _find_altium_project(path)
    if ext == ".kicad_sch":
        return _find_kicad_root(path)
    return None


def _find_altium_project(schdoc: Path) -> Path | None:
    """Find a .PrjPcb that references *schdoc*.

    Searches the schdoc's own directory first, then walks up parent
    directories (up to 5 levels) to handle projects whose DocumentPath
    values place schematics in subdirectories.
    """
    schdoc_resolved = schdoc.resolve()
    search_dir = schdoc.parent.resolve()
    for _ in range(5):
        for child in search_dir.iterdir():
            if not child.is_file() or child.suffix.lower() != ".prjpcb":
                continue
            project = parse_prjpcb_file(str(child))
            for rel_path in project.schematic_paths:
                if (child.parent / rel_path.replace("\\", "/")).resolve() == schdoc_resolved:
                    return child
        parent = search_dir.parent
        if parent == search_dir:
            break  # filesystem root
        search_dir = parent
    return None


def _find_kicad_root(sch: Path) -> Path | None:
    """Find a .kicad_sch that references *sch* as a child sheet.

    Handles Sheetfile values that may use Windows backslash separators or
    include subdirectory prefixes (e.g. ``"sheets\\child.kicad_sch"``).
    Searches the child's own directory first, then walks up parent
    directories (up to 5 levels).
    """
    sch_resolved = sch.resolve()
    search_dir = sch.parent.resolve()
    for _ in range(5):
        for sibling in search_dir.iterdir():
            if not sibling.is_file() or sibling.suffix.lower() != ".kicad_sch":
                continue
            if sibling.resolve() == sch_resolved:
                continue
            try:
                text = sibling.read_text()
            except OSError:
                continue
            # Extract all Sheetfile values and resolve them relative to the
            # sibling's directory, normalizing Windows backslashes.
            for match in _SHEETFILE_RE.finditer(text):
                sheet_ref = match.group(1).replace("\\", "/")
                if (sibling.parent / sheet_ref).resolve() == sch_resolved:
                    return sibling
        parent = search_dir.parent
        if parent == search_dir:
            break  # filesystem root
        search_dir = parent
    return None


def load_design(path: Path) -> Schematic:
    """Parse a schematic file into a Schematic (no serialization)."""
    ext = path.suffix.lower()
    loader = _DESIGN_LOADERS.get(ext)
    if loader is None:
        supported = ", ".join(sorted(SCHEMATIC_EXTENSIONS))
        raise ValueError(f"Unsupported schematic format: '{ext}'. Supported: {supported}")
    return loader(path)


def convert(path: Path) -> str:
    """Parse a schematic and serialize to LLM-friendly text."""
    return serialize_design(load_design(path))


# ---------------------------------------------------------------------------
# Project-level loading
# ---------------------------------------------------------------------------

PCB_EXTENSIONS: frozenset[str] = frozenset({".kicad_pcb", ".pcbdoc"})


def load_project(path: Path) -> Project:
    """Load a complete project from any entry-point file.

    Discovers sibling files and assembles all available data into a Project.
    Supported entry points:
      - .kicad_pcb → look for .kicad_pro, .kicad_dru, .kicad_sch (same stem)
      - .kicad_pro → derive PCB/schematic/DRU paths from same stem
      - .kicad_sch → look for sibling .kicad_pcb, .kicad_pro, .kicad_dru
      - .PcbDoc   → parse PCB + enrichment streams (Rules6, Classes6, etc.)
      - .PrjPcb   → find referenced PcbDoc and SchDoc files
    """
    ext = path.suffix.lower()

    if ext in (".kicad_pcb", ".kicad_pro", ".kicad_sch", ".kicad_dru"):
        return _load_kicad_project(path)
    if ext == ".pcbdoc":
        return _load_altium_project_from_pcb(path)
    if ext == ".prjpcb":
        return _load_altium_project_from_prj(path)

    supported = ".kicad_pcb, .kicad_pro, .kicad_sch, .PcbDoc, .PrjPcb"
    raise ValueError(f"Unsupported project entry point: '{ext}'. Supported: {supported}")


def _load_kicad_project(entry: Path) -> Project:
    """Assemble a KiCad project from any entry file."""
    # Determine stem and directory
    stem = entry.stem
    parent = entry.parent

    # Resolve paths for all possible project files
    pcb_path = parent / f"{stem}.kicad_pcb"
    pro_path = parent / f"{stem}.kicad_pro"
    dru_path = parent / f"{stem}.kicad_dru"
    sch_path = parent / f"{stem}.kicad_sch"

    # Parse PCB and stackup from a single read of the .kicad_pcb file
    pcb = None
    stackup = None
    if pcb_path.exists():
        sexpr = read_kicad_pcb_sexpr(pcb_path)
        pcb = parse_kicad_pcb_from_sexpr(sexpr, default_name=stem)
        stackup = parse_kicad_stackup(sexpr)

    # Parse net classes from .kicad_pro
    net_classes = parse_kicad_pro(pro_path) if pro_path.exists() else []

    # Parse design rules from .kicad_dru
    design_rules = parse_kicad_dru(dru_path) if dru_path.exists() else []

    # Parse schematic
    schematic = kicad_to_design(sch_path, name=stem) if sch_path.exists() else None

    return Project(
        name=stem,
        schematic=schematic,
        pcb=pcb,
        stackup=stackup,
        net_classes=net_classes,
        design_rules=design_rules,
    )


def _load_altium_project_from_pcb(pcb_path: Path) -> Project:
    """Load an Altium project starting from a .PcbDoc file."""
    import olefile

    pcb = parse_altium_pcb(pcb_path)

    # Re-open for enrichment streams
    ole = olefile.OleFileIO(str(pcb_path))
    try:
        rules_data = ole.openstream("Rules6/Data").read() if ole.exists("Rules6/Data") else b""
        classes_data = (
            ole.openstream("Classes6/Data").read() if ole.exists("Classes6/Data") else b""
        )
        dp_data = (
            ole.openstream("DifferentialPairs6/Data").read()
            if ole.exists("DifferentialPairs6/Data")
            else b""
        )
        board_data = ole.openstream("Board6/Data").read() if ole.exists("Board6/Data") else b""
    finally:
        ole.close()

    # Parse enrichment data
    design_rules = parse_altium_rules(rules_data) if rules_data else []
    net_classes = parse_altium_classes(classes_data) if classes_data else []
    diff_pairs = parse_altium_diff_pairs(dp_data) if dp_data else []

    # Parse stackup from Board6
    stackup = None
    if board_data:
        records = read_text_records(board_data)
        if records:
            stackup = parse_altium_stackup(records[0])

    return Project(
        name=pcb_path.stem,
        pcb=pcb,
        stackup=stackup,
        net_classes=net_classes,
        design_rules=design_rules,
        diff_pairs=diff_pairs,
    )


def _load_altium_project_from_prj(prj_path: Path) -> Project:
    """Load an Altium project starting from a .PrjPcb file."""
    project_info = parse_prjpcb_file(str(prj_path))

    # Find and parse PCB
    pcb_project = None
    for pcb_rel in project_info.pcb_paths:
        pcb_abs = prj_path.parent / pcb_rel.replace("\\", "/")
        if pcb_abs.exists():
            pcb_project = _load_altium_project_from_pcb(pcb_abs)
            break

    # Parse schematic if available
    schematic = None
    if project_info.schematic_paths:
        schematic = altium_to_design(prj_path, name=prj_path.stem)

    if pcb_project:
        pcb_project.schematic = schematic
        pcb_project.name = prj_path.stem
        return pcb_project

    return Project(
        name=prj_path.stem,
        schematic=schematic,
    )
