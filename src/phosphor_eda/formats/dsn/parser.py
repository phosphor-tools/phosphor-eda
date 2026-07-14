"""Parser for OrCAD Capture .DSN files.

Extracts component information, net names, and connectivity from the
binary OLE compound document format used by OrCAD Capture.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

import json
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import olefile

from phosphor_eda.formats.common.diagnostics import ParseContext, warn_optional
from phosphor_eda.formats.dsn.binary_reader import (
    PAGE_SETTINGS_SIZE,
    PRIM_COMMENT_TEXT,
    PRIM_ELLIPSE,
    PRIM_LINE,
    PRIM_RECT,
    STRUCT_BUS_ENTRY,
    STRUCT_DRAWN_INST,
    STRUCT_DSN_STREAM,
    STRUCT_GLOBAL,
    STRUCT_GRAPHIC_BITMAP,
    STRUCT_GRAPHIC_BOX,
    STRUCT_GRAPHIC_COMMENT_TEXT,
    STRUCT_GRAPHIC_ELLIPSE,
    STRUCT_GRAPHIC_LINE,
    STRUCT_GRAPHIC_OLE_EMBED,
    STRUCT_NET_GROUP,
    STRUCT_OFF_PAGE_CONNECTOR,
    STRUCT_PART_INST,
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
    build_occurrence_to_instance,
    merge_net_id_mappings,
    parse_hierarchy,
    parse_hierarchy_occurrences,
    parse_hierarchy_stream,
    warn_repeated_sheet_blocks,
)
from phosphor_eda.formats.dsn.library import (
    ole_stream_entries,
    parse_cache_from_ole,
    parse_library,
    parse_packages_from_ole,
)
from phosphor_eda.formats.dsn.pins import ORCAD_PORT_TYPES
from phosphor_eda.formats.dsn.raw_models import (
    DsnBlockInstance,
    DsnBlockPinBinding,
    DsnBlockSheetPin,
    DsnBusEntry,
    DsnCommentText,
    DsnDesignStream,
    DsnNetBundleMap,
    DsnNetBundleMember,
    DsnNetDisplayProp,
    DsnPageGraphic,
    DsnPageImage,
    DsnStreamInventory,
    DsnStreamRef,
    DsnSymbolType,
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
    # Pre-check the 1 unknown byte + uint16 count against the wire body before
    # reading them, so a truncated/foreign wire layout fails here instead of
    # reading a bogus count out of the following structure.
    if wire_end_offset > 0 and r.pos + 3 > wire_end_offset:
        msg = "wire body ends before its alias count"
        raise ValueError(msg)
    r.skip(1)
    num_aliases = r.read_uint16()
    if num_aliases == 0:
        return []
    if num_aliases > _MAX_WIRE_ALIASES:
        msg = f"implausible wire alias count {num_aliases}; wire body layout unknown"
        raise ValueError(msg)
    aliases: list[WireAlias] = []
    for _ in range(num_aliases):
        if wire_end_offset > 0 and r.pos >= wire_end_offset:
            msg = "wire alias record starts past the wire body"
            raise ValueError(msg)
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


def _warn_unknown_structure(
    ctx: ParseContext | None, section: str, type_id: int, offset: int
) -> None:
    warn_optional(
        ctx,
        "dsn_unknown_structure",
        f"page {section} section: unsupported structure type 0x{type_id:02x} at offset {offset}",
    )


def _parse_trailing_flag_graphic(
    r: BinaryReader,
    string_list: list[str],
    target: list[GraphicInst],
    *,
    struct_type: int,
    error_label: str,
    warn_code: str,
    unknown_section: str,
    ctx: ParseContext | None,
) -> None:
    """Parse one Global/OffPageConnector record and append it to *target*.

    Both structures share a StructGraphicInst body followed by a one-byte
    unknownFlag and five stream-level trailing bytes. The prefix chain's
    end_offset resyncs the cursor whether or not the body decodes, so the
    unknownFlag is consumed only after a successful parse.
    """
    offset = r.pos
    type_id, end_offset, pairs = r.read_prefix_chain()
    r.try_read_preamble()
    gi: GraphicInst | None = None
    if type_id == struct_type:
        try:
            gi = _parse_graphic_inst(r, string_list, pairs, type_id, has_name_indices=True)
            r.skip(1)  # unknownFlag (0x21 for Global; same shape for off-page connectors)
        except (struct.error, IndexError, ValueError) as e:
            if ctx is not None:
                ctx.warn(warn_code, f"{error_label} parse error: {e}")
    else:
        _warn_unknown_structure(ctx, unknown_section, type_id, offset)
    if end_offset > 0:
        r.pos = end_offset
    if gi is not None:
        target.append(gi)
    r.skip(5)  # trailing data per record at stream level


def _parse_block_sheet_pin(r: BinaryReader) -> DsnBlockSheetPin:
    """Read one StructSymbolPin from a block instance's embedded symbol body."""
    _type_id, pin_end, _pairs = r.read_prefix_chain()
    r.try_read_preamble()
    name = r.read_string_len_zero()
    start_x = r.read_int32()
    start_y = r.read_int32()
    r.skip(8)  # hotpoint x/y
    r.read_uint16()  # pin shape
    r.skip(2)  # StructSymbolPin unknown field after pinShape
    port_type = r.read_uint32()
    r.skip(4)  # StructSymbolPin unknown field before display properties
    display_prop_count = r.read_uint16()
    for _ in range(display_prop_count):
        skip_structure(r)
    port_type_info = ORCAD_PORT_TYPES.get(port_type)
    pin = DsnBlockSheetPin(
        name=name,
        x=start_x,
        y=start_y,
        port_type=port_type,
        port_type_name=port_type_info.name if port_type_info is not None else "",
    )
    if pin_end > 0:
        r.pos = pin_end
    return pin


