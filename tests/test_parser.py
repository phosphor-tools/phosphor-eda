import struct

import pytest
from fixture_paths import FIXTURES

from phosphor_eda.formats.dsn.binary_reader import BinaryReader, skip_self_describing
from phosphor_eda.formats.dsn.errors import DsnFormatError
from phosphor_eda.formats.dsn.parser import parse_dsn, parse_page

DSN_FILE = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"


def test_parse_dsn_single_page():
    """Single-page design should have one page."""
    design = parse_dsn(DSN_FILE)
    assert len(design.pages) == 1
    assert design.pages[0].name == "PAGE1"
    assert design.pages[0].size == "A3"


def test_parse_dsn_components():
    """Parse the Pico DSN and verify known components exist."""
    design = parse_dsn(DSN_FILE)
    refs = {inst.reference for inst in design.pages[0].instances}
    assert "U1" in refs  # RP2040
    assert "U2" in refs  # RT6150B
    assert "U3" in refs  # W25Q16JV
    assert len(design.pages[0].instances) == 51


def test_parse_dsn_nets():
    design = parse_dsn(DSN_FILE)
    net_names = {n.name for n in design.pages[0].nets}
    assert "GND" in net_names
    assert "3V3" in net_names
    assert "VBUS" in net_names


def test_parse_dsn_globals():
    design = parse_dsn(DSN_FILE)
    assert len(design.pages[0].globals) == 52


def test_parse_dsn_string_list():
    design = parse_dsn(DSN_FILE)
    assert len(design.string_list) > 0


def test_public_api_imports():
    from phosphor_eda.formats.dsn import (
        ParsedDesign,
        SchematicPage,
        build_netlist,
        dsn_to_design,
        parse_dsn,
    )

    assert callable(parse_dsn)
    assert callable(build_netlist)
    assert callable(dsn_to_design)
    assert ParsedDesign is not None
    assert SchematicPage is not None


def test_skip_self_describing_bounds_checks_body_length():
    # Header claims a 258MB body inside a 16-byte stream (PSD-era files
    # carry structures this parser misinterprets — the declared length is
    # garbage and must not be trusted).
    data = bytes([0x34]) + struct.pack("<I", 258_000_000) + b"\x00" * 11
    reader = BinaryReader(data, "Page")

    with pytest.raises(DsnFormatError) as excinfo:
        skip_self_describing(reader)

    message = str(excinfo.value)
    assert "0x34" in message
    assert "offset 0" in message
    assert excinfo.value.type_id == 0x34
    assert excinfo.value.offset == 0


def test_skip_self_describing_accepts_valid_body():
    data = bytes([0x35]) + struct.pack("<I", 4) + b"\x00" * 4 + b"BODY"
    reader = BinaryReader(data, "Page")
    assert skip_self_describing(reader) == 0x35
    assert reader.pos == len(data)


def _prefix_short(type_id: int) -> bytes:
    # Short-form prefix: [type_id u8][pair count i16 = 0]
    return bytes([type_id]) + struct.pack("<h", 0)


def _string_len_zero(text: str) -> bytes:
    raw = text.encode("cp1252")
    return struct.pack("<H", len(raw)) + raw + b"\x00"


def _minimal_page_with_aliased_wire() -> bytes:
    """A hand-built Page stream: one net, one wire carrying one net label.

    Layout follows parse_page / OpenOrCadParser StructWire: header, 156-byte
    page settings, empty title-block/T0x34/T0x35 sections, a one-entry net
    list, and a single scalar wire whose body carries one StructAlias.
    """
    out = bytearray()
    out += _prefix_short(0x31)  # page prefix chain (short form only)
    out += _string_len_zero("PAGE1")  # page name
    out += _string_len_zero("A4")  # page size
    out += b"\x00" * 156  # page settings
    out += struct.pack("<H", 0)  # title blocks
    out += struct.pack("<H", 0)  # T0x34
    out += struct.pack("<H", 0)  # T0x35
    out += struct.pack("<H", 1)  # nets
    out += _string_len_zero("WD_NET")
    out += struct.pack("<I", 7)  # net id

    out += struct.pack("<H", 1)  # wires

    # Wire body per StructWire: unknown u32, id, color, start/end coords,
    # then [unknown u8][alias count u16][aliases...].
    alias_body = bytearray()
    alias_body += _prefix_short(0x0D)  # StructAlias prefix
    alias_body += struct.pack("<ii", 5, 0)  # locX, locY
    alias_body += struct.pack("<I", 0)  # color
    alias_body += struct.pack("<I", 0)  # rotation
    alias_body += struct.pack("<I", 2)  # font index
    alias_body += _string_len_zero("IO_RESET")

    wire_body = bytearray()
    wire_body += struct.pack("<I", 0)  # unknown
    wire_body += struct.pack("<I", 7)  # wire id
    wire_body += struct.pack("<I", 0)  # color
    wire_body += struct.pack("<ii", 0, 0)  # start
    wire_body += struct.pack("<ii", 10, 0)  # end
    wire_body += b"\x00"  # unknown byte before alias count
    wire_body += struct.pack("<H", 1)  # alias count
    wire_body += alias_body

    # Long-form prefix so the wire has an end offset the parser can re-anchor
    # to: [type u8][byte_offset u32][padding u32] + short-form prefix.
    wire_record = bytearray()
    short_prefix_len = 3
    byte_offset = short_prefix_len + len(wire_body)
    wire_record += bytes([20]) + struct.pack("<I", byte_offset) + struct.pack("<I", 0)
    wire_record += _prefix_short(20)
    wire_record += wire_body
    out += wire_record

    out += struct.pack("<H", 0)  # instances
    out += struct.pack("<H", 0)  # ports
    out += struct.pack("<H", 0)  # globals
    out += struct.pack("<H", 0)  # off-page connectors
    return bytes(out)


def test_parse_page_reads_wire_net_labels():
    page = parse_page(_minimal_page_with_aliased_wire(), [])

    assert len(page.wires) == 1
    wire = page.wires[0]
    assert wire.wire_id == 7
    assert [alias.name for alias in wire.aliases] == ["IO_RESET"]
    assert wire.aliases[0].x == 5
    assert wire.aliases[0].font_idx == 2
