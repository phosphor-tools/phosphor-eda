"""Tests for Altium PCB record types with from_bytes loaders."""

import math
import struct

from phosphor_eda.formats.altium.geometry import linearize_arc_vertices
from phosphor_eda.formats.altium.pcb_records import (
    ArcRecord,
    ExtendedVertex,
    FillRecord,
    PadRecord,
    RegionRecord,
    TextRecord,
    TrackRecord,
    ViaRecord,
)
from phosphor_eda.formats.common.diagnostics import ParseContext


def _pack_i32(val: int) -> bytes:
    return val.to_bytes(4, "little", signed=True)


def _pack_u16(val: int) -> bytes:
    return val.to_bytes(2, "little", signed=False)


def _pack_u32(val: int) -> bytes:
    return val.to_bytes(4, "little", signed=False)


def _pack_f64(val: float) -> bytes:
    return struct.pack("<d", val)


# ---------------------------------------------------------------------------
# TrackRecord
# ---------------------------------------------------------------------------


def _make_track_body(
    layer: int = 1,
    flags2: int = 0,
    net: int = 5,
    polygon: int = 0xFFFF,
    component: int = 0xFFFF,
    subpoly_index: int = 0,
    x1: int = 100000,
    y1: int = 200000,
    x2: int = 300000,
    y2: int = 200000,
    width: int = 10000,
    keepout_restrictions: int = 0,
) -> bytes:
    """Build a track body."""
    body = bytearray(57)
    body[0] = layer
    body[2] = flags2
    body[3:5] = _pack_u16(net)
    body[5:7] = _pack_u16(polygon)
    body[7:9] = _pack_u16(component)
    body[9:11] = _pack_u16(subpoly_index)
    body[13:17] = _pack_i32(x1)
    body[17:21] = _pack_i32(y1)
    body[21:25] = _pack_i32(x2)
    body[25:29] = _pack_i32(y2)
    body[29:33] = _pack_i32(width)
    body[56] = keepout_restrictions
    return bytes(body)


def test_track_from_bytes():
    ctx = ParseContext()
    body = _make_track_body(layer=1, net=5, x1=100, y1=200, x2=300, y2=200, width=50)
    rec = TrackRecord.from_bytes(body, ctx)
    assert rec is not None
    assert rec.layer == 1
    assert rec.net == 5
    assert rec.component == 0xFFFF
    assert rec.polygon == 0xFFFF
    assert rec.subpoly_index == 0
    assert rec.start == (100, 200)
    assert rec.end == (300, 200)
    assert rec.width == 50
    assert len(ctx.issues) == 0


def test_track_from_bytes_parses_polygon_and_keepout_metadata():
    ctx = ParseContext()
    body = _make_track_body(
        polygon=29,
        subpoly_index=6,
        flags2=0x02,
        keepout_restrictions=31,
    )
    rec = TrackRecord.from_bytes(body, ctx)
    assert rec is not None
    assert rec.polygon == 29
    assert rec.subpoly_index == 6
    assert rec.keepout_restrictions == 31
    assert rec.is_keepout is True


def test_track_truncated():
    ctx = ParseContext()
    body = b"\x01\x00" * 10  # 20 bytes, too short
    rec = TrackRecord.from_bytes(body, ctx)
    assert rec is None
    assert len(ctx.issues) == 1
    assert "too short" in ctx.issues[0].message.lower()


# ---------------------------------------------------------------------------
# ViaRecord
# ---------------------------------------------------------------------------


def _make_via_body(
    net: int = 3,
    component: int = 0xFFFF,
    x: int = 500000,
    y: int = 600000,
    diameter: int = 50000,
    hole: int = 25000,
    start_layer: int = 1,
    end_layer: int = 32,
) -> bytes:
    body = bytearray(31)
    body[3:5] = _pack_u16(net)
    body[7:9] = _pack_u16(component)
    body[13:17] = _pack_i32(x)
    body[17:21] = _pack_i32(y)
    body[21:25] = _pack_i32(diameter)
    body[25:29] = _pack_i32(hole)
    body[29] = start_layer
    body[30] = end_layer
    return bytes(body)