def _parse_block_instance(r: BinaryReader) -> DsnBlockInstance:
    """Parse a 0x0c DrawnInst (hierarchical block placement).

    Layout (fixture-proven; OOCP names ``DrawnInst`` but ships no parser):
    preamble, 8 unknown bytes, an empty package name, the block db_id (joins
    the Hierarchy stream's child-schematic edge), a 4x int16 bounding box,
    loc x/y, 4 unknown bytes, the SymbolDisplayProp list, one separator byte,
    then an embedded LibraryPart-shaped struct holding the block's sheet pins
    (StructSymbolPin: name, coordinates, port-type direction). After the
    embedded struct comes the block instance label, 14 bytes, and the T0x10
    records binding each sheet pin (by 1-based order) to a parent-page net id.
    """
    block = DsnBlockInstance()
    r.try_read_preamble()
    r.skip(8)  # unknown
    r.read_string_len_zero()  # empty package name
    block.db_id = r.read_uint32()
    r.skip(8)  # bounding box (4x int16)
    block.loc_x = r.read_int16()
    block.loc_y = r.read_int16()
    r.skip(4)  # unknown

    num_display_props = r.read_uint16()
    for _ in range(num_display_props):
        skip_structure(r)
    r.skip(1)  # separator before the embedded symbol struct

    # Embedded LibraryPart-shaped struct carrying the block's sheet pins.
    _emb_type, emb_end, _emb_pairs = r.read_prefix_chain()
    r.try_read_preamble()
    r.read_string_len_zero()  # empty symbol name
    r.read_string_len_zero()  # empty source library
    r.skip(4)  # unknown
    r.read_uint16()  # nPrimitives
    r.skip(8)  # unknown
    num_pins = r.read_uint16()
    for _ in range(num_pins):
        block.sheet_pins.append(_parse_block_sheet_pin(r))
    if emb_end > 0:
        r.pos = emb_end

    block.reference = r.read_string_len_zero()
    r.skip(14)  # unknown
    num_bindings = r.read_uint16()
    for _ in range(num_bindings):
        _t_type, t_end, _t_pairs = r.read_prefix_chain()
        r.try_read_preamble()
        binding = DsnBlockPinBinding(
            pin_order=r.read_uint16(),
            pin_x=r.read_int16(),
            pin_y=r.read_int16(),
            net_id=r.read_uint32(),
        )
        block.net_bindings.append(binding)
        if t_end > 0:
            r.pos = t_end
    return block


