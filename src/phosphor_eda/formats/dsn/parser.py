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

from phosphor_eda.formats.common.diagnostics import ParseContext, warn_optional
from phosphor_eda.formats.dsn.binary_reader import (
    PAGE_SETTINGS_SIZE,
    STRUCT_BUS_ENTRY,
    STRUCT_GLOBAL,
    STRUCT_NET_GROUP,
    STRUCT_OFF_PAGE_CONNECTOR,
    STRUCT_PORT,
    STRUCT_WIRE_BUS,
    STRUCT_WIRE_SCALAR,
    BinaryReader,
    skip_counted_self_describing,
    skip_structure,
)
from phosphor_eda.formats.dsn.cis import parse_cis_variant_store
from phosphor_eda.formats.dsn.erc import (
    MAX_ERC_OBJECTS,
    parse_erc_object,
    parse_erc_symbol_stream,
)
from phosphor_eda.formats.dsn.errors import DsnFormatError
from phosphor_eda.formats.dsn.hierarchy import (
    merge_net_id_mappings,
    parse_hierarchy,
    parse_hierarchy_occurrences,
)
from phosphor_eda.formats.dsn.library import (
    ole_stream_entries,
    parse_cache_from_ole,
    parse_library,
    parse_packages_from_ole,
)
from phosphor_eda.formats.dsn.raw_models import (
    DsnBusEntry,
    DsnNetBundleMap,
    DsnNetBundleMember,
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


# Net labels placed on a wire never exceed a handful; a larger count means
# the wire body layout differs from the StructWire layout we know.
_MAX_WIRE_ALIASES = 64
_MAX_NET_BUNDLE_GROUPS = 4096
_MAX_NET_BUNDLE_MEMBERS = 4096


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
                warn_optional(ctx, "dsn_title_block", f"Title block parse error: {e}")
            r.pos = end_offset
        else:
            r.try_read_preamble()
        title_blocks.append(block)
    return title_blocks


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
        if num_erc_objects > MAX_ERC_OBJECTS:
            msg = f"implausible page-tail ERC object count {num_erc_objects}"
            raise ValueError(msg)
        for _ in range(num_erc_objects):
            erc_object = parse_erc_object(r, page.name, ctx)
            if erc_object is not None:
                page.erc_objects.append(erc_object)
    except (struct.error, IndexError, ValueError) as e:
        warn_optional(ctx, "dsn_page_tail", f"Page tail ERC object parse error: {e}")
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
        warn_optional(ctx, "dsn_bus_entry", f"Bus entry parse error: {e}")


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
        warn_optional(ctx, "dsn_net_bundle_map", f"NetBundleMapData parse error: {e}")
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

        stream_entries = ole_stream_entries(ole)
        stream_paths = [path for path, _entry in stream_entries]
        storage_paths = {"/".join(entry) for entry in ole.listdir(streams=False, storages=True)}
        hierarchy_stream_paths_by_view: dict[str, list[str]] = {}
        for path in stream_paths:
            if path.startswith("Views/") and "/Hierarchy/Hierarchy" in path:
                hierarchy_stream_paths_by_view.setdefault(view_name_from_path(path), []).append(
                    path
                )

        # 2. Parse raw Packages/* streams.
        design.packages = parse_packages_from_ole(ole, ctx, stream_entries)

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
        instance_db_id_set = {
            instance.db_id for page in design.pages for instance in page.instances if instance.db_id
        }
        mapping_groups: list[Iterable[NetIdMapping]] = [design.net_id_mappings]
        for path, entry in stream_entries:
            if "Hierarchy/Hierarchy" not in path:
                continue
            hier_data = ole.openstream(entry).read()
            mapping_groups.append(parse_hierarchy(hier_data))
            design.hierarchy_occurrences.extend(
                parse_hierarchy_occurrences(hier_data, instance_db_id_set)
            )
        design.net_id_mappings = merge_net_id_mappings(*mapping_groups)

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
        cache_symbols = parse_cache_from_ole(ole, stream_entries, ctx)
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