def test_via_from_bytes():
    ctx = ParseContext()
    body = _make_via_body(net=3, x=500, y=600, diameter=50, hole=25)
    rec = ViaRecord.from_bytes(body, ctx)
    assert rec is not None
    assert rec.net == 3
    assert rec.position == (500, 600)
    assert rec.diameter == 50
    assert rec.hole_size == 25
    assert rec.start_layer == 1
    assert rec.end_layer == 32


def test_via_from_bytes_parses_component():
    ctx = ParseContext()
    body = _make_via_body(component=7)
    rec = ViaRecord.from_bytes(body, ctx)
    assert rec is not None
    assert rec.component == 7


def test_via_truncated():
    ctx = ParseContext()
    rec = ViaRecord.from_bytes(b"\x00" * 20, ctx)
    assert rec is None
    assert len(ctx.issues) == 1


# ---------------------------------------------------------------------------
# ArcRecord
# ---------------------------------------------------------------------------


def _make_arc_body(
    layer: int = 1,
    flags1: int = 0,
    flags2: int = 0,
    net: int = 0,
    polygon: int = 0xFFFF,
    component: int = 0xFFFF,
    cx: int = 1000,
    cy: int = 2000,
    radius: int = 500,
    start_angle: float = 0.0,
    end_angle: float = 180.0,
    width: int = 100,
    subpoly_index: int = 0,
    keepout_restrictions: int = 0,
) -> bytes:
    body = bytearray(57)
    body[0] = layer
    body[1] = flags1
    body[2] = flags2
    body[3:5] = _pack_u16(net)
    body[5:7] = _pack_u16(polygon)
    body[7:9] = _pack_u16(component)
    body[13:17] = _pack_i32(cx)
    body[17:21] = _pack_i32(cy)
    body[21:25] = _pack_u32(radius)
    body[25:33] = _pack_f64(start_angle)
    body[33:41] = _pack_f64(end_angle)
    body[41:45] = _pack_u32(width)
    body[45:47] = _pack_u16(subpoly_index)
    body[56] = keepout_restrictions
    return bytes(body)


def test_arc_from_bytes():
    ctx = ParseContext()
    body = _make_arc_body(layer=1, cx=1000, cy=2000, radius=500, start_angle=45.0, end_angle=270.0)
    rec = ArcRecord.from_bytes(body, ctx)
    assert rec is not None
    assert rec.layer == 1
    assert rec.center == (1000, 2000)
    assert rec.radius == 500
    assert rec.start_angle == 45.0
    assert rec.end_angle == 270.0


def test_arc_from_bytes_parses_keepout_metadata():
    ctx = ParseContext()
    body = _make_arc_body(
        flags2=0x02,
        polygon=3,
        subpoly_index=7,
        keepout_restrictions=31,
    )
    rec = ArcRecord.from_bytes(body, ctx)

    assert rec is not None
    assert rec.flags2 == 0x02
    assert rec.polygon == 3
    assert rec.subpoly_index == 7
    assert rec.keepout_restrictions == 31
    assert rec.is_keepout is True


def test_arc_truncated():
    ctx = ParseContext()
    rec = ArcRecord.from_bytes(b"\x00" * 30, ctx)
    assert rec is None
    assert len(ctx.issues) == 1


# ---------------------------------------------------------------------------
# TextRecord
# ---------------------------------------------------------------------------