def _parse_net_display_props(r: BinaryReader, ctx: ParseContext | None) -> list[DsnNetDisplayProp]:
    """Parse the T0x34 section: per-net display records keyed by runtime net id.

    Each record is a self-describing structure (``type:1``, ``body_len:4``,
    ``zero:4``, then ``body_len`` body bytes). The body holds the runtime
    page-net id, an empty string, a zero uint32, and color/lineStyle/lineWidth.
    The declared body length keeps the section in sync even when a body fails
    to decode.
    """
    count = r.read_uint16()
    records: list[DsnNetDisplayProp] = []
    for _ in range(count):
        offset = r.pos
        r.read_uint8()  # structure type (0x34)
        body_len = r.read_uint32()
        r.skip(4)  # zero padding
        if body_len > r.remaining():
            msg = (
                f"T0x34 record at offset {offset} declares a {body_len}-byte body "
                f"but only {r.remaining()} bytes remain"
            )
            raise ValueError(msg)
        body = r.read_bytes(body_len)
        try:
            br = BinaryReader(body, "T0x34")
            net_id = br.read_uint32()
            br.read_string_len_zero()  # empty label
            br.read_uint32()  # unknown zero
            record = DsnNetDisplayProp(
                net_id=net_id,
                color=br.read_uint32(),
                line_style=br.read_uint32(),
                line_width=br.read_uint32(),
            )
            records.append(record)
        except (struct.error, IndexError, ValueError) as e:
            warn_optional(ctx, "dsn_t0x34", f"T0x34 record parse error: {e}")
    return records


def _skip_graphic_primitive(r: BinaryReader) -> tuple[int, DsnPageGraphic | None, int]:
    """Read one StructSthInPages0 primitive; return (prim_type, shape, body_len).

    A shape is returned for line/rect/ellipse primitives; the caller records
    CommentText separately and treats every other primitive (bitmap/OLE
    payloads) as an envelope to skip. All primitives share the ``u32 len`` + 4
    zero-byte envelope; the body spans ``len`` bytes after the zeros.
    """
    prim_type = r.read_uint8()
    prim_type2 = r.read_uint8()
    if prim_type != prim_type2:
        msg = f"primitive prefix mismatch {prim_type} != {prim_type2}"
        raise ValueError(msg)
    body_len = r.read_uint32()
    body_end = r.pos + 4 + body_len
    shape: DsnPageGraphic | None = None
    if prim_type in {PRIM_LINE, PRIM_RECT, PRIM_ELLIPSE}:
        r.skip(4)  # 4 zero bytes
        x1 = r.read_int32()
        y1 = r.read_int32()
        x2 = r.read_int32()
        y2 = r.read_int32()
        line_style = 0
        line_width = 0
        if r.pos + 8 <= body_end:
            line_style = r.read_uint32()
            line_width = r.read_uint32()
        kind = {PRIM_LINE: "line", PRIM_RECT: "box", PRIM_ELLIPSE: "ellipse"}[prim_type]
        shape = DsnPageGraphic(
            kind=kind,
            type_id=prim_type,
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            line_style=line_style,
            line_width=line_width,
        )
    r.pos = body_end
    r.try_read_preamble()
    return prim_type, shape, body_len


def _parse_comment_text_primitive(r: BinaryReader) -> DsnCommentText:
    """Parse a CommentText primitive (StructSthInPages0 body)."""
    body_len = r.read_uint32()
    body_end = r.pos + 4 + body_len
    r.skip(4)  # 4 zero bytes
    loc_x = r.read_int32()
    loc_y = r.read_int32()
    x2 = r.read_int32()
    y2 = r.read_int32()
    x1 = r.read_int32()
    y1 = r.read_int32()
    font_idx = r.read_uint16()
    r.skip(2)  # unknown
    text = r.read_string_len_zero()
    r.pos = body_end
    r.try_read_preamble()
    return DsnCommentText(
        text=text,
        loc_x=loc_x,
        loc_y=loc_y,
        bbox_x1=x1,
        bbox_y1=y1,
        bbox_x2=x2,
        bbox_y2=y2,
        font_idx=font_idx,
    )


