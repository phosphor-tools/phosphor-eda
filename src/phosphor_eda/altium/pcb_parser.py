"""Parse an Altium Designer .PcbDoc file into the PCB domain model.

A .PcbDoc is an OLE compound document containing separate streams for each
primitive type (tracks, pads, vias, etc.).  Text-based streams use
pipe-delimited ASCII properties; binary streams use fixed-size records with
a type(u8) + length(u32) header.

Coordinates in binary streams are stored as i32 in units of 0.1 µinch.
Text streams store coordinates as mil strings (e.g. "1153.8945mil").
All output coordinates are in millimetres with Y increasing downward.
"""

from __future__ import annotations

import math
import struct
from typing import TYPE_CHECKING

import olefile

from phosphor_eda.altium.record_parser import parse_record_payload
from phosphor_eda.pcb import (
    LayerFunction,
    PcbArc,
    PcbBoard,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbModel3D,
    PcbNet,
    PcbPad,
    PcbPolygon,
    PcbSegment,
    PcbText,
    PcbTraceArc,
    PcbVia,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Typed struct helpers — struct.unpack_from returns tuple[Any, ...] in
# typeshed, so these thin wrappers keep basedpyright happy.
# ---------------------------------------------------------------------------


def _u16(data: bytes | memoryview, offset: int) -> int:
    """Read uint16 (little-endian) from *data* at *offset*."""
    return int.from_bytes(data[offset : offset + 2], "little", signed=False)


def _i32(data: bytes | memoryview, offset: int) -> int:
    """Read int32 (little-endian) from *data* at *offset*."""
    return int.from_bytes(data[offset : offset + 4], "little", signed=True)


def _u32(data: bytes | memoryview, offset: int) -> int:
    """Read uint32 (little-endian) from *data* at *offset*."""
    return int.from_bytes(data[offset : offset + 4], "little", signed=False)


def _f64(data: bytes | memoryview, offset: int) -> float:
    """Read float64 (little-endian) from *data* at *offset*."""
    (val,) = struct.unpack_from("<d", data, offset)  # pyright: ignore[reportAny]
    return float(val)  # pyright: ignore[reportAny]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 1 internal unit = 0.1 µinch = 0.0000001 inch = 0.00000254 mm
_INT_TO_MM = 0.00000254

# 1 mil = 0.001 inch = 0.0254 mm
_MIL_TO_MM = 0.0254

_NET_UNCONNECTED = 0xFFFF
_COMPONENT_NONE = 0xFFFF

# Static Altium layer info: (native_name, function, side).
# Mechanical layers (57-72) are defaults overridden by Board6 MECHKIND.
_ALTIUM_LAYER_INFO: dict[int, tuple[str, LayerFunction, str]] = {
    1: ("Top Layer", LayerFunction.COPPER, "front"),
    **{i: (f"Mid-Layer {i - 1}", LayerFunction.COPPER, "") for i in range(2, 32)},
    32: ("Bottom Layer", LayerFunction.COPPER, "back"),
    33: ("Top Overlay", LayerFunction.SILKSCREEN, "front"),
    34: ("Bottom Overlay", LayerFunction.SILKSCREEN, "back"),
    35: ("Top Paste", LayerFunction.SOLDER_PASTE, "front"),
    36: ("Bottom Paste", LayerFunction.SOLDER_PASTE, "back"),
    37: ("Top Solder", LayerFunction.SOLDER_MASK, "front"),
    38: ("Bottom Solder", LayerFunction.SOLDER_MASK, "back"),
    **{i: (f"Mechanical {i - 56}", LayerFunction.MECHANICAL, "") for i in range(57, 73)},
}

# MECHKIND values from Board6 → (function, side).
_MECHKIND_MAP: dict[str, tuple[LayerFunction, str]] = {
    "assemblytop": (LayerFunction.FAB, "front"),
    "assemblybottom": (LayerFunction.FAB, "back"),
    "courtyardtop": (LayerFunction.COURTYARD, "front"),
    "courtyardbottom": (LayerFunction.COURTYARD, "back"),
    "boardshape": (LayerFunction.EDGE, ""),
    "componentoutlinetop": (LayerFunction.FAB, "front"),
    "componentoutlinebottom": (LayerFunction.FAB, "back"),
    "3dbodytop": (LayerFunction.OTHER, "front"),
    "3dbodybottom": (LayerFunction.OTHER, "back"),
    "designatortop": (LayerFunction.SILKSCREEN, "front"),
    "designatorbottom": (LayerFunction.SILKSCREEN, "back"),
    "fabnotes": (LayerFunction.FAB, ""),
    "vcut": (LayerFunction.MECHANICAL, ""),
}

# Copper layer numbers for filtering.
_COPPER_LAYERS = frozenset(range(1, 33))

# V7 layer name → Altium layer number.  Used to resolve the V7_LAYER property
# that overrides the byte-level layer number in region records.
_V7_NAME_TO_NUM: dict[str, int] = {
    "TOP": 1,
    **{f"MID{i - 1}": i for i in range(2, 32)},
    "BOTTOM": 32,
    "TOPOVERLAY": 33,
    "BOTTOMOVERLAY": 34,
    "TOPPASTE": 35,
    "BOTTOMPASTE": 36,
    "TOPSOLDER": 37,
    "BOTTOMSOLDER": 38,
    **{f"MECHANICAL{i}": 56 + i for i in range(1, 17)},
}

# Pad shape byte → domain string.
_PAD_SHAPES: dict[int, str] = {
    1: "circle",
    2: "rect",
    3: "rect",  # octagonal — treat as rect
}

# Pad shape_alt values (sub6) that override the base shape.
_PAD_SHAPE_ALT_ROUNDRECT = 9


# ---------------------------------------------------------------------------
# Low-level stream readers
# ---------------------------------------------------------------------------


def _read_text_records(data: bytes) -> list[dict[str, str]]:
    """Read pipe-delimited text records with a 4-byte LE length prefix."""
    records: list[dict[str, str]] = []
    pos = 0
    while pos + 4 <= len(data):
        length = _u32(data, pos)
        pos += 4
        if length == 0 or pos + length > len(data):
            break
        payload = data[pos : pos + length]
        pos += length
        props = parse_record_payload(payload)
        if props:
            records.append(props)
    return records


def _read_binary_records(data: bytes) -> list[tuple[int, bytes]]:
    """Read binary records with type(u8) + length(u32) + body framing."""
    records: list[tuple[int, bytes]] = []
    pos = 0
    while pos + 5 <= len(data):
        rec_type = data[pos]
        rec_len = _u32(data, pos + 1)
        pos += 5
        if pos + rec_len > len(data):
            break
        records.append((rec_type, data[pos : pos + rec_len]))
        pos += rec_len
    return records


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------


def _int_to_mm(val: int) -> float:
    """Convert Altium internal units (0.1 µinch) to millimetres."""
    return val * _INT_TO_MM


def _parse_mil(s: str) -> float:
    """Parse a mil-string like ``'1153.8945mil'`` and return mm."""
    return float(s.rstrip("mil")) * _MIL_TO_MM


def _parse_rotation(s: str) -> float:
    """Parse a rotation string (may be scientific notation)."""
    return float(s)


def _build_layer_map(board_props: dict[str, str]) -> dict[int, PcbLayer]:
    """Build layer definitions from Board6 properties and static defaults.

    Reads mechanical layer names and MECHKIND from the Board6 property
    record to determine each mechanical layer's function (assembly,
    courtyard, board outline, etc.).
    """
    layers: dict[int, PcbLayer] = {}
    for num, (name, fn, side) in _ALTIUM_LAYER_INFO.items():
        layers[num] = PcbLayer(name=name, function=fn, side=side, number=num)

    # Override mechanical layer metadata from Board6 if available.
    for i in range(1, 17):
        layer_num = 56 + i
        # Try multiple property name patterns (lowercase from parse_record_payload)
        name_key = f"mechanical{i}name"
        kind_key = f"mechanical{i}kind"
        v9_kind_key = f"v9_mechanical{i}kind"

        custom_name = board_props.get(name_key, "")
        if custom_name:
            layers[layer_num] = PcbLayer(
                name=custom_name,
                function=layers[layer_num].function,
                side=layers[layer_num].side,
                number=layer_num,
            )

        kind = (board_props.get(kind_key) or board_props.get(v9_kind_key) or "").lower()
        if kind and kind in _MECHKIND_MAP:
            fn, side = _MECHKIND_MAP[kind]
            layers[layer_num] = PcbLayer(
                name=layers[layer_num].name,
                function=fn,
                side=side,
                number=layer_num,
            )

    return layers


def _layer_name(num: int, layer_map: dict[int, PcbLayer]) -> str:
    """Get native layer name for a layer number, or '' if unmapped."""
    layer = layer_map.get(num)
    return layer.name if layer else ""


def _net_number(raw: int) -> int:
    """Map Altium net index to domain net number (0 = unconnected)."""
    return 0 if raw == _NET_UNCONNECTED else raw + 1


# ---------------------------------------------------------------------------
# Arc conversion: center/radius/angles → three-point
# ---------------------------------------------------------------------------


def _arc_to_three_point(
    cx_mm: float,
    cy_mm: float,
    radius_mm: float,
    start_deg: float,
    end_deg: float,
) -> tuple[float, float, float, float, float, float]:
    """Convert a center/radius/angle arc to (sx, sy, mx, my, ex, ey).

    Altium angles are in degrees counter-clockwise from the X-axis.
    Y is negated in the caller so we just use standard trig here.
    """
    sa = math.radians(start_deg)
    ea = math.radians(end_deg)
    # Mid-angle: halfway around the arc from start to end.
    if end_deg >= start_deg:
        ma = (sa + ea) / 2
    else:
        # Arc wraps past 360°.
        ma = (sa + ea + 2 * math.pi) / 2
        if ma >= 2 * math.pi:
            ma -= 2 * math.pi

    sx = cx_mm + radius_mm * math.cos(sa)
    sy = cy_mm + radius_mm * math.sin(sa)
    mx = cx_mm + radius_mm * math.cos(ma)
    my = cy_mm + radius_mm * math.sin(ma)
    ex = cx_mm + radius_mm * math.cos(ea)
    ey = cy_mm + radius_mm * math.sin(ea)
    return (sx, sy, mx, my, ex, ey)


# ---------------------------------------------------------------------------
# Stream parsers
# ---------------------------------------------------------------------------


def _parse_nets(data: bytes) -> dict[int, PcbNet]:
    """Parse Nets6/Data → {net_number: PcbNet}.

    Nets are numbered starting at 1 (index+1 in the stream order).
    Net 0 is reserved for "unconnected".
    """
    records = _read_text_records(data)
    nets: dict[int, PcbNet] = {0: PcbNet(number=0, name="")}
    for i, rec in enumerate(records):
        num = i + 1
        nets[num] = PcbNet(number=num, name=rec.get("name", ""))
    return nets


def _parse_components(data: bytes, layer_map: dict[int, PcbLayer]) -> list[PcbFootprint]:
    """Parse Components6/Data → list of footprint shells.

    Component records are text-based and contain position, pattern,
    layer, rotation, and designator.  Pads and geometry are added later.
    """
    records = _read_text_records(data)
    footprints: list[PcbFootprint] = []
    front_name = _layer_name(1, layer_map) or "Top Layer"
    back_name = _layer_name(32, layer_map) or "Bottom Layer"
    for rec in records:
        x_str = rec.get("x", "0mil")
        y_str = rec.get("y", "0mil")
        x_mm = _parse_mil(x_str)
        y_mm = -_parse_mil(y_str)  # Negate Y

        layer_str = rec.get("layer", "TOP")
        layer = front_name if layer_str.upper() == "TOP" else back_name

        rot = _parse_rotation(rec.get("rotation", "0"))

        ref = rec.get("sourcedesignator", rec.get("designator", "?"))
        pattern = rec.get("pattern", "")

        footprints.append(
            PcbFootprint(
                reference=ref,
                footprint_lib=pattern,
                x=x_mm,
                y=y_mm,
                rotation=rot,
                layer=layer,
            )
        )
    return footprints


def _parse_tracks(
    data: bytes, layer_map: dict[int, PcbLayer]
) -> tuple[list[PcbSegment], dict[int, list[PcbLine]]]:
    """Parse Tracks6/Data → board-level segments + per-component lines.

    Returns (segments, comp_lines) where comp_lines maps component index
    to a list of PcbLine objects for silkscreen/fab assignment.
    """
    records = _read_binary_records(data)
    segments: list[PcbSegment] = []
    comp_lines: dict[int, list[PcbLine]] = {}

    for rec_type, body in records:
        if rec_type != 4 or len(body) < 33:
            continue

        layer_num = body[0]
        net_raw = _u16(body, 3)
        comp_idx = _u16(body, 7)
        x1 = _int_to_mm(_i32(body, 13))
        y1 = -_int_to_mm(_i32(body, 17))
        x2 = _int_to_mm(_i32(body, 21))
        y2 = -_int_to_mm(_i32(body, 25))
        width = _int_to_mm(_i32(body, 29))

        layer = _layer_name(layer_num, layer_map)
        if not layer:
            continue

        if comp_idx == _COMPONENT_NONE:
            if layer_num in _COPPER_LAYERS:
                segments.append(
                    PcbSegment(
                        start_x=x1,
                        start_y=y1,
                        end_x=x2,
                        end_y=y2,
                        width=width,
                        layer=layer,
                        net_number=_net_number(net_raw),
                    )
                )
        else:
            comp_lines.setdefault(comp_idx, []).append(
                PcbLine(
                    start_x=x1,
                    start_y=y1,
                    end_x=x2,
                    end_y=y2,
                    layer=layer,
                    width=width,
                )
            )

    return segments, comp_lines


def _parse_vias(data: bytes, layer_map: dict[int, PcbLayer]) -> list[PcbVia]:
    """Parse Vias6/Data → list of PcbVia."""
    records = _read_binary_records(data)
    vias: list[PcbVia] = []

    for rec_type, body in records:
        if rec_type != 3 or len(body) < 31:
            continue

        net_raw = _u16(body, 3)
        x = _int_to_mm(_i32(body, 13))
        y = -_int_to_mm(_i32(body, 17))
        diameter = _int_to_mm(_i32(body, 21))
        hole = _int_to_mm(_i32(body, 25))
        start_layer = body[29]
        end_layer = body[30]

        layers = [_layer_name(start_layer, layer_map), _layer_name(end_layer, layer_map)]
        layers = [ly for ly in layers if ly]
        if not layers:
            continue

        vias.append(
            PcbVia(
                x=x,
                y=y,
                size=diameter,
                drill=hole,
                layers=layers,
                net_number=_net_number(net_raw),
            )
        )

    return vias


def _parse_arcs(
    data: bytes, layer_map: dict[int, PcbLayer]
) -> tuple[list[PcbTraceArc], dict[int, list[PcbArc]]]:
    """Parse Arcs6/Data → board-level trace arcs + per-component arcs.

    Returns (trace_arcs, comp_arcs) where comp_arcs maps component index
    to a list of PcbArc objects.
    """
    records = _read_binary_records(data)
    trace_arcs: list[PcbTraceArc] = []
    comp_arcs: dict[int, list[PcbArc]] = {}

    for rec_type, body in records:
        if rec_type != 1 or len(body) < 45:
            continue

        layer_num = body[0]
        net_raw = _u16(body, 3)
        comp_idx = _u16(body, 7)
        cx = _int_to_mm(_i32(body, 13))
        cy = -_int_to_mm(_i32(body, 17))
        radius = _int_to_mm(_u32(body, 21))
        start_angle = _f64(body, 25)
        end_angle = _f64(body, 33)
        width = _int_to_mm(_u32(body, 41))

        layer = _layer_name(layer_num, layer_map)
        if not layer:
            continue

        # Y is already negated in cy; negate angles to flip arc direction.
        sx, sy, mx, my, ex, ey = _arc_to_three_point(cx, cy, radius, -start_angle, -end_angle)

        if comp_idx == _COMPONENT_NONE and layer_num in _COPPER_LAYERS:
            trace_arcs.append(
                PcbTraceArc(
                    start_x=sx,
                    start_y=sy,
                    mid_x=mx,
                    mid_y=my,
                    end_x=ex,
                    end_y=ey,
                    width=width,
                    layer=layer,
                    net_number=_net_number(net_raw),
                )
            )
        elif comp_idx != _COMPONENT_NONE:
            comp_arcs.setdefault(comp_idx, []).append(
                PcbArc(
                    start_x=sx,
                    start_y=sy,
                    mid_x=mx,
                    mid_y=my,
                    end_x=ex,
                    end_y=ey,
                    layer=layer,
                    width=width,
                )
            )

    return trace_arcs, comp_arcs


def _parse_pads(
    data: bytes, nets: dict[int, PcbNet], layer_map: dict[int, PcbLayer]
) -> list[tuple[int, PcbPad]]:
    """Parse Pads6/Data → list of (component_index, PcbPad).

    Each pad record has 6 subrecords: name, skip, skip, skip, geometry,
    per-layer-overrides.
    """
    pos = 0
    pads: list[tuple[int, PcbPad]] = []

    while pos < len(data):
        if data[pos] != 2:
            break
        pos += 1

        # Sub1: pad name (Pascal string)
        if pos + 4 > len(data):
            break
        sub1_len = _u32(data, pos)
        pos += 4
        pad_name = ""
        if sub1_len > 0:
            name_len = data[pos]
            pad_name = data[pos + 1 : pos + 1 + name_len].decode("cp1252", errors="replace")
        pos += sub1_len

        # Sub2–Sub4: skip
        for _ in range(3):
            if pos + 4 > len(data):
                break
            sl = _u32(data, pos)
            pos += 4 + sl

        # Sub5: main pad geometry
        if pos + 4 > len(data):
            break
        sub5_len = _u32(data, pos)
        pos += 4
        sub5 = data[pos : pos + sub5_len]
        pos += sub5_len

        # Sub6: per-layer overrides
        if pos + 4 > len(data):
            break
        sub6_len = _u32(data, pos)
        pos += 4
        sub6 = data[pos : pos + sub6_len] if sub6_len > 0 else b""
        pos += sub6_len

        if sub5_len < 61:
            continue

        layer_num = sub5[0]
        net_raw = _u16(sub5, 3)
        comp_idx = _u16(sub5, 7)
        px = _int_to_mm(_i32(sub5, 13))
        py = -_int_to_mm(_i32(sub5, 17))
        top_sx = _int_to_mm(_i32(sub5, 21))
        top_sy = _int_to_mm(_i32(sub5, 25))
        holesize = _int_to_mm(_u32(sub5, 45))
        shape_byte = sub5[49] if sub5_len > 49 else 1
        # Determine shape
        shape = _PAD_SHAPES.get(shape_byte, "rect")
        # Check sub6 shape_alt for roundrect
        if sub6 and len(sub6) > 551:
            # shape_alt array starts at offset 519, 32 entries of u8
            # Check top layer (index 0) shape_alt
            shape_alt = sub6[519]
            if shape_alt == _PAD_SHAPE_ALT_ROUNDRECT:
                shape = "roundrect"

        # Determine layers (multi-layer pad = layer 74 = through-hole)
        layers = [_layer_name(layer_num, layer_map)] if layer_num in _COPPER_LAYERS else ["*.Cu"]

        net_num = _net_number(net_raw)
        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        pads.append(
            (
                comp_idx,
                PcbPad(
                    number=pad_name,
                    x=px,
                    y=py,
                    width=top_sx,
                    height=top_sy,
                    shape=shape,
                    layers=layers,
                    net_number=net_num,
                    net_name=net_name,
                    footprint_ref="",  # Set during footprint assembly
                    drill=holesize,
                ),
            )
        )

    return pads


def _parse_texts(data: bytes, layer_map: dict[int, PcbLayer]) -> list[tuple[int, PcbText]]:
    """Parse Texts6/Data → list of (component_index, PcbText).

    Each text record has 2 subrecords: binary properties + Pascal string.
    """
    pos = 0
    texts: list[tuple[int, PcbText]] = []

    while pos < len(data):
        if data[pos] != 5:
            break
        pos += 1

        # Sub1: binary properties
        if pos + 4 > len(data):
            break
        sub1_len = _u32(data, pos)
        pos += 4
        sub1 = data[pos : pos + sub1_len]
        pos += sub1_len

        # Sub2: text content (Pascal string)
        if pos + 4 > len(data):
            break
        sub2_len = _u32(data, pos)
        pos += 4
        sub2 = data[pos : pos + sub2_len]
        pos += sub2_len

        if sub1_len < 40:
            continue

        layer_num = sub1[0]
        comp_idx = _u16(sub1, 7)
        tx = _int_to_mm(_i32(sub1, 13))
        ty = -_int_to_mm(_i32(sub1, 17))
        height = _int_to_mm(_u32(sub1, 21))
        rotation = _f64(sub1, 27)

        is_comment = sub1[40] if sub1_len > 40 else 0
        is_designator = sub1[41] if sub1_len > 41 else 0

        # Text content from sub2 Pascal string
        text_content = ""
        if sub2_len > 0:
            str_len = sub2[0]
            text_content = sub2[1 : 1 + str_len].decode("cp1252", errors="replace")

        layer = _layer_name(layer_num, layer_map)
        if not layer:
            continue

        kind = ""
        if is_designator:
            kind = "reference"
        elif is_comment:
            kind = "value"

        texts.append(
            (
                comp_idx,
                PcbText(
                    text=text_content,
                    x=tx,
                    y=ty,
                    rotation=rotation,
                    layer=layer,
                    font_size=height,
                    kind=kind,
                ),
            )
        )

    return texts


def _parse_fills(data: bytes, layer_map: dict[int, PcbLayer]) -> list[PcbPolygon]:
    """Parse Fills6/Data → list of PcbPolygon (rectangular copper fills)."""
    records = _read_binary_records(data)
    fills: list[PcbPolygon] = []

    for rec_type, body in records:
        if rec_type != 6 or len(body) < 37:
            continue

        layer_num = body[0]
        if layer_num not in _COPPER_LAYERS:
            continue

        net_raw = _u16(body, 3)
        x1 = _int_to_mm(_i32(body, 13))
        y1 = -_int_to_mm(_i32(body, 17))
        x2 = _int_to_mm(_i32(body, 21))
        y2 = -_int_to_mm(_i32(body, 25))
        rotation = _f64(body, 29)

        # Build 4-corner rectangle, apply rotation around center
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        hw, hh = (x2 - x1) / 2, (y2 - y1) / 2
        corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]

        if rotation != 0:
            rad = math.radians(rotation)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            corners = [(dx * cos_r - dy * sin_r, dx * sin_r + dy * cos_r) for dx, dy in corners]

        points = [(cx + dx, cy + dy) for dx, dy in corners]
        net_num = _net_number(net_raw)

        fills.append(
            PcbPolygon(
                points=points,
                layer=_layer_name(layer_num, layer_map),
                net_number=net_num,
            )
        )

    return fills