def _make_text_record(
    layer: int = 33,
    component: int = 5,
    x: int = 100,
    y: int = 200,
    height: int = 50,
    rotation: float = 0.0,
    is_comment: int = 0,
    is_designator: int = 1,
    text: str = "U1",
) -> bytes:
    """Build a text record with 2 subrecords: binary + Pascal string."""
    # Sub1: binary properties (need at least 42 bytes)
    sub1 = bytearray(42)
    sub1[0] = layer
    sub1[7:9] = _pack_u16(component)
    sub1[13:17] = _pack_i32(x)
    sub1[17:21] = _pack_i32(y)
    sub1[21:25] = _pack_u32(height)
    sub1[27:35] = _pack_f64(rotation)
    sub1[40] = is_comment
    sub1[41] = is_designator
    sub1_bytes = bytes(sub1)

    # Sub2: Pascal string
    text_bytes = text.encode("cp1252")
    sub2 = bytes([len(text_bytes)]) + text_bytes

    # Assemble: type(5) + sub1_len + sub1 + sub2_len + sub2
    result = bytearray()
    result.append(5)  # record type
    result.extend(_pack_u32(len(sub1_bytes)))
    result.extend(sub1_bytes)
    result.extend(_pack_u32(len(sub2)))
    result.extend(sub2)
    return bytes(result)


def test_text_from_bytes():
    ctx = ParseContext()
    data = _make_text_record(
        layer=33,
        component=5,
        x=100,
        y=200,
        height=50,
        rotation=90.0,
        is_designator=1,
        text="U1",
    )
    rec = TextRecord.from_bytes(data, ctx)
    assert rec is not None
    assert rec.layer == 33
    assert rec.component == 5
    assert rec.position == (100, 200)
    assert rec.height == 50
    assert rec.rotation == 90.0
    assert rec.is_designator is True
    assert rec.is_comment is False
    assert rec.text == "U1"


def test_text_truncated():
    ctx = ParseContext()
    rec = TextRecord.from_bytes(b"\x05\x05\x00\x00\x00\x01\x02", ctx)
    assert rec is None
    assert len(ctx.issues) == 1


# ---------------------------------------------------------------------------
# FillRecord
# ---------------------------------------------------------------------------


def _make_fill_body(
    layer: int = 1,
    net: int = 2,
    component: int = 0xFFFF,
    x1: int = 100,
    y1: int = 200,
    x2: int = 300,
    y2: int = 400,
    rotation: float = 45.0,
) -> bytes:
    body = bytearray(37)
    body[0] = layer
    body[3:5] = _pack_u16(net)
    body[7:9] = _pack_u16(component)
    body[13:17] = _pack_i32(x1)
    body[17:21] = _pack_i32(y1)
    body[21:25] = _pack_i32(x2)
    body[25:29] = _pack_i32(y2)
    body[29:37] = _pack_f64(rotation)
    return bytes(body)


def test_fill_from_bytes():
    ctx = ParseContext()
    body = _make_fill_body(layer=1, net=2, x1=100, y1=200, x2=300, y2=400, rotation=45.0)
    rec = FillRecord.from_bytes(body, ctx)
    assert rec is not None
    assert rec.layer == 1
    assert rec.net == 2
    assert rec.pos1 == (100, 200)
    assert rec.pos2 == (300, 400)
    assert rec.rotation == 45.0


def test_fill_from_bytes_parses_component():
    ctx = ParseContext()
    body = _make_fill_body(component=12)
    rec = FillRecord.from_bytes(body, ctx)
    assert rec is not None
    assert rec.component == 12


def test_fill_truncated():
    ctx = ParseContext()
    rec = FillRecord.from_bytes(b"\x00" * 20, ctx)
    assert rec is None
    assert len(ctx.issues) == 1


# ---------------------------------------------------------------------------
# PadRecord
# ---------------------------------------------------------------------------


