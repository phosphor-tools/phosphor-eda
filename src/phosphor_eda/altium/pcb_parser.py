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
    PcbArc,
    PcbBoard,
    PcbFootprint,
    PcbLine,
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
# Constants
# ---------------------------------------------------------------------------

# 1 internal unit = 0.1 µinch = 0.0000001 inch = 0.00000254 mm
_INT_TO_MM = 0.00000254

# 1 mil = 0.001 inch = 0.0254 mm
_MIL_TO_MM = 0.0254

_NET_UNCONNECTED = 0xFFFF
_COMPONENT_NONE = 0xFFFF

# Altium layer number → KiCad-convention layer name.
_LAYER_MAP: dict[int, str] = {
    1: "F.Cu",
    **{i: f"In{i - 1}.Cu" for i in range(2, 32)},
    32: "B.Cu",
    33: "F.SilkS",
    34: "B.SilkS",
    35: "F.Paste",
    36: "B.Paste",
    37: "F.Mask",
    38: "B.Mask",
}

# Copper layer numbers for filtering.
_COPPER_LAYERS = frozenset(range(1, 33))

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
        length = struct.unpack_from("<I", data, pos)[0]
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
        rec_len = struct.unpack_from("<I", data, pos + 1)[0]
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


def _layer_name(num: int) -> str:
    """Map an Altium layer number to the renderer's layer name string."""
    return _LAYER_MAP.get(num, "")


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


def _parse_components(data: bytes) -> list[PcbFootprint]:
    """Parse Components6/Data → list of footprint shells.

    Component records are text-based and contain position, pattern,
    layer, rotation, and designator.  Pads and geometry are added later.
    """
    records = _read_text_records(data)
    footprints: list[PcbFootprint] = []
    for rec in records:
        x_str = rec.get("x", "0mil")
        y_str = rec.get("y", "0mil")
        x_mm = _parse_mil(x_str)
        y_mm = -_parse_mil(y_str)  # Negate Y

        layer_str = rec.get("layer", "TOP")
        layer = "F.Cu" if layer_str.upper() == "TOP" else "B.Cu"

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
    data: bytes,
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
        net_raw = struct.unpack_from("<H", body, 3)[0]
        comp_idx = struct.unpack_from("<H", body, 7)[0]
        x1 = _int_to_mm(struct.unpack_from("<i", body, 13)[0])
        y1 = -_int_to_mm(struct.unpack_from("<i", body, 17)[0])
        x2 = _int_to_mm(struct.unpack_from("<i", body, 21)[0])
        y2 = -_int_to_mm(struct.unpack_from("<i", body, 25)[0])
        width = _int_to_mm(struct.unpack_from("<i", body, 29)[0])

        layer = _layer_name(layer_num)
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


def _parse_vias(data: bytes) -> list[PcbVia]:
    """Parse Vias6/Data → list of PcbVia."""
    records = _read_binary_records(data)
    vias: list[PcbVia] = []

    for rec_type, body in records:
        if rec_type != 3 or len(body) < 31:
            continue

        net_raw = struct.unpack_from("<H", body, 3)[0]
        x = _int_to_mm(struct.unpack_from("<i", body, 13)[0])
        y = -_int_to_mm(struct.unpack_from("<i", body, 17)[0])
        diameter = _int_to_mm(struct.unpack_from("<i", body, 21)[0])
        hole = _int_to_mm(struct.unpack_from("<i", body, 25)[0])
        start_layer = body[29]
        end_layer = body[30]

        layers = [_layer_name(start_layer), _layer_name(end_layer)]
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
    data: bytes,
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
        net_raw = struct.unpack_from("<H", body, 3)[0]
        comp_idx = struct.unpack_from("<H", body, 7)[0]
        cx = _int_to_mm(struct.unpack_from("<i", body, 13)[0])
        cy = -_int_to_mm(struct.unpack_from("<i", body, 17)[0])
        radius = _int_to_mm(struct.unpack_from("<I", body, 21)[0])
        start_angle = struct.unpack_from("<d", body, 25)[0]
        end_angle = struct.unpack_from("<d", body, 33)[0]
        width = _int_to_mm(struct.unpack_from("<I", body, 41)[0])

        layer = _layer_name(layer_num)
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


def _parse_pads(data: bytes, nets: dict[int, PcbNet]) -> list[tuple[int, PcbPad]]:
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
        sub1_len = struct.unpack_from("<I", data, pos)[0]
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
            sl = struct.unpack_from("<I", data, pos)[0]
            pos += 4 + sl

        # Sub5: main pad geometry
        if pos + 4 > len(data):
            break
        sub5_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        sub5 = data[pos : pos + sub5_len]
        pos += sub5_len

        # Sub6: per-layer overrides
        if pos + 4 > len(data):
            break
        sub6_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        sub6 = data[pos : pos + sub6_len] if sub6_len > 0 else b""
        pos += sub6_len

        if sub5_len < 61:
            continue

        layer_num = sub5[0]
        net_raw = struct.unpack_from("<H", sub5, 3)[0]
        comp_idx = struct.unpack_from("<H", sub5, 7)[0]
        px = _int_to_mm(struct.unpack_from("<i", sub5, 13)[0])
        py = -_int_to_mm(struct.unpack_from("<i", sub5, 17)[0])
        top_sx = _int_to_mm(struct.unpack_from("<i", sub5, 21)[0])
        top_sy = _int_to_mm(struct.unpack_from("<i", sub5, 25)[0])
        holesize = _int_to_mm(struct.unpack_from("<I", sub5, 45)[0])
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

        # Determine layers (multi-layer pad = layer 74 = keepout = through-hole)
        layers = [_layer_name(layer_num)] if layer_num in _COPPER_LAYERS else ["*.Cu"]

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