def _parse_regions(
    data: bytes, nets: dict[int, PcbNet], layer_map: dict[int, PcbLayer]
) -> list[PcbPolygon]:
    """Parse Regions6/Data → list of PcbPolygon.

    Region records contain a property string followed by vertex data
    (pairs of float64 in Altium internal units).  All layers are included —
    copper regions carry net info, non-copper regions (silkscreen fills,
    paste openings, etc.) have net_number 0.
    """
    records = _read_binary_records(data)
    polygons: list[PcbPolygon] = []

    for rec_type, body in records:
        if rec_type != 11 or len(body) < 22:
            continue

        layer_num = body[0]
        net_raw = _u16(body, 3)
        holecount = _u16(body, 14)

        # Property string
        prop_len = _u32(body, 18)
        prop_end = 22 + prop_len
        if prop_end > len(body):
            continue

        prop_str = body[22:prop_end]
        props = parse_record_payload(prop_str)

        # Determine layer from V7 property or fallback to byte
        v7_layer = props.get("v7_layer", "").upper()
        resolved_num = (
            _V7_NAME_TO_NUM[v7_layer] if v7_layer and v7_layer in _V7_NAME_TO_NUM else layer_num
        )

        layer = _layer_name(resolved_num, layer_map)
        if not layer:
            continue

        # Read outline vertices (f64 pairs)
        vpos = prop_end
        if vpos + 4 > len(body):
            continue
        vertex_count = _u32(body, vpos)
        vpos += 4

        points: list[tuple[float, float]] = []
        for _ in range(vertex_count):
            if vpos + 16 > len(body):
                break
            vx = _f64(body, vpos)
            vy = _f64(body, vpos + 8)
            points.append((_int_to_mm(int(vx)), -_int_to_mm(int(vy))))
            vpos += 16

        if len(points) < 3:
            continue

        # Skip hole vertices
        for _ in range(holecount):
            if vpos + 4 > len(body):
                break
            hole_vc = _u32(body, vpos)
            vpos += 4 + hole_vc * 16

        net_num = _net_number(net_raw) if resolved_num in _COPPER_LAYERS else 0
        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        polygons.append(
            PcbPolygon(
                points=points,
                layer=layer,
                net_number=net_num,
                net_name=net_name,
            )
        )

    return polygons


