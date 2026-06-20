"""Native Cadence Allegro / OrCAD PCB parser and oracle helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.formats.allegro.parser import parse_allegro_pcb as _parse_allegro_pcb
from phosphor_eda.formats.allegro.project_loader import (
    load_allegro_pcb_project as _load_allegro_pcb_project,
)

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.project import Project
    from phosphor_eda.formats.common.diagnostics import ParseContext

__all__ = [
    "load_allegro_pcb_project",
    "parse_allegro_pcb",
]


def parse_allegro_pcb(path: Path, ctx: ParseContext | None = None) -> Board:
    """Parse a native Allegro/OrCAD ``.brd`` file into the PCB domain model."""
    return _parse_allegro_pcb(path, ctx)


def load_allegro_pcb_project(path: str | Path) -> Project:
    """Load board-side Allegro project enrichment."""
    return _load_allegro_pcb_project(path)
