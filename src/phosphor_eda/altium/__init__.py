"""Altium Designer schematic and PCB file parsers.

Schematic pipeline: OLE → raw dicts → typed records → net resolution → domain model.
PCB pipeline: OLE → binary/text streams → typed records → domain model.
"""

from phosphor_eda.altium.netlist import build_netlist
from phosphor_eda.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.altium.to_schematic import altium_to_design

__all__ = [
    "altium_to_design",
    "build_netlist",
    "parse_altium_pcb",
]