def _parse_texts(data: bytes) -> list[tuple[int, PcbText]]:
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
        sub1_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        sub1 = data[pos : pos + sub1_len]
        pos += sub1_len

        # Sub2: text content (Pascal string)
        if pos + 4 > len(data):
            break
        sub2_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        sub2 = data[pos : pos + sub2_len]
        pos += sub2_len

        if sub1_len < 40:
            continue

        layer_num = sub1[0]
        comp_idx = struct.unpack_from("<H", sub1, 7)[0]
        tx = _int_to_mm(struct.unpack_from("<i", sub1, 13)[0])
        ty = -_int_to_mm(struct.unpack_from("<i", sub1, 17)[0])
        height = _int_to_mm(struct.unpack_from("<I", sub1, 21)[0])
        rotation = struct.unpack_from("<d", sub1, 27)[0]

        is_comment = sub1[40] if sub1_len > 40 else 0
        is_designator = sub1[41] if sub1_len > 41 else 0

        # Text content from sub2 Pascal string
        text_content = ""
        if sub2_len > 0:
            str_len = sub2[0]
            text_content = sub2[1 : 1 + str_len].decode("cp1252", errors="replace")

        layer = _layer_name(layer_num)
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


def _parse_fills(data: bytes) -> list[PcbPolygon]:
    """Parse Fills6/Data → list of PcbPolygon (rectangular copper fills)."""
    records = _read_binary_records(data)
    fills: list[PcbPolygon] = []

    for rec_type, body in records:
        if rec_type != 6 or len(body) < 37:
            continue

        layer_num = body[0]
        if layer_num not in _COPPER_LAYERS:
            continue

        net_raw = struct.unpack_from("<H", body, 3)[0]
        x1 = _int_to_mm(struct.unpack_from("<i", body, 13)[0])
        y1 = -_int_to_mm(struct.unpack_from("<i", body, 17)[0])
        x2 = _int_to_mm(struct.unpack_from("<i", body, 21)[0])
        y2 = -_int_to_mm(struct.unpack_from("<i", body, 25)[0])
        rotation = struct.unpack_from("<d", body, 29)[0]

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
                layer=_layer_name(layer_num),
                net_number=net_num,
            )
        )

    return fills


def _parse_regions(data: bytes, nets: dict[int, PcbNet]) -> list[PcbPolygon]:
    """Parse Regions6/Data → list of PcbPolygon (copper zone fills).

    Region records contain a property string followed by vertex data.
    Only copper-layer regions are included.
    """
    records = _read_binary_records(data)
    polygons: list[PcbPolygon] = []

    for rec_type, body in records:
        if rec_type != 11 or len(body) < 22:
            continue

        layer_num = body[0]
        net_raw = struct.unpack_from("<H", body, 3)[0]
        holecount = struct.unpack_from("<H", body, 14)[0]

        # Property string
        prop_len = struct.unpack_from("<I", body, 18)[0]
        prop_end = 22 + prop_len
        if prop_end > len(body):
            continue

        prop_str = body[22:prop_end]
        props = parse_record_payload(prop_str)

        # Determine layer from V7 property or fallback to byte
        v7_layer = props.get("v7_layer", "").upper()
        layer = ""
        if v7_layer:
            # Map common V7 layer names
            v7_map = {
                "TOP": "F.Cu",
                "BOTTOM": "B.Cu",
                "TOPOVERLAY": "F.SilkS",
                "BOTTOMOVERLAY": "B.SilkS",
            }
            for k, v in v7_map.items():
                if v7_layer == k:
                    layer = v
                    break
            # Check for mid-layer pattern
            if not layer and v7_layer.startswith("MID"):
                layer = _layer_name(layer_num)
        if not layer:
            layer = _layer_name(layer_num)

        # Only include copper regions
        if not layer.endswith(".Cu"):
            continue

        # Read outline vertices (f64 pairs)
        vpos = prop_end
        if vpos + 4 > len(body):
            continue
        vertex_count = struct.unpack_from("<I", body, vpos)[0]
        vpos += 4

        points: list[tuple[float, float]] = []
        for _ in range(vertex_count):
            if vpos + 16 > len(body):
                break
            vx = struct.unpack_from("<d", body, vpos)[0]
            vy = struct.unpack_from("<d", body, vpos + 8)[0]
            points.append((_int_to_mm(int(vx)), -_int_to_mm(int(vy))))
            vpos += 16

        if len(points) < 3:
            continue

        # Skip hole vertices
        for _ in range(holecount):
            if vpos + 4 > len(body):
                break
            hole_vc = struct.unpack_from("<I", body, vpos)[0]
            vpos += 4 + hole_vc * 16

        net_num = _net_number(net_raw)
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