_IMAGE_GRAPHIC_KINDS = {STRUCT_GRAPHIC_BITMAP: "bitmap", STRUCT_GRAPHIC_OLE_EMBED: "ole_embed"}
# Graphic-inst structure types validated on the corpus. Others (polygon,
# polyline, arc, bezier — zero fixture instances) get a diagnostic and are
# skipped via the record's prefix-chain end offset.
_KNOWN_GRAPHIC_TYPES = frozenset(
    {
        STRUCT_GRAPHIC_BOX,
        STRUCT_GRAPHIC_LINE,
        STRUCT_GRAPHIC_ELLIPSE,
        STRUCT_GRAPHIC_COMMENT_TEXT,
        STRUCT_GRAPHIC_BITMAP,
        STRUCT_GRAPHIC_OLE_EMBED,
    }
)


def _parse_page_graphic_inst(
    r: BinaryReader, page: DsnSchematicPage, ctx: ParseContext | None
) -> None:
    """Parse one page-tail StructGraphicInst and record it on *page*.

    The record's own prefix-chain end offset bounds the whole decode (including
    the SthInPages0 primitive wrapper), so a layout surprise never desyncs the
    graphic section — the caller re-anchors at ``end_offset``.
    """
    graphic_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    if type_id not in _KNOWN_GRAPHIC_TYPES:
        _warn_unknown_structure(ctx, "graphic", type_id, graphic_offset)
    try:
        r.try_read_preamble()
        r.skip(8)  # unknown
        r.read_string_len_zero()  # name (empty in the corpus)
        r.read_uint32()  # db_id
        r.skip(12)  # 6x int16 loc/bbox coordinates
        r.skip(1)  # color
        r.skip(3)  # unknown
        num_display_props = r.read_uint16()
        for _ in range(num_display_props):
            skip_structure(r)
        flag = r.read_uint8()
        if flag == 0x02:
            _read_sth_in_pages0(r, page, type_id, ctx)
    except (struct.error, IndexError, ValueError) as e:
        warn_optional(ctx, "dsn_graphic_inst", f"Graphic instance parse error: {e}")
    if end_offset > 0:
        r.pos = end_offset


def _read_sth_in_pages0(
    r: BinaryReader, page: DsnSchematicPage, type_id: int, ctx: ParseContext | None
) -> None:
    """Read the StructSthInPages0 wrapper and its primitives for a graphic inst."""
    r.read_prefix_chain()
    r.try_read_preamble()
    r.read_string_len_zero()  # symbol name
    r.read_string_len_zero()  # source library
    r.read_uint32()  # color
    num_prims = r.read_uint16()
    payload_size = 0
    for _ in range(num_prims):
        if r.data[r.pos] == PRIM_COMMENT_TEXT and r.data[r.pos + 1] == PRIM_COMMENT_TEXT:
            r.skip(2)  # matched primitive prefix
            page.comment_texts.append(_parse_comment_text_primitive(r))
        else:
            _prim_type, shape, body_len = _skip_graphic_primitive(r)
            if shape is not None:
                page.page_graphics.append(shape)
            payload_size = max(payload_size, body_len)
    if type_id in _IMAGE_GRAPHIC_KINDS:
        # Bitmap/OLE embeds carry a heavy payload; record kind and the envelope
        # byte size only, never the (multi-megabyte) payload bytes themselves.
        page.page_images.append(
            DsnPageImage(
                kind=_IMAGE_GRAPHIC_KINDS[type_id], type_id=type_id, payload_size=payload_size
            )
        )


def parse_page(
    data: bytes, string_list: list[str], ctx: ParseContext | None = None
) -> DsnSchematicPage:
    """Parse a Page stream into a SchematicPage.

    The per-section loops (wires, instances, ports, globals, off-page
    connectors) trap their own read errors and diagnose them, but the early
    structural reads — header, page name/size, title blocks, T0x34 records,
    and the net list — run outside those guards. A truncated or corrupt stream
    there raises a raw ``struct.error``/``IndexError`` that the loader path only
    catches as ``DsnFormatError``; convert it so a bad page is reported as a
    malformed file rather than crashing the whole project load (matches the
    Library-stream hardening).
    """
    try:
        return _parse_page(data, string_list, ctx)
    except (struct.error, IndexError, DsnFormatError) as exc:
        msg = f"Page stream is truncated or uses an unsupported layout: {exc}"
        raise DsnFormatError(msg, offset=0, type_id=0) from exc


