"""Parser for OrCAD Capture .DSN files.

Extracts component information, net names, and connectivity from the
binary OLE compound document format used by OrCAD Capture.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

import struct
from pathlib import Path

import olefile

from ecad_tools.dsn.binary_reader import (
    BinaryReader,
    PAGE_SETTINGS_SIZE,
    PREAMBLE,
)
from ecad_tools.dsn.models import (
    GraphicInst,
    NetIdMapping,
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    SchematicPage,
)


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
    """
    type_id = r.read_uint8()
    body_len = r.read_uint32()
    r.skip(4)  # zero padding
    r.skip(body_len)  # skip the body
    return type_id


def skip_counted_structures(r: BinaryReader, label: str = "") -> int:
    """Read a uint16 count, then skip that many structures. Returns the count."""
    count = r.read_uint16()
    for _ in range(count):
        skip_structure(r)
    return count


def skip_counted_self_describing(r: BinaryReader) -> int:
    """Read a uint16 count, then skip that many self-describing structures."""
    count = r.read_uint16()
    for _ in range(count):
        skip_self_describing(r)
    return count


# --- Stream parsers ---


def parse_library(data: bytes) -> tuple[list[str], list[str]]:
    """Parse the Library stream. Returns (string_list, part_fields)."""
    r = BinaryReader(data, "Library")

    # Introduction (32-byte padded string)
    intro_start = r.pos
    r.read_string_zero()
    r.pos = intro_start + 32  # pad to 32 bytes

    # Version
    r.read_uint16()  # version_major
    r.read_uint16()  # version_minor

    # Timestamps (uint32 + uint32 = 8 bytes)
    r.read_uint32()  # create_date
    r.read_uint32()  # modify_date

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
    part_fields = []
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

    string_list = []
    for _ in range(str_lst_len):
        string_list.append(r.read_string_len_zero())

    return string_list, part_fields


