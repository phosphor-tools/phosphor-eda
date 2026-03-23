"""Unified file conversion API.

Converts EE documents (schematics, PDFs, spreadsheets, etc.) to
LLM-friendly text, dispatching on file extension.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from phosphor_eda.altium.parser import parse_altium
from phosphor_eda.altium.project import parse_prjpcb_file
from phosphor_eda.altium.to_schematic import altium_to_design
from phosphor_eda.docx.extractor import convert as docx_convert
from phosphor_eda.dsn.parser import parse_dsn
from phosphor_eda.dsn.to_schematic import dsn_to_design
from phosphor_eda.eagle.to_schematic import eagle_to_design
from phosphor_eda.kicad.to_schematic import kicad_to_design
from phosphor_eda.pdf.extractor import convert as pdf_convert
from phosphor_eda.schematic import Design
from phosphor_eda.serialize import serialize_design
from phosphor_eda.xlsx.extractor import convert as xlsx_convert


def _load_altium(path: Path) -> Design:
    raw = parse_altium(path)
    return altium_to_design(raw, name=path.stem)


def _load_dsn(path: Path) -> Design:
    raw = parse_dsn(path)
    return dsn_to_design(raw, name=path.stem)


def _load_eagle(path: Path) -> Design:
    return eagle_to_design(path, name=path.stem)


def _load_kicad(path: Path) -> Design:
    return kicad_to_design(path, name=path.stem)


_DESIGN_LOADERS: dict[str, Callable[[Path], Design]] = {
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
    """Find a .PrjPcb in the same directory that references *schdoc*."""
    schdoc_resolved = schdoc.resolve()
    for child in schdoc.parent.iterdir():
        if not child.is_file() or child.suffix.lower() != ".prjpcb":
            continue
        project = parse_prjpcb_file(str(child))
        for rel_path in project.schematic_paths:
            if (child.parent / rel_path).resolve() == schdoc_resolved:
                return child
    return None


def _find_kicad_root(sch: Path) -> Path | None:
    """Find a sibling .kicad_sch that references *sch* as a child sheet."""
    target_name = sch.name
    for sibling in sch.parent.iterdir():
        if not sibling.is_file() or sibling.suffix.lower() != ".kicad_sch":
            continue
        if sibling.resolve() == sch.resolve():
            continue
        try:
            text = sibling.read_text()
        except OSError:
            continue
        if f'"Sheetfile" "{target_name}"' in text:
            return sibling
    return None


def load_design(path: Path) -> Design:
    """Parse a schematic file into a Design (no serialization)."""
    ext = path.suffix.lower()
    loader = _DESIGN_LOADERS.get(ext)
    if loader is None:
        raise ValueError(
            f"Unsupported schematic format: '{ext}'. "
            f"Supported: {', '.join(sorted(SCHEMATIC_EXTENSIONS))}"
        )
    return loader(path)


def _convert_schematic(path: Path) -> str:
    return serialize_design(load_design(path))


_CONVERTERS: dict[str, Callable[[Path], str]] = {
    **{ext: _convert_schematic for ext in _DESIGN_LOADERS},
    ".pdf": pdf_convert,
    ".docx": docx_convert,
    ".xlsx": xlsx_convert,
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_CONVERTERS)


def convert(path: Path) -> str:
    """Convert a single supported file to LLM-friendly text.

    Raises ValueError for unsupported file types.
    """
    ext = path.suffix.lower()
    converter = _CONVERTERS.get(ext)
    if converter is None:
        raise ValueError(
            f"Unsupported file type: '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return converter(path)


def convert_directory(directory: Path) -> dict[Path, str]:
    """Convert all supported files in a directory.

    Deduplicates Altium projects: when a .PrjPcb is found, its referenced
    .SchDoc files are not converted separately.

    Returns a mapping of input file path to converted text.
    """
    # Collect all supported files
    supported: list[Path] = []
    for child in sorted(directory.iterdir()):
        if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
            supported.append(child)

    # Find .PrjPcb files and mark their referenced .SchDoc as claimed
    claimed: set[Path] = set()
    for path in supported:
        if path.suffix.lower() == ".prjpcb":
            project = parse_prjpcb_file(str(path))
            for sch_rel in project.schematic_paths:
                claimed.add((path.parent / sch_rel).resolve())

    results: dict[Path, str] = {}
    for path in supported:
        if path.resolve() in claimed:
            continue
        results[path] = convert(path)

    return results