def _parse_page(
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

    # T0x34 - per-net display records (color/lineStyle/lineWidth) keyed by
    # runtime page-net id; one per net group per page.
    page.net_display_props = _parse_net_display_props(r, ctx)

    # T0x35 - self-describing format; zero instances in the corpus. Keep the
    # skip and diagnose if any ever appear so a new layout is not lost silently.
    num_t35 = skip_counted_self_describing(r)
    if num_t35:
        warn_optional(ctx, "dsn_t0x35", f"page {page.name!r}: {num_t35} T0x35 record(s) skipped")

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
        wire_offset = r.pos
        type_id, end_offset, pairs = r.read_prefix_chain()
        r.try_read_preamble()
        wire = Wire(type_id=type_id)
        # Wire prefix-pair net properties (CDS_PHYS_NET_NAME, DIFFERENTIAL_PAIR,
        # VOLTAGE) resolve against the Library string list like instance props.
        wire.net_properties = _prop_entries_from_pairs(pairs, string_list)
        parsed_wire = False
        if type_id not in {STRUCT_WIRE_SCALAR, STRUCT_WIRE_BUS}:
            _warn_unknown_structure(ctx, "wire", type_id, wire_offset)

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

    # Placed instances and hierarchical block instances. The section mixes two
    # structure types: 0x0d part instances and 0x0c block placements, each with
    # its own body layout. Type-gate so a block record never desyncs the 0x0d
    # decode, and diagnose anything else instead of guessing.
    num_instances = r.read_uint16()
    for _ in range(num_instances):
        inst_offset = r.pos
        type_id, end_offset, pairs = r.read_prefix_chain()

        if type_id == STRUCT_PART_INST:
            r.try_read_preamble()
            inst = PlacedInstance()
            inst.props_list = _prop_entries_from_pairs(pairs, string_list)
            inst.props = dict(inst.props_list)
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

                # T0x10 structures = pin instances with net assignments. The
                # pin-order field is int16: |v| is the 1-based display order and
                # a negative sign is a user-placed no-connect marker.
                num_t0x10 = r.read_uint16()
                for _ in range(num_t0x10):
                    _t_type, t_end, _t_pairs = r.read_prefix_chain()
                    r.try_read_preamble()

                    pin = PinConnection()
                    order = r.read_int16()
                    pin.pin_order = abs(order)
                    pin.has_no_connect_marker = order < 0
                    pin.pin_number = str(pin.pin_order)
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
        elif type_id == STRUCT_DRAWN_INST:
            try:
                page.block_instances.append(_parse_block_instance(r))
            except (struct.error, IndexError, ValueError) as e:
                warn_optional(ctx, "dsn_block_instance", f"Block instance parse error: {e}")
        else:
            _warn_unknown_structure(ctx, "instance", type_id, inst_offset)

        # Jump to end_offset for safety
        if end_offset > 0:
            r.pos = end_offset

    # Ports. Like globals, the port body opens with an 8-byte string-index
    # prefix (net-name index + source-library index) ahead of the symbol name.
    num_ports = r.read_uint16()
    for _ in range(num_ports):
        port_offset = r.pos
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
                    has_name_indices=True,
                )
            except (struct.error, IndexError, ValueError) as e:
                if ctx is not None:
                    ctx.warn("dsn_port", f"Port parse error: {e}")
        else:
            _warn_unknown_structure(ctx, "port", type_id, port_offset)
        if end_offset > 0:
            r.pos = end_offset
        if port is not None:
            page.ports.append(port)

    # Globals (power symbols) — extract name, properties, and display props
    num_globals = r.read_uint16()
    for _ in range(num_globals):
        _parse_trailing_flag_graphic(
            r,
            string_list,
            page.globals,
            struct_type=STRUCT_GLOBAL,
            error_label="Global",
            warn_code="dsn_global",
            unknown_section="global",
            ctx=ctx,
        )

    # Off-page connectors
    num_opc = r.read_uint16()
    for _ in range(num_opc):
        _parse_trailing_flag_graphic(
            r,
            string_list,
            page.off_page_connectors,
            struct_type=STRUCT_OFF_PAGE_CONNECTOR,
            error_label="Off-page connector",
            warn_code="dsn_off_page_connector",
            unknown_section="off-page connector",
            ctx=ctx,
        )

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
            bus_entry_offset = r.pos
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
            else:
                _warn_unknown_structure(ctx, "bus entry", type_id, bus_entry_offset)
            if end_offset > 0:
                r.pos = end_offset
            if entry is not None:
                page.bus_entries.append(entry)
    except (struct.error, IndexError, ValueError) as e:
        warn_optional(ctx, "dsn_bus_entry", f"Bus entry parse error: {e}")
        return

    _parse_page_graphic_section(r, page, ctx)


