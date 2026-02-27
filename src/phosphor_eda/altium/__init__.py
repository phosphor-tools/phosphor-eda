"""Altium Designer schematic file parser."""

from ecad_tools.altium.netlist import build_netlist
from ecad_tools.altium.parser import parse_altium
from ecad_tools.altium.to_schematic import altium_to_design
from ecad_tools.models import ParsedDesign, SchematicPage

__all__ = [
    "ParsedDesign",
    "SchematicPage",
    "parse_altium",
    "build_netlist",
    "altium_to_design",
]
