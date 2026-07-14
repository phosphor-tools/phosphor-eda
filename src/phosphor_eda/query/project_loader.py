"""Schematic, PCB, and project loading.

This module owns public loader dispatch. Format-specific project assembly lives
with the corresponding parser package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import PcbBuildError
from phosphor_eda.domain.project import DocumentKind
from phosphor_eda.domain.variant_materializer import materialize_project_variant
from phosphor_eda.formats.allegro import load_allegro_pcb_project, parse_allegro_pcb
from phosphor_eda.formats.allegro.errors import AllegroParseError
from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.formats.altium.project_loader import (
    load_altium_project,
    resolve_prjpcb_pcbdoc,
)
from phosphor_eda.formats.altium.to_schematic import altium_to_design
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.project import load_orcad_project
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design
from phosphor_eda.formats.eagle.to_schematic import eagle_to_design
from phosphor_eda.formats.kicad.board import parse_kicad_pcb
from phosphor_eda.formats.kicad.project import load_kicad_project
from phosphor_eda.formats.kicad.to_schematic import kicad_to_design

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.project import Project, ProjectDocument
    from phosphor_eda.domain.schematic import Schematic


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


def load_design(path: Path) -> Schematic:
    """Parse a schematic file into a Schematic."""
    ext = path.suffix.lower()
    loader = _DESIGN_LOADERS.get(ext)
    if loader is None:
        supported = ", ".join(sorted(SCHEMATIC_EXTENSIONS))
        raise ValueError(f"Unsupported schematic format: '{ext}'. Supported: {supported}")
    return loader(path)


def _load_kicad_pcb(path: Path) -> Board:
    return parse_kicad_pcb(path)


def _load_altium_pcb(path: Path) -> Board:
    return parse_altium_pcb(path)


def _load_allegro_pcb(path: Path) -> Board:
    return parse_allegro_pcb(path)


def _load_prjpcb(path: Path) -> Board:
    return parse_altium_pcb(resolve_prjpcb_pcbdoc(path))


@dataclass(frozen=True)
class _PcbBackend:
    """A PCB layout backend: its loader and its format name."""

    loader: Callable[[Path], Board]
    format: str


_PCB_BACKENDS: dict[str, _PcbBackend] = {
    ".brd": _PcbBackend(_load_allegro_pcb, "allegro"),
    ".kicad_pcb": _PcbBackend(_load_kicad_pcb, "kicad"),
    ".pcbdoc": _PcbBackend(_load_altium_pcb, "altium"),
    ".prjpcb": _PcbBackend(_load_prjpcb, "altium"),
}

PCB_EXTENSIONS: frozenset[str] = frozenset(_PCB_BACKENDS)
PROJECT_EXTENSIONS: frozenset[str] = frozenset({".kicad_pro", ".prjpcb", ".opj"})


def _pcb_backend_for(ext: str) -> _PcbBackend:
    backend = _PCB_BACKENDS.get(ext)
    if backend is None:
        supported = ", ".join(sorted(PCB_EXTENSIONS))
        raise ValueError(f"Unsupported PCB format: '{ext}'. Supported: {supported}")
    return backend


def load_pcb(path: Path) -> Board:
    """Parse a PCB layout file into a Board."""
    return _pcb_backend_for(path.suffix.lower()).loader(path)


def pcb_format_for(ext: str) -> str:
    """Return the PCB backend format name for a lowercased file extension."""
    return _pcb_backend_for(ext).format


def load_project(
    path: Path,
    *,
    variant_name: str | None = None,
    base_variant: bool = False,
) -> Project:
    """Load a complete project from a project manifest file."""
    ext = path.suffix.lower()

    if ext == ".kicad_pro":
        project = load_kicad_project(path)
    elif ext == ".prjpcb":
        project = load_altium_project(path)
    elif ext == ".opj":
        project = load_orcad_project(path)
        _attach_orcad_boards(project)
    else:
        supported = ", ".join(sorted(PROJECT_EXTENSIONS))
        raise ValueError(
            f"project file required: '{path.suffix}' is not a project entry point. "
            f"Supported: {supported}"
        )

    _fill_metadata_from_title_block(project)
    materialize_project_variant(project, variant_name=variant_name, base_variant=base_variant)
    return project


def _attach_orcad_boards(project: Project) -> None:
    """Load paired Allegro boards referenced by an OrCAD project manifest.

    OrCAD projects pair an OrCAD schematic with an Allegro ``.brd`` layout of a
    different format. This composition lives above both backends; parse failures
    stay per-document diagnostics rather than discarding the schematic project.
    """
    loaded_paths: set[str] = set()
    for doc in project.documents:
        if doc.kind is not DocumentKind.PCB:
            continue
        resolved_path = doc.metadata.get("resolved_path")
        if not resolved_path:
            _record_board_document_error(
                doc,
                "board path is not local to the OPJ project",
                category="board_path",
            )
            continue
        if not doc.exists:
            _record_board_document_error(
                doc,
                f"missing board file: {resolved_path}",
                category="missing_board",
            )
            continue
        if resolved_path in loaded_paths:
            doc.parsed = True
            continue
        try:
            board_project = load_allegro_pcb_project(resolved_path)
        except (AllegroParseError, OSError, PcbBuildError) as exc:
            _record_board_document_error(doc, exc, category=_board_error_category(exc))
            continue
        loaded_paths.add(resolved_path)
        doc.parsed = True
        project.boards.extend(board_project.boards)
        project.net_classes.extend(board_project.net_classes)
        project.design_rules.extend(board_project.design_rules)
        project.diff_pairs.extend(board_project.diff_pairs)


def _record_board_document_error(
    doc: ProjectDocument,
    error: Exception | str,
    *,
    category: str,
) -> None:
    message = str(error)
    doc.metadata["parse_error"] = message
    doc.metadata["parse_error_category"] = category
    if isinstance(error, AllegroParseError):
        if error.offset is not None:
            doc.metadata["parse_error_offset"] = str(error.offset)
        doc.metadata["parse_error_code"] = error.code
    doc.metadata["parse_issue_count"] = "1"


def _board_error_category(error: Exception) -> str:
    if isinstance(error, AllegroParseError):
        return "allegro_parse"
    if isinstance(error, PcbBuildError):
        return "pcb_build"
    return "board_io"


def _fill_metadata_from_title_block(project: Project) -> None:
    """Fill empty ProjectMetadata fields from the root page's title block."""
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
    metadata.organization = metadata.organization or block.organization
    metadata.author = metadata.author or block.author or block.metadata.get("Author", "")
