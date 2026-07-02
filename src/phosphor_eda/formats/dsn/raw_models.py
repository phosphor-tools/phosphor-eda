"""Data models for parsed schematic designs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DsnResolutionKind = Literal["unresolved", "hierarchy_occurrence"]
DsnMarkerCategory = Literal["erc", "erc_physical"]


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
    """A component pin's net assignment.

    ``pin_order`` is the 1-based display pin order decoded from the T0x10 int16
    field; ``has_no_connect_marker`` is that field's sign bit — a user-placed
    no-connect X. ``pin_number`` is the public designator string derived from
    ``pin_order``.
    """

    pin_number: str = ""
    pin_order: int = 0
    has_no_connect_marker: bool = False
    package_pin_number: str = ""
    pin_x: int = 0
    pin_y: int = 0
    net_id: int = 0  # matches page net list IDs
    no_connect: bool = False
    no_connect_metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # The parser sets pin_order explicitly; callers that construct a pin
        # from only a designator get the order derived for free so package
        # evidence keeps working.
        if not self.pin_order and self.pin_number.isdigit():
            self.pin_order = int(self.pin_number)


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
    # Project/package context can override Cache display-order pin names with
    # primitive PIN_NUMBER mappings from Cadence packaged netlists.
    pin_name_overrides: dict[str, str] = field(default_factory=dict)


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
class DsnBlockSheetPin:
    """A sheet pin on a hierarchical block instance (0x0c DrawnInst).

    Decoded from the block's embedded LibraryPart-shaped struct; ``port_type``
    gives the pin direction (see ``ORCAD_PORT_TYPES``).
    """

    name: str = ""
    x: int = 0
    y: int = 0
    port_type: int = 0
    port_type_name: str = ""


@dataclass
class DsnBlockPinBinding:
    """A T0x10 record binding a block sheet pin to a parent-page net."""

    pin_order: int = 0
    pin_x: int = 0
    pin_y: int = 0
    net_id: int = 0


@dataclass
class DsnBlockInstance:
    """A hierarchical block placement (0x0c DrawnInst) on a schematic page.

    ``db_id`` joins the Hierarchy stream's child-schematic edge; ``reference``
    is the block instance label (CONTROLS, CH1, …); ``sheet_pins`` are the
    block's ports and ``net_bindings`` bind each sheet pin (by pin order) to a
    parent-page net id.
    """

    db_id: int = 0
    reference: str = ""
    loc_x: int = 0
    loc_y: int = 0
    sheet_pins: list[DsnBlockSheetPin] = field(default_factory=list)
    net_bindings: list[DsnBlockPinBinding] = field(default_factory=list)


@dataclass
class NetIdMapping:
    db_id: int = 0
    name: str = ""


@dataclass
class DsnHierarchyOccurrence:
    """Raw OrCAD hierarchy occurrence linked to a placed instance."""

    occurrence_id: int = 0
    instance_db_id: int = 0


@dataclass
class DsnLibraryHeader:
    """Header fields from OrCAD Capture's Library stream."""

    intro: str = ""
    version_major: int = 0
    version_minor: int = 0
    created_timestamp: int = 0
    modified_timestamp: int = 0


@dataclass
class DsnPackageLibraryPart:
    """Raw OrCAD package library-part reference."""

    name: str = ""
    source_library: str = ""


@dataclass
class DsnPackagePartCell:
    """Raw OrCAD package part-cell record from a Packages/* stream."""

    ref: str = ""
    name: str = ""
    normal_name: str = ""
    convert_name: str = ""
    library_parts: list[DsnPackageLibraryPart] = field(default_factory=list)


@dataclass
class DsnPackageDevicePin:
    """Raw OrCAD package device pin mapping."""

    order: int = 0
    package_pin: str = ""
    ignored: bool = False
    group: str = ""


@dataclass
class DsnPackageDevice:
    """Raw OrCAD package device section."""

    unit_ref: str = ""
    refdes_suffix: str = ""
    pins: list[DsnPackageDevicePin] = field(default_factory=list)


@dataclass
class DsnPackage:
    """Raw OrCAD Packages/* stream inventory."""

    stream_path: str = ""
    name: str = ""
    source_library: str = ""
    refdes_prefix: str = ""
    unknown_name: str = ""
    pcb_footprint: str = ""
    part_cells: list[DsnPackagePartCell] = field(default_factory=list)
    library_parts: list[DsnPackageLibraryPart] = field(default_factory=list)
    devices: list[DsnPackageDevice] = field(default_factory=list)