def _parse_board_outline(
    tracks_data: bytes, arcs_data: bytes
) -> tuple[list[PcbLine], list[PcbArc]]:
    """Extract board outline from tracks and arcs on Mechanical 1 (layer 57).

    Falls back to Keep-Out layer (74) if no Mechanical 1 primitives found.
    """
    outline_lines: list[PcbLine] = []
    outline_arcs: list[PcbArc] = []

    # Try Mechanical 1 first, then Keep-Out
    for target_layer in (57, 74):
        if outline_lines or outline_arcs:
            break

        for rec_type, body in _read_binary_records(tracks_data):
            if rec_type != 4 or len(body) < 33:
                continue
            if body[0] != target_layer:
                continue
            comp_idx = struct.unpack_from("<H", body, 7)[0]
            if comp_idx != _COMPONENT_NONE:
                continue

            x1 = _int_to_mm(struct.unpack_from("<i", body, 13)[0])
            y1 = -_int_to_mm(struct.unpack_from("<i", body, 17)[0])
            x2 = _int_to_mm(struct.unpack_from("<i", body, 21)[0])
            y2 = -_int_to_mm(struct.unpack_from("<i", body, 25)[0])
            width = _int_to_mm(struct.unpack_from("<i", body, 29)[0])

            outline_lines.append(
                PcbLine(
                    start_x=x1,
                    start_y=y1,
                    end_x=x2,
                    end_y=y2,
                    layer="Edge.Cuts",
                    width=width,
                )
            )

        for rec_type, body in _read_binary_records(arcs_data):
            if rec_type != 1 or len(body) < 45:
                continue
            if body[0] != target_layer:
                continue
            comp_idx = struct.unpack_from("<H", body, 7)[0]
            if comp_idx != _COMPONENT_NONE:
                continue

            cx = _int_to_mm(struct.unpack_from("<i", body, 13)[0])
            cy = -_int_to_mm(struct.unpack_from("<i", body, 17)[0])
            radius = _int_to_mm(struct.unpack_from("<I", body, 21)[0])
            start_angle = struct.unpack_from("<d", body, 25)[0]
            end_angle = struct.unpack_from("<d", body, 33)[0]
            width = _int_to_mm(struct.unpack_from("<I", body, 41)[0])

            sx, sy, mx, my, ex, ey = _arc_to_three_point(cx, cy, radius, -start_angle, -end_angle)
            outline_arcs.append(
                PcbArc(
                    start_x=sx,
                    start_y=sy,
                    mid_x=mx,
                    mid_y=my,
                    end_x=ex,
                    end_y=ey,
                    layer="Edge.Cuts",
                    width=width,
                )
            )

    return outline_lines, outline_arcs


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
        board_data = _read_stream(ole, "Board6/Data")
    finally:
        ole.close()

    # Parse text streams
    nets = _parse_nets(nets_data)
    footprints = _parse_components(comp_data)

    # Parse binary streams
    segments, comp_lines = _parse_tracks(tracks_data)
    vias = _parse_vias(vias_data)
    trace_arcs, comp_arcs = _parse_arcs(arcs_data)
    raw_pads = _parse_pads(pads_data, nets)
    raw_texts = _parse_texts(texts_data)
    fills = _parse_fills(fills_data)
    regions = _parse_regions(regions_data, nets)

    # Board outline
    outline_lines, outline_arcs = _parse_board_outline(tracks_data, arcs_data)

    # Assemble footprints: assign pads, texts, lines, arcs by component index
    _SILK_LAYERS = {"F.SilkS", "B.SilkS"}
    _FAB_LAYERS = {"F.Fab", "B.Fab"}

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
                if line.layer in _SILK_LAYERS:
                    fp.silkscreen_lines.append(line)
                elif line.layer in _FAB_LAYERS:
                    fp.fab_lines.append(line)

    for comp_idx, arcs in comp_arcs.items():
        if comp_idx < len(footprints):
            fp = footprints[comp_idx]
            for arc in arcs:
                if arc.layer in _FAB_LAYERS:
                    fp.fab_arcs.append(arc)

    # Compute footprint bounding boxes
    for fp in footprints:
        fp.bbox = _compute_bbox(fp)

    # Board name from Board6/Data
    board_name = ""
    if board_data:
        board_records = _read_text_records(board_data)
        if board_records:
            board_name = board_records[0].get("filename", "")
            # Extract just the filename from the path
            if "\\" in board_name:
                board_name = board_name.rsplit("\\", 1)[-1]
            if board_name.endswith(".$$$"):
                board_name = board_name[:-4]

    polygons = fills + regions

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
    )