# Bytes per extended vertex in ShapeBasedRegions6:
# 1 (isRound) + 4 (x) + 4 (y) + 4 (cx) + 4 (cy) + 4 (radius)
# + 8 (angle1) + 8 (angle2) = 37
_EXTENDED_VERTEX_SIZE = 37


def _parse_shape_based_regions(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
) -> tuple[list[PcbPolygon], dict[int, list[PcbPolygon]]]:
    """Parse ShapeBasedRegions6/Data → board polygons + per-component polygons.

    Uses the extended vertex format (37 bytes per vertex with arc support).
    Returns (board_polygons, comp_polygons) where comp_polygons maps
    component index → list of body-outline polygons.
    """
    records = _read_binary_records(data)
    board_polygons: list[PcbPolygon] = []
    comp_polygons: dict[int, list[PcbPolygon]] = {}

    for rec_type, body in records:
        if rec_type != 11 or len(body) < 22:
            continue

        layer_num = body[0]
        net_raw = _u16(body, 3)
        comp_idx = _u16(body, 7)
        holecount = _u16(body, 14)

        # Property string
        prop_len = _u32(body, 18)
        prop_end = 22 + prop_len
        if prop_end > len(body):
            continue

        props = parse_record_payload(body[22:prop_end])

        # Determine layer from V7 property or fallback to byte
        v7_layer = props.get("v7_layer", "").upper()
        resolved_num = (
            _V7_NAME_TO_NUM[v7_layer] if v7_layer and v7_layer in _V7_NAME_TO_NUM else layer_num
        )

        layer = _layer_name(resolved_num, layer_map)
        if not layer:
            continue

        # Read extended vertices.  Count is stored as N but there are N+1
        # vertices (closing vertex repeats the first point).
        vpos = prop_end
        if vpos + 4 > len(body):
            continue
        stored_count = _u32(body, vpos)
        vertex_count = stored_count + 1  # includes closing vertex
        vpos += 4

        points: list[tuple[float, float]] = []
        for _ in range(vertex_count):
            if vpos + _EXTENDED_VERTEX_SIZE > len(body):
                break
            # byte 0: isRound (ignored for now — arcs linearised to endpoint)
            vx = _i32(body, vpos + 1)
            vy = _i32(body, vpos + 5)
            points.append((_int_to_mm(vx), -_int_to_mm(vy)))
            vpos += _EXTENDED_VERTEX_SIZE

        if len(points) < 3:
            continue

        # Skip hole vertices (simple f64 pairs, same as Regions6)
        for _ in range(holecount):
            if vpos + 4 > len(body):
                break
            hole_vc = _u32(body, vpos)
            vpos += 4 + hole_vc * 16

        net_num = _net_number(net_raw) if resolved_num in _COPPER_LAYERS else 0
        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        poly = PcbPolygon(
            points=points,
            layer=layer,
            net_number=net_num,
            net_name=net_name,
        )

        if comp_idx == _COMPONENT_NONE:
            board_polygons.append(poly)
        else:
            comp_polygons.setdefault(comp_idx, []).append(poly)

    return board_polygons, comp_polygons


