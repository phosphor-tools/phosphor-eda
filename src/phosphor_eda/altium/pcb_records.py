"""Altium PCB binary record types with from_bytes loaders.

Each record type corresponds to a binary primitive in an Altium .PcbDoc
OLE stream. Coordinates are stored in raw Altium internal units (i32,
where 1 unit = 0.1 µinch). Conversion to mm happens at the boundary
in pcb_parser.py.

Binary layout documentation from the KiCad Altium importer
(pcbnew/pcb_io/altium/altium_parser_pcb.h) and empirical analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Self

from phosphor_eda.altium._helpers import f64, i32, u16, u32
from phosphor_eda.altium.enums import PcbPrimitiveFlags2
from phosphor_eda.altium.record_parser import parse_record_payload

if TYPE_CHECKING:
    from phosphor_eda.altium.errors import ParseContext

# Sentinel values used across record types.
NET_UNCONNECTED = 0xFFFF
COMPONENT_NONE = 0xFFFF


@dataclass
class TrackRecord:
    """Binary track/line record (Tracks6, rec_type=4).

    Byte layout (33 bytes):
      [0]     layer    u8
      [1]     flags1   u8
      [2]     flags2   u8
      [3:5]   net      u16
      [5:7]   polygon  u16
      [7:9]   component u16
      [9:11]  subpoly_index u16
      [13:17] x1       i32
      [17:21] y1       i32
      [21:25] x2       i32
      [25:29] y2       i32
      [29:33] width    i32
      [56]    keepout restrictions bitmask, when present
    """

    layer: int
    flags1: int
    flags2: int
    net: int
    polygon: int
    component: int
    subpoly_index: int
    start: tuple[int, int]
    end: tuple[int, int]
    width: int
    keepout_restrictions: int = 0

    MIN_SIZE: ClassVar[int] = 33

    @property
    def is_keepout(self) -> bool:
        """Return whether this track is an Altium keepout primitive."""
        return bool(PcbPrimitiveFlags2(self.flags2) & PcbPrimitiveFlags2.KEEPOUT)

    @classmethod
    def from_bytes(cls, body: bytes, ctx: ParseContext) -> Self | None:
        if len(body) < cls.MIN_SIZE:
            ctx.warn("truncated_record", f"Track record too short ({len(body)} < {cls.MIN_SIZE})")
            return None
        return cls(
            layer=body[0],
            flags1=body[1] if len(body) > 1 else 0,
            flags2=body[2] if len(body) > 2 else 0,
            net=u16(body, 3),
            polygon=u16(body, 5),
            component=u16(body, 7),
            subpoly_index=u16(body, 9) if len(body) >= 11 else 0,
            start=(i32(body, 13), i32(body, 17)),
            end=(i32(body, 21), i32(body, 25)),
            width=i32(body, 29),
            keepout_restrictions=body[56] if len(body) >= 57 else 0,
        )


@dataclass
class ArcRecord:
    """Binary arc record (Arcs6, rec_type=1).

    Byte layout (45+ bytes):
      [0]     layer    u8
      [1]     flags1   u8
      [2]     flags2   u8
      [3:5]   net      u16
      [5:7]   polygon  u16
      [7:9]   component u16
      [13:17] cx       i32
      [17:21] cy       i32
      [21:25] radius   u32
      [25:33] start_angle f64
      [33:41] end_angle   f64
      [41:45] width    u32
      [45:47] subpoly_index u16
      [56]    keepout restrictions bitmask, when present
    """

    layer: int
    flags1: int
    flags2: int
    net: int
    polygon: int
    component: int
    center: tuple[int, int]
    radius: int
    start_angle: float
    end_angle: float
    width: int
    subpoly_index: int = 0
    keepout_restrictions: int = 0

    MIN_SIZE: ClassVar[int] = 45

    @property
    def is_keepout(self) -> bool:
        """Return whether this arc is an Altium keepout primitive."""
        return bool(PcbPrimitiveFlags2(self.flags2) & PcbPrimitiveFlags2.KEEPOUT)

    @classmethod
    def from_bytes(cls, body: bytes, ctx: ParseContext) -> Self | None:
        if len(body) < cls.MIN_SIZE:
            ctx.warn("truncated_record", f"Arc record too short ({len(body)} < {cls.MIN_SIZE})")
            return None
        return cls(
            layer=body[0],
            flags1=body[1],
            flags2=body[2],
            net=u16(body, 3),
            polygon=u16(body, 5),
            component=u16(body, 7),
            center=(i32(body, 13), i32(body, 17)),
            radius=u32(body, 21),
            start_angle=f64(body, 25),
            end_angle=f64(body, 33),
            width=u32(body, 41),
            subpoly_index=u16(body, 45) if len(body) >= 47 else 0,
            keepout_restrictions=body[56] if len(body) >= 57 else 0,
        )


@dataclass
class ViaRecord:
    """Binary via record (Vias6, rec_type=3).

    Byte layout (31 bytes):
      [3:5]   net       u16
      [13:17] x         i32
      [17:21] y         i32
      [21:25] diameter  i32
      [25:29] hole_size i32
      [29]    start_layer u8
      [30]    end_layer   u8
    """

    net: int
    position: tuple[int, int]
    diameter: int
    hole_size: int
    start_layer: int
    end_layer: int

    MIN_SIZE: ClassVar[int] = 31

    @classmethod
    def from_bytes(cls, body: bytes, ctx: ParseContext) -> Self | None:
        if len(body) < cls.MIN_SIZE:
            ctx.warn("truncated_record", f"Via record too short ({len(body)} < {cls.MIN_SIZE})")
            return None
        return cls(
            net=u16(body, 3),
            position=(i32(body, 13), i32(body, 17)),
            diameter=i32(body, 21),
            hole_size=i32(body, 25),
            start_layer=body[29],
            end_layer=body[30],
        )


@dataclass
class FillRecord:
    """Binary fill record (Fills6, rec_type=6).

    Byte layout (37 bytes):
      [0]     layer  u8
      [1]     flags1 u8
      [2]     flags2 u8
      [3:5]   net    u16
      [13:17] x1     i32
      [17:21] y1     i32
      [21:25] x2     i32
      [25:29] y2     i32
      [29:37] rotation f64
      [56]    keepout restrictions bitmask, when present
    """

    layer: int
    flags1: int
    flags2: int
    net: int
    pos1: tuple[int, int]
    pos2: tuple[int, int]
    rotation: float
    keepout_restrictions: int = 0

    MIN_SIZE: ClassVar[int] = 37

    @property
    def is_keepout(self) -> bool:
        """Return whether this fill is an Altium keepout primitive."""
        return bool(PcbPrimitiveFlags2(self.flags2) & PcbPrimitiveFlags2.KEEPOUT)

    @classmethod
    def from_bytes(cls, body: bytes, ctx: ParseContext) -> Self | None:
        if len(body) < cls.MIN_SIZE:
            ctx.warn("truncated_record", f"Fill record too short ({len(body)} < {cls.MIN_SIZE})")
            return None
        return cls(
            layer=body[0],
            flags1=body[1] if len(body) > 1 else 0,
            flags2=body[2] if len(body) > 2 else 0,
            net=u16(body, 3),
            pos1=(i32(body, 13), i32(body, 17)),
            pos2=(i32(body, 21), i32(body, 25)),
            rotation=f64(body, 29),
            keepout_restrictions=body[56] if len(body) >= 57 else 0,
        )


@dataclass
class TextRecord:
    """Text record (Texts6, rec_type=5).

    Text records have 2 subrecords: binary properties + Pascal string.
    The from_bytes method processes the complete record starting from the
    type byte.

    Sub1 layout (≥42 bytes):
      [0]     layer   u8
      [7:9]   component u16
      [13:17] x       i32
      [17:21] y       i32
      [21:25] height  u32
      [27:35] rotation f64
      [40]    is_comment u8
      [41]    is_designator u8
    """

    layer: int
    component: int
    position: tuple[int, int]
    height: int
    rotation: float
    is_comment: bool
    is_designator: bool
    text: str

    @classmethod
    def from_bytes(cls, data: bytes, ctx: ParseContext) -> Self | None:
        """Parse from complete record data including the type byte."""
        pos = 0
        if len(data) < 1 or data[pos] != 5:
            ctx.warn("truncated_record", "Text record missing type byte")
            return None
        pos += 1

        # Sub1: binary properties
        if pos + 4 > len(data):
            ctx.warn("truncated_record", "Text record sub1 length missing")
            return None
        sub1_len = u32(data, pos)
        pos += 4
        if pos + sub1_len > len(data) or sub1_len < 40:
            ctx.warn("truncated_record", f"Text record sub1 too short ({sub1_len})")
            return None
        sub1 = data[pos : pos + sub1_len]
        pos += sub1_len

        # Sub2: text content (Pascal string)
        if pos + 4 > len(data):
            ctx.warn("truncated_record", "Text record sub2 length missing")
            return None
        sub2_len = u32(data, pos)
        pos += 4
        sub2 = data[pos : pos + sub2_len] if pos + sub2_len <= len(data) else b""

        # Parse fields
        is_comment_byte = sub1[40] if sub1_len > 40 else 0
        is_designator_byte = sub1[41] if sub1_len > 41 else 0

        text_content = ""
        if sub2_len > 0 and len(sub2) > 0:
            str_len = sub2[0]
            text_content = sub2[1 : 1 + str_len].decode("cp1252", errors="replace")

        return cls(
            layer=sub1[0],
            component=u16(sub1, 7),
            position=(i32(sub1, 13), i32(sub1, 17)),
            height=u32(sub1, 21),
            rotation=f64(sub1, 27),
            is_comment=bool(is_comment_byte),
            is_designator=bool(is_designator_byte),
            text=text_content,
        )


@dataclass
class PadRecord:
    """Pad record (Pads6, rec_type=2).

    Pad records have 6 subrecords: name, skip×3, geometry, per-layer overrides.
    The from_bytes method processes the complete record starting from the
    type byte.

    Sub5 (geometry) layout (≥61 bytes):
      [0]     layer    u8
      [3:5]   net      u16
      [7:9]   component u16
      [13:17] x        i32
      [17:21] y        i32
      [21:25] top_sx   i32
      [25:29] top_sy   i32
      [45:49] hole_size u32
      [49]    shape    u8
    """

    name: str
    layer: int
    net: int
    component: int
    position: tuple[int, int]
    top_size: tuple[int, int]
    hole_size: int
    shape: int
    shape_alt: int | None = None  # from sub6 per-layer override

    @classmethod
    def from_bytes(cls, data: bytes, ctx: ParseContext) -> Self | None:
        """Parse from complete record data including the type byte."""
        pos = 0
        if len(data) < 1 or data[pos] != 2:
            ctx.warn("truncated_record", "Pad record missing type byte")
            return None
        pos += 1

        # Sub1: pad name (Pascal string)
        if pos + 4 > len(data):
            ctx.warn("truncated_record", "Pad record sub1 length missing")
            return None
        sub1_len = u32(data, pos)
        pos += 4
        pad_name = ""
        if sub1_len > 0 and pos + sub1_len <= len(data):
            name_len = data[pos]
            pad_name = data[pos + 1 : pos + 1 + name_len].decode("cp1252", errors="replace")
        pos += sub1_len

        # Sub2–Sub4: skip
        for _ in range(3):
            if pos + 4 > len(data):
                ctx.warn("truncated_record", "Pad record sub2-4 truncated")
                return None
            sl = u32(data, pos)
            pos += 4 + sl

        # Sub5: main pad geometry
        if pos + 4 > len(data):
            ctx.warn("truncated_record", "Pad record sub5 length missing")
            return None
        sub5_len = u32(data, pos)
        pos += 4
        if sub5_len < 61 or pos + sub5_len > len(data):
            ctx.warn("truncated_record", f"Pad record sub5 too short ({sub5_len})")
            return None
        sub5 = data[pos : pos + sub5_len]
        pos += sub5_len

        # Sub6: per-layer overrides
        sub6 = b""
        if pos + 4 <= len(data):
            sub6_len = u32(data, pos)
            pos += 4
            if sub6_len > 0 and pos + sub6_len <= len(data):
                sub6 = data[pos : pos + sub6_len]

        shape_byte = sub5[49] if sub5_len > 49 else 1
        shape_alt_val: int | None = None
        if sub6 and len(sub6) > 551:
            shape_alt_val = sub6[519]

        return cls(
            name=pad_name,
            layer=sub5[0],
            net=u16(sub5, 3),
            component=u16(sub5, 7),
            position=(i32(sub5, 13), i32(sub5, 17)),
            top_size=(i32(sub5, 21), i32(sub5, 25)),
            hole_size=u32(sub5, 45),
            shape=shape_byte,
            shape_alt=shape_alt_val,
        )


@dataclass
class RegionRecord:
    """Region record (Regions6 / ShapeBasedRegions6, rec_type=11).

    Contains property string + vertex data. Vertices are f64 pairs
    in Altium internal units.

    Header layout (22 bytes):
      [0]     layer     u8
      [3:5]   net       u16
      [7:9]   component u16
      [14:16] holecount u16
      [18:22] prop_len  u32
    """

    layer: int
    net: int
    component: int
    holecount: int
    properties: dict[str, str]
    vertices: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)

    MIN_HEADER: ClassVar[int] = 22

    @classmethod
    def from_bytes(cls, body: bytes, ctx: ParseContext) -> Self | None:
        if len(body) < cls.MIN_HEADER:
            ctx.warn(
                "truncated_record",
                f"Region record too short ({len(body)} < {cls.MIN_HEADER})",
            )
            return None

        layer = body[0]
        net = u16(body, 3)
        component = u16(body, 7)
        holecount = u16(body, 14)

        prop_len = u32(body, 18)
        prop_end = 22 + prop_len
        if prop_end > len(body):
            ctx.warn("truncated_record", "Region record property string truncated")
            return None

        props = parse_record_payload(body[22:prop_end])

        # Read outline vertices (f64 pairs)
        vpos = prop_end
        if vpos + 4 > len(body):
            ctx.warn("truncated_record", "Region record vertex count missing")
            return None
        vertex_count = u32(body, vpos)
        vpos += 4

        vertices: list[tuple[float, float]] = []
        for _ in range(vertex_count):
            if vpos + 16 > len(body):
                break
            vx = f64(body, vpos)
            vy = f64(body, vpos + 8)
            vertices.append((vx, vy))
            vpos += 16

        # Read hole vertices
        holes: list[list[tuple[float, float]]] = []
        for _ in range(holecount):
            if vpos + 4 > len(body):
                break
            hole_vc = u32(body, vpos)
            vpos += 4
            hole_verts: list[tuple[float, float]] = []
            for _ in range(hole_vc):
                if vpos + 16 > len(body):
                    break
                hx = f64(body, vpos)
                hy = f64(body, vpos + 8)
                hole_verts.append((hx, hy))
                vpos += 16
            holes.append(hole_verts)

        return cls(
            layer=layer,
            net=net,
            component=component,
            holecount=holecount,
            properties=props,
            vertices=vertices,
            holes=holes,
        )


# Extended vertex size for ShapeBasedRegions6:
# 1 (isRound) + 4 (x) + 4 (y) + 4 (cx) + 4 (cy) + 4 (radius)
# + 8 (angle1) + 8 (angle2) = 37
EXTENDED_VERTEX_SIZE = 37


@dataclass
class ExtendedVertex:
    """A single vertex in a ShapeBasedRegions6 outline, with arc data."""

    x: int
    y: int
    is_round: bool = False
    center_x: int = 0
    center_y: int = 0
    radius: int = 0
    start_angle: float = 0.0
    end_angle: float = 0.0


@dataclass
class ShapeBasedRegionRecord:
    """ShapeBasedRegions6 record (rec_type=11) with extended vertices.

    Uses the extended vertex format (37 bytes per vertex with arc support).
    The stored vertex count is N, but there are N+1 vertices (closing vertex
    repeats the first point).
    """

    layer: int
    net: int
    component: int
    holecount: int
    properties: dict[str, str]
    vertices: list[ExtendedVertex]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)

    MIN_HEADER: ClassVar[int] = 22

    @classmethod
    def from_bytes(cls, body: bytes, ctx: ParseContext) -> Self | None:
        if len(body) < cls.MIN_HEADER:
            ctx.warn(
                "truncated_record",
                f"ShapeBasedRegion too short ({len(body)} < {cls.MIN_HEADER})",
            )
            return None

        layer = body[0]
        net = u16(body, 3)
        component = u16(body, 7)
        holecount = u16(body, 14)

        prop_len = u32(body, 18)
        prop_end = 22 + prop_len
        if prop_end > len(body):
            ctx.warn("truncated_record", "ShapeBasedRegion property string truncated")
            return None

        props = parse_record_payload(body[22:prop_end])

        # Read extended vertices. Count is N but there are N+1 vertices.
        vpos = prop_end
        if vpos + 4 > len(body):
            ctx.warn("truncated_record", "ShapeBasedRegion vertex count missing")
            return None
        stored_count = u32(body, vpos)
        vertex_count = stored_count + 1
        vpos += 4

        vertices: list[ExtendedVertex] = []
        for _ in range(vertex_count):
            if vpos + EXTENDED_VERTEX_SIZE > len(body):
                break
            is_round = bool(body[vpos])
            vx = i32(body, vpos + 1)
            vy = i32(body, vpos + 5)
            cx = i32(body, vpos + 9)
            cy = i32(body, vpos + 13)
            radius = i32(body, vpos + 17)
            angle1 = f64(body, vpos + 21)
            angle2 = f64(body, vpos + 29)
            vertices.append(
                ExtendedVertex(
                    x=vx,
                    y=vy,
                    is_round=is_round,
                    center_x=cx,
                    center_y=cy,
                    radius=radius,
                    start_angle=angle1,
                    end_angle=angle2,
                )
            )
            vpos += EXTENDED_VERTEX_SIZE

        # Read hole vertices (simple f64 pairs)
        holes: list[list[tuple[float, float]]] = []
        for _ in range(holecount):
            if vpos + 4 > len(body):
                break
            hole_vc = u32(body, vpos)
            vpos += 4
            hole_verts: list[tuple[float, float]] = []
            for _ in range(hole_vc):
                if vpos + 16 > len(body):
                    break
                hx = f64(body, vpos)
                hy = f64(body, vpos + 8)
                hole_verts.append((hx, hy))
                vpos += 16
            holes.append(hole_verts)

        return cls(
            layer=layer,
            net=net,
            component=component,
            holecount=holecount,
            properties=props,
            vertices=vertices,
            holes=holes,
        )
