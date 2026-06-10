"""Region net inheritance for Altium pours.

Both Regions6 and ShapeBasedRegions6 must inherit the parent pour's net when
the record carries the unconnected sentinel (net == 0xFFFF) and points at a
known parent pour via its (sub)polygon index.
"""

import struct

from phosphor_eda.altium.pcb_parser import (
    _parse_regions,  # pyright: ignore[reportPrivateUsage]
    _parse_shape_based_regions,  # pyright: ignore[reportPrivateUsage]
)
from phosphor_eda.altium.pcb_records import EXTENDED_VERTEX_SIZE
from phosphor_eda.diagnostics import ParseContext
from phosphor_eda.pcb import LayerRole, PcbLayer, PcbNet

_NET_UNCONNECTED = 0xFFFF


def _pack_u16(v: int) -> bytes:
    return struct.pack("<H", v)


def _pack_u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _pack_i32(v: int) -> bytes:
    return struct.pack("<i", v)


def _pack_f64(v: float) -> bytes:
    return struct.pack("<d", v)


def _frame(rec_type: int, body: bytes) -> bytes:
    return bytes([rec_type]) + _pack_u32(len(body)) + body


def _region_body(net: int, props: bytes) -> bytes:
    """A Regions6 record body on layer 1 (Top copper) with three f64 vertices."""
    body = bytearray()
    body.append(1)  # layer
    body.extend(b"\x00\x00")  # 1-2
    body.extend(_pack_u16(net))  # 3-4
    body.extend(b"\x00\x00")  # 5-6
    body.extend(_pack_u16(0xFFFF))  # 7-8 component (none)
    body.extend(b"\x00\x00\x00\x00\x00\x00")  # 9-14
    body[14:16] = _pack_u16(0)  # holecount
    body.extend(b"\x00\x00")  # 16-17
    body.extend(_pack_u32(len(props)))  # 18-21
    body.extend(props)
    verts = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0)]
    body.extend(_pack_u32(len(verts)))
    for vx, vy in verts:
        body.extend(_pack_f64(vx))
        body.extend(_pack_f64(vy))
    return bytes(body)


def _shape_region_body(net: int, props: bytes) -> bytes:
    """A ShapeBasedRegions6 record body on layer 1 with three extended vertices."""
    body = bytearray()
    body.append(1)  # layer
    body.extend(b"\x00\x00")  # 1-2
    body.extend(_pack_u16(net))  # 3-4
    body.extend(b"\x00\x00")  # 5-6
    body.extend(_pack_u16(0xFFFF))  # 7-8 component (none)
    body.extend(b"\x00\x00\x00\x00\x00\x00")  # 9-14
    body[14:16] = _pack_u16(0)  # holecount
    body.extend(b"\x00\x00")  # 16-17
    body.extend(_pack_u32(len(props)))  # 18-21
    body.extend(props)

    pts = [(0, 0), (1000, 0), (1000, 1000)]
    # Stored count is N; parser reads N+1 vertices.
    body.extend(_pack_u32(len(pts) - 1))
    for vx, vy in pts:
        vertex = bytearray(EXTENDED_VERTEX_SIZE)
        vertex[0] = 0  # not round
        vertex[1:5] = _pack_i32(vx)
        vertex[5:9] = _pack_i32(vy)
        body.extend(vertex)
    return bytes(body)


def _layer_map() -> dict[int, PcbLayer]:
    return {1: PcbLayer(name="Top Layer", roles=(LayerRole.COPPER, LayerRole.FRONT), number=1)}


def _nets() -> dict[int, PcbNet]:
    return {7: PcbNet(number=7, name="GND")}


_PROPS_INHERIT = b"|POLYGONINDEX=2|SUBPOLYINDEX=5\x00"


def test_regions_inherit_pour_net_when_unconnected() -> None:
    ctx = ParseContext()
    stream = _frame(11, _region_body(_NET_UNCONNECTED, _PROPS_INHERIT))
    result = _parse_regions(
        stream, _nets(), _layer_map(), ctx, pour_id_map={5: "pour:1"}, pour_net_map={5: 7}
    )
    assert len(result) == 1
    assert result[0].net_number == 7
    assert result[0].net_name == "GND"


def test_shape_based_regions_inherit_pour_net_when_unconnected() -> None:
    ctx = ParseContext()
    stream = _frame(11, _shape_region_body(_NET_UNCONNECTED, _PROPS_INHERIT))
    result = _parse_shape_based_regions(
        stream, _nets(), _layer_map(), ctx, pour_id_map={5: "pour:1"}, pour_net_map={5: 7}
    )
    assert len(result) == 1
    assert result[0].net_number == 7, "shape-based region should inherit the pour's net"
    assert result[0].net_name == "GND"


def test_shape_based_regions_keep_direct_net() -> None:
    """A directly-assigned net is used as-is, not overridden by the pour map.

    Altium raw net indices map to domain numbers via +1, so raw 7 → domain 8;
    the parent pour map (5 -> 7) must not win here.
    """
    ctx = ParseContext()
    stream = _frame(11, _shape_region_body(7, _PROPS_INHERIT))
    result = _parse_shape_based_regions(
        stream, _nets(), _layer_map(), ctx, pour_id_map={5: "pour:1"}, pour_net_map={5: 7}
    )
    assert len(result) == 1
    assert result[0].net_number == 8