def _parse_board_outline(
    tracks_data: bytes, arcs_data: bytes, layer_map: dict[int, PcbLayer]
) -> tuple[list[PcbLine], list[PcbArc]]:
    """Extract board outline from tracks and arcs on Mechanical 1 (layer 57).

    Falls back to Keep-Out layer (74) if no Mechanical 1 primitives found.
    Also checks for any mechanical layer whose MECHKIND is EDGE.
    """
    outline_lines: list[PcbLine] = []
    outline_arcs: list[PcbArc] = []

    # Prefer a layer with EDGE function (from MECHKIND=BoardShape), then
    # fall back to Mechanical 1 (57), then Keep-Out (74).
    edge_layers = [
        num for num, lyr in layer_map.items() if lyr.function == LayerFunction.EDGE and num >= 57
    ]
    candidates = edge_layers or [57]
    candidates.append(74)
    # Deduplicate while preserving order
    seen: set[int] = set()
    target_layers: list[int] = []
    for n in candidates:
        if n not in seen:
            seen.add(n)
            target_layers.append(n)

    for target_layer in target_layers:
        if outline_lines or outline_arcs:
            break

        edge_name = _layer_name(target_layer, layer_map) or "Edge"

        for rec_type, body in _read_binary_records(tracks_data):
            if rec_type != 4 or len(body) < 33:
                continue
            if body[0] != target_layer:
                continue
            comp_idx = _u16(body, 7)
            if comp_idx != _COMPONENT_NONE:
                continue

            x1 = _int_to_mm(_i32(body, 13))
            y1 = -_int_to_mm(_i32(body, 17))
            x2 = _int_to_mm(_i32(body, 21))
            y2 = -_int_to_mm(_i32(body, 25))
            width = _int_to_mm(_i32(body, 29))

            outline_lines.append(
                PcbLine(
                    start_x=x1,
                    start_y=y1,
                    end_x=x2,
                    end_y=y2,
                    layer=edge_name,
                    width=width,
                )
            )

        for rec_type, body in _read_binary_records(arcs_data):
            if rec_type != 1 or len(body) < 45:
                continue
            if body[0] != target_layer:
                continue
            comp_idx = _u16(body, 7)
            if comp_idx != _COMPONENT_NONE:
                continue

            cx = _int_to_mm(_i32(body, 13))
            cy = -_int_to_mm(_i32(body, 17))
            radius = _int_to_mm(_u32(body, 21))
            start_angle = _f64(body, 25)
            end_angle = _f64(body, 33)
            width = _int_to_mm(_u32(body, 41))

            sx, sy, mx, my, ex, ey = _arc_to_three_point(cx, cy, radius, -start_angle, -end_angle)
            outline_arcs.append(
                PcbArc(
                    start_x=sx,
                    start_y=sy,
                    mid_x=mx,
                    mid_y=my,
                    end_x=ex,
                    end_y=ey,
                    layer=edge_name,
                    width=width,
                )
            )

    return outline_lines, outline_arcs


