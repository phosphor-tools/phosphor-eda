"""Altium Designer schematic and PCB file parsers.

Schematic pipeline: OLE → raw dicts → typed records → net resolution → domain model.
PCB pipeline: OLE → binary/text streams → typed records → domain model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.pcb import Pcb
    from phosphor_eda.domain.schematic import Schematic
    from phosphor_eda.formats.common.diagnostics import ParseContext

__all__ = [
    "altium_to_design",
    "parse_altium_pcb",
]


def altium_to_design(path: Path, name: str = "") -> Schematic:
    """Convert an Altium .PrjPcb or single .SchDoc to a Schematic."""
    from phosphor_eda.formats.altium.to_schematic import altium_to_design as _altium_to_design

    return _altium_to_design(path, name)


def parse_altium_pcb(path: Path, ctx: ParseContext | None = None) -> Pcb:
    """Parse an Altium .PcbDoc file into the PCB domain model."""
    from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb as _parse_altium_pcb

    return _parse_altium_pcb(path, ctx)
