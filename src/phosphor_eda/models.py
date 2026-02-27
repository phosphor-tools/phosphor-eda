"""Data models for parsed schematic designs."""

from dataclasses import dataclass, field


@dataclass
class SymbolDisplayProp:
    name_idx: int = 0
    x: int = 0
    y: int = 0
    text_font_idx: int = 0
    rotation: int = 0
    color: int = 0


@dataclass
class WireAlias:
    x: int = 0
    y: int = 0
    color: int = 0
    rotation: int = 0
    font_idx: int = 0
    name: str = ""


@dataclass
class Wire:
    wire_id: int = 0
    color: int = 0
    start_x: int = 0
    start_y: int = 0
    end_x: int = 0
    end_y: int = 0
    aliases: list[WireAlias] = field(default_factory=list)
    display_props: list[SymbolDisplayProp] = field(default_factory=list)
    is_bus: bool = False


@dataclass
class PinConnection:
    """A component pin's net assignment."""

    pin_number: str = ""
    pin_x: int = 0
    pin_y: int = 0
    net_id: int = 0  # matches page net list IDs


@dataclass
class PlacedInstance:
    package_name: str = ""
    db_id: int = 0
    loc_x: int = 0
    loc_y: int = 0
    display_props: list[SymbolDisplayProp] = field(default_factory=list)
    reference: str = ""
    source_package: str = ""
    pin_connections: list[PinConnection] = field(default_factory=list)


@dataclass
class GraphicInst:
    """Base for Port, Global, OffPageConnector."""

    name: str = ""
    db_id: int = 0
    loc_x: int = 0
    loc_y: int = 0
    display_props: list[SymbolDisplayProp] = field(default_factory=list)


@dataclass
class NetIdMapping:
    db_id: int = 0
    name: str = ""


@dataclass
class PageNetEntry:
    """Net name + ID from the page-level net list."""

    name: str = ""
    net_id: int = 0


@dataclass
class SchematicPage:
    """A single schematic page within a design."""

    name: str = ""
    size: str = ""
    nets: list[PageNetEntry] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    instances: list[PlacedInstance] = field(default_factory=list)
    ports: list[GraphicInst] = field(default_factory=list)
    globals: list[GraphicInst] = field(default_factory=list)
    off_page_connectors: list[GraphicInst] = field(default_factory=list)
    # Internal: coordinate -> set of net_ids, used by build_netlist
    wire_net_map: dict[tuple[int, int], set[int]] = field(default_factory=dict)


@dataclass
class ParsedDesign:
    # Library data
    string_list: list[str] = field(default_factory=list)
    part_fields: list[str] = field(default_factory=list)

    # Pages
    pages: list[SchematicPage] = field(default_factory=list)

    # Hierarchy data
    net_id_mappings: list[NetIdMapping] = field(default_factory=list)

    # Cache data: symbol_name -> [pin_name_1, pin_name_2, ...]
    # Pin order matches T0x10 pin_number (1-indexed: pin_number=1 -> index 0)
    symbol_pin_names: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class NetlistEntry:
    """A component pin on a net."""

    reference: str = ""
    pin_number: str = ""
    pin_name: str = ""
    net_name: str = ""
