"""Unified file conversion API.

Converts EE documents (schematics, PDFs, spreadsheets, etc.) to
LLM-friendly text, dispatching on file extension.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ecad_tools.altium.parser import parse_altium
from ecad_tools.altium.project import parse_prjpcb_file
from ecad_tools.altium.to_schematic import altium_to_design
from ecad_tools.docx.extractor import convert as docx_convert
from ecad_tools.dsn.parser import parse_dsn
from ecad_tools.dsn.to_schematic import dsn_to_design
from ecad_tools.eagle.to_schematic import eagle_to_design
from ecad_tools.kicad.to_schematic import kicad_to_design
from ecad_tools.pdf.extractor import convert as pdf_convert
from ecad_tools.schematic import Design
from ecad_tools.serialize import serialize_design
from ecad_tools.xlsx.extractor import convert as xlsx_convert


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
