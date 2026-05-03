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
from typing import TYPE_CHECKING

import olefile

from phosphor_eda.altium._helpers import u32
from phosphor_eda.altium.errors import ParseContext
from phosphor_eda.altium.pcb_records import (
    COMPONENT_NONE,
    NET_UNCONNECTED,
    ArcRecord,
    ExtendedVertex,
    FillRecord,
    PadRecord,
    RegionRecord,
    ShapeBasedRegionRecord,
    TextRecord,
    TrackRecord,
    ViaRecord,
)
from phosphor_eda.altium.record_parser import parse_record_payload
from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
    PcbArc,
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
    PcbZone,
)
from phosphor_eda.project import DesignRule, DiffPair, NetClass, Stackup, StackupLayer
from phosphor_eda.text import strip_overline

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 1 internal unit = 0.1 µinch = 0.0000001 inch = 0.00000254 mm
_INT_TO_MM = 0.00000254

# 1 mil = 0.001 inch = 0.0254 mm
_MIL_TO_MM = 0.0254

_NET_UNCONNECTED = NET_UNCONNECTED
_COMPONENT_NONE = COMPONENT_NONE

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


def read_text_records(data: bytes) -> list[dict[str, str]]:
    """Read pipe-delimited text records with a 4-byte LE length prefix."""
    records: list[dict[str, str]] = []
    pos = 0
    while pos + 4 <= len(data):
        length = u32(data, pos)
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
        rec_len = u32(data, pos + 1)
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
    return float(s.removesuffix("mil")) * _MIL_TO_MM


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

    The arc goes **counter-clockwise** from ``start_deg`` to ``end_deg``.
    When ``end_deg < start_deg`` the arc wraps past 360°.

    Callers that negate Y should use original (non-negated) angles here,
    then negate the Y coordinates of the returned points.
    """
    sa = math.radians(start_deg)
    ea = math.radians(end_deg)
    # Mid-angle: halfway around the CCW arc from start to end.
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
# Arc linearization for ShapeBasedRegion extended vertices
# ---------------------------------------------------------------------------

# Number of line segments per full circle when linearizing arcs
_ARC_SEGMENTS_PER_CIRCLE = 64


def linearize_arc_vertices(
    vertices: list[ExtendedVertex],
    segments_per_circle: int = _ARC_SEGMENTS_PER_CIRCLE,
) -> list[tuple[int, int]]:
    """Convert extended vertices to a polyline, interpolating arc edges.

    When a vertex has ``is_round=True``, the edge from that vertex to the
    next is an arc defined by center/radius/angles. This function replaces
    each arc edge with a sequence of line segments approximating the curve.

    Coordinates remain in Altium internal units (0.1 µinch). The caller
    handles mm conversion.
    """
    if not vertices:
        return []

    points: list[tuple[int, int]] = []

    for v in vertices:
        if not v.is_round:
            points.append((v.x, v.y))
            continue

        # Arc edge: interpolate from start_angle to end_angle
        cx, cy = v.center_x, v.center_y
        radius = v.radius
        start_deg = v.start_angle
        end_deg = v.end_angle

        # Compute sweep angle (always CCW in Altium)
        sweep = end_deg - start_deg
        if sweep <= 0:
            sweep += 360.0

        # Number of segments proportional to sweep angle
        n_segs = max(2, round(segments_per_circle * sweep / 360.0))

        for j in range(n_segs):
            angle_deg = start_deg + sweep * j / n_segs
            angle_rad = math.radians(angle_deg)
            px = round(cx + radius * math.cos(angle_rad))
            py = round(cy + radius * math.sin(angle_rad))
            points.append((px, py))

    return points


# ---------------------------------------------------------------------------
# Stream parsers
# ---------------------------------------------------------------------------


def _parse_nets(data: bytes) -> dict[int, PcbNet]:
    """Parse Nets6/Data → {net_number: PcbNet}.

    Nets are numbered starting at 1 (index+1 in the stream order).
    Net 0 is reserved for "unconnected".
    """
    records = read_text_records(data)
    nets: dict[int, PcbNet] = {0: PcbNet(number=0, name="")}
    for i, rec in enumerate(records):
        num = i + 1
        raw_name = rec.get("name", "")
        # Strip Altium overline markup (e.g. "C\S\" → "CS") so net names
        # are clean for CSS selectors and downstream tooling.
        clean_name = strip_overline(raw_name)[0]
        nets[num] = PcbNet(number=num, name=clean_name)
    return nets


def _parse_components(data: bytes, layer_map: dict[int, PcbLayer]) -> list[PcbFootprint]:
    """Parse Components6/Data → list of footprint shells.

    Component records are text-based and contain position, pattern,
    layer, rotation, and designator.  Pads and geometry are added later.
    """
    records = read_text_records(data)
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
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> tuple[list[PcbSegment], dict[int, list[PcbLine]]]:
    """Parse Tracks6/Data → board-level segments + per-component lines.

    Returns (segments, comp_lines) where comp_lines maps component index
    to a list of PcbLine objects for silkscreen/fab assignment.
    """
    records = _read_binary_records(data)
    segments: list[PcbSegment] = []
    comp_lines: dict[int, list[PcbLine]] = {}

    for rec_type, body in records:
        if rec_type != 4:
            continue
        track = TrackRecord.from_bytes(body, ctx)
        if track is None:
            continue

        layer = _layer_name(track.layer, layer_map)
        if not layer:
            continue

        x1 = _int_to_mm(track.start[0])
        y1 = -_int_to_mm(track.start[1])
        x2 = _int_to_mm(track.end[0])
        y2 = -_int_to_mm(track.end[1])
        width = _int_to_mm(track.width)

        if track.component == _COMPONENT_NONE:
            if track.layer in _COPPER_LAYERS:
                segments.append(
                    PcbSegment(
                        start_x=x1,
                        start_y=y1,
                        end_x=x2,
                        end_y=y2,
                        width=width,
                        layer=layer,
                        net_number=_net_number(track.net),
                    )
                )
        else:
            comp_lines.setdefault(track.component, []).append(
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


def _parse_vias(data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext) -> list[PcbVia]:
    """Parse Vias6/Data → list of PcbVia."""
    records = _read_binary_records(data)
    vias: list[PcbVia] = []

    for rec_type, body in records:
        if rec_type != 3:
            continue
        via = ViaRecord.from_bytes(body, ctx)
        if via is None:
            continue

        layers = [_layer_name(via.start_layer, layer_map), _layer_name(via.end_layer, layer_map)]
        layers = [ly for ly in layers if ly]
        if not layers:
            continue

        vias.append(
            PcbVia(
                x=_int_to_mm(via.position[0]),
                y=-_int_to_mm(via.position[1]),
                size=_int_to_mm(via.diameter),
                drill=_int_to_mm(via.hole_size),
                layers=layers,
                net_number=_net_number(via.net),
            )
        )

    return vias


def _parse_arcs(
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> tuple[list[PcbTraceArc], dict[int, list[PcbArc]]]:
    """Parse Arcs6/Data → board-level trace arcs + per-component arcs.

    Returns (trace_arcs, comp_arcs) where comp_arcs maps component index
    to a list of PcbArc objects.
    """
    records = _read_binary_records(data)
    trace_arcs: list[PcbTraceArc] = []
    comp_arcs: dict[int, list[PcbArc]] = {}

    for rec_type, body in records:
        if rec_type != 1:
            continue
        arc = ArcRecord.from_bytes(body, ctx)
        if arc is None:
            continue

        layer = _layer_name(arc.layer, layer_map)
        if not layer:
            continue

        cx = _int_to_mm(arc.center[0])
        cy_orig = _int_to_mm(arc.center[1])
        radius = _int_to_mm(arc.radius)
        width = _int_to_mm(arc.width)

        # Compute arc CCW in original Altium coords, then negate Y.
        sx, sy, mx, my, ex, ey = _arc_to_three_point(
            cx, cy_orig, radius, arc.start_angle, arc.end_angle
        )
        sy, my, ey = -sy, -my, -ey

        if arc.component == _COMPONENT_NONE and arc.layer in _COPPER_LAYERS:
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
                    net_number=_net_number(arc.net),
                )
            )
        elif arc.component != _COMPONENT_NONE:
            comp_arcs.setdefault(arc.component, []).append(
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
    data: bytes, nets: dict[int, PcbNet], layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[tuple[int, PcbPad]]:
    """Parse Pads6/Data → list of (component_index, PcbPad).

    Each pad record has 6 subrecords: name, skip, skip, skip, geometry,
    per-layer-overrides. PadRecord.from_bytes handles the subrecord chain.
    """
    pads: list[tuple[int, PcbPad]] = []
    pos = 0

    while pos < len(data):
        if data[pos] != 2:
            break
        # Find end of this pad record by parsing subrecord chain
        rec_data = data[pos:]
        pad = PadRecord.from_bytes(rec_data, ctx)

        # Advance past this record regardless of parse success.
        # Re-parse subrecord lengths to advance the position.
        pos += 1  # type byte
        for _ in range(4):  # sub1-sub4
            if pos + 4 > len(data):
                break
            sl = u32(data, pos)
            pos += 4 + sl
        for _ in range(2):  # sub5-sub6
            if pos + 4 > len(data):
                break
            sl = u32(data, pos)
            pos += 4 + sl

        if pad is None:
            continue

        # Determine shape string
        shape = _PAD_SHAPES.get(pad.shape, "rect")
        if pad.shape_alt == _PAD_SHAPE_ALT_ROUNDRECT:
            shape = "roundrect"

        # Determine layers (multi-layer pad = layer 74 = through-hole)
        layers = [_layer_name(pad.layer, layer_map)] if pad.layer in _COPPER_LAYERS else ["*.Cu"]

        net_num = _net_number(pad.net)
        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        pads.append(
            (
                pad.component,
                PcbPad(
                    number=pad.name,
                    x=_int_to_mm(pad.position[0]),
                    y=-_int_to_mm(pad.position[1]),
                    width=_int_to_mm(pad.top_size[0]),
                    height=_int_to_mm(pad.top_size[1]),
                    shape=shape,
                    layers=layers,
                    net_number=net_num,
                    net_name=net_name,
                    footprint_ref="",
                    drill=_int_to_mm(pad.hole_size),
                ),
            )
        )

    return pads


def _parse_texts(
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[tuple[int, PcbText]]:
    """Parse Texts6/Data → list of (component_index, PcbText).

    Each text record has 2 subrecords: binary properties + Pascal string.
    TextRecord.from_bytes handles both subrecords.
    """
    texts: list[tuple[int, PcbText]] = []
    pos = 0

    while pos < len(data):
        if data[pos] != 5:
            break

        rec_data = data[pos:]
        text_rec = TextRecord.from_bytes(rec_data, ctx)

        # Advance past this record by re-parsing subrecord lengths
        pos += 1  # type byte
        for _ in range(2):  # sub1, sub2
            if pos + 4 > len(data):
                break
            sl = u32(data, pos)
            pos += 4 + sl

        if text_rec is None:
            continue

        layer = _layer_name(text_rec.layer, layer_map)
        if not layer:
            continue

        kind = ""
        if text_rec.is_designator:
            kind = "reference"
        elif text_rec.is_comment:
            kind = "value"

        texts.append(
            (
                text_rec.component,
                PcbText(
                    text=text_rec.text,
                    x=_int_to_mm(text_rec.position[0]),
                    y=-_int_to_mm(text_rec.position[1]),
                    rotation=text_rec.rotation,
                    layer=layer,
                    font_size=_int_to_mm(text_rec.height),
                    kind=kind,
                ),
            )
        )

    return texts


def _parse_fills(
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[PcbPolygon]:
    """Parse Fills6/Data → list of PcbPolygon (rectangular copper fills)."""
    records = _read_binary_records(data)
    fills: list[PcbPolygon] = []

    for rec_type, body in records:
        if rec_type != 6:
            continue
        fill = FillRecord.from_bytes(body, ctx)
        if fill is None or fill.layer not in _COPPER_LAYERS:
            continue

        x1 = _int_to_mm(fill.pos1[0])
        y1 = -_int_to_mm(fill.pos1[1])
        x2 = _int_to_mm(fill.pos2[0])
        y2 = -_int_to_mm(fill.pos2[1])

        # Build 4-corner rectangle, apply rotation around center
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        hw, hh = (x2 - x1) / 2, (y2 - y1) / 2
        corners: list[tuple[float, float]] = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]

        if fill.rotation != 0:
            rad = math.radians(fill.rotation)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            corners = [(dx * cos_r - dy * sin_r, dx * sin_r + dy * cos_r) for dx, dy in corners]

        points = [(cx + dx, cy + dy) for dx, dy in corners]

        fills.append(
            PcbPolygon(
                points=points,
                layer=_layer_name(fill.layer, layer_map),
                net_number=_net_number(fill.net),
            )
        )

    return fills


def _parse_polygon_pours(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
) -> tuple[list[PcbZone], dict[int, int]]:
    """Parse Polygons6/Data → zone definitions and pour-to-net mapping.

    Returns (zones, pour_net_map) where pour_net_map maps pourindex → net_number
    in the pcb.nets 1-based numbering. This mapping is needed by _parse_regions
    to inherit net assignments for filled copper regions.
    """
    records = read_text_records(data)
    zones: list[PcbZone] = []
    pour_net_map: dict[int, int] = {}

    for rec in records:
        pourindex = int(rec.get("pourindex", "-1") or "-1")

        # Resolve net: text records store 0-based Nets6 index,
        # apply _net_number() to convert to 1-based pcb.nets key
        net_raw = int(rec.get("net", str(_NET_UNCONNECTED)) or str(_NET_UNCONNECTED))
        net_num = _net_number(net_raw)
        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        # Store pour → net mapping for Regions6 inheritance
        if pourindex >= 0:
            pour_net_map[pourindex] = net_num

        # Resolve layer from V7 layer name
        layer_id = rec.get("layer", "").upper()
        layer_num = _V7_NAME_TO_NUM.get(layer_id)
        if layer_num is None:
            continue
        layer = _layer_name(layer_num, layer_map)
        if not layer:
            continue

        # Extract boundary vertices (vx0..vxN, vy0..vyN in mils)
        boundary: list[tuple[float, float]] = []
        i = 0
        while True:
            vx_key = f"vx{i}"
            vy_key = f"vy{i}"
            if vx_key not in rec or vy_key not in rec:
                break
            x_mm = _parse_mil(rec[vx_key])
            y_mm = -_parse_mil(rec[vy_key])  # Altium Y is inverted
            boundary.append((x_mm, y_mm))
            i += 1

        if len(boundary) < 3:
            continue

        # Fill type from hatchstyle
        hatchstyle = rec.get("hatchstyle", "").lower()
        fill_type = "solid" if hatchstyle == "solid" else "hatch" if hatchstyle else ""

        # Track width (min thickness within pour)
        trackwidth_str = rec.get("trackwidth", "")
        min_thickness = _parse_mil(trackwidth_str) if trackwidth_str else 0.0

        zones.append(
            PcbZone(
                net_number=net_num,
                net_name=net_name,
                layer=layer,
                boundary=boundary,
                priority=pourindex,
                min_thickness_mm=min_thickness,
                fill_type=fill_type,
            )
        )

    return zones, pour_net_map


def _parse_regions(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_net_map: dict[int, int] | None = None,
) -> list[PcbPolygon]:
    """Parse Regions6/Data → list of PcbPolygon.

    Region records contain a property string followed by vertex data
    (pairs of float64 in Altium internal units).  All layers are included —
    copper regions carry net info, non-copper regions (silkscreen fills,
    paste openings, etc.) have net_number 0.

    When pour_net_map is provided, regions with net=0xFFFF (inherit) and
    a valid subpolyindex will inherit the net from their parent polygon pour.
    """
    records = _read_binary_records(data)
    polygons: list[PcbPolygon] = []

    for rec_type, body in records:
        if rec_type != 11:
            continue
        region = RegionRecord.from_bytes(body, ctx)
        if region is None:
            continue

        # Determine layer from V7 property or fallback to byte
        v7_layer = region.properties.get("v7_layer", "").upper()
        resolved_num = (
            _V7_NAME_TO_NUM[v7_layer] if v7_layer and v7_layer in _V7_NAME_TO_NUM else region.layer
        )

        layer = _layer_name(resolved_num, layer_map)
        if not layer:
            continue

        points = [(_int_to_mm(int(vx)), -_int_to_mm(int(vy))) for vx, vy in region.vertices]
        if len(points) < 3:
            continue

        # Convert hole vertices
        holes: list[list[tuple[float, float]]] = []
        for hole_verts in region.holes:
            h_pts = [(_int_to_mm(int(vx)), -_int_to_mm(int(vy))) for vx, vy in hole_verts]
            if len(h_pts) >= 3:
                holes.append(h_pts)

        # Net resolution: use direct net if assigned, otherwise inherit from pour
        if resolved_num in _COPPER_LAYERS:
            if region.net == _NET_UNCONNECTED and pour_net_map:
                # Inherit from parent polygon pour via subpolyindex
                subpoly = int(region.properties.get("subpolyindex", "-1") or "-1")
                net_num = pour_net_map.get(subpoly, 0)
            else:
                net_num = _net_number(region.net)
        else:
            net_num = 0

        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        polygons.append(
            PcbPolygon(
                points=points,
                layer=layer,
                net_number=net_num,
                net_name=net_name,
                holes=holes,
            )
        )

    return polygons


def _parse_shape_based_regions(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
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
        if rec_type != 11:
            continue
        region = ShapeBasedRegionRecord.from_bytes(body, ctx)
        if region is None:
            continue

        # Determine layer from V7 property or fallback to byte
        v7_layer = region.properties.get("v7_layer", "").upper()
        resolved_num = (
            _V7_NAME_TO_NUM[v7_layer] if v7_layer and v7_layer in _V7_NAME_TO_NUM else region.layer
        )

        layer = _layer_name(resolved_num, layer_map)
        if not layer:
            continue

        # Linearize arc edges, then convert to mm with Y negated
        raw_pts = linearize_arc_vertices(region.vertices)
        points: list[tuple[float, float]] = [(_int_to_mm(x), -_int_to_mm(y)) for x, y in raw_pts]
        if len(points) < 3:
            continue

        # Convert hole vertices (stored as f64 in internal units)
        holes: list[list[tuple[float, float]]] = []
        for hole_verts in region.holes:
            h_pts = [(_int_to_mm(int(vx)), -_int_to_mm(int(vy))) for vx, vy in hole_verts]
            if len(h_pts) >= 3:
                holes.append(h_pts)

        net_num = _net_number(region.net) if resolved_num in _COPPER_LAYERS else 0
        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        poly = PcbPolygon(
            points=points,
            layer=layer,
            net_number=net_num,
            net_name=net_name,
            holes=holes,
        )

        if region.component == _COMPONENT_NONE:
            board_polygons.append(poly)
        else:
            comp_polygons.setdefault(region.component, []).append(poly)

    return board_polygons, comp_polygons


def _parse_board_outline(
    tracks_data: bytes,
    arcs_data: bytes,
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
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
            if rec_type != 4:
                continue
            track = TrackRecord.from_bytes(body, ctx)
            if track is None or track.layer != target_layer:
                continue
            if track.component != _COMPONENT_NONE:
                continue

            outline_lines.append(
                PcbLine(
                    start_x=_int_to_mm(track.start[0]),
                    start_y=-_int_to_mm(track.start[1]),
                    end_x=_int_to_mm(track.end[0]),
                    end_y=-_int_to_mm(track.end[1]),
                    layer=edge_name,
                    width=_int_to_mm(track.width),
                )
            )

        for rec_type, body in _read_binary_records(arcs_data):
            if rec_type != 1:
                continue
            arc = ArcRecord.from_bytes(body, ctx)
            if arc is None or arc.layer != target_layer:
                continue
            if arc.component != _COMPONENT_NONE:
                continue

            cx = _int_to_mm(arc.center[0])
            cy_orig = _int_to_mm(arc.center[1])
            radius = _int_to_mm(arc.radius)
            width = _int_to_mm(arc.width)

            # Compute arc CCW in original Altium coords, then negate Y.
            sx, sy, mx, my, ex, ey = _arc_to_three_point(
                cx, cy_orig, radius, arc.start_angle, arc.end_angle
            )
            sy, my, ey = -sy, -my, -ey
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
    records = read_text_records(data)
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
# Project-level data: rules, classes, diff pairs, stackup
# ---------------------------------------------------------------------------


def _read_rules6_records(data: bytes) -> list[dict[str, str]]:
    """Read Rules6 stream records (2-byte header + 4-byte LE length framing)."""
    records: list[dict[str, str]] = []
    pos = 0
    while pos + 6 <= len(data):
        # 2-byte header (type + padding) + 4-byte LE length
        length = u32(data, pos + 2)
        pos += 6
        if length == 0 or pos + length > len(data):
            break
        payload = data[pos : pos + length]
        pos += length
        props = parse_record_payload(payload)
        if props:
            records.append(props)
    return records


def parse_altium_rules(data: bytes) -> list[DesignRule]:
    """Parse Altium Rules6 stream into DesignRule objects."""
    records = _read_rules6_records(data)
    rules: list[DesignRule] = []
    for props in records:
        name = props.get("name", "")
        kind = props.get("rulekind", "")
        enabled = props.get("enabled", "TRUE").upper() == "TRUE"
        priority = int(props.get("priority", "0") or "0")
        scope1 = props.get("scope1expression", "")
        scope2 = props.get("scope2expression", "")

        # Extract numeric values (may be in mils, convert to mm).
        # Different rule kinds use different property names for their values.
        min_val = _rule_value_mm(
            props,
            "minlimit",
            "gap",
            "genericclearance",
            "clearance",
            "minimumring",
            "minsoldermaskwidth",
            "minsilkscreentomaskgap",
            "minwidth",
            "minholewidth",
            "minheight",
            "minsize",
        )
        max_val = _rule_value_mm(
            props,
            "maxlimit",
            "maxwidth",
            "maxholewidth",
            "maxheight",
            "maxsize",
            "maxuncoupledlength",
            "tolerance",
            "limit",
        )
        pref_val = _rule_value_mm(
            props,
            "preferedwidth",
            "preferredwidth",
            "expansion",
            "prefheight",
            "preferedsize",
            "toplayer_prefwidth",
        )

        # Collect remaining properties
        skip_keys = {
            "name",
            "rulekind",
            "enabled",
            "priority",
            "scope1expression",
            "scope2expression",
            "selection",
            "layer",
            "locked",
            "polygonoutline",
            "userrouted",
            "keepout",
            "unionindex",
            "netscope",
            "layerkind",
            "superclass",
        }
        extra: dict[str, str] = {}
        for k, v in props.items():
            if k not in skip_keys and v:
                extra[k] = v

        rules.append(
            DesignRule(
                name=name,
                kind=kind,
                enabled=enabled,
                priority=priority,
                scope1=scope1,
                scope2=scope2,
                min_value_mm=min_val,
                max_value_mm=max_val,
                preferred_value_mm=pref_val,
                properties=extra,
            )
        )
    return rules


def _rule_value_mm(props: dict[str, str], *keys: str) -> float | None:
    """Extract a rule value in mm from property keys (values stored in mils).

    Values may have a "mil" suffix that must be stripped before conversion.
    """
    for key in keys:
        val_str = props.get(key, "")
        if val_str:
            try:
                return float(_strip_mil(val_str)) * _MIL_TO_MM
            except ValueError:
                continue
    return None


def parse_altium_classes(data: bytes) -> list[NetClass]:
    """Parse Altium Classes6 stream into NetClass objects."""
    records = read_text_records(data)
    classes: list[NetClass] = []
    for props in records:
        name = props.get("name", "")
        kind = int(props.get("kind", "0") or "0")
        # Extract members (M0, M1, M2, ...)
        members: list[str] = []
        i = 0
        while True:
            key = f"m{i}"
            if key in props:
                members.append(props[key])
                i += 1
            else:
                break
        classes.append(NetClass(name=name, kind=kind, members=members))
    return classes


def parse_altium_diff_pairs(data: bytes) -> list[DiffPair]:
    """Parse Altium DifferentialPairs6 stream into DiffPair objects."""
    records = read_text_records(data)
    pairs: list[DiffPair] = []
    for props in records:
        name = props.get("name", "")
        pos_net = props.get("positivenetname", "")
        neg_net = props.get("negativenetname", "")
        if name and pos_net and neg_net:
            pairs.append(DiffPair(name=name, positive_net=pos_net, negative_net=neg_net))
    return pairs


def parse_altium_stackup(board_props: dict[str, str]) -> Stackup | None:
    """Extract PCB stackup from Board6 properties.

    Prefers the v9 stackup format (v9_stack_layerN_*) which stores explicit
    layer names, correct physical ordering, and separate core/prepreg entries.
    Falls back to the legacy format (layerN + next-pointer chain) for older files.
    """
    stackup = _parse_v9_stackup(board_props)
    if stackup:
        return stackup
    return _parse_legacy_stackup(board_props)


def _parse_v9_stackup(board_props: dict[str, str]) -> Stackup | None:
    """Parse the v9 stackup format (Altium Designer 19+).

    v9 layers are stored as v9_stack_layer{N}_* in physical order from top
    to bottom. Includes solder mask, copper, prepreg, and core layers with
    explicit user-assigned names.
    """
    # Discover which v9 layer indices exist
    layer_indices: list[int] = []
    for key in board_props:
        if key.startswith("v9_stack_layer") and key.endswith("_name"):
            try:
                idx = int(key[len("v9_stack_layer") : -len("_name")])
                layer_indices.append(idx)
            except ValueError:
                continue

    if not layer_indices:
        return None

    layer_indices.sort()

    layers: list[StackupLayer] = []
    # Track whether we've seen the first and last copper to determine sides
    copper_indices: list[int] = []
    for idx in layer_indices:
        copthick = board_props.get(f"v9_stack_layer{idx}_copthick", "")
        if copthick:
            copper_indices.append(idx)

    first_copper = copper_indices[0] if copper_indices else -1
    last_copper = copper_indices[-1] if copper_indices else -1

    for idx in layer_indices:
        prefix = f"v9_stack_layer{idx}_"
        name = board_props.get(f"{prefix}name", "")
        if not name:
            continue

        copthick_str = _strip_mil(board_props.get(f"{prefix}copthick", ""))
        diel_type_raw = board_props.get(f"{prefix}dieltype", "")
        diel_height_str = _strip_mil(board_props.get(f"{prefix}dielheight", ""))
        diel_const_str = board_props.get(f"{prefix}dielconst", "")
        diel_material = board_props.get(f"{prefix}dielmaterial", "").strip()
        diel_loss_str = board_props.get(f"{prefix}diellosstangent", "")
        copper_orient = board_props.get(f"{prefix}copperorientation", "")

        if copthick_str:
            # Copper layer
            cop_thick_mm = float(copthick_str) * _MIL_TO_MM

            side = ""
            if idx == first_copper:
                side = "front"
            elif idx == last_copper:
                side = "back"

            orientation = ""
            if copper_orient == "1":
                orientation = "reversed"
            elif copper_orient == "0" or (copper_orient == "" and copthick_str):
                orientation = "normal"

            layers.append(
                StackupLayer(
                    name=name,
                    layer_type="copper",
                    thickness_mm=cop_thick_mm,
                    side=side,
                    copper_orientation=orientation,
                )
            )
        elif diel_height_str:
            # Dielectric layer (prepreg, core, or solder mask)
            thickness_mm = float(diel_height_str) * _MIL_TO_MM
            epsilon_r = float(diel_const_str) if diel_const_str else 0.0
            loss_tangent = float(diel_loss_str) if diel_loss_str else 0.0

            # dieltype: 0=unspecified, 1=core, 2=prepreg, 3=solder_mask
            diel_type_map = {"1": "core", "2": "prepreg", "3": "solder_mask"}
            layer_type = diel_type_map.get(diel_type_raw, "prepreg")

            layers.append(
                StackupLayer(
                    name=name,
                    layer_type=layer_type,
                    thickness_mm=thickness_mm,
                    material=diel_material,
                    epsilon_r=epsilon_r,
                    loss_tangent=loss_tangent,
                )
            )
        # Skip non-physical layers (paste, overlay) that have neither
        # copper thickness nor dielectric height

    if not layers:
        return None

    total = sum(ly.thickness_mm for ly in layers)
    return Stackup(layers=layers, total_thickness_mm=total)


def _parse_legacy_stackup(board_props: dict[str, str]) -> Stackup | None:
    """Parse the legacy layerN + next-pointer stackup format.

    Used by older Altium files that lack v9_stack_layer data. Follows the
    layer{N}next chain starting at layer 1. Dielectrics are numbered
    sequentially by traversal position.
    """
    layers: list[StackupLayer] = []

    # Follow the next-pointer chain starting at layer 1
    i = 1
    visited: set[int] = set()
    diel_counter = 0
    while i > 0 and i not in visited:
        visited.add(i)
        prefix = f"layer{i}"
        name = board_props.get(f"{prefix}name", "")
        if not name:
            break

        # Copper thickness (value may have "mil" suffix)
        cop_thick_str = _strip_mil(board_props.get(f"{prefix}copthick", ""))
        cop_thick_mm = float(cop_thick_str) * _MIL_TO_MM if cop_thick_str else 0.0

        # Dielectric properties
        diel_type_raw = board_props.get(f"{prefix}dieltype", "")
        diel_const_str = board_props.get(f"{prefix}dielconst", "")
        diel_height_str = _strip_mil(board_props.get(f"{prefix}dielheight", ""))
        diel_material = board_props.get(f"{prefix}dielmaterial", "").strip()
        diel_loss_str = board_props.get(f"{prefix}diellosstangent", "")

        epsilon_r = float(diel_const_str) if diel_const_str else 0.0
        diel_height_mm = float(diel_height_str) * _MIL_TO_MM if diel_height_str else 0.0
        loss_tangent = float(diel_loss_str) if diel_loss_str else 0.0

        # Dielectric type mapping
        diel_type_map = {"0": "prepreg", "1": "core", "2": "prepreg"}
        diel_type = diel_type_map.get(diel_type_raw, "prepreg")

        # Determine side
        side = ""
        name_lower = name.lower()
        if "top" in name_lower:
            side = "front"
        elif "bottom" in name_lower or "bot" in name_lower:
            side = "back"

        # Add copper layer
        layers.append(
            StackupLayer(
                name=name,
                layer_type="copper",
                thickness_mm=cop_thick_mm,
                side=side,
            )
        )

        # Follow next pointer
        next_str = board_props.get(f"{prefix}next", "0")
        next_layer = int(next_str) if next_str else 0

        # Add dielectric layer between this copper and the next (skip after last)
        if diel_height_mm > 0 and next_layer > 0:
            diel_counter += 1
            layers.append(
                StackupLayer(
                    name=f"Dielectric {diel_counter}",
                    layer_type=diel_type,
                    thickness_mm=diel_height_mm,
                    material=diel_material,
                    epsilon_r=epsilon_r,
                    loss_tangent=loss_tangent,
                )
            )

        i = next_layer

    if not layers:
        return None

    total = sum(ly.thickness_mm for ly in layers)
    return Stackup(layers=layers, total_thickness_mm=total)


def _strip_mil(s: str) -> str:
    """Strip 'mil' suffix from an Altium dimension string."""
    s = s.strip()
    if s.lower().endswith("mil"):
        return s[:-3]
    return s


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def _read_stream(ole: olefile.OleFileIO, name: str) -> bytes:
    """Read a stream from the OLE container, returning empty bytes if absent."""
    if ole.exists(name):
        return ole.openstream(name).read()
    return b""


def parse_altium_pcb(
    path: Path,
    ctx: ParseContext | None = None,
) -> Pcb:
    """Parse an Altium .PcbDoc file into the PCB domain model."""
    if ctx is None:
        ctx = ParseContext()
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
        polygons6_data = _read_stream(ole, "Polygons6/Data")
        sb_regions_data = _read_stream(ole, "ShapeBasedRegions6/Data")
        comp_bodies_data = _read_stream(ole, "ComponentBodies6/Data")
        board_data = _read_stream(ole, "Board6/Data")
    finally:
        ole.close()

    # Build layer map from Board6 metadata + static defaults
    board_props: dict[str, str] = {}
    if board_data:
        board_records = read_text_records(board_data)
        if board_records:
            board_props = board_records[0]
    layer_map = _build_layer_map(board_props)

    # Parse text streams
    nets = _parse_nets(nets_data)
    footprints = _parse_components(comp_data, layer_map)

    # Parse binary streams
    segments, comp_lines = _parse_tracks(tracks_data, layer_map, ctx)
    vias = _parse_vias(vias_data, layer_map, ctx)
    trace_arcs, comp_arcs = _parse_arcs(arcs_data, layer_map, ctx)
    raw_pads = _parse_pads(pads_data, nets, layer_map, ctx)
    raw_texts = _parse_texts(texts_data, layer_map, ctx)
    fills = _parse_fills(fills_data, layer_map, ctx)
    zones, pour_net_map = _parse_polygon_pours(polygons6_data, nets, layer_map)
    regions = _parse_regions(regions_data, nets, layer_map, ctx, pour_net_map)
    sb_board_polys, sb_comp_polys = _parse_shape_based_regions(
        sb_regions_data, nets, layer_map, ctx
    )
    comp_models = _parse_component_bodies(comp_bodies_data)

    # Board outline
    outline_lines, outline_arcs = _parse_board_outline(tracks_data, arcs_data, layer_map, ctx)

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
            text.footprint_ref = footprints[comp_idx].reference
            footprints[comp_idx].texts.append(text)

    for comp_idx, lines in comp_lines.items():
        if comp_idx < len(footprints):
            fp = footprints[comp_idx]
            for line in lines:
                line.footprint_ref = fp.reference
                if line.layer in silk_names:
                    fp.silkscreen_lines.append(line)
                elif line.layer in fab_names:
                    fp.fab_lines.append(line)

    for comp_idx, arcs in comp_arcs.items():
        if comp_idx < len(footprints):
            fp = footprints[comp_idx]
            for arc in arcs:
                arc.footprint_ref = fp.reference
                if arc.layer in fab_names:
                    fp.fab_arcs.append(arc)

    for comp_idx, polys in sb_comp_polys.items():
        if comp_idx < len(footprints):
            fp = footprints[comp_idx]
            for poly in polys:
                poly.footprint_ref = fp.reference
                if poly.layer in silk_names:
                    fp.silkscreen_polygons.append(poly)
                elif poly.layer in fab_names:
                    fp.fab_polygons.append(poly)

    for comp_idx, models in comp_models.items():
        if comp_idx < len(footprints):
            footprints[comp_idx].models_3d.extend(models)

    # Extract value text and compute bounding boxes
    for fp in footprints:
        if not fp.value:
            fp.value = next((t.text for t in fp.texts if t.kind == "value"), "")
        fp.bbox = _compute_bbox(fp)

    # Board name from Board6/Data (board_props already parsed above)
    board_name = board_props.get("filename", "")
    if "\\" in board_name:
        board_name = board_name.rsplit("\\", 1)[-1]
    if board_name.endswith(".$$$"):
        board_name = board_name[:-4]

    polygons = fills + regions + sb_board_polys

    return Pcb(
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
        zones=zones,
    )