def _make_pad_record(
    layer: int = 74,
    net: int = 5,
    component: int = 3,
    x: int = 1000,
    y: int = 2000,
    top_sx: int = 500,
    top_sy: int = 500,
    hole_size: int = 250,
    shape: int = 1,
    rotation: float = 0.0,
    pad_name: str = "1",
) -> bytes:
    """Build a minimal pad record with subrecords."""
    result = bytearray()
    result.append(2)  # record type

    # Sub1: pad name (Pascal string)
    name_bytes = pad_name.encode("cp1252")
    sub1 = bytes([len(name_bytes)]) + name_bytes
    result.extend(_pack_u32(len(sub1)))
    result.extend(sub1)

    # Sub2-Sub4: empty
    for _ in range(3):
        result.extend(_pack_u32(0))

    # Sub5: main geometry (need at least 61 bytes)
    sub5 = bytearray(61)
    sub5[0] = layer
    sub5[3:5] = _pack_u16(net)
    sub5[7:9] = _pack_u16(component)
    sub5[13:17] = _pack_i32(x)
    sub5[17:21] = _pack_i32(y)
    sub5[21:25] = _pack_i32(top_sx)
    sub5[25:29] = _pack_i32(top_sy)
    sub5[45:49] = _pack_u32(hole_size)
    sub5[49] = shape
    sub5[52:60] = _pack_f64(rotation)
    result.extend(_pack_u32(len(sub5)))
    result.extend(bytes(sub5))

    # Sub6: empty
    result.extend(_pack_u32(0))

    return bytes(result)


def test_pad_from_bytes():
    ctx = ParseContext()
    data = _make_pad_record(
        layer=74,
        net=5,
        component=3,
        x=1000,
        y=2000,
        top_sx=500,
        top_sy=500,
        hole_size=250,
        shape=1,
        rotation=30.0,
        pad_name="A1",
    )
    rec = PadRecord.from_bytes(data, ctx)
    assert rec is not None
    assert rec.name == "A1"
    assert rec.layer == 74
    assert rec.net == 5
    assert rec.component == 3
    assert rec.position == (1000, 2000)
    assert rec.top_size == (500, 500)
    assert rec.hole_size == 250
    assert rec.shape == 1  # circle
    assert rec.rotation == 30.0


def test_pad_truncated():
    ctx = ParseContext()
    rec = PadRecord.from_bytes(b"\x02\x00\x00\x00\x00", ctx)
    assert rec is None
    assert len(ctx.issues) == 1


# ---------------------------------------------------------------------------
# RegionRecord
# ---------------------------------------------------------------------------


def _make_region_body(
    layer: int = 1,
    net: int = 0,
    component: int = 0xFFFF,
    holecount: int = 0,
    props_str: bytes = b"|V7_LAYER=TOP\x00",
    vertices: list[tuple[float, float]] | None = None,
) -> bytes:
    """Build a region record body."""
    if vertices is None:
        vertices = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]

    body = bytearray()
    body.append(layer)  # 0
    body.extend(b"\x00\x00")  # 1-2
    body.extend(_pack_u16(net))  # 3-4
    body.extend(b"\x00\x00")  # 5-6
    body.extend(_pack_u16(component))  # 7-8
    body.extend(b"\x00\x00\x00\x00\x00\x00")  # 9-14
    body[14:16] = _pack_u16(holecount)
    body.extend(b"\x00\x00")  # 16-17
    body.extend(_pack_u32(len(props_str)))  # 18-21
    body.extend(props_str)

    # Vertex count + vertices (f64 pairs)
    body.extend(_pack_u32(len(vertices)))
    for vx, vy in vertices:
        body.extend(_pack_f64(vx))
        body.extend(_pack_f64(vy))

    return bytes(body)


def test_region_from_bytes():
    ctx = ParseContext()
    verts = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    body = _make_region_body(layer=1, net=3, vertices=verts)
    rec = RegionRecord.from_bytes(body, ctx)
    assert rec is not None
    assert rec.layer == 1
    assert rec.net == 3
    assert len(rec.vertices) == 3
    assert rec.vertices[0] == (0.0, 0.0)
    assert rec.vertices[2] == (100.0, 100.0)


def test_region_truncated():
    ctx = ParseContext()
    rec = RegionRecord.from_bytes(b"\x00" * 10, ctx)
    assert rec is None
    assert len(ctx.issues) == 1


# ---------------------------------------------------------------------------
# Arc linearization in ExtendedVertex lists
# ---------------------------------------------------------------------------


