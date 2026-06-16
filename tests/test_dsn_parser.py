"""Tests for OrCAD DSN binary stream parsing."""

import struct

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.parser import _parse_net_bundle_map_streams, parse_net_bundle_map_data


def _dsn_string(value: str) -> bytes:
    encoded = value.encode("ascii")
    return struct.pack("<H", len(encoded)) + encoded + b"\x00"


def _short_prefix(type_id: int) -> bytes:
    return bytes([type_id]) + struct.pack("<h", 0)


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
