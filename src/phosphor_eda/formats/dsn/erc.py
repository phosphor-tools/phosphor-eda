"""Parsers for OrCAD Capture ERC records and marker-symbol catalog streams.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from phosphor_eda.formats.common.diagnostics import warn_optional
from phosphor_eda.formats.dsn.binary_reader import (
    STRUCT_ERC_OBJECT,
    STRUCT_ERC_SYMBOL,
    BinaryReader,
    skip_structure,
)
from phosphor_eda.formats.dsn.raw_models import (
    DsnErcObject,
    DsnErcSymbol,
    DsnMarkerCategory,
)

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext

MAX_ERC_OBJECTS = 4096
_MAX_ERC_DISPLAY_PROPS = 4096


def parse_erc_object(
    r: BinaryReader,
    page_name: str,
    ctx: ParseContext | None,
) -> DsnErcObject | None:
    start_offset = r.pos
    type_id, end_offset, _pairs = r.read_prefix_chain()
    try:
        if type_id != STRUCT_ERC_OBJECT:
            warn_optional(
                ctx,
                "dsn_erc_object",
                f"{page_name}: unsupported ERC object type 0x{type_id:02x} "
                f"at offset {start_offset}",
            )
            return None
        # StructERCObject embeds StructGraphicInst, then stores the three
        # diagnostic strings as message/subject/detail (OOCP s0/s1/s2).
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
        erc_object.message = r.read_string_len_zero()
        erc_object.subject = r.read_string_len_zero()
        erc_object.detail = r.read_string_len_zero()
        if end_offset > 0:
            if r.pos > end_offset:
                msg = f"ERC object parsed to byte {r.pos}, expected end offset {end_offset}"
                raise ValueError(msg)
        # A short-form record carries no declared end offset, so the long-form
        # overrun guard and cursor restore below never run. Bound it against the
        # stream end instead: a desync that slices past EOF must fail into the
        # caller's warn-and-drop path, not silently return truncated fields.
        elif r.pos > len(r.data):
            msg = f"short-form ERC object parsed to byte {r.pos} past stream end {len(r.data)}"
            raise ValueError(msg)
        return erc_object
    finally:
        if end_offset > 0:
            r.pos = end_offset


def _erc_marker_category(stream_path: str) -> DsnMarkerCategory:
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
            warn_optional(
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
        warn_optional(ctx, "dsn_erc_symbol", f"{stream_path}: ERC symbol parse error: {e}")
        return None
