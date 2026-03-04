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
from ecad_tools.serialize import serialize_design
from ecad_tools.xlsx.extractor import convert as xlsx_convert


def _convert_altium(path: Path) -> str:
    raw = parse_altium(path)
    design = altium_to_design(raw, name=path.stem)
    return serialize_design(design)


def _convert_dsn(path: Path) -> str:
    raw = parse_dsn(path)
    design = dsn_to_design(raw, name=path.stem)
    return serialize_design(design)


def _convert_eagle(path: Path) -> str:
    design = eagle_to_design(path, name=path.stem)
    return serialize_design(design)


def _convert_kicad(path: Path) -> str:
    design = kicad_to_design(path, name=path.stem)
    return serialize_design(design)


_CONVERTERS: dict[str, Callable[[Path], str]] = {
    ".schdoc": _convert_altium,
    ".prjpcb": _convert_altium,
    ".dsn": _convert_dsn,
    ".kicad_sch": _convert_kicad,
    ".sch": _convert_eagle,
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