def _parse_component_bodies(data: bytes) -> dict[int, list[PcbModel3D]]:
    """Parse ComponentBodies6/Data → {component_index: [PcbModel3D, ...]}.

    Text records with pipe-delimited properties. Key properties:
    - ``MODELID``: OLE stream ID for the embedded STEP data
    - ``COMPONENT``: component index (int, 65535 = board-level body)
    - ``MODEL.2D.X``, ``MODEL.2D.Y``: 2D position in mil
    - ``MODEL.3D.ROTX/Y/Z``: rotation in degrees
    - ``MODEL.3D.DZ``: Z offset in mil
    """
    records = _read_text_records(data)
    result: dict[int, list[PcbModel3D]] = {}

    for rec in records:
        model_id = rec.get("modelid", "")
        if not model_id:
            continue

        comp_str = rec.get("component", "")
        if not comp_str:
            continue
        comp_idx = int(comp_str)
        if comp_idx == _COMPONENT_NONE:
            continue

        # 2D position (mil → mm)
        x_str = rec.get("model.2d.x", "0mil")
        y_str = rec.get("model.2d.y", "0mil")
        offset_x = _parse_mil(x_str)
        offset_y = -_parse_mil(y_str)

        # Z offset (mil → mm)
        dz_str = rec.get("model.3d.dz", "0mil")
        offset_z = _parse_mil(dz_str)

        # Rotation (degrees, may be scientific notation)
        rot_x = float(rec.get("model.3d.rotx", "0"))
        rot_y = float(rec.get("model.3d.roty", "0"))
        rot_z = float(rec.get("model.3d.rotz", "0"))

        model = PcbModel3D(
            source=model_id,
            offset=(offset_x, offset_y, offset_z),
            rotation=(rot_x, rot_y, rot_z),
        )
        result.setdefault(comp_idx, []).append(model)

    return result


