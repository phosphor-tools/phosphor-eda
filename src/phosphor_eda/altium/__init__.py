"""Altium Designer schematic file parser."""

from phosphor_eda.altium.netlist import build_netlist
from phosphor_eda.altium.parser import parse_altium
from phosphor_eda.altium.to_schematic import altium_to_design
from phosphor_eda.models import ParsedDesign, SchematicPage

__all__ = [
    "ParsedDesign",
    "SchematicPage",
    "parse_altium",
    "build_netlist",
    "altium_to_design",
]