def _make_vertex(
    x: int,
    y: int,
    is_round: bool = False,
    cx: int = 0,
    cy: int = 0,
    radius: int = 0,
    start_angle: float = 0.0,
    end_angle: float = 0.0,
) -> ExtendedVertex:
    return ExtendedVertex(
        x=x,
        y=y,
        is_round=is_round,
        center_x=cx,
        center_y=cy,
        radius=radius,
        start_angle=start_angle,
        end_angle=end_angle,
    )


def test_linearize_straight_edges_only():
    """No arc vertices — returns the same points as simple coordinate extraction."""
    # A simple triangle, coordinates in Altium internal units (0.1 µinch)
    verts = [
        _make_vertex(0, 0),
        _make_vertex(100000, 0),
        _make_vertex(50000, 86603),
    ]
    points = linearize_arc_vertices(verts)
    assert len(points) == 3
    assert points[0] == (0, 0)
    assert points[1] == (100000, 0)
    assert points[2] == (50000, 86603)


def test_linearize_90_degree_arc():
    """A 90° arc edge should produce intermediate points along the arc."""
    # Square with one rounded corner: 3 straight edges + 1 arc edge.
    # Arc from (1000, 0) to (0, 1000) with center at (0, 0), radius 1000.
    # Angles: 0° to 90° CCW.
    verts = [
        _make_vertex(
            x=1000,
            y=0,
            is_round=True,
            cx=0,
            cy=0,
            radius=1000,
            start_angle=0.0,
            end_angle=90.0,
        ),
        _make_vertex(0, 1000),
        _make_vertex(-1000, 1000),
        _make_vertex(-1000, 0),
    ]
    points = linearize_arc_vertices(verts)

    # Should have the start point + interpolated arc points + remaining straight edges
    assert len(points) > 4  # more than the 4 original vertices

    # First point is the arc start
    assert points[0] == (1000, 0)

    # Check that interpolated points lie on the arc (radius ~1000 from origin)
    for px, py in points[:-2]:  # last two are straight vertices
        dist = math.sqrt(px**2 + py**2)
        assert abs(dist - 1000) < 1, f"Point ({px}, {py}) not on arc: dist={dist}"

    # Last two points are the straight vertices
    assert points[-2] == (-1000, 1000)
    assert points[-1] == (-1000, 0)


def test_linearize_full_circle_arc():
    """A full 360° arc edge produces a full circle of interpolated points."""
    # A single arc vertex that sweeps a full circle
    verts = [
        _make_vertex(
            x=1000,
            y=0,
            is_round=True,
            cx=0,
            cy=0,
            radius=1000,
            start_angle=0.0,
            end_angle=360.0,
        ),
        _make_vertex(1000, 0),  # closing vertex
    ]
    points = linearize_arc_vertices(verts)
    # Should have many interpolated points
    assert len(points) > 10

    # All points should lie on the circle
    for px, py in points:
        dist = math.sqrt(px**2 + py**2)
        assert abs(dist - 1000) < 1, f"Point ({px}, {py}) not on circle: dist={dist}"


def test_linearize_arc_wrapping_past_360():
    """An arc that wraps past 360° (e.g. 350° to 10° going CCW)."""
    radius = 1000
    verts = [
        _make_vertex(
            x=round(radius * math.cos(math.radians(350))),
            y=round(radius * math.sin(math.radians(350))),
            is_round=True,
            cx=0,
            cy=0,
            radius=radius,
            start_angle=350.0,
            end_angle=10.0,
        ),
        _make_vertex(
            x=round(radius * math.cos(math.radians(10))),
            y=round(radius * math.sin(math.radians(10))),
        ),
    ]
    points = linearize_arc_vertices(verts)
    # Small arc (20°), should have a few interpolated points
    assert len(points) >= 2
    for px, py in points:
        dist = math.sqrt(px**2 + py**2)
        assert abs(dist - 1000) < 2  # small tolerance for rounding
