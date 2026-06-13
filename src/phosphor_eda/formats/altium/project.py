"""Parser for Altium .PrjPcb project files (INI format)."""

import configparser
from dataclasses import dataclass, field
from enum import IntEnum


class AltiumHierarchyMode(IntEnum):
    """Altium project net identifier scope mode."""

    SMART = 0
    FLAT = 1
    HIERARCHICAL_POWER_GLOBAL = 2
    GLOBAL = 3
    HIERARCHICAL_POWER_LOCAL = 4


@dataclass
class AltiumProject:
    """Parsed Altium project file."""

    hierarchy_mode: AltiumHierarchyMode = AltiumHierarchyMode.FLAT
    allow_port_net_names: bool = False
    allow_sheet_entry_net_names: bool = True
    append_sheet_number_to_local_nets: bool = False
    name_nets_hierarchically: bool = False
    netlist_single_pin_nets: bool = False
    power_port_names_take_priority: bool = False
    schematic_paths: list[str] = field(default_factory=list)
    pcb_paths: list[str] = field(default_factory=list)


def _get_hierarchy_mode(parser: configparser.ConfigParser) -> AltiumHierarchyMode:
    raw_value = parser.get("Design", "HierarchyMode", fallback=str(AltiumHierarchyMode.FLAT.value))
    try:
        value = int(raw_value)
        return AltiumHierarchyMode(value)
    except ValueError as exc:
        raise ValueError(f"HierarchyMode has unknown value {raw_value}") from exc


def parse_prjpcb(content: str) -> AltiumProject:
    """Parse a .PrjPcb file's text content into an AltiumProject."""
    parser = configparser.ConfigParser(strict=False)
    content = content.lstrip("\ufeff")
    parser.read_string(content)

    project = AltiumProject()

    if parser.has_section("Design"):
        project.hierarchy_mode = _get_hierarchy_mode(parser)
        project.allow_port_net_names = parser.getboolean(
            "Design",
            "AllowPortNetNames",
            fallback=project.allow_port_net_names,
        )
        project.allow_sheet_entry_net_names = parser.getboolean(
            "Design",
            "AllowSheetEntryNetNames",
            fallback=project.allow_sheet_entry_net_names,
        )
        project.append_sheet_number_to_local_nets = parser.getboolean(
            "Design",
            "AppendSheetNumberToLocalNets",
            fallback=project.append_sheet_number_to_local_nets,
        )
        project.name_nets_hierarchically = parser.getboolean(
            "Design",
            "NameNetsHierarchically",
            fallback=project.name_nets_hierarchically,
        )
        project.netlist_single_pin_nets = parser.getboolean(
            "Design",
            "NetlistSinglePinNets",
            fallback=project.netlist_single_pin_nets,
        )
        project.power_port_names_take_priority = parser.getboolean(
            "Design",
            "PowerPortNamesTakePriority",
            fallback=project.power_port_names_take_priority,
        )

    sections: list[str] = parser.sections()
    for section in sections:
        if section.startswith("Document"):
            doc_path = parser.get(section, "DocumentPath", fallback="")
            lower = doc_path.lower()
            if lower.endswith(".schdoc"):
                project.schematic_paths.append(doc_path)
            elif lower.endswith(".pcbdoc"):
                project.pcb_paths.append(doc_path)

    return project


def parse_prjpcb_file(path: str) -> AltiumProject:
    """Read and parse a .PrjPcb file from disk."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return parse_prjpcb(f.read())
    except UnicodeDecodeError:
        with open(path, encoding="latin-1") as f:
            return parse_prjpcb(f.read())