def _compute_bbox(
    fp: PcbFootprint,
) -> tuple[float, float, float, float] | None:
    """Compute footprint bounding box from pads with 0.5mm margin."""
    if not fp.pads:
        return None
    xs = [p.x - p.width / 2 for p in fp.pads] + [p.x + p.width / 2 for p in fp.pads]
    ys = [p.y - p.height / 2 for p in fp.pads] + [p.y + p.height / 2 for p in fp.pads]
    margin = 0.5
    return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def _read_stream(ole: olefile.OleFileIO, name: str) -> bytes:
    """Read a stream from the OLE container, returning empty bytes if absent."""
    if ole.exists(name):
        return ole.openstream(name).read()
    return b""


def parse_altium_pcb(path: Path) -> PcbBoard:
    """Parse an Altium .PcbDoc file into the PCB domain model."""
    ole = olefile.OleFileIO(str(path))
    try:
        # Read all streams
        nets_data = _read_stream(ole, "Nets6/Data")
        comp_data = _read_stream(ole, "Components6/Data")
        tracks_data = _read_stream(ole, "Tracks6/Data")
        vias_data = _read_stream(ole, "Vias6/Data")
        arcs_data = _read_stream(ole, "Arcs6/Data")
        pads_data = _read_stream(ole, "Pads6/Data")
        texts_data = _read_stream(ole, "Texts6/Data")
        fills_data = _read_stream(ole, "Fills6/Data")
        regions_data = _read_stream(ole, "Regions6/Data")
        sb_regions_data = _read_stream(ole, "ShapeBasedRegions6/Data")
        comp_bodies_data = _read_stream(ole, "ComponentBodies6/Data")
        board_data = _read_stream(ole, "Board6/Data")
    finally:
        ole.close()

    # Build layer map from Board6 metadata + static defaults
    board_props: dict[str, str] = {}
    if board_data:
        board_records = _read_text_records(board_data)
        if board_records:
            board_props = board_records[0]
    layer_map = _build_layer_map(board_props)

    # Parse text streams
    nets = _parse_nets(nets_data)
    footprints = _parse_components(comp_data, layer_map)

    # Parse binary streams
    segments, comp_lines = _parse_tracks(tracks_data, layer_map)
    vias = _parse_vias(vias_data, layer_map)
    trace_arcs, comp_arcs = _parse_arcs(arcs_data, layer_map)
    raw_pads = _parse_pads(pads_data, nets, layer_map)
    raw_texts = _parse_texts(texts_data, layer_map)
    fills = _parse_fills(fills_data, layer_map)
    regions = _parse_regions(regions_data, nets, layer_map)
    sb_board_polys, sb_comp_polys = _parse_shape_based_regions(sb_regions_data, nets, layer_map)
    comp_models = _parse_component_bodies(comp_bodies_data)

    # Board outline
    outline_lines, outline_arcs = _parse_board_outline(tracks_data, arcs_data, layer_map)

    # Assemble footprints: assign pads, texts, lines, arcs by component index.
    # Build name→function lookup for categorising component geometry.
    silk_names = {
        lyr.name for lyr in layer_map.values() if lyr.function == LayerFunction.SILKSCREEN
    }
    fab_names = {lyr.name for lyr in layer_map.values() if lyr.function == LayerFunction.FAB}

    for comp_idx, pad in raw_pads:
        if comp_idx < len(footprints):
            pad.footprint_ref = footprints[comp_idx].reference
            footprints[comp_idx].pads.append(pad)

    for comp_idx, text in raw_texts:
        if comp_idx != _COMPONENT_NONE and comp_idx < len(footprints):
            footprints[comp_idx].texts.append(text)

    for comp_idx, lines in comp_lines.items():
        if comp_idx < len(footprints):
            fp = footprints[comp_idx]
            for line in lines:
                if line.layer in silk_names:
                    fp.silkscreen_lines.append(line)
                elif line.layer in fab_names:
                    fp.fab_lines.append(line)

    for comp_idx, arcs in comp_arcs.items():
        if comp_idx < len(footprints):
            fp = footprints[comp_idx]
            for arc in arcs:
                if arc.layer in fab_names:
                    fp.fab_arcs.append(arc)

    for comp_idx, polys in sb_comp_polys.items():
        if comp_idx < len(footprints):
            fp = footprints[comp_idx]
            for poly in polys:
                if poly.layer in silk_names:
                    fp.silkscreen_polygons.append(poly)
                elif poly.layer in fab_names:
                    fp.fab_polygons.append(poly)

    for comp_idx, models in comp_models.items():
        if comp_idx < len(footprints):
            footprints[comp_idx].models_3d.extend(models)

    # Compute footprint bounding boxes
    for fp in footprints:
        fp.bbox = _compute_bbox(fp)

    # Board name from Board6/Data (board_props already parsed above)
    board_name = board_props.get("filename", "")
    if "\\" in board_name:
        board_name = board_name.rsplit("\\", 1)[-1]
    if board_name.endswith(".$$$"):
        board_name = board_name[:-4]

    polygons = fills + regions + sb_board_polys

    return PcbBoard(
        name=board_name,
        nets=nets,
        footprints=footprints,
        segments=segments,
        vias=vias,
        outline_lines=outline_lines,
        outline_arcs=outline_arcs,
        polygons=polygons,
        trace_arcs=trace_arcs,
        layers=list(layer_map.values()),
    )
