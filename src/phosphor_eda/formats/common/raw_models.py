"""Data models for parsed schematic designs."""

from __future__ import annotations

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


@dataclass(kw_only=True)
class Wire:
    type_id: int = 0
    # Persistent wire dbid (allocated monotonically, survives saves). The
    # minimum dbid over a net's wire cluster is the number in Capture's
    # stored N##### autoname.
    db_id: int = 0
    wire_id: int = 0
    color: int = 0
    start_x: int = 0
    start_y: int = 0
    end_x: int = 0
    end_y: int = 0
    aliases: list[WireAlias] = field(default_factory=list)
    display_props: list[SymbolDisplayProp] = field(default_factory=list)
    is_bus: bool = False
    # All vertex coordinates along the wire, used for net resolution
    points: list[tuple[int, int]] = field(default_factory=list)


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
    # Arbitrary name-value properties from parsed binary data.
    props: dict[str, str] = field(default_factory=dict)
    props_list: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(kw_only=True)
class GraphicInst:
    """Base for Port, Global, OffPageConnector."""

    type_id: int = 0
    name: str = ""
    db_id: int = 0
    loc_x: int = 0
    loc_y: int = 0
    # Symbol bounding box (x1,y1)-(x2,y2); the electrical connection point
    # of a power symbol lies inside it, not at (loc_x, loc_y).
    bbox_x1: int = 0
    bbox_y1: int = 0
    bbox_x2: int = 0
    bbox_y2: int = 0
    display_props: list[SymbolDisplayProp] = field(default_factory=list)
    # Arbitrary name-value properties from parsed binary data.
    props: dict[str, str] = field(default_factory=dict)


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
class DsnNetBundleMember:
    """A named member from OrCAD Capture's NetBundleMapData stream."""

    name: str = ""
    wire_type: int = 0


@dataclass
class DsnNetBundleMap:
    """A net group from OrCAD Capture's NetBundleMapData stream."""

    name: str = ""
    members: list[DsnNetBundleMember] = field(default_factory=list)


@dataclass
class DsnBusEntry:
    """A page-level OrCAD bus-entry graphic."""

    color: int = 0
    start_x: int = 0
    start_y: int = 0
    end_x: int = 0
    end_y: int = 0


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
    bus_entries: list[DsnBusEntry] = field(default_factory=list)
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

    # OrCAD Capture NetBundleMapData stream: design-level net groups.
    net_bundle_maps: list[DsnNetBundleMap] = field(default_factory=list)


@dataclass
class NetlistEntry:
    """A component pin on a net."""

    reference: str = ""
    pin_number: str = ""
    pin_name: str = ""
    net_name: str = ""