def _parse_page_graphic_section(
    r: BinaryReader,
    page: DsnSchematicPage,
    ctx: ParseContext | None,
) -> None:
    """Parse the page-tail GraphicInst section plus the two trailing sub-lists.

    After bus entries the page carries ``u16 lenGraphicInsts`` + StructGraphicInst
    records (CommentText notes, Line/Box/Ellipse shapes, Bitmap/OLE image
    envelopes), then two more counted structure lists (``len10``/``len11``),
    both empty on every fixture. Each graphic's prefix-chain end offset keeps
    the section byte-exact, so the page parses to residue 0.
    """
    if r.remaining() < 2:
        return
    try:
        num_graphics = r.read_uint16()
        for _ in range(num_graphics):
            _parse_page_graphic_inst(r, page, ctx)

        len10 = r.read_uint16() if r.remaining() >= 2 else 0
        for _ in range(len10):
            skip_structure(r)
        len11 = r.read_uint16() if r.remaining() >= 2 else 0
        for _ in range(len11):
            skip_structure(r)
        if len10 or len11:
            warn_optional(
                ctx,
                "dsn_page_tail_structures",
                f"page {page.name!r}: unexpected trailing structures len10={len10} len11={len11}",
            )
    except (struct.error, IndexError, ValueError) as e:
        warn_optional(ctx, "dsn_graphic_section", f"Page graphic section parse error: {e}")


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


def parse_dsn_stream(
    data: bytes, string_list: list[str], ctx: ParseContext | None = None
) -> DsnDesignStream | None:
    """Parse the top-level ``DsnStream`` design-settings stream (F4).

    Structure type 0x04 whose prefix-chain pairs index the Library string list
    (``Library guid``, ``Time Format Index``), followed by a preamble whose
    optional payload embeds an ``InstalledVersion*`` JSON blob on 17.4-era
    files.
    """
    r = BinaryReader(data, "DsnStream")
    try:
        type_id, _end_offset, pairs = r.read_prefix_chain()
        if type_id != STRUCT_DSN_STREAM:
            warn_optional(
                ctx, "dsn_dsn_stream", f"DsnStream root structure is 0x{type_id:02x}, not 0x04"
            )
            return None
        props = _props_from_pairs(pairs, string_list)
        stream = DsnDesignStream(
            library_guid=props.get("Library guid", ""),
            time_format_index=props.get("Time Format Index", ""),
            properties=props,
        )
        if r.at_preamble():
            r.skip(4)  # preamble magic
            payload_len = r.read_uint32()
            payload = r.read_bytes(payload_len)
            stream.version_info = _extract_version_json(payload)
        return stream
    except (struct.error, IndexError, ValueError) as e:
        warn_optional(ctx, "dsn_dsn_stream", f"DsnStream parse error: {e}")
        return None