@dataclass
class DsnLibraryPackageInventory:
    """Summary of a raw OrCAD library package stream."""

    stream_path: str = ""
    name: str = ""
    source_package_names: list[str] = field(default_factory=list)
    source_library_references: list[str] = field(default_factory=list)
    pcb_footprint: str = ""
    device_count: int = 0
    pin_count: int = 0


@dataclass
class DsnLibraryInventory:
    """Raw OrCAD DSN/OLB library and design-cache inventory."""

    path: str = ""
    library_header: DsnLibraryHeader | None = None
    string_list: list[str] = field(default_factory=list)
    part_fields: list[str] = field(default_factory=list)
    packages: dict[str, DsnPackage] = field(default_factory=dict)
    package_inventory: list[DsnLibraryPackageInventory] = field(default_factory=list)
    cache_part_names: list[str] = field(default_factory=list)
    cache_pin_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class DsnView:
    """Raw OrCAD Capture schematic view metadata."""

    name: str = ""
    page_names: list[str] = field(default_factory=list)
    hierarchy_stream_paths: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class DsnCisVariantName:
    """Raw OrCAD CIS variant name in stream order."""

    stream_path: str = ""
    order: int = 0
    duplicate_index: int = 0
    name: str = ""


@dataclass
class DsnCisStringList:
    """Raw OrCAD CIS string-list stream."""

    stream_path: str = ""
    values: list[str] = field(default_factory=list)


@dataclass
class DsnCisBomEntry:
    """Raw OrCAD CIS BOMPartData ID with best-effort resolution."""

    stream_path: str = ""
    row_order: int = 0
    raw_id: int = 0
    resolved_instance_db_id: int | None = None
    resolution_kind: DsnResolutionKind = "unresolved"
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class DsnCisBom:
    """Raw OrCAD CIS BOM stream and child rows."""

    name: str = ""
    stream_path: str = ""
    child_string_lists: list[DsnCisStringList] = field(default_factory=list)
    entries: list[DsnCisBomEntry] = field(default_factory=list)


@dataclass
class DsnCisGroupMember:
    """Raw OrCAD CIS group membership row."""

    stream_path: str = ""
    row_order: int = 0
    state: str = ""
    occurrence_id: int | None = None
    resolved_instance_db_id: int | None = None
    resolution_kind: DsnResolutionKind = "unresolved"
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class DsnCisUpdateStorageRow:
    """Raw OrCAD CIS update-storage row."""

    stream_path: str = ""
    row_order: int = 0
    occurrence_id: int | None = None
    resolved_instance_db_id: int | None = None
    resolution_kind: DsnResolutionKind = "unresolved"
    columns: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class DsnCisGroup:
    """Raw OrCAD CIS group definition plus membership/update rows."""

    name: str = ""
    stream_path: str = ""
    row_order: int = 0
    raw_fields: list[str] = field(default_factory=list)
    members: list[DsnCisGroupMember] = field(default_factory=list)
    update_storage_rows: list[DsnCisUpdateStorageRow] = field(default_factory=list)


@dataclass
class DsnCisRawStream:
    """Raw OrCAD CIS stream preserved for unsupported children."""

    stream_path: str = ""
    size: int = 0
    reason: str = ""


@dataclass
class DsnCisVariantStore:
    """Raw OrCAD CIS VariantStore evidence."""

    present: bool = False
    placeholder: bool = False
    variant_names: list[DsnCisVariantName] = field(default_factory=list)
    # BOMDataStream field list (declared count + BOM names): stream-level
    # evidence recorded once, not duplicated onto every DsnCisBom.
    bom_raw_fields: list[str] = field(default_factory=list)
    boms: list[DsnCisBom] = field(default_factory=list)
    groups: list[DsnCisGroup] = field(default_factory=list)
    unknown_streams: list[DsnCisRawStream] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


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


@dataclass(frozen=True)
class DsnSymbolPin:
    """Structured pin definition from an OrCAD design-cache symbol."""

    name: str = ""
    structure_type: int = 0
    start_x: int = 0
    start_y: int = 0
    hotpt_x: int = 0
    hotpt_y: int = 0
    pin_shape: int = 0
    port_type: int = 0
    port_type_name: str = ""
    display_prop_count: int = 0

    @property
    def start(self) -> tuple[int, int]:
        return (self.start_x, self.start_y)

    @property
    def hotpt(self) -> tuple[int, int]:
        return (self.hotpt_x, self.hotpt_y)


@dataclass(frozen=True)
class DsnCacheSymbols:
    """Parsed design-cache symbol pin names plus structured pin records."""

    pin_names: dict[str, list[str]]
    pins: dict[str, list[DsnSymbolPin]]


