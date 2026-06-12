"""Schematic loading and project detection.

Dispatches on file extension to parse schematics from Altium, KiCad,
OrCAD, and Eagle into a unified Schematic model.  Also provides
load_project() for loading a full Project (PCB, stackup, rules, etc.).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from phosphor_eda.domain.project import Project
from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.formats.altium.pcb_project import load_altium_enrichment
from phosphor_eda.formats.altium.project import parse_prjpcb_file
from phosphor_eda.formats.altium.to_schematic import altium_to_design
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design
from phosphor_eda.formats.eagle.to_schematic import eagle_to_design
from phosphor_eda.formats.kicad.board import parse_kicad_pcb
from phosphor_eda.formats.kicad.dru_parser import parse_kicad_dru
from phosphor_eda.formats.kicad.pro_parser import parse_kicad_pro
from phosphor_eda.formats.kicad.to_schematic import kicad_to_design
from phosphor_eda.query.format import serialize_design

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.schematic import Schematic

# Matches "Sheetfile" "<value>" in KiCad S-expression text.
_SHEETFILE_RE = re.compile(r'"Sheetfile"\s+"([^"]+)"')

# Walk-up search depth for project-root detection.
_PROJECT_SEARCH_MAX_LEVELS = 5


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


def _walk_up_for(
    start: Path,
    predicate: Callable[[Path], Path | None],
    *,
    max_levels: int = _PROJECT_SEARCH_MAX_LEVELS,
) -> Path | None:
    """Walk up from *start* applying *predicate* to each directory.

    *predicate* inspects one directory and returns the matched project root
    (or ``None`` to keep searching). Stops at the filesystem root or after
    *max_levels* directories.
    """
    search_dir = start.resolve()
    for _ in range(max_levels):
        result = predicate(search_dir)
        if result is not None:
            return result
        parent = search_dir.parent
        if parent == search_dir:
            break  # filesystem root
        search_dir = parent
    return None


def _find_altium_project(schdoc: Path) -> Path | None:
    """Find a .PrjPcb that references *schdoc*.

    Searches the schdoc's own directory first, then walks up parent
    directories to handle projects whose DocumentPath values place
    schematics in subdirectories.
    """
    schdoc_resolved = schdoc.resolve()

    def find_in(search_dir: Path) -> Path | None:
        for child in search_dir.iterdir():
            if not child.is_file() or child.suffix.lower() != ".prjpcb":
                continue
            project = parse_prjpcb_file(str(child))
            for rel_path in project.schematic_paths:
                if (child.parent / rel_path.replace("\\", "/")).resolve() == schdoc_resolved:
                    return child
        return None

    return _walk_up_for(schdoc.parent, find_in)


def _find_kicad_root(sch: Path) -> Path | None:
    """Find a .kicad_sch that references *sch* as a child sheet.

    Handles Sheetfile values that may use Windows backslash separators or
    include subdirectory prefixes (e.g. ``"sheets\\child.kicad_sch"``).
    Searches the child's own directory first, then walks up parent
    directories.
    """
    sch_resolved = sch.resolve()

    def find_in(search_dir: Path) -> Path | None:
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
        return None

    return _walk_up_for(sch.parent, find_in)


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
# PCB board loading
# ---------------------------------------------------------------------------


def _load_kicad_pcb(path: Path) -> Board:
    return parse_kicad_pcb(path)


def _load_altium_pcb(path: Path) -> Board:
    return parse_altium_pcb(path)


def _load_prjpcb(path: Path) -> Board:
    return parse_altium_pcb(resolve_prjpcb_pcbdoc(path))


_PCB_LOADERS: dict[str, Callable[[Path], Board]] = {
    ".kicad_pcb": _load_kicad_pcb,
    ".pcbdoc": _load_altium_pcb,
    ".prjpcb": _load_prjpcb,
}

PCB_EXTENSIONS: frozenset[str] = frozenset(_PCB_LOADERS)


def load_pcb(path: Path) -> Board:
    """Parse a PCB layout file into a Board board.

    Dispatches on extension; ``.prjpcb`` resolves to its referenced
    ``.PcbDoc`` first. Raises ``ValueError`` for unsupported formats.
    """
    ext = path.suffix.lower()
    loader = _PCB_LOADERS.get(ext)
    if loader is None:
        supported = ", ".join(sorted(PCB_EXTENSIONS))
        raise ValueError(f"Unsupported PCB format: '{path.suffix}'. Supported: {supported}")
    return loader(path)


def resolve_prjpcb_pcbdoc(prj_path: Path) -> Path:
    """Resolve a .PrjPcb to exactly one existing referenced .PcbDoc."""
    project = parse_prjpcb_file(str(prj_path))
    existing_pcbdocs: list[Path] = []
    seen_resolved: set[Path] = set()

    for pcb_rel in project.pcb_paths:
        pcb_path = prj_path.parent / pcb_rel.replace("\\", "/")
        if not pcb_path.exists():
            continue
        resolved = pcb_path.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        existing_pcbdocs.append(pcb_path)

    if not existing_pcbdocs:
        raise ValueError(
            f"{prj_path.name} does not reference an existing .PcbDoc. "
            "Pass a .PcbDoc directly or update the project DocumentPath."
        )
    if len(existing_pcbdocs) > 1:
        boards = ", ".join(str(path) for path in existing_pcbdocs)
        raise ValueError(
            f"{prj_path.name} references multiple existing .PcbDoc files: {boards}. "
            "Pass the intended .PcbDoc directly."
        )
    return existing_pcbdocs[0]


# ---------------------------------------------------------------------------
# Project-level loading
# ---------------------------------------------------------------------------


def load_schematic(path: Path) -> Schematic | None:
    """Load just the schematic associated with a project entry point.

    Returns ``None`` when no schematic is discoverable: a KiCad entry with no
    sibling ``.kicad_sch`` of the same stem, a ``.PrjPcb`` listing no
    schematic documents, or a bare ``.PcbDoc`` (which carries no schematic
    reference). Cheaper than :func:`load_project` — the PCB is not parsed.
    """
    ext = path.suffix.lower()
    if ext in (".kicad_pcb", ".kicad_pro", ".kicad_sch", ".kicad_dru"):
        sch_path = path.parent / f"{path.stem}.kicad_sch"
        return kicad_to_design(sch_path, name=path.stem) if sch_path.exists() else None
    if ext == ".prjpcb":
        project_info = parse_prjpcb_file(str(path))
        if project_info.schematic_paths:
            return altium_to_design(path, name=path.stem)
        return None
    return None


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
        project = _load_kicad_project(path)
    elif ext == ".pcbdoc":
        project = _load_altium_project_from_pcb(path)
    elif ext == ".prjpcb":
        project = _load_altium_project_from_prj(path)
    else:
        supported = ".kicad_pcb, .kicad_pro, .kicad_sch, .PcbDoc, .PrjPcb"
        raise ValueError(f"Unsupported project entry point: '{ext}'. Supported: {supported}")

    _fill_metadata_from_title_block(project)
    return project


def _fill_metadata_from_title_block(project: Project) -> None:
    """Fill empty ProjectMetadata fields from the root page's title block.

    The root page is the shallowest scope; project files rarely carry
    name/revision/date themselves, the title block is where designers put
    them.
    """
    schematic = project.schematic
    if schematic is None or not schematic.pages:
        return
    root_page = min(schematic.pages, key=lambda page: len(page.scope_id.path))
    block = root_page.title_block
    if block is None:
        return
    metadata = project.metadata
    metadata.name = metadata.name or block.title
    metadata.revision = metadata.revision or block.revision
    metadata.date = metadata.date or block.date
    metadata.organization = metadata.organization or block.company
    metadata.author = metadata.author or block.metadata.get("Author", "")


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

    # Parse PCB (the board parse attaches the stackup)
    board = parse_kicad_pcb(pcb_path) if pcb_path.exists() else None

    # Parse net classes from .kicad_pro
    net_classes = parse_kicad_pro(pro_path) if pro_path.exists() else []

    # Parse design rules from .kicad_dru
    design_rules = parse_kicad_dru(dru_path) if dru_path.exists() else []

    # Parse schematic
    schematic = kicad_to_design(sch_path, name=stem) if sch_path.exists() else None

    return Project(
        name=stem,
        schematic=schematic,
        boards=[board] if board else [],
        net_classes=net_classes,
        design_rules=design_rules,
    )


def _load_altium_project_from_pcb(pcb_path: Path) -> Project:
    """Load an Altium project starting from a .PcbDoc file."""
    ctx = ParseContext()
    board = parse_altium_pcb(pcb_path, ctx)
    enrichment = load_altium_enrichment(pcb_path, ctx)

    return Project(
        name=pcb_path.stem,
        boards=[board],
        net_classes=enrichment.net_classes,
        design_rules=enrichment.design_rules,
        diff_pairs=enrichment.diff_pairs,
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