def _extract_version_json(payload: bytes) -> dict[str, str]:
    """Decode the embedded ``{...}`` JSON object from a DsnStream payload."""
    start = payload.find(b"{")
    end = payload.rfind(b"}")
    if start < 0 or end < start:
        return {}
    # A ``{...}`` slice always decodes to a JSON object (dict); the annotation
    # gives it a concrete type since json.loads returns Any.
    try:
        decoded: dict[str, object] = json.loads(payload[start : end + 1].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return {key: str(value) for key, value in decoded.items()}


def parse_symbol_types(data: bytes, ctx: ParseContext | None = None) -> list[DsnSymbolType]:
    """Parse a ``Symbols/$Types$`` name -> structure-type catalog (F5).

    Layout: a flat sequence of ``[len-zero name][uint16 type]`` entries until
    end of stream (e.g. ``ERC`` -> 0x4b, ``ERC_PHYSICAL`` -> 0x4b). Empty on
    most designs.
    """
    r = BinaryReader(data, "$Types$")
    types: list[DsnSymbolType] = []
    try:
        while r.remaining() >= 3:
            name = r.read_string_len_zero()
            type_id = r.read_uint16()
            types.append(DsnSymbolType(name=name, type_id=type_id))
    except (struct.error, IndexError, ValueError) as e:
        warn_optional(ctx, "dsn_symbol_types", f"Symbols/$Types$ parse error: {e}")
    return types


def _stream_is_parsed(path: str) -> bool:
    """True when *path* is consumed by a known DSN stream parser."""
    if path in {"Library", "Cache", "DsnStream", "NetBundleMapData"}:
        return True
    if path.endswith("/NetBundleMapData"):
        return True
    if path.startswith("Packages/"):
        return True
    if path.startswith("Views/") and (path.endswith("/Schematic") or "/Pages/" in path):
        return True
    if "Hierarchy/Hierarchy" in path:
        return True
    if path.startswith("CIS/VariantStore/"):
        return True
    if path.startswith("Symbols/ERC"):
        return True
    return path == "Symbols/$Types$"


def _stream_is_known_unparsed(path: str) -> bool:
    """True when *path* is an intentionally-skipped known stream."""
    if path in {"AnnotateCtrl", "AdminData", "HSObjects"}:
        return True
    if path.endswith(" Directory"):
        return True
    if path == "Graphics/$Types$" or path.startswith("Graphics/"):
        return True
    if path.startswith("CIS/"):
        return True
    # Views-nested CIS schematic storage and the constraint track (DCF,
    # Metadata, index storage) are recognised families handled elsewhere or
    # deliberately deferred; they are not unknown.
    return "/CISSchematic" in path or "/Constraint/" in path


def _build_stream_inventory(
    stream_entries: list[tuple[str, list[str]]],
    ole: olefile.OleFileIO,
    ctx: ParseContext | None,
) -> DsnStreamInventory:
    """Inventory every top-level OLE stream as parsed, known-unparsed, or unknown."""
    inventory = DsnStreamInventory()
    for path, _entry in stream_entries:
        if _stream_is_parsed(path):
            continue
        size = ole.get_size(path)
        ref = DsnStreamRef(path=path, size=size)
        if _stream_is_known_unparsed(path):
            inventory.known_unparsed_streams.append(ref)
        else:
            inventory.unknown_streams.append(ref)
    if inventory.unknown_streams:
        warn_optional(
            ctx,
            "dsn_unknown_stream",
            f"{len(inventory.unknown_streams)} unrecognised OLE stream(s): "
            + ", ".join(ref.path for ref in inventory.unknown_streams),
        )
    return inventory


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
        library_header, string_list, part_fields = parse_library(lib_data, ctx)

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
            design.hierarchies.append(
                parse_hierarchy_stream(hier_data, instance_db_id_set, stream_path=path, ctx=ctx)
            )
        design.net_id_mappings = merge_net_id_mappings(*mapping_groups)
        warn_repeated_sheet_blocks(design.hierarchies, ctx)

        # 6. Parse raw CIS VariantStore evidence when present.
        occurrence_to_instance = build_occurrence_to_instance(design.hierarchy_occurrences, ctx)
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

        # 10. Parse the DsnStream design-settings stream (GUID, version JSON).
        for path, entry in stream_entries:
            if path == "DsnStream":
                design.dsn_stream = parse_dsn_stream(ole.openstream(entry).read(), string_list, ctx)
                break

        # 11. Parse the Symbols/$Types$ name -> structure-type catalog.
        for path, entry in stream_entries:
            if path == "Symbols/$Types$":
                design.symbol_types = parse_symbol_types(ole.openstream(entry).read(), ctx)
                break

        # 12. Inventory every top-level stream (unknown vs known-unparsed).
        design.stream_inventory = _build_stream_inventory(stream_entries, ole, ctx)

        return design
    finally:
        ole.close()
