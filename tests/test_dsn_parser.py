"""Tests for OrCAD DSN binary stream parsing."""

import struct

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.parser import parse_net_bundle_map_data


def _dsn_string(value: str) -> bytes:
    encoded = value.encode("ascii")
    return struct.pack("<H", len(encoded)) + encoded + b"\x00"


def _short_prefix(type_id: int) -> bytes:
    return bytes([type_id]) + struct.pack("<h", 0)


def test_parse_net_bundle_map_data_stream() -> None:
    data = (
        b"\x00\x00"
        + struct.pack("<H", 1)
        + _dsn_string("I2C")
        + _short_prefix(103)
        + (b"\x00" * 6)
        + struct.pack("<H", 2)
        + _dsn_string("SDA")
        + struct.pack("<H", 1)
        + _dsn_string("SCL")
        + struct.pack("<H", 1)
    )

    bundles = parse_net_bundle_map_data(data)

    assert len(bundles) == 1
    assert bundles[0].name == "I2C"
    assert [(member.name, member.wire_type) for member in bundles[0].members] == [
        ("SDA", 1),
        ("SCL", 1),
    ]


def test_malformed_net_bundle_map_data_warns_and_returns_no_bundles() -> None:
    ctx = ParseContext()

    bundles = parse_net_bundle_map_data(b"\x00\x00\x01", ctx)

    assert bundles == []
    assert any(issue.category == "dsn_net_bundle_map" for issue in ctx.issues)
