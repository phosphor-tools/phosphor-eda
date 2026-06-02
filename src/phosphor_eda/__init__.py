"""phosphor-eda: Query electronic schematics as structured data."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.project import Project
    from phosphor_eda.schematic import Schematic

__all__ = ["PCB_EXTENSIONS", "SCHEMATIC_EXTENSIONS", "convert", "load_design", "load_project"]
__version__ = "0.1.0"

PCB_EXTENSIONS: frozenset[str] = frozenset({".kicad_pcb", ".pcbdoc"})
SCHEMATIC_EXTENSIONS: frozenset[str] = frozenset(
    {".schdoc", ".prjpcb", ".dsn", ".kicad_sch", ".sch"},
)


def convert(path: Path) -> str:
    """Parse a schematic and serialize to LLM-friendly text."""
    from phosphor_eda.convert import convert as convert_impl

    return convert_impl(path)


def load_design(path: Path) -> Schematic:
    """Parse a schematic file into a Schematic."""
    from phosphor_eda.convert import load_design as load_design_impl

    return load_design_impl(path)


def load_project(path: Path) -> Project:
    """Load a complete schematic/PCB project."""
    from phosphor_eda.convert import load_project as load_project_impl

    return load_project_impl(path)