def parse_page(data: bytes, string_list: list[str]) -> SchematicPage:
    """Parse a Page stream into a SchematicPage.

    Uses skip-based approach: read the header and net list precisely,
    then skip structures we don't need using prefix chain end_offsets.
    For placed instances and globals, we use end_offsets to bound our parsing
    so errors don't cascade.
    """
    page = SchematicPage()
    r = BinaryReader(data, "Page")

    # Page prefixes
    r.read_prefix_chain()
    r.try_read_preamble()

    # Page name and size
    page.name = r.read_string_len_zero()
    page.size = r.read_string_len_zero()

    # Page settings (inline, 156 bytes)
    r.skip(PAGE_SETTINGS_SIZE)

    # Title blocks - skip
    skip_counted_structures(r, "titleBlocks")

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
        type_id, end_offset, pairs = r.read_prefix_chain()
        r.try_read_preamble()

        try:
            r.skip(4)  # unknown_0
            wire_id = r.read_uint32()  # this IS the net_id from page net list
            r.skip(4)  # color
            start_x = r.read_int32()
            start_y = r.read_int32()
            end_x = r.read_int32()
            end_y = r.read_int32()
            wire_net_map.setdefault((start_x, start_y), set()).add(wire_id)
            wire_net_map.setdefault((end_x, end_y), set()).add(wire_id)
        except (struct.error, IndexError, ValueError):
            pass

        if end_offset > 0:
            r.pos = end_offset
    page.wire_net_map = wire_net_map

    # Placed instances — parse body to extract reference designator
    num_instances = r.read_uint16()
    for _ in range(num_instances):
        type_id, end_offset, pairs = r.read_prefix_chain()
        r.try_read_preamble()

        inst = PlacedInstance()

        # Resolve name-value pairs from the string list
        props = {}
        for name_idx, value_idx in pairs:
            name = string_list[name_idx] if 0 <= name_idx < len(string_list) else f"idx:{name_idx}"
            value = string_list[value_idx] if 0 <= value_idx < len(string_list) else f"idx:{value_idx}"
            props[name] = value
        inst._props = props

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
                t_type, t_end, t_pairs = r.read_prefix_chain()
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
        except (struct.error, IndexError, ValueError) as e:
            print(f"    Warning: PlacedInstance parse error: {e}")

        # Jump to end_offset for safety
        if end_offset > 0:
            r.pos = end_offset
        page.instances.append(inst)

    # Ports - skip
    num_ports = r.read_uint16()
    for _ in range(num_ports):
        skip_structure(r)

    # Globals (power symbols) — extract name, properties, and display props
    num_globals = r.read_uint16()
    for _ in range(num_globals):
        type_id, end_offset, pairs = r.read_prefix_chain()
        r.try_read_preamble()

        gi = GraphicInst()

        # Resolve name-value pairs from global's own prefix
        global_props = {}
        for name_idx, value_idx in pairs:
            name = string_list[name_idx] if 0 <= name_idx < len(string_list) else f"idx:{name_idx}"
            value = string_list[value_idx] if 0 <= value_idx < len(string_list) else f"idx:{value_idx}"
            global_props[name] = value

        try:
            # The 8 "unknown" bytes are two string list indices:
            # idx1 = actual net name, idx2 = source library path
            net_name_idx = r.read_uint32()
            r.skip(4)  # source library path index (not needed)
            if 0 <= net_name_idx < len(string_list):
                global_props["_net_name"] = string_list[net_name_idx]

            gi.name = r.read_string_len_zero()  # symbol name (GND, VCC_BAR)
            gi.db_id = r.read_uint32()

            # Read coordinates and color per StructGraphicInst
            gi.loc_y = r.read_int16()
            gi.loc_x = r.read_int16()
            r.skip(8)  # y2, x2, x1, y1
            r.skip(1)  # color
            r.skip(3)  # 3 unknown bytes

            # Skip SymbolDisplayProp structures
            num_display_props = r.read_uint16()
            for _ in range(num_display_props):
                skip_structure(r)

            r.skip(1)  # unknownFlag (0x21 for Global)
        except (struct.error, IndexError, ValueError) as e:
            print(f"    Warning: Global parse error: {e}")

        gi._props = global_props

        if end_offset > 0:
            r.pos = end_offset
        page.globals.append(gi)
        r.skip(5)  # trailing data per global at stream level

    # Off-page connectors - skip
    num_opc = r.read_uint16()
    for _ in range(num_opc):
        skip_structure(r)
        r.skip(5)  # trailing data

    return page


def parse_hierarchy(data: bytes) -> list[NetIdMapping]:
    """Parse the Hierarchy stream for net-to-ID mappings."""
    r = BinaryReader(data, "Hierarchy")
    mappings = []

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


def parse_cache(data: bytes) -> dict[str, list[str]]:
    """Parse the Cache stream to extract symbol pin names.

    Returns a dict mapping symbol names (without .Normal suffix) to ordered
    lists of pin names. Pin order matches T0x10 pin_number (1-indexed).

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

    return pin_map


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


# --- Main entry point ---


def parse_dsn(dsn_path: Path) -> ParsedDesign:
    """Parse an OrCAD Capture .DSN file."""
    ole = olefile.OleFileIO(str(dsn_path))

    # 1. Parse Library stream first (needed for string list)
    lib_data = ole.openstream("Library").read()
    string_list, part_fields = parse_library(lib_data)

    design = ParsedDesign()
    design.string_list = string_list
    design.part_fields = part_fields

    # 2. Parse Page stream(s)
    for entry in ole.listdir():
        path = "/".join(entry)
        if path.startswith("Views/") and "/Pages/" in path:
            page_data = ole.openstream(entry).read()
            page = parse_page(page_data, string_list)
            design.pages.append(page)

    # 3. Parse Hierarchy stream
    for entry in ole.listdir():
        path = "/".join(entry)
        if "Hierarchy/Hierarchy" in path:
            hier_data = ole.openstream(entry).read()
            design.net_id_mappings = parse_hierarchy(hier_data)

    # 4. Parse Cache stream for symbol pin names
    for entry in ole.listdir():
        path = "/".join(entry)
        if path == "Cache":
            cache_data = ole.openstream(entry).read()
            design.symbol_pin_names = parse_cache(cache_data)

    ole.close()
    return design
