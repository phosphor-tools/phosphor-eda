"""Parser for the OrCAD Capture design-cache (``Cache``) stream.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from phosphor_eda.formats.dsn.binary_reader import (
    PREAMBLE,
    STRUCT_LIBRARY_PART,
    STRUCT_SYMBOL_PIN_BUS,
    STRUCT_SYMBOL_PIN_SCALAR,
    BinaryReader,
    decode_orcad_text,
)
from phosphor_eda.formats.dsn.pins import ORCAD_PORT_TYPES
from phosphor_eda.formats.dsn.raw_models import DsnCacheSymbols, DsnSymbolPin

if TYPE_CHECKING:
    from collections.abc import Iterator

    from phosphor_eda.formats.common.diagnostics import ParseContext

_MAX_SYMBOL_PIN_DISPLAY_PROPS = 64
_SYMBOL_PIN_TYPES = (STRUCT_SYMBOL_PIN_SCALAR, STRUCT_SYMBOL_PIN_BUS)


def _can_read_string_len_zero(data: bytes, pos: int, limit: int | None = None) -> bool:
    """Check if readStringLenZeroTerm would succeed at `pos`.

    Matches C++ semantics: reads uint16 length, then scans for null terminator,
    and verifies the distance to null equals the length prefix. ``limit`` bounds
    the terminator scan so the probe cannot accept a string (and terminator)
    that runs past the end of the structure being decoded.
    """
    size = len(data) if limit is None else min(limit, len(data))
    if pos + 2 > size:
        return False
    length = struct.unpack_from("<H", data, pos)[0]
    if length == 0:
        return pos + 2 < size and data[pos + 2] == 0
    start = pos + 2
    try:
        null_pos = data.index(b"\x00", start, size)
    except ValueError:
        return False
    return (null_pos - start) == length


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
                if r.pos + 2 > size:
                    break
                some_val = r.read_uint16()
                if r.pos >= size:
                    break
                if r.pos + 1 >= size:
                    r.skip(1)
                    break
                # hasMysterious2Byte = !can_read_string
                if _can_read_string_len_zero(data, r.pos, size):
                    r.read_string_len_zero()
                elif _can_read_string_len_zero(data, r.pos + 2, size):
                    r.skip(2)  # mysterious 2 bytes
                    r.read_string_len_zero()
                else:
                    break
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

        # Extract pin names from symbol entries (struct_type 0x18 = LibraryPart)
        if struct_type == STRUCT_LIBRARY_PART and name:
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


def _scan_preamble_names(data: bytes, start: int, end: int) -> Iterator[tuple[int, str]]:
    """Yield ``(preamble_offset, name)`` for each named preamble in ``data[start:end]``.

    A LibraryPart body interleaves ``FF E4 5C 39`` preamble records; each one
    is followed by a self-describing length and, when present, a
    length-prefixed name. This is the single byte scan the legacy pin-name and
    sub-symbol-marker readers both fold over.
    """
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
        if _can_read_string_len_zero(data, p, end):
            length = struct.unpack_from("<H", data, p)[0]
            if length > 0:
                name = decode_orcad_text(data[p + 2 : p + 2 + length])
                yield (idx, name)
        pos = idx + 1  # advance past this preamble


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

    for _offset, name in _scan_preamble_names(data, start, end):
        if ".Normal" in name:
            # Start of a new sub-symbol
            current_sym = name.replace(".Normal", "")
            if current_sym not in result:
                result[current_sym] = []
        elif name != sym_name and len(name) < 30:
            result[current_sym].append(name)

    return result


def _symbol_name_markers(
    data: bytes,
    start: int,
    end: int,
    sym_name: str,
) -> list[tuple[int, str]]:
    """Return sub-symbol markers from the legacy cache preamble convention."""
    markers: list[tuple[int, str]] = []
    for idx, name in _scan_preamble_names(data, start, end):
        if ".Normal" in name:
            markers.append((idx, name.replace(".Normal", "")))
        elif name == sym_name:
            markers.append((idx, sym_name.replace(".Normal", "")))
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
        if data[offset] not in _SYMBOL_PIN_TYPES:
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
    if structure_type not in _SYMBOL_PIN_TYPES:
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
