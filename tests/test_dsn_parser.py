"""Tests for OrCAD DSN binary stream parsing."""

import struct

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.parser import (
    _parse_net_bundle_map_streams,
    parse_net_bundle_map_data,
    parse_package_stream,
)


def _dsn_string(value: str) -> bytes:
    encoded = value.encode("ascii")
    return struct.pack("<H", len(encoded)) + encoded + b"\x00"


def _short_prefix(type_id: int, size: int = 0) -> bytes:
    return bytes([type_id]) + struct.pack("<h", size)


def _structure(type_id: int, body: bytes) -> bytes:
    return (
        bytes([type_id])
        + struct.pack("<I", len(body) + 3)
        + (b"\x00" * 4)
        + _short_prefix(type_id, -1)
        + body
    )


def _structure_with_end_offset(type_id: int, body: bytes, byte_offset: int) -> bytes:
    return (
        bytes([type_id])
        + struct.pack("<I", byte_offset)
        + (b"\x00" * 4)
        + _short_prefix(type_id, -1)
        + body
    )


def _net_bundle_map_stream(name: str, members: list[str]) -> bytes:
    member_data = b"".join(_dsn_string(member) + struct.pack("<H", 1) for member in members)
    return (
        b"\x00\x00"
        + struct.pack("<H", 1)
        + _dsn_string(name)
        + _short_prefix(103)
        + (b"\x00" * 6)
        + struct.pack("<H", len(members))
        + member_data
    )


def test_parse_net_bundle_map_data_stream() -> None:
    data = _net_bundle_map_stream("I2C", ["SDA", "SCL"])

    bundles = parse_net_bundle_map_data(data)

    assert len(bundles) == 1
    assert bundles[0].name == "I2C"
    assert [(member.name, member.wire_type) for member in bundles[0].members] == [
        ("SDA", 1),
        ("SCL", 1),
    ]


def test_parse_net_bundle_map_streams_accumulates_multiple_streams() -> None:
    bundles = _parse_net_bundle_map_streams(
        [
            _net_bundle_map_stream("I2C", ["SDA", "SCL"]),
            _net_bundle_map_stream("SPI", ["MOSI", "MISO"]),
        ]
    )

    assert [bundle.name for bundle in bundles] == ["I2C", "SPI"]


def test_malformed_net_bundle_map_data_warns_and_returns_no_bundles() -> None:
    ctx = ParseContext()

    bundles = parse_net_bundle_map_data(b"\x00\x00\x01", ctx)

    assert bundles == []
    assert any(issue.category == "dsn_net_bundle_map" for issue in ctx.issues)


def test_parse_package_stream_preserves_uncommon_raw_fields() -> None:
    stream_path = "Packages/SYNTH"
    part_cell = _structure(
        6,
        _dsn_string("SYNTH_CELL")
        + _dsn_string("unused")
        + struct.pack("<H", 2)
        + _dsn_string("SYNTH_CELL.Normal")
        + _dsn_string("SYNTH_CELL.Convert"),
    )
    library_part = _structure(
        24,
        _dsn_string("SYNTH_CELL.Normal") + _dsn_string("synthetic.olb"),
    )
    device = _structure(
        32,
        _dsn_string("A")
        + _dsn_string("SYNTH")
        + struct.pack("<H", 3)
        + struct.pack("<h", -1)
        + _dsn_string("A1")
        + bytes([0x80 | 5])
        + _dsn_string("B2")
        + bytes([0x7F]),
    )
    package = _structure(
        31,
        _dsn_string("SYNTH")
        + _dsn_string("synthetic.olb")
        + _dsn_string("U")
        + _dsn_string("")
        + _dsn_string("SYNTH_FOOTPRINT")
        + struct.pack("<H", 1)
        + device,
    )
    data = struct.pack("<H", 1) + part_cell + struct.pack("<H", 1) + library_part + package

    parsed = parse_package_stream(data, ParseContext(), stream_path)

    assert parsed is not None
    assert parsed.name == "SYNTH"
    assert parsed.source_library == "synthetic.olb"
    assert parsed.pcb_footprint == "SYNTH_FOOTPRINT"
    assert len(parsed.part_cells) == 1
    assert parsed.part_cells[0].convert_name == "SYNTH_CELL.Convert"
    assert [part.name for part in parsed.part_cells[0].library_parts] == ["SYNTH_CELL.Normal"]
    assert [part.name for part in parsed.library_parts] == ["SYNTH_CELL.Normal"]
    assert len(parsed.devices) == 1
    assert [
        (pin.order, pin.package_pin, pin.ignored, pin.group) for pin in parsed.devices[0].pins
    ] == [
        (0, "", False, ""),
        (1, "A1", True, "5"),
        (2, "B2", False, ""),
    ]


def test_package_stream_diagnostic_has_one_authoritative_offset() -> None:
    ctx = ParseContext()
    stream_path = "Packages/BAD"

    parsed = parse_package_stream(struct.pack("<H", 65535), ctx, stream_path)

    assert parsed is None
    assert len(ctx.issues) == 1
    assert ctx.issues[0].message == (
        "Packages/BAD at byte offset 0: implausible part-cell count 65535"
    )


def test_package_library_part_overrun_is_diagnostic() -> None:
    stream_path = "Packages/BAD_LIB"
    part_cell = _structure(
        6,
        _dsn_string("SYNTH_CELL")
        + _dsn_string("unused")
        + struct.pack("<H", 1)
        + _dsn_string("SYNTH_CELL.Normal"),
    )
    library_part = _structure_with_end_offset(
        24,
        _dsn_string("SYNTH_CELL.Normal") + _dsn_string("synthetic.olb"),
        byte_offset=6,
    )
    data = struct.pack("<H", 1) + part_cell + struct.pack("<H", 1) + library_part
    ctx = ParseContext()

    parsed = parse_package_stream(data, ctx, stream_path)

    assert parsed is None
    assert len(ctx.issues) == 1
    assert ctx.issues[0].category == "dsn_package_stream"
    assert "library part parsed to byte" in ctx.issues[0].message
