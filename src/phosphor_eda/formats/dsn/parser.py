"""Parser for OrCAD Capture .DSN files.

Extracts component information, net names, and connectivity from the
binary OLE compound document format used by OrCAD Capture.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import olefile

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.raw_models import (
    DsnBusEntry,
    DsnErcObject,
    DsnErcSymbol,
    DsnHierarchyOccurrence,
    DsnLibraryHeader,
    DsnLibraryInventory,
    DsnLibraryPackageInventory,
    DsnNetBundleMap,
    DsnNetBundleMember,
    DsnPackage,
    DsnPackageDevice,
    DsnPackageDevicePin,
    DsnPackageLibraryPart,
    DsnPackagePartCell,
    DsnSymbolPin,
    GraphicInst,
    NetIdMapping,
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    SchematicPage,
    Wire,
    WireAlias,
)
from phosphor_eda.formats.dsn.binary_reader import (
    PAGE_SETTINGS_SIZE,
    PREAMBLE,
    STRUCT_BUS_ENTRY,
    STRUCT_DEVICE,
    STRUCT_ERC_OBJECT,
    STRUCT_ERC_SYMBOL,
    STRUCT_GLOBAL,
    STRUCT_LIBRARY_PART,
    STRUCT_NET_GROUP,
    STRUCT_OFF_PAGE_CONNECTOR,
    STRUCT_PACKAGE,
    STRUCT_PART_CELL,
    STRUCT_PORT,
    STRUCT_WIRE_BUS,
    STRUCT_WIRE_SCALAR,
    BinaryReader,
)
from phosphor_eda.formats.dsn.cis import parse_cis_variant_store
from phosphor_eda.formats.dsn.errors import DsnFormatError
from phosphor_eda.formats.dsn.pins import ORCAD_PORT_TYPES
from phosphor_eda.formats.dsn.views import (
    parse_view_schematic,
    view_name_from_path,
    warn_repeated_sheet_identity,
)


@dataclass
class RawTitleBlock:
    """A page title block: symbol name plus its name/value properties.

    The Title/Doc/RevCode/OrgName values live in the record's prefix-chain
    name/value pairs, exactly like ``PlacedInstance.props``.
    """

    name: str = ""
    props: dict[str, str] = field(default_factory=dict)


@dataclass
class DsnSchematicPage(SchematicPage):
    """DSN page raw model extended with title block records."""

    title_blocks: list[RawTitleBlock] = field(default_factory=list)


class DsnPackageStreamError(ValueError):
    """A non-fatal Packages/* layout error with the most useful byte offset."""

    def __init__(self, offset: int, message: str) -> None:
        super().__init__(message)
        self.offset = offset


@dataclass(frozen=True)
class DsnCacheSymbols:
    """Parsed design-cache symbol pin names plus structured pin records."""

    pin_names: dict[str, list[str]]
    pins: dict[str, list[DsnSymbolPin]]


# --- Structure parsers ---


def skip_structure(r: BinaryReader) -> int:
    """Skip a structure using prefix chain end_offset.

    Returns the type_id of the skipped structure.
    """
    type_id, end_offset, _ = r.read_prefix_chain()
    if end_offset > 0:
        r.pos = end_offset
    else:
        r.try_read_preamble()
    return type_id


def skip_self_describing(r: BinaryReader) -> int:
    """Skip a self-describing structure: [type:1][body_len:4][zero:4][body].

    T0x34, T0x35, and similar structures use this format.
    body_len is the byte count of the body AFTER the 9-byte header.

    The declared length comes straight from the stream; older (PSD-era)
    files carry layouts this parser misreads, producing absurd lengths.
    Validate against the bytes that actually remain so those files fail
    with a typed, located error instead of an IndexError far away.
    """
    offset = r.pos
    type_id = r.read_uint8()
    body_len = r.read_uint32()
    r.skip(4)  # zero padding
    remaining = len(r.data) - r.pos
    if body_len > remaining:
        msg = (
            f"self-describing structure 0x{type_id:02x} at offset {offset} declares a "
            f"{body_len}-byte body but only {remaining} bytes remain in the stream"
        )
        raise DsnFormatError(msg, offset=offset, type_id=type_id)
    r.skip(body_len)  # skip the body
    return type_id


# Net labels placed on a wire never exceed a handful; a larger count means
# the wire body layout differs from the StructWire layout we know.
_MAX_WIRE_ALIASES = 64
_MAX_NET_BUNDLE_GROUPS = 4096
_MAX_NET_BUNDLE_MEMBERS = 4096
_MAX_PACKAGE_PART_CELLS = 4096
_MAX_PACKAGE_LIBRARY_PARTS = 4096
_MAX_PACKAGE_DEVICES = 4096
_MAX_PACKAGE_DEVICE_PINS = 4096
_MAX_ERC_OBJECTS = 4096
_MAX_ERC_DISPLAY_PROPS = 4096
_MAX_SYMBOL_PIN_DISPLAY_PROPS = 64


def _parse_wire_aliases(
    r: BinaryReader, wire_end_offset: int, ctx: ParseContext | None
) -> list[WireAlias]:
    """Parse the StructAlias (net label) records inside a wire body.

    Layout per OpenOrCadParser ``StructWire.cpp``: after the end coordinates
    come one unknown byte, a uint16 alias count, then one StructAlias per
    label (prefix chain, preamble, locX/locY/color/rotation/fontIdx, name).
    Versions whose wire body differs fail the bounds checks and are recorded
    as diagnostics; the wire itself stays (the caller re-anchors at the
    wire's end offset).
    """
    r.skip(1)
    num_aliases = r.read_uint16()
    if num_aliases == 0:
        return []
    if num_aliases > _MAX_WIRE_ALIASES:
        msg = f"implausible wire alias count {num_aliases}; wire body layout unknown"
        raise ValueError(msg)
    aliases: list[WireAlias] = []
    for _ in range(num_aliases):
        _tid, alias_end, _pairs = r.read_prefix_chain()
        r.try_read_preamble()
        alias = WireAlias()
        alias.x = r.read_int32()
        alias.y = r.read_int32()
        alias.color = r.read_uint32()
        alias.rotation = r.read_uint32()
        alias.font_idx = r.read_uint32()
        alias.name = r.read_string_len_zero()
        if wire_end_offset > 0 and r.pos > wire_end_offset:
            msg = "wire alias overruns the wire body"
            raise ValueError(msg)
        aliases.append(alias)
        if alias_end > 0:
            r.pos = alias_end
    return aliases


def skip_counted_self_describing(r: BinaryReader) -> int:
    """Read a uint16 count, then skip that many self-describing structures."""
    count = r.read_uint16()
    for _ in range(count):
        skip_self_describing(r)
    return count


def _warn(ctx: ParseContext | None, category: str, message: str) -> None:
    if ctx is not None:
        ctx.warn(category, message)


def _props_from_pairs(
    pairs: list[tuple[int, int]],
    string_list: list[str],
) -> dict[str, str]:
    return dict(_prop_entries_from_pairs(pairs, string_list))


def _prop_entries_from_pairs(
    pairs: list[tuple[int, int]],
    string_list: list[str],
) -> tuple[tuple[str, str], ...]:
    entries: list[tuple[str, str]] = []
    for name_idx, value_idx in pairs:
        name = string_list[name_idx] if 0 <= name_idx < len(string_list) else f"idx:{name_idx}"
        value = string_list[value_idx] if 0 <= value_idx < len(string_list) else f"idx:{value_idx}"
        entries.append((name, value))
    return tuple(entries)


def _parse_graphic_inst(
    r: BinaryReader,
    string_list: list[str],
    pairs: list[tuple[int, int]],
    type_id: int,
    *,
    has_name_indices: bool,
) -> GraphicInst:
    """Parse the shared StructGraphicInst body used by known graphic objects."""
    gi = GraphicInst(type_id=type_id)
    props = _props_from_pairs(pairs, string_list)

    if has_name_indices:
        net_name_idx = r.read_uint32()
        r.skip(4)  # source library path index
        if 0 <= net_name_idx < len(string_list):
            props["_net_name"] = string_list[net_name_idx]

    gi.name = r.read_string_len_zero()
    gi.db_id = r.read_uint32()

    # Read coordinates and color per StructGraphicInst.
    gi.loc_y = r.read_int16()
    gi.loc_x = r.read_int16()
    gi.bbox_y2 = r.read_int16()
    gi.bbox_x2 = r.read_int16()
    gi.bbox_x1 = r.read_int16()
    gi.bbox_y1 = r.read_int16()
    r.skip(1)  # color
    r.skip(3)  # 3 unknown bytes

    num_display_props = r.read_uint16()
    for _ in range(num_display_props):
        skip_structure(r)

    gi.props = props
    return gi


def _parse_title_blocks(
    r: BinaryReader, string_list: list[str], ctx: ParseContext | None
) -> list[RawTitleBlock]:
    """Parse the page's StructTitleBlock records (uint16 count, then records).

    Each record is a StructGraphicInst (per OpenOrCadParser
    ``StructTitleBlock.cpp``): the field values (Title, Doc, RevCode,
    OrgName, …) ride in the prefix-chain name/value pairs, and the body
    starts with 8 unknown bytes followed by the title block symbol name.
    The record's end offset bounds the body decode, so a layout mismatch
    is recorded as a diagnostic without losing stream position.
    """
    count = r.read_uint16()
    title_blocks: list[RawTitleBlock] = []
    for _ in range(count):
        _type_id, end_offset, pairs = r.read_prefix_chain()
        block = RawTitleBlock(props=_props_from_pairs(pairs, string_list))
        if end_offset > 0:
            try:
                r.try_read_preamble()
                r.skip(8)  # unknown bytes before the name, per StructGraphicInst
                block.name = r.read_string_len_zero()
            except (struct.error, IndexError, ValueError) as e:
                if ctx is not None:
                    ctx.warn("dsn_title_block", f"Title block parse error: {e}")
            r.pos = end_offset
        else:
            r.try_read_preamble()
        title_blocks.append(block)
    return title_blocks


# --- Stream parsers ---


def parse_library(data: bytes) -> tuple[DsnLibraryHeader, list[str], list[str]]:
    """Parse the Library stream. Returns (header, string_list, part_fields)."""
    r = BinaryReader(data, "Library")

    # Introduction (32-byte padded string)
    intro_start = r.pos
    intro = r.read_string_zero().strip()
    r.pos = intro_start + 32  # pad to 32 bytes

    # Version
    version_major = r.read_uint16()
    version_minor = r.read_uint16()

    # Timestamps (uint32 + uint32 = 8 bytes)
    created_timestamp = r.read_uint32()
    modified_timestamp = r.read_uint32()
    header = DsnLibraryHeader(
        intro=intro,
        version_major=version_major,
        version_minor=version_minor,
        created_timestamp=created_timestamp,
        modified_timestamp=modified_timestamp,
    )

    # Zero padding (4 bytes)
    r.skip(4)

    # Text fonts
    text_font_len = r.read_uint16()
    for _ in range(text_font_len - 1):
        r.skip(60)  # LOGFONTA structure

    # someLen array
    some_len = r.read_uint16()
    r.skip(some_len * 2)  # uint16 array

    # Unknown data
    r.skip(4)  # unknown_2_0
    r.skip(4)  # unknown_2_1

    # Part field mapping (8 strings)
    part_fields: list[str] = []
    for _ in range(8):
        part_fields.append(r.read_string_len_zero())

    # Page settings (fixed 156 bytes)
    r.skip(PAGE_SETTINGS_SIZE)

    # String list - determine length based on version
    # Version A uses uint16, others use uint32
    str_lst_len = r.read_uint32()
    if str_lst_len > 100000:
        # Probably was uint16, rewind and read as uint16
        r.pos -= 4
        str_lst_len = r.read_uint16()

    string_list: list[str] = []
    for _ in range(str_lst_len):
        string_list.append(r.read_string_len_zero())

    return header, string_list, part_fields


def _package_parse_error(
    ctx: ParseContext | None,
    stream_path: str,
    offset: int,
    message: str,
) -> None:
    _warn(ctx, "dsn_package_stream", f"{stream_path} at byte offset {offset}: {message}")


def _require_structure_type(type_id: int, expected: int, offset: int) -> None:
    if type_id != expected:
        msg = f"expected structure 0x{expected:02x}, got 0x{type_id:02x}"
        raise DsnPackageStreamError(offset, msg)


def _finish_parsed_package_structure(
    r: BinaryReader,
    end_offset: int,
    structure_name: str,
) -> None:
    if end_offset <= 0:
        return
    if r.pos != end_offset:
        msg = f"{structure_name} parsed to byte {r.pos}, expected end offset {end_offset}"
        raise DsnPackageStreamError(r.pos, msg)


def _parse_package_part_cell(r: BinaryReader, stream_path: str) -> DsnPackagePartCell:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    _require_structure_type(type_id, STRUCT_PART_CELL, start_offset)
    r.try_read_preamble()

    part_cell = DsnPackagePartCell(
        ref=r.read_string_len_zero(),
        name=r.read_string_len_zero(),
    )
    view_count = r.read_uint16()
    if view_count == 1:
        part_cell.normal_name = r.read_string_len_zero()
    elif view_count == 2:
        part_cell.normal_name = r.read_string_len_zero()
        part_cell.convert_name = r.read_string_len_zero()
    else:
        msg = f"unsupported part-cell view count {view_count}"
        raise DsnPackageStreamError(start_offset, msg)

    _finish_parsed_package_structure(r, end_offset, "part cell")
    return part_cell


def _parse_package_library_part(r: BinaryReader, stream_path: str) -> DsnPackageLibraryPart:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    _require_structure_type(type_id, STRUCT_LIBRARY_PART, start_offset)
    r.try_read_preamble()

    library_part = DsnPackageLibraryPart(
        name=r.read_string_len_zero(),
        source_library=r.read_string_len_zero(),
    )
    if end_offset <= 0:
        msg = "library part lacks an end offset"
        raise DsnPackageStreamError(start_offset, msg)
    if r.pos > end_offset:
        msg = f"library part parsed to byte {r.pos}, expected end offset {end_offset}"
        raise DsnPackageStreamError(r.pos, msg)
    r.pos = end_offset
    return library_part


def _parse_package_device(r: BinaryReader, stream_path: str) -> DsnPackageDevice:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    _require_structure_type(type_id, STRUCT_DEVICE, start_offset)
    r.try_read_preamble()

    device = DsnPackageDevice(
        unit_ref=r.read_string_len_zero(),
        refdes_suffix=r.read_string_len_zero(),
    )
    pin_count = r.read_uint16()
    if pin_count > _MAX_PACKAGE_DEVICE_PINS:
        msg = f"implausible device pin count {pin_count}"
        raise DsnPackageStreamError(r.pos - 2, msg)

    for order in range(pin_count):
        pin_offset = r.pos
        string_len = struct.unpack_from("<h", r.data, r.pos)[0]
        if string_len == -1:
            r.read_int16()
            device.pins.append(DsnPackageDevicePin(order=order))
            continue
        package_pin = r.read_string_len_zero()
        pin_group_config = r.read_uint8()
        group_number = pin_group_config & 0x7F
        group = "" if group_number == 127 else str(group_number)
        device.pins.append(
            DsnPackageDevicePin(
                order=order,
                package_pin=package_pin,
                ignored=bool(pin_group_config & 0x80),
                group=group,
            )
        )
        if end_offset > 0 and r.pos > end_offset:
            msg = "device pin overruns its structure"
            raise DsnPackageStreamError(pin_offset, msg)

    _finish_parsed_package_structure(r, end_offset, "device")
    return device


def _parse_final_package(
    r: BinaryReader,
    stream_path: str,
    part_cells: list[DsnPackagePartCell],
    library_parts: list[DsnPackageLibraryPart],
) -> DsnPackage:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    _require_structure_type(type_id, STRUCT_PACKAGE, start_offset)
    r.try_read_preamble()

    package = DsnPackage(
        stream_path=stream_path,
        name=r.read_string_len_zero(),
        source_library=r.read_string_len_zero(),
        refdes_prefix=r.read_string_len_zero(),
        unknown_name=r.read_string_len_zero(),
        pcb_footprint=r.read_string_len_zero(),
        part_cells=part_cells,
        library_parts=library_parts,
    )
    device_count = r.read_uint16()
    if device_count > _MAX_PACKAGE_DEVICES:
        msg = f"implausible device count {device_count}"
        raise DsnPackageStreamError(r.pos - 2, msg)
    for _ in range(device_count):
        package.devices.append(_parse_package_device(r, stream_path))

    _finish_parsed_package_structure(r, end_offset, "package")
    return package


def parse_package_stream(
    data: bytes,
    ctx: ParseContext | None,
    stream_path: str,
) -> DsnPackage | None:
    """Parse one OrCAD Capture Packages/* stream.

    The parser follows OpenOrCadParser's package stream shape: a counted
    part-cell/library-part section followed by one final Package structure.
    Successful parses must consume the stream exactly to EOF.
    """
    r = BinaryReader(data, stream_path)
    try:
        part_cell_count = r.read_uint16()
        if part_cell_count > _MAX_PACKAGE_PART_CELLS:
            msg = f"implausible part-cell count {part_cell_count}"
            raise DsnPackageStreamError(0, msg)

        part_cells: list[DsnPackagePartCell] = []
        library_parts: list[DsnPackageLibraryPart] = []
        for _ in range(part_cell_count):
            part_cell = _parse_package_part_cell(r, stream_path)
            part_cells.append(part_cell)
            library_part_count = r.read_uint16()
            if library_part_count > _MAX_PACKAGE_LIBRARY_PARTS:
                msg = f"implausible library-part count {library_part_count}"
                raise DsnPackageStreamError(r.pos - 2, msg)
            for _ in range(library_part_count):
                library_part = _parse_package_library_part(r, stream_path)
                part_cell.library_parts.append(library_part)
                library_parts.append(library_part)

        package = _parse_final_package(r, stream_path, part_cells, library_parts)
        if not r.eof():
            msg = f"package stream has {r.remaining()} trailing bytes"
            raise DsnPackageStreamError(r.pos, msg)
        return package
    except DsnPackageStreamError as exc:
        _package_parse_error(ctx, stream_path, exc.offset, str(exc))
        return None
    except (struct.error, IndexError, ValueError) as exc:
        _package_parse_error(ctx, stream_path, r.pos, str(exc))
        return None


def parse_page(
    data: bytes, string_list: list[str], ctx: ParseContext | None = None
) -> DsnSchematicPage:
    """Parse a Page stream into a SchematicPage.

    Uses skip-based approach: read the header and net list precisely,
    then skip structures we don't need using prefix chain end_offsets.
    For placed instances and globals, we use end_offsets to bound our parsing
    so errors don't cascade.
    """
    page = DsnSchematicPage()
    r = BinaryReader(data, "Page")

    # Page prefixes
    r.read_prefix_chain()
    r.try_read_preamble()

    # Page name and size
    page.name = r.read_string_len_zero()
    page.size = r.read_string_len_zero()

    # Page settings (inline, 156 bytes)
    r.skip(PAGE_SETTINGS_SIZE)

    # Title blocks — field values ride in the record's prefix-chain pairs
    page.title_blocks = _parse_title_blocks(r, string_list, ctx)

    # T0x34 - self-describing format, skip
    skip_counted_self_describing(r)

    # T0x35 - self-describing format, skip
    skip_counted_self_describing(r)

    # Net-to-ID list - parse this carefully, it's just strings + uint32
    num_nets = r.read_uint16()
    for _ in range(num_nets):
        net = PageNetEntry()
        net.name = r.read_string_len_zero()
        net.net_id = r.read_uint32()
        page.nets.append(net)

    # Wires — parse to extract wire ID (net assignment) and endpoint coordinates
    num_wires = r.read_uint16()
    # Map: coordinate (x,y) -> set of net_ids
    wire_net_map: dict[tuple[int, int], set[int]] = {}
    for _ in range(num_wires):
        type_id, end_offset, _pairs = r.read_prefix_chain()
        r.try_read_preamble()
        wire = Wire(type_id=type_id)
        parsed_wire = False

        try:
            # OpenOrCadParser's StructWire marks these 8 bytes "might be
            # swapped" — confirmed: the first u32 is the persistent wire
            # dbid (seed of N##### autonames), the second is the runtime
            # page-net id matching the page net list.
            wire.db_id = r.read_uint32()
            wire.wire_id = r.read_uint32()
            wire.color = r.read_uint32()
            wire.start_x = r.read_int32()
            wire.start_y = r.read_int32()
            wire.end_x = r.read_int32()
            wire.end_y = r.read_int32()
            wire.is_bus = type_id == STRUCT_WIRE_BUS
            wire.points = [(wire.start_x, wire.start_y), (wire.end_x, wire.end_y)]
            if type_id in {STRUCT_WIRE_SCALAR, STRUCT_WIRE_BUS}:
                parsed_wire = True
                wire_net_map.setdefault((wire.start_x, wire.start_y), set()).add(wire.wire_id)
                wire_net_map.setdefault((wire.end_x, wire.end_y), set()).add(wire.wire_id)
                wire.aliases = _parse_wire_aliases(r, end_offset, ctx)
        except (struct.error, IndexError, ValueError) as e:
            if ctx is not None:
                ctx.warn("dsn_wire", f"Wire parse error: {e}")

        if end_offset > 0:
            r.pos = end_offset
        if parsed_wire:
            page.wires.append(wire)
    page.wire_net_map = wire_net_map

    # Placed instances — parse body to extract reference designator
    num_instances = r.read_uint16()
    for _ in range(num_instances):
        _type_id, end_offset, pairs = r.read_prefix_chain()
        r.try_read_preamble()

        inst = PlacedInstance()

        inst.props_list = _prop_entries_from_pairs(pairs, string_list)
        inst.props = dict(inst.props_list)

        # Parse body to get package name, dbId, reference, and pin connections
        try:
            r.skip(8)  # instance_id_idx + source_library_idx
            inst.package_name = r.read_string_len_zero()
            inst.db_id = r.read_uint32()
            r.skip(8)  # unknown_1
            inst.loc_x = r.read_int16()
            inst.loc_y = r.read_int16()
            r.skip(4)  # unknown_2

            # Skip SymbolDisplayProp structures
            num_display_props = r.read_uint16()
            for _ in range(num_display_props):
                skip_structure(r)

            r.skip(1)  # unknown_3

            # Checkpoint boundary — may have preamble
            r.try_read_preamble()

            # Reference designator
            inst.reference = r.read_string_len_zero()

            # 14 unknown bytes after reference
            r.skip(14)

            # T0x10 structures = pin instances with net assignments
            num_t0x10 = r.read_uint16()
            for _ in range(num_t0x10):
                _t_type, t_end, _t_pairs = r.read_prefix_chain()
                r.try_read_preamble()

                pin = PinConnection()
                pin.pin_number = str(r.read_uint16())
                pin.pin_x = r.read_int16()
                pin.pin_y = r.read_int16()
                pin.net_id = r.read_uint32()
                inst.pin_connections.append(pin)

                if t_end > 0:
                    r.pos = t_end

            # Checkpoint: source_package string
            r.try_read_preamble()
            inst.source_package = r.read_string_len_zero()
            page.instances.append(inst)
        except (struct.error, IndexError, ValueError) as e:
            if ctx is not None:
                ctx.warn("dsn_instance", f"PlacedInstance parse error: {e}")

        # Jump to end_offset for safety
        if end_offset > 0:
            r.pos = end_offset

    # Ports
    num_ports = r.read_uint16()
    for _ in range(num_ports):
        type_id, end_offset, pairs = r.read_prefix_chain()
        r.try_read_preamble()
        port: GraphicInst | None = None
        if type_id == STRUCT_PORT:
            try:
                port = _parse_graphic_inst(
                    r,
                    string_list,
                    pairs,
                    type_id,
                    has_name_indices=False,
                )
            except (struct.error, IndexError, ValueError) as e:
                if ctx is not None:
                    ctx.warn("dsn_port", f"Port parse error: {e}")
        if end_offset > 0:
            r.pos = end_offset
        if port is not None:
            page.ports.append(port)

    # Globals (power symbols) — extract name, properties, and display props
    num_globals = r.read_uint16()
    for _ in range(num_globals):
        type_id, end_offset, pairs = r.read_prefix_chain()
        r.try_read_preamble()

        gi: GraphicInst | None = None

        try:
            if type_id == STRUCT_GLOBAL:
                gi = _parse_graphic_inst(
                    r,
                    string_list,
                    pairs,
                    type_id,
                    has_name_indices=True,
                )
            r.skip(1)  # unknownFlag (0x21 for Global)
        except (struct.error, IndexError, ValueError) as e:
            if ctx is not None:
                ctx.warn("dsn_global", f"Global parse error: {e}")

        if end_offset > 0:
            r.pos = end_offset
        if gi is not None:
            page.globals.append(gi)
        r.skip(5)  # trailing data per global at stream level

    # Off-page connectors
    num_opc = r.read_uint16()
    for _ in range(num_opc):
        type_id, end_offset, pairs = r.read_prefix_chain()
        r.try_read_preamble()
        connector: GraphicInst | None = None
        if type_id == STRUCT_OFF_PAGE_CONNECTOR:
            try:
                connector = _parse_graphic_inst(
                    r,
                    string_list,
                    pairs,
                    type_id,
                    has_name_indices=True,
                )
                r.skip(1)  # unknownFlag, same trailing flag shape as globals
            except (struct.error, IndexError, ValueError) as e:
                if ctx is not None:
                    ctx.warn("dsn_off_page_connector", f"Off-page connector parse error: {e}")
        if end_offset > 0:
            r.pos = end_offset
        if connector is not None:
            page.off_page_connectors.append(connector)
        r.skip(5)  # trailing data

    parse_page_tail_objects(r, page, ctx)

    return page


def parse_page_tail_objects(
    r: BinaryReader,
    page: DsnSchematicPage,
    ctx: ParseContext | None,
) -> None:
    """Parse known post-connector page structures without guessing at the rest."""
    if r.remaining() < 2:
        return
    erc_parse_failed = False
    try:
        num_erc_objects = r.read_uint16()
        if num_erc_objects > _MAX_ERC_OBJECTS:
            msg = f"implausible page-tail ERC object count {num_erc_objects}"
            raise ValueError(msg)
        for _ in range(num_erc_objects):
            erc_object = _parse_erc_object(r, page.name, ctx)
            if erc_object is not None:
                page.erc_objects.append(erc_object)
    except (struct.error, IndexError, ValueError) as e:
        _warn(ctx, "dsn_page_tail", f"Page tail ERC object parse error: {e}")
        # A hard ERC decode failure can leave the page-tail cursor ambiguous.
        # Skip bus entries rather than risking bogus connectivity-adjacent data.
        erc_parse_failed = True

    if erc_parse_failed or r.remaining() < 2:
        return
    try:
        num_bus_entries = r.read_uint16()
        for _ in range(num_bus_entries):
            type_id, end_offset, _pairs = r.read_prefix_chain()
            r.try_read_preamble()
            entry: DsnBusEntry | None = None
            if type_id == STRUCT_BUS_ENTRY:
                entry = DsnBusEntry(
                    color=r.read_uint32(),
                    start_x=r.read_int32(),
                    start_y=r.read_int32(),
                    end_x=r.read_int32(),
                    end_y=r.read_int32(),
                )
                r.skip(8)
            if end_offset > 0:
                r.pos = end_offset
            if entry is not None:
                page.bus_entries.append(entry)
    except (struct.error, IndexError, ValueError) as e:
        _warn(ctx, "dsn_bus_entry", f"Bus entry parse error: {e}")


def _parse_erc_object(
    r: BinaryReader,
    page_name: str,
    ctx: ParseContext | None,
) -> DsnErcObject | None:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    try:
        if type_id != STRUCT_ERC_OBJECT:
            _warn(
                ctx,
                "dsn_erc_object",
                f"{page_name}: unsupported ERC object type 0x{type_id:02x} "
                f"at offset {start_offset}",
            )
            return None
        # StructERCObject embeds StructGraphicInst, then stores the three
        # diagnostic strings as s0/s1/s2.
        r.try_read_preamble()
        r.skip(8)
        symbol_name = r.read_string_len_zero()
        db_id = r.read_uint32()
        loc_y = r.read_int16()
        loc_x = r.read_int16()
        bbox_y2 = r.read_int16()
        bbox_x2 = r.read_int16()
        bbox_x1 = r.read_int16()
        bbox_y1 = r.read_int16()
        color = r.read_uint8()
        erc_object = DsnErcObject(
            page_name=page_name,
            type_id=type_id,
            symbol_name=symbol_name,
            db_id=db_id,
            loc_y=loc_y,
            loc_x=loc_x,
            bbox_y2=bbox_y2,
            bbox_x2=bbox_x2,
            bbox_x1=bbox_x1,
            bbox_y1=bbox_y1,
            color=color,
        )
        r.skip(3)
        num_display_props = r.read_uint16()
        if num_display_props > _MAX_ERC_DISPLAY_PROPS:
            msg = f"implausible ERC display-prop count {num_display_props}"
            raise ValueError(msg)
        for _ in range(num_display_props):
            skip_structure(r)
        erc_object.unknown_flag = r.read_uint8()
        erc_object.s0 = r.read_string_len_zero()
        erc_object.s1 = r.read_string_len_zero()
        erc_object.s2 = r.read_string_len_zero()
        if end_offset > 0 and r.pos > end_offset:
            msg = f"ERC object parsed to byte {r.pos}, expected end offset {end_offset}"
            raise ValueError(msg)
        return erc_object
    finally:
        if end_offset > 0:
            r.pos = end_offset


def parse_net_bundle_map_data(
    data: bytes,
    ctx: ParseContext | None = None,
) -> list[DsnNetBundleMap]:
    """Parse OrCAD Capture's NetBundleMapData stream.

    Layout follows OpenOrCadParser ``StreamNetBundleMapData``: two unknown
    bytes, a group count, then named ``NetGroup`` structures with member
    names and a scalar/bus wire-type marker.
    """
    r = BinaryReader(data, "NetBundleMapData")
    try:
        r.skip(2)
        number_groups = r.read_uint16()
        if number_groups > _MAX_NET_BUNDLE_GROUPS:
            msg = f"implausible NetBundleMapData group count {number_groups}"
            raise ValueError(msg)

        groups: list[DsnNetBundleMap] = []
        for _ in range(number_groups):
            group = DsnNetBundleMap(name=r.read_string_len_zero())
            type_id, end_offset, _pairs = r.read_prefix_chain()
            if type_id != STRUCT_NET_GROUP:
                msg = f"expected NetGroup structure 0x{STRUCT_NET_GROUP:02x}, got 0x{type_id:02x}"
                raise ValueError(msg)
            r.try_read_preamble()
            unknown = r.read_bytes(6)
            if unknown != b"\x00" * 6:
                msg = "NetBundleMapData NetGroup header contains unknown non-zero bytes"
                raise ValueError(msg)

            number_members = r.read_uint16()
            if number_members > _MAX_NET_BUNDLE_MEMBERS:
                msg = f"implausible NetBundleMapData member count {number_members}"
                raise ValueError(msg)
            for _ in range(number_members):
                group.members.append(
                    DsnNetBundleMember(
                        name=r.read_string_len_zero(),
                        wire_type=r.read_uint16(),
                    )
                )
            if end_offset > 0:
                r.pos = end_offset
            groups.append(group)

        if not r.eof():
            msg = f"NetBundleMapData has {r.remaining()} trailing bytes"
            raise ValueError(msg)
    except (struct.error, IndexError, ValueError) as e:
        _warn(ctx, "dsn_net_bundle_map", f"NetBundleMapData parse error: {e}")
        return []
    return groups


def parse_net_bundle_map_streams(
    streams: Iterable[bytes],
    ctx: ParseContext | None = None,
) -> list[DsnNetBundleMap]:
    net_bundle_maps: list[DsnNetBundleMap] = []
    for stream in streams:
        net_bundle_maps.extend(parse_net_bundle_map_data(stream, ctx))
    return net_bundle_maps


def _erc_marker_category(stream_path: str) -> str:
    if stream_path.endswith("ERC_PHYSICAL"):
        return "erc_physical"
    return "erc"


def parse_erc_symbol_stream(
    data: bytes,
    stream_path: str,
    ctx: ParseContext | None = None,
) -> DsnErcSymbol | None:
    """Parse the fixture-proven header of a ``Symbols/ERC*`` stream.

    OpenOrCadParser models this as one ``StructERCSymbol`` containing a raw
    ``StructSthInPages0`` payload. For this slice, preserve the marker name,
    source library, category, and remaining primitive bytes without promoting
    any geometry semantics.
    """
    r = BinaryReader(data, stream_path)
    try:
        type_id, end_offset, _pairs = r.read_prefix_chain()
        if type_id != STRUCT_ERC_SYMBOL:
            _warn(
                ctx,
                "dsn_erc_symbol",
                f"{stream_path}: unsupported ERC symbol type 0x{type_id:02x}",
            )
            return None
        r.try_read_preamble()
        symbol = DsnErcSymbol(
            stream_path=stream_path,
            type_id=type_id,
            name=r.read_string_len_zero(),
            source_library=r.read_string_len_zero(),
            marker_category=_erc_marker_category(stream_path),
            color=r.read_uint32(),
            primitive_count=r.read_uint16(),
        )
        stop = end_offset if end_offset > 0 else len(data)
        if r.pos > stop:
            msg = f"ERC symbol parsed to byte {r.pos}, expected end offset {stop}"
            raise ValueError(msg)
        symbol.raw_payload = data[r.pos : stop]
        return symbol
    except (struct.error, IndexError, ValueError) as e:
        _warn(ctx, "dsn_erc_symbol", f"{stream_path}: ERC symbol parse error: {e}")
        return None


def parse_hierarchy(data: bytes) -> list[NetIdMapping]:
    """Parse the Hierarchy stream for net-to-ID mappings."""
    r = BinaryReader(data, "Hierarchy")
    mappings: list[NetIdMapping] = []

    r.skip(9)  # unknown_0
    r.read_string_len_zero()  # schematic_name
    r.skip(7)  # unknown_1

    # SthInHierarchy2 list
    num_sth2 = r.read_uint16()
    for _ in range(num_sth2):
        skip_structure(r)
        r.skip(4)  # trailing uint32
        r.read_string_len_zero()  # someName

    # Net DB ID Mappings - this is what we want
    num_mappings = r.read_uint16()
    for _ in range(num_mappings):
        skip_structure(r)  # StructNetDbIdMapping (empty payload)
        mapping = NetIdMapping()
        mapping.db_id = r.read_uint32()
        mapping.name = r.read_string_len_zero()
        mappings.append(mapping)

    return mappings


def _merge_net_id_mappings(*mapping_groups: Iterable[NetIdMapping]) -> list[NetIdMapping]:
    """Merge hierarchy net mappings in stream order, keeping the first DB ID."""
    merged: list[NetIdMapping] = []
    seen_db_ids: set[int] = set()
    for mappings in mapping_groups:
        for mapping in mappings:
            if mapping.db_id in seen_db_ids:
                continue
            seen_db_ids.add(mapping.db_id)
            merged.append(mapping)
    return merged


def parse_hierarchy_occurrences(
    data: bytes,
    placed_instance_db_ids: Iterable[int],
) -> list[DsnHierarchyOccurrence]:
    """Parse raw hierarchy occurrence links to placed instance DB IDs.

    Capture hierarchy streams contain occurrence/object IDs that are distinct
    from the placed-instance DB IDs exposed by page records. In fixture-backed
    streams, the hierarchy occurrence ID is immediately followed by the page
    placed-instance DB ID and then a `0x42` structure marker. Preserve these
    links so CIS group rows can later resolve to schematic objects.
    """
    instance_ids = {db_id for db_id in placed_instance_db_ids if db_id > 0}
    if not instance_ids:
        return []

    occurrences: list[DsnHierarchyOccurrence] = []
    seen: set[tuple[int, int]] = set()
    for offset in range(0, max(0, len(data) - 8)):
        if data[offset + 8] != 0x42:
            continue
        occurrence_id = struct.unpack_from("<I", data, offset)[0]
        instance_db_id = struct.unpack_from("<I", data, offset + 4)[0]
        key = (occurrence_id, instance_db_id)
        if (
            occurrence_id <= 0
            or occurrence_id == instance_db_id
            or instance_db_id not in instance_ids
            or key in seen
        ):
            continue
        seen.add(key)
        occurrences.append(
            DsnHierarchyOccurrence(
                occurrence_id=occurrence_id,
                instance_db_id=instance_db_id,
            )
        )
    return occurrences


def _can_read_string_len_zero(data: bytes, pos: int) -> bool:
    """Check if readStringLenZeroTerm would succeed at `pos`.

    Matches C++ semantics: reads uint16 length, then scans for null terminator,
    and verifies the distance to null equals the length prefix.
    """
    size = len(data)
    if pos + 2 > size:
        return False
    length = struct.unpack_from("<H", data, pos)[0]
    if length == 0:
        return pos + 2 < size and data[pos + 2] == 0
    start = pos + 2
    try:
        null_pos = data.index(b"\x00", start)
    except ValueError:
        return False
    return (null_pos - start) == length


def parse_cache(data: bytes, ctx: ParseContext | None = None) -> dict[str, list[str]]:
    """Parse the Cache stream to extract symbol pin names.

    Returns a dict mapping symbol names (without .Normal suffix) to ordered
    lists of pin names. Pin order matches T0x10 pin_number (1-indexed).
    """
    return parse_cache_symbols(data, ctx).pin_names


def parse_cache_symbols(data: bytes, ctx: ParseContext | None = None) -> DsnCacheSymbols:
    """Parse the Cache stream's fixture-proven symbol pin data.

    The Cache stream format (per OpenOrCadParser StreamCache.cpp):
    - 4-byte header: 0x00 0x00 + 2 unknown bytes
    - Repeated entries until EOF, each containing:
      1. Optional preamble data (string or 8-byte prefix + string + refdes)
      2. Symbol name (readStringLenZeroTerm)
      3. Optional package reference loop (when id0 != id1)
      4. Two matching uint32 IDs + uint16 struct_type + structure data

    The C++ tryRead() is a non-consuming probe: it saves/restores position
    and only reports success/failure.
    """
    r = BinaryReader(data, "Cache")
    size = len(data)
    pin_map: dict[str, list[str]] = {}
    structured_pins: dict[str, list[DsnSymbolPin]] = {}

    # 4-byte header: 0x00 0x00 (assumed) + 2 unknown bytes
    r.skip(4)

    while r.pos < size - 4:
        # Probe: hasStrAfter0Byte (non-consuming check)
        if not _can_read_string_len_zero(data, r.pos):
            # !hasStrAfter0Byte path
            if _can_read_string_len_zero(data, r.pos + 8):
                # hasStrAfter8Byte: consume 8 bytes + lib path + 2 unknown + refdes
                r.skip(8)
                r.read_string_len_zero()  # library path
                r.skip(2)  # unknown bytes
                r.read_string_len_zero()  # someRefDes
            # Always: 2 unknown bytes (outside hasStrAfter8Byte)
            r.skip(2)

        # Symbol name
        if r.pos >= size - 2:
            break
        name = r.read_string_len_zero()

        # Peek id0, id1 to check for package reference loop
        if r.pos + 8 > size:
            break
        id0 = struct.unpack_from("<I", data, r.pos)[0]
        id1 = struct.unpack_from("<I", data, r.pos + 4)[0]

        if id0 != id1:
            # Package reference do-while loop
            while True:
                some_val = r.read_uint16()
                if r.pos >= size:
                    break
                if r.pos + 1 >= size:
                    r.skip(1)
                    break
                # hasMysterious2Byte = !can_read_string
                if _can_read_string_len_zero(data, r.pos):
                    r.read_string_len_zero()
                else:
                    r.skip(2)  # mysterious 2 bytes
                    r.read_string_len_zero()
                if some_val != 0:
                    break

        # Read some_id0, some_id1, struct_type
        if r.pos + 10 > size:
            break
        r.skip(8)  # some_id0 + some_id1
        struct_type = r.read_uint16()

        # Skip the structure via prefix chain
        struct_start = r.pos
        try:
            _type_id, end_offset, _pairs = r.read_prefix_chain()
            if end_offset > 0:
                struct_end = end_offset
                r.pos = end_offset
            else:
                struct_end = r.pos
        except ValueError:
            break

        # Extract pin names from symbol entries (struct_type 0x18 = 24)
        if struct_type == 24 and name:
            sub_symbols = _extract_pin_names(data, struct_start, struct_end, name)
            for sym_key, pins in sub_symbols.items():
                if pins:
                    pin_map[sym_key] = pins
            symbol_pins = _extract_structured_symbol_pins(
                data,
                struct_start,
                struct_end,
                name,
            )
            for sym_key, pins in symbol_pins.items():
                if pins:
                    structured_pins[sym_key] = pins

    _warn_for_unstructured_cache_symbols(pin_map, structured_pins, ctx)
    return DsnCacheSymbols(pin_names=pin_map, pins=structured_pins)


def _extract_pin_names(
    data: bytes,
    start: int,
    end: int,
    sym_name: str,
) -> dict[str, list[str]]:
    """Extract pin names from a symbol structure in data[start:end].

    A single Cache structure may contain multiple sub-symbols (e.g., a
    package entry can hold a DIODE_SCHOTTKY symbol with its own pins).
    When a '.Normal' name is encountered, subsequent pins belong to that
    sub-symbol until the next '.Normal' or end of range.

    Returns a dict mapping symbol names (without .Normal) to pin lists.
    The top-level symbol is keyed by sym_name (also without .Normal).
    """
    result: dict[str, list[str]] = {}
    current_sym = sym_name.replace(".Normal", "")
    result[current_sym] = []

    pos = start
    while pos < end - 4:
        idx = data.find(PREAMBLE, pos, end)
        if idx == -1:
            break
        # Skip preamble magic + trailing data
        p = idx + 4
        if p + 4 > end:
            break
        data_len = struct.unpack_from("<I", data, p)[0]
        p += 4 + data_len
        if p >= end:
            break
        # Try reading a name after the preamble
        if _can_read_string_len_zero(data, p):
            length = struct.unpack_from("<H", data, p)[0]
            if length > 0:
                name = data[p + 2 : p + 2 + length].decode("ascii", errors="replace")
                if ".Normal" in name:
                    # Start of a new sub-symbol
                    current_sym = name.replace(".Normal", "")
                    if current_sym not in result:
                        result[current_sym] = []
                elif name != sym_name and len(name) < 30:
                    result[current_sym].append(name)
        pos = idx + 1  # advance past this preamble

    return result


def _symbol_name_markers(
    data: bytes,
    start: int,
    end: int,
    sym_name: str,
) -> list[tuple[int, str]]:
    """Return sub-symbol markers from the legacy cache preamble convention."""
    markers: list[tuple[int, str]] = []
    pos = start
    while pos < end - 4:
        idx = data.find(PREAMBLE, pos, end)
        if idx == -1:
            break
        p = idx + 4
        if p + 4 > end:
            break
        data_len = struct.unpack_from("<I", data, p)[0]
        p += 4 + data_len
        if p >= end:
            break
        if _can_read_string_len_zero(data, p):
            length = struct.unpack_from("<H", data, p)[0]
            if length > 0:
                name = data[p + 2 : p + 2 + length].decode("ascii", errors="replace")
                if ".Normal" in name:
                    markers.append((idx, name.replace(".Normal", "")))
                elif name == sym_name:
                    markers.append((idx, sym_name.replace(".Normal", "")))
        pos = idx + 1
    return markers


def _symbol_name_for_offset(
    offset: int,
    outer_name: str,
    markers: list[tuple[int, str]],
) -> str:
    symbol_name = outer_name.replace(".Normal", "")
    for marker_offset, marker_name in markers:
        if marker_offset < offset:
            symbol_name = marker_name
        else:
            break
    return symbol_name


def _extract_structured_symbol_pins(
    data: bytes,
    start: int,
    end: int,
    sym_name: str,
) -> dict[str, list[DsnSymbolPin]]:
    """Extract fixture-proven StructSymbolPin records from a LibraryPart body."""
    result: dict[str, list[DsnSymbolPin]] = {}
    markers = _symbol_name_markers(data, start, end, sym_name)

    offset = start
    while offset < end - 20:
        if data[offset] not in (26, 27):
            offset += 1
            continue
        parsed = _try_read_structured_symbol_pin(data, offset, end)
        if parsed is None:
            offset += 1
            continue
        pin, pin_end = parsed
        symbol_name = _symbol_name_for_offset(offset, sym_name, markers)
        result.setdefault(symbol_name, []).append(pin)
        offset = pin_end
    return result


def _try_read_structured_symbol_pin(
    data: bytes,
    offset: int,
    library_part_end: int,
) -> tuple[DsnSymbolPin, int] | None:
    r = BinaryReader(data, "Cache.SymbolPin")
    r.pos = offset
    try:
        structure_type, pin_end, _pairs = r.read_prefix_chain()
    except ValueError:
        return None
    if structure_type not in (26, 27):
        return None
    if pin_end <= r.pos or pin_end > library_part_end:
        return None
    if data[r.pos : r.pos + 4] != PREAMBLE:
        return None
    try:
        r.try_read_preamble()
        name = r.read_string_len_zero()
        start_x = r.read_int32()
        start_y = r.read_int32()
        hotpt_x = r.read_int32()
        hotpt_y = r.read_int32()
        pin_shape = r.read_uint16()
        r.skip(2)  # StructSymbolPin unknown field after pinShape.
        port_type = r.read_uint32()
        r.skip(4)  # StructSymbolPin unknown field before display properties.
        display_prop_count = r.read_uint16()
    except (IndexError, struct.error, ValueError):
        return None
    if not name:
        return None
    port_type_info = ORCAD_PORT_TYPES.get(port_type)
    if port_type_info is None:
        return None
    if display_prop_count > _MAX_SYMBOL_PIN_DISPLAY_PROPS or r.pos > pin_end:
        return None
    return (
        DsnSymbolPin(
            name=name,
            structure_type=structure_type,
            start_x=start_x,
            start_y=start_y,
            hotpt_x=hotpt_x,
            hotpt_y=hotpt_y,
            pin_shape=pin_shape,
            port_type=port_type,
            port_type_name=port_type_info.name,
            display_prop_count=display_prop_count,
        ),
        pin_end,
    )


def _warn_for_unstructured_cache_symbols(
    pin_map: dict[str, list[str]],
    structured_pins: dict[str, list[DsnSymbolPin]],
    ctx: ParseContext | None,
) -> None:
    if ctx is None:
        return
    for symbol_name in sorted(pin_map.keys() - structured_pins.keys()):
        ctx.warn(
            "dsn_cache_structured_pins",
            f"{symbol_name}: Cache pin names used legacy heuristic; structured symbol-pin "
            "layout was not recognized",
        )


def _ole_entries(ole: olefile.OleFileIO) -> list[tuple[list[str], str]]:
    return [(entry, "/".join(entry)) for entry in ole.listdir()]


def _parse_package_streams_from_ole(
    ole: olefile.OleFileIO,
    ctx: ParseContext | None,
    entries: Iterable[tuple[list[str], str]] | None = None,
) -> dict[str, DsnPackage]:
    packages: dict[str, DsnPackage] = {}
    for entry, path in entries if entries is not None else _ole_entries(ole):
        if path.startswith("Packages/"):
            package_data = ole.openstream(entry).read()
            package = parse_package_stream(package_data, ctx, path)
            if package is not None:
                packages[path] = package
    return packages


def _parse_cache_from_ole(
    ole: olefile.OleFileIO,
    entries: Iterable[tuple[list[str], str]] | None = None,
) -> dict[str, list[str]]:
    for entry, path in entries if entries is not None else _ole_entries(ole):
        if path == "Cache":
            return parse_cache(ole.openstream(entry).read())
    return {}


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _package_inventory(package: DsnPackage) -> DsnLibraryPackageInventory:
    source_package_names: list[str] = []
    source_library_references: list[str] = []
    _append_unique(source_package_names, package.name)
    _append_unique(source_library_references, package.source_library)
    for part_cell in package.part_cells:
        _append_unique(source_package_names, part_cell.name)
        _append_unique(source_package_names, part_cell.normal_name)
        _append_unique(source_package_names, part_cell.convert_name)
        for library_part in part_cell.library_parts:
            _append_unique(source_package_names, library_part.name)
            _append_unique(source_library_references, library_part.source_library)
    for library_part in package.library_parts:
        _append_unique(source_package_names, library_part.name)
        _append_unique(source_library_references, library_part.source_library)

    return DsnLibraryPackageInventory(
        stream_path=package.stream_path,
        name=package.name,
        source_package_names=source_package_names,
        source_library_references=source_library_references,
        pcb_footprint=package.pcb_footprint,
        device_count=len(package.devices),
        pin_count=sum(len(device.pins) for device in package.devices),
    )


def parse_library_inventory(
    library_path: Path,
    ctx: ParseContext | None = None,
) -> DsnLibraryInventory:
    """Parse OrCAD DSN/OLB library streams without requiring schematic pages."""
    if ctx is None:
        ctx = ParseContext()

    with olefile.OleFileIO(str(library_path)) as ole:
        entries = _ole_entries(ole)
        lib_data = ole.openstream("Library").read()
        _library_header, string_list, part_fields = parse_library(lib_data)
        packages = _parse_package_streams_from_ole(ole, ctx, entries)
        symbol_pin_names = _parse_cache_from_ole(ole, entries)

    cache_part_names = sorted(symbol_pin_names)
    return DsnLibraryInventory(
        path=str(library_path),
        string_list=string_list,
        part_fields=part_fields,
        packages=packages,
        package_inventory=[_package_inventory(package) for package in packages.values()],
        cache_part_names=cache_part_names,
        cache_pin_counts={name: len(symbol_pin_names[name]) for name in cache_part_names},
    )


# --- Main entry point ---


def parse_dsn(dsn_path: Path, ctx: ParseContext | None = None) -> ParsedDesign:
    """Parse an OrCAD Capture .DSN file.

    Non-fatal parse issues (wire/instance/global decode errors) are recorded
    on *ctx* when provided, mirroring the Altium parser API.
    """
    if ctx is None:
        ctx = ParseContext()
    ole = olefile.OleFileIO(str(dsn_path))
    try:
        # 1. Parse Library stream first (needed for string list)
        lib_data = ole.openstream("Library").read()
        library_header, string_list, part_fields = parse_library(lib_data)

        design = ParsedDesign()
        design.library_header = library_header
        design.string_list = string_list
        design.part_fields = part_fields

        stream_entries = [
            ("/".join(entry), entry) for entry in ole.listdir(streams=True, storages=False)
        ]
        stream_paths = [path for path, _entry in stream_entries]
        storage_paths = {"/".join(entry) for entry in ole.listdir(streams=False, storages=True)}
        hierarchy_stream_paths_by_view: dict[str, list[str]] = {}
        for path in stream_paths:
            if path.startswith("Views/") and "/Hierarchy/Hierarchy" in path:
                hierarchy_stream_paths_by_view.setdefault(view_name_from_path(path), []).append(
                    path
                )

        # 2. Parse raw Packages/* streams.
        for path, entry in stream_entries:
            if not path.startswith("Packages/"):
                continue
            package_data = ole.openstream(entry).read()
            package = parse_package_stream(package_data, ctx, path)
            if package is not None:
                design.packages[path] = package

        # 3. Parse schematic view metadata.
        for path, entry in stream_entries:
            if not (path.startswith("Views/") and path.endswith("/Schematic")):
                continue
            view_name = view_name_from_path(path)
            view_data = ole.openstream(entry).read()
            view = parse_view_schematic(
                view_data,
                stream_path=path,
                hierarchy_stream_paths=hierarchy_stream_paths_by_view.get(view_name, []),
                ctx=ctx,
            )
            if view is not None:
                design.views.append(view)
        warn_repeated_sheet_identity(design.views, ctx)

        # 4. Parse Page stream(s)
        for path, entry in stream_entries:
            if not (path.startswith("Views/") and "/Pages/" in path):
                continue
            page_data = ole.openstream(entry).read()
            try:
                page = parse_page(page_data, string_list, ctx)
            except DsnFormatError as exc:
                # Leave the failure observable on the context, then surface
                # it — a misread page header poisons everything after it.
                ctx.error("dsn_format", f"page {path!r}: {exc}")
                raise
            page.view_name = view_name_from_path(path)
            page.stream_path = path
            design.pages.append(page)

        # 5. Parse Hierarchy stream
        instance_db_ids = (
            instance.db_id for page in design.pages for instance in page.instances if instance.db_id
        )
        instance_db_id_set = set(instance_db_ids)
        for path, entry in stream_entries:
            if "Hierarchy/Hierarchy" not in path:
                continue
            hier_data = ole.openstream(entry).read()
            design.net_id_mappings = _merge_net_id_mappings(
                design.net_id_mappings,
                parse_hierarchy(hier_data),
            )
            design.hierarchy_occurrences.extend(
                parse_hierarchy_occurrences(hier_data, instance_db_id_set)
            )

        # 6. Parse raw CIS VariantStore evidence when present.
        occurrence_to_instance = {
            occurrence.occurrence_id: occurrence.instance_db_id
            for occurrence in design.hierarchy_occurrences
        }
        cis_stream_data_by_path: dict[str, bytes] = {}
        for path, entry in stream_entries:
            if path.startswith("CIS/VariantStore/"):
                cis_stream_data_by_path[path] = ole.openstream(entry).read()
        design.cis_variant_store = parse_cis_variant_store(
            cis_stream_data_by_path,
            storage_paths,
            occurrence_to_instance,
            ctx,
        )

        # 7. Parse Cache stream for symbol pin names
        for path, entry in stream_entries:
            if path != "Cache":
                continue
            cache_data = ole.openstream(entry).read()
            cache_symbols = parse_cache_symbols(cache_data, ctx)
            design.symbol_pin_names = cache_symbols.pin_names
            design.symbol_pins = cache_symbols.pins

        # 8. Parse design-level net bundle groups when present.
        for path, entry in stream_entries:
            if path != "NetBundleMapData" and not path.endswith("/NetBundleMapData"):
                continue
            bundle_data = ole.openstream(entry).read()
            design.net_bundle_maps.extend(parse_net_bundle_map_streams([bundle_data], ctx))

        # 9. Parse raw ERC marker symbol catalog entries.
        for path, entry in sorted(stream_entries):
            if not path.startswith("Symbols/ERC"):
                continue
            erc_symbol = parse_erc_symbol_stream(ole.openstream(entry).read(), path, ctx)
            if erc_symbol is not None:
                design.erc_symbols.append(erc_symbol)

        return design
    finally:
        ole.close()