@dataclass
class DsnBusEntry:
    """A page-level OrCAD bus-entry graphic."""

    color: int = 0
    start_x: int = 0
    start_y: int = 0
    end_x: int = 0
    end_y: int = 0


@dataclass
class DsnErcSymbol:
    """Raw OrCAD ERC marker symbol catalog entry."""

    stream_path: str = ""
    type_id: int = 0
    name: str = ""
    source_library: str = ""
    marker_category: DsnMarkerCategory = "erc"
    color: int = 0
    primitive_count: int = 0
    raw_payload: bytes = b""


@dataclass
class DsnErcObject:
    """Raw OrCAD page-tail ERC object instance.

    ``message``/``subject``/``detail`` are the three diagnostic strings the
    record stores (OOCP ``s0``/``s1``/``s2``): the ERC rule message, the net
    it fired on, and the ``"SCHEMATIC1, <page> (x_mm, y_mm)"`` anchor.
    """

    page_name: str = ""
    type_id: int = 0
    symbol_name: str = ""
    db_id: int = 0
    loc_x: int = 0
    loc_y: int = 0
    bbox_x1: int = 0
    bbox_y1: int = 0
    bbox_x2: int = 0
    bbox_y2: int = 0
    color: int = 0
    unknown_flag: int = 0
    message: str = ""
    subject: str = ""
    detail: str = ""


@dataclass
class DsnNoConnectPin:
    """Raw OrCAD packaged-netlist NC pseudo-net member."""

    source_path: str = ""
    refdes: str = ""
    pin_token: str = ""
    pin_name: str = ""
    raw_net_name: str = ""
    matched_pin_id: str = ""


@dataclass
class SchematicPage:
    """A single schematic page within a design."""

    name: str = ""
    view_name: str = ""
    stream_path: str = ""
    size: str = ""
    nets: list[PageNetEntry] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    instances: list[PlacedInstance] = field(default_factory=list)
    block_instances: list[DsnBlockInstance] = field(default_factory=list)
    ports: list[GraphicInst] = field(default_factory=list)
    globals: list[GraphicInst] = field(default_factory=list)
    off_page_connectors: list[GraphicInst] = field(default_factory=list)
    bus_entries: list[DsnBusEntry] = field(default_factory=list)
    erc_objects: list[DsnErcObject] = field(default_factory=list)
    # Internal: coordinate -> set of net_ids, used by build_netlist
    wire_net_map: dict[tuple[int, int], set[int]] = field(default_factory=dict)


@dataclass
class ParsedDesign:
    # Library data
    library_header: DsnLibraryHeader | None = None
    string_list: list[str] = field(default_factory=list)
    part_fields: list[str] = field(default_factory=list)

    # Pages
    pages: list[SchematicPage] = field(default_factory=list)
    views: list[DsnView] = field(default_factory=list)

    # Hierarchy data
    net_id_mappings: list[NetIdMapping] = field(default_factory=list)
    hierarchy_occurrences: list[DsnHierarchyOccurrence] = field(default_factory=list)

    # Raw OrCAD Capture Packages/* streams, keyed by OLE stream path.
    packages: dict[str, DsnPackage] = field(default_factory=dict)

    # Cache data: symbol_name -> [pin_name_1, pin_name_2, ...]
    # Pin order matches T0x10 pin_number (1-indexed: pin_number=1 -> index 0)
    symbol_pin_names: dict[str, list[str]] = field(default_factory=dict)
    symbol_pins: dict[str, list[DsnSymbolPin]] = field(default_factory=dict)

    # OrCAD Capture NetBundleMapData stream: design-level net groups.
    net_bundle_maps: list[DsnNetBundleMap] = field(default_factory=list)

    # Raw OrCAD Capture CIS VariantStore evidence. Public project variants are
    # mapped only by later slices after raw row semantics are fixture-locked.
    cis_variant_store: DsnCisVariantStore = field(default_factory=DsnCisVariantStore)

    # OrCAD Capture Symbols/ERC* marker catalog entries.
    erc_symbols: list[DsnErcSymbol] = field(default_factory=list)

    # OrCAD packaged-netlist NC pseudo-net members from pstxnet.dat sidecars.
    no_connect_pins: list[DsnNoConnectPin] = field(default_factory=list)


@dataclass
class NetlistEntry:
    """A component pin on a net."""

    reference: str = ""
    pin_number: str = ""
    pin_name: str = ""
    net_name: str = ""
