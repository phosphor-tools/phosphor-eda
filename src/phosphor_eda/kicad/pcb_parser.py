"""Parse a KiCad .kicad_pcb file into the PCB domain model.

Uses sexpdata and the same helper pattern as to_schematic.py.
Handles both KiCad 6 (fp_text reference) and KiCad 8 (property
"Reference") formats.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import sexpdata

from phosphor_eda.kicad import sexp
from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
    PcbArc,
    PcbCircle,
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

    from phosphor_eda.kicad.sexp import SExpNode


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------


def _xy(item: SExpNode) -> tuple[float, float]:
    """Extract (x, y) from an S-expression like (start 1.0 2.0)."""
    return (sexp.num(item, 1), sexp.num(item, 2))


def _float_val(item: SExpNode) -> float:
    """Extract a single float from item[1]."""
    return sexp.num(item, 1)


def _at(item: SExpNode) -> tuple[float, float, float]:
    """Extract (x, y, rotation) from (at X Y [ROT]).

    The rotation field may be absent, or followed by keywords like
    ``unlocked`` which must be skipped.
    """
    x = sexp.num(item, 1)
    y = sexp.num(item, 2)
    rot = 0.0
    if len(item) > 3:
        v = item[3]
        if isinstance(v, (int, float)):
            rot = float(v)
    return (x, y, rot)


def _transform_point(
    local_x: float, local_y: float, fp_x: float, fp_y: float, fp_rot_deg: float
) -> tuple[float, float]:
    """Transform footprint-local coords to absolute board coords."""
    rad = math.radians(-fp_rot_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    abs_x = fp_x + local_x * cos_r - local_y * sin_r
    abs_y = fp_y + local_x * sin_r + local_y * cos_r
    return (abs_x, abs_y)


def _layers(item: SExpNode) -> list[str]:
    """Extract layer names from (layers "F.Cu" "B.Cu" ...)."""
    result: list[str] = []
    for v in item[1:]:
        if isinstance(v, str):
            result.append(v)
        elif isinstance(v, sexpdata.Symbol):
            result.append(v.value())
    return result


# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------

# KiCad name-pattern → function mapping.
_KICAD_FUNCTION_RULES: list[tuple[str, LayerFunction]] = [
    (".Cu", LayerFunction.COPPER),
    ("SilkS", LayerFunction.SILKSCREEN),
    ("Silkscreen", LayerFunction.SILKSCREEN),
    ("Mask", LayerFunction.SOLDER_MASK),
    ("Paste", LayerFunction.SOLDER_PASTE),
    ("Fab", LayerFunction.FAB),
    ("CrtYd", LayerFunction.COURTYARD),
    ("Courtyard", LayerFunction.COURTYARD),
]


def _infer_kicad_function(name: str) -> LayerFunction:
    """Infer layer function from a KiCad layer name."""
    if name == "Edge.Cuts":
        return LayerFunction.EDGE
    for pattern, fn in _KICAD_FUNCTION_RULES:
        if pattern in name:
            return fn
    return LayerFunction.OTHER


def _infer_kicad_side(name: str) -> str:
    """Infer front/back side from a KiCad layer name prefix."""
    if name.startswith("F."):
        return "front"
    if name.startswith("B."):
        return "back"
    return ""


def _parse_layer_defs(sexpr: SExpNode) -> list[PcbLayer]:
    """Parse the board-level ``(layers ...)`` section into PcbLayer objects."""
    layers_section = sexp.find(sexpr, "layers")
    if not layers_section:
        return []
    result: list[PcbLayer] = []
    for item in layers_section[1:]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        raw_num = item[0]
        num = int(raw_num) if isinstance(raw_num, (int, float)) else 0
        raw_name = item[1]
        name = raw_name.value() if isinstance(raw_name, sexpdata.Symbol) else str(raw_name)
        fn = _infer_kicad_function(name)
        side = _infer_kicad_side(name)
        result.append(PcbLayer(name=name, function=fn, side=side, number=num))
    return result


# ---------------------------------------------------------------------------
# Net parsing
# ---------------------------------------------------------------------------


def _parse_nets(sexpr: SExpNode) -> dict[int, PcbNet]:
    """Parse top-level (net N "name") entries."""
    nets: dict[int, PcbNet] = {}
    for item in sexp.find_all(sexpr, "net"):
        if len(item) >= 3:
            num = int(sexp.num(item, 1))
            name = str(item[2])
            nets[num] = PcbNet(number=num, name=name)
    return nets


# ---------------------------------------------------------------------------
# Footprint / pad parsing
# ---------------------------------------------------------------------------


def _extract_reference(fp_sexpr: SExpNode) -> str:
    """Get reference designator, handling both KiCad 6 and 8 formats."""
    # KiCad 8: (property "Reference" "R1" ...)
    ref = sexp.find_property(fp_sexpr, "Reference")
    if ref:
        return ref
    # KiCad 6: (fp_text reference "R1" ...)
    for item in fp_sexpr:
        if isinstance(item, list) and sexp.tag(item) == "fp_text" and len(item) > 2:
            v = item[1]
            if isinstance(v, sexpdata.Symbol) and v.value() == "reference":
                return str(item[2])
    return "?"


def _extract_value(fp_sexpr: SExpNode) -> str:
    """Get component value, handling both KiCad 6 and 8 formats."""
    # KiCad 8: (property "Value" "100nF" ...)
    val = sexp.find_property(fp_sexpr, "Value")
    if val:
        return val
    # KiCad 6: (fp_text value "100nF" ...)
    for item in fp_sexpr:
        if isinstance(item, list) and sexp.tag(item) == "fp_text" and len(item) > 2:
            v = item[1]
            if isinstance(v, sexpdata.Symbol) and v.value() == "value":
                return str(item[2])
    return ""


def _parse_pad(
    pad_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    fp_ref: str,
) -> PcbPad:
    """Parse a (pad ...) S-expression into a PcbPad with absolute coords."""
    number = str(pad_sexpr[1])
    # pad_sexpr[2] = type (smd/thru_hole), pad_sexpr[3] = shape
    shape_sym = pad_sexpr[3]
    shape = shape_sym.value() if isinstance(shape_sym, sexpdata.Symbol) else str(shape_sym)

    at_node = sexp.find(pad_sexpr, "at")
    local_x, local_y, _pad_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

    size_node = sexp.find(pad_sexpr, "size")
    width = sexp.num(size_node, 1) if size_node else 0.0
    height = sexp.num(size_node, 2) if size_node and len(size_node) > 2 else width

    layers_node = sexp.find(pad_sexpr, "layers")
    pad_layers = _layers(layers_node) if layers_node else []

    net_node = sexp.find(pad_sexpr, "net")
    net_num = int(sexp.num(net_node, 1)) if net_node and len(net_node) > 1 else 0
    net_name = str(net_node[2]) if net_node and len(net_node) > 2 else ""

    drill_node = sexp.find(pad_sexpr, "drill")
    drill = 0.0
    if drill_node and len(drill_node) > 1:
        # (drill 3.2) or (drill oval 0.6 1.2) — take first numeric value
        for v in drill_node[1:]:
            if isinstance(v, (int, float)):
                drill = float(v)
                break

    abs_x, abs_y = _transform_point(local_x, local_y, fp_x, fp_y, fp_rot)

    return PcbPad(
        number=number,
        x=abs_x,
        y=abs_y,
        width=width,
        height=height,
        shape=shape,
        layers=pad_layers,
        net_number=net_num,
        net_name=net_name,
        footprint_ref=fp_ref,
        drill=drill,
    )


def _parse_fp_lines(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    fp_ref: str = "",
) -> list[PcbLine]:
    """Parse fp_line elements matching layer_filter, transform to absolute."""
    lines: list[PcbLine] = []
    for item in sexp.find_all(fp_sexpr, "fp_line"):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        start_node = sexp.find(item, "start")
        end_node = sexp.find(item, "end")
        if not start_node or not end_node:
            continue
        sx, sy = _xy(start_node)
        ex, ey = _xy(end_node)
        abs_s = _transform_point(sx, sy, fp_x, fp_y, fp_rot)
        abs_e = _transform_point(ex, ey, fp_x, fp_y, fp_rot)
        width_node = sexp.find(item, "width")
        stroke_node = sexp.find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = sexp.find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        lines.append(
            PcbLine(abs_s[0], abs_s[1], abs_e[0], abs_e[1], layer, w, footprint_ref=fp_ref)
        )
    return lines


def _parse_fp_circles(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    fp_ref: str = "",
) -> list[PcbCircle]:
    """Parse fp_circle elements matching layer_filter, transform to absolute."""
    circles: list[PcbCircle] = []
    for item in sexp.find_all(fp_sexpr, "fp_circle"):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        center_node = sexp.find(item, "center")
        end_node = sexp.find(item, "end")
        if not center_node or not end_node:
            continue
        cx, cy = _xy(center_node)
        ex, ey = _xy(end_node)
        radius = math.hypot(ex - cx, ey - cy)
        abs_c = _transform_point(cx, cy, fp_x, fp_y, fp_rot)
        width_node = sexp.find(item, "width")
        stroke_node = sexp.find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = sexp.find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        fill_node = sexp.find(item, "fill")
        filled = fill_node is not None and sexp.val(fill_node) == "solid"
        circles.append(
            PcbCircle(abs_c[0], abs_c[1], radius, layer, w, filled, footprint_ref=fp_ref)
        )
    return circles


def _parse_fp_rects_as_lines(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    fp_ref: str = "",
) -> list[PcbLine]:
    """Parse fp_rect elements as four PcbLine segments."""
    lines: list[PcbLine] = []
    for item in sexp.find_all(fp_sexpr, "fp_rect"):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        start_node = sexp.find(item, "start")
        end_node = sexp.find(item, "end")
        if not start_node or not end_node:
            continue
        sx, sy = _xy(start_node)
        ex, ey = _xy(end_node)
        width_node = sexp.find(item, "width")
        stroke_node = sexp.find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = sexp.find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        # Four corners
        corners = [(sx, sy), (ex, sy), (ex, ey), (sx, ey)]
        abs_corners = [_transform_point(cx, cy, fp_x, fp_y, fp_rot) for cx, cy in corners]
        for i in range(4):
            j = (i + 1) % 4
            lines.append(
                PcbLine(
                    abs_corners[i][0],
                    abs_corners[i][1],
                    abs_corners[j][0],
                    abs_corners[j][1],
                    layer,
                    w,
                    footprint_ref=fp_ref,
                )
            )
    return lines


def _parse_fp_arcs(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    fp_ref: str = "",
) -> list[PcbArc]:
    """Parse fp_arc elements matching layer_filter, transform to absolute."""
    arcs: list[PcbArc] = []
    for item in sexp.find_all(fp_sexpr, "fp_arc"):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        start_node = sexp.find(item, "start")
        mid_node = sexp.find(item, "mid")
        end_node = sexp.find(item, "end")
        if not start_node or not mid_node or not end_node:
            continue
        sx, sy = _xy(start_node)
        mx, my = _xy(mid_node)
        ex, ey = _xy(end_node)
        abs_s = _transform_point(sx, sy, fp_x, fp_y, fp_rot)
        abs_m = _transform_point(mx, my, fp_x, fp_y, fp_rot)
        abs_e = _transform_point(ex, ey, fp_x, fp_y, fp_rot)
        width_node = sexp.find(item, "width")
        stroke_node = sexp.find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = sexp.find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        arcs.append(
            PcbArc(
                abs_s[0],
                abs_s[1],
                abs_m[0],
                abs_m[1],
                abs_e[0],
                abs_e[1],
                layer,
                w,
                footprint_ref=fp_ref,
            )
        )
    return arcs


_SILK_LAYERS = {"F.SilkS", "B.SilkS", "F.Silkscreen", "B.Silkscreen"}
_COURTYARD_LAYERS = {"F.CrtYd", "B.CrtYd"}
_FAB_LAYERS = {"F.Fab", "B.Fab"}
_EDGE_LAYERS = {"Edge.Cuts"}


def _compute_bbox(
    pads: list[PcbPad], courtyard_lines: list[PcbLine]
) -> tuple[float, float, float, float] | None:
    """Compute bounding box from courtyard lines, or pad extents + margin."""
    xs: list[float] = []
    ys: list[float] = []
    if courtyard_lines:
        for ln in courtyard_lines:
            xs.extend([ln.start_x, ln.end_x])
            ys.extend([ln.start_y, ln.end_y])
    elif pads:
        margin = 0.5
        for p in pads:
            xs.extend([p.x - p.width / 2 - margin, p.x + p.width / 2 + margin])
            ys.extend([p.y - p.height / 2 - margin, p.y + p.height / 2 + margin])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _parse_fp_texts(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    fp_ref: str,
) -> list[PcbText]:
    """Parse fp_text elements into PcbText with absolute coords."""
    texts: list[PcbText] = []
    for item in sexp.find_all(fp_sexpr, "fp_text"):
        if len(item) < 3:
            continue
        kind_sym = item[1]
        kind = kind_sym.value() if isinstance(kind_sym, sexpdata.Symbol) else str(kind_sym)
        raw_text = str(item[2])

        # Resolve ${REFERENCE} placeholder
        if "${REFERENCE}" in raw_text:
            raw_text = raw_text.replace("${REFERENCE}", fp_ref)

        # Check hidden flag
        hidden = any(isinstance(x, sexpdata.Symbol) and x.value() == "hide" for x in item)

        layer_node = sexp.find(item, "layer")
        layer = sexp.val(layer_node) if layer_node else ""

        at_node = sexp.find(item, "at")
        local_x, local_y, text_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

        # Font size
        effects = sexp.find(item, "effects")
        font = sexp.find(effects, "font") if effects else None
        size_node = sexp.find(font, "size") if font else None
        font_size = sexp.num(size_node, 1) if size_node else 1.0

        abs_x, abs_y = _transform_point(local_x, local_y, fp_x, fp_y, fp_rot)
        abs_rot = fp_rot + text_rot

        texts.append(
            PcbText(
                text=raw_text,
                x=abs_x,
                y=abs_y,
                rotation=abs_rot,
                layer=layer,
                font_size=font_size,
                kind=kind,
                hidden=hidden,
                footprint_ref=fp_ref,
            )
        )
    return texts


def _parse_fp_models(fp_sexpr: SExpNode) -> list[PcbModel3D]:
    """Parse all (model ...) entries from a footprint s-expression."""
    models: list[PcbModel3D] = []
    for node in sexp.find_all(fp_sexpr, "model"):
        if len(node) < 2:
            continue
        raw_path = node[1]
        source = raw_path.value() if isinstance(raw_path, sexpdata.Symbol) else str(raw_path)

        # KiCad 6+ uses (offset (xyz ...)), KiCad 5 uses (at (xyz ...))
        offset_node = sexp.find(node, "offset") or sexp.find(node, "at")
        scale_node = sexp.find(node, "scale")
        rotate_node = sexp.find(node, "rotate")

        def _xyz(parent: SExpNode | None) -> tuple[float, float, float]:
            if not parent:
                return (0.0, 0.0, 0.0)
            xyz = sexp.find(parent, "xyz")
            if not xyz or len(xyz) < 4:
                return (0.0, 0.0, 0.0)
            return (sexp.num(xyz, 1), sexp.num(xyz, 2), sexp.num(xyz, 3))

        offset = _xyz(offset_node)
        scale = _xyz(scale_node)
        rotation = _xyz(rotate_node)

        # Default scale to (1, 1, 1) if all zeros (missing node)
        if scale == (0.0, 0.0, 0.0) and not scale_node:
            scale = (1.0, 1.0, 1.0)

        models.append(
            PcbModel3D(
                source=source,
                offset=offset,
                rotation=rotation,
                scale=scale,
            )
        )
    return models


def _parse_footprint(
    fp_sexpr: SExpNode,
) -> tuple[PcbFootprint, list[PcbLine], list[PcbArc], list[PcbPolygon]]:
    """Parse a footprint, returning (PcbFootprint, edge_cuts_lines, edge_cuts_arcs, fp_polys)."""
    lib_name = str(fp_sexpr[1])

    layer_node = sexp.find(fp_sexpr, "layer")
    layer = sexp.val(layer_node) if layer_node else "F.Cu"

    at_node = sexp.find(fp_sexpr, "at")
    fp_x, fp_y, fp_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

    ref = _extract_reference(fp_sexpr)
    value = _extract_value(fp_sexpr)

    pads = [_parse_pad(p, fp_x, fp_y, fp_rot, ref) for p in sexp.find_all(fp_sexpr, "pad")]

    silk_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _SILK_LAYERS, fp_ref=ref)
    court_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _COURTYARD_LAYERS, fp_ref=ref)
    fab_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS, fp_ref=ref)
    fab_lines.extend(
        _parse_fp_rects_as_lines(fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS, fp_ref=ref)
    )
    fab_circles = _parse_fp_circles(fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS, fp_ref=ref)
    fab_arcs = _parse_fp_arcs(fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS, fp_ref=ref)
    edge_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _EDGE_LAYERS)
    edge_arcs = _parse_fp_arcs(fp_sexpr, fp_x, fp_y, fp_rot, _EDGE_LAYERS)
    fp_polys = _parse_fp_polys(
        fp_sexpr,
        fp_x,
        fp_y,
        fp_rot,
        _FAB_LAYERS | _SILK_LAYERS,
        fp_ref=ref,
    )

    texts = _parse_fp_texts(fp_sexpr, fp_x, fp_y, fp_rot, ref)

    models = _parse_fp_models(fp_sexpr)

    bbox = _compute_bbox(pads, court_lines)

    fp = PcbFootprint(
        reference=ref,
        footprint_lib=lib_name,
        x=fp_x,
        y=fp_y,
        rotation=fp_rot,
        layer=layer,
        value=value,
        pads=pads,
        silkscreen_lines=silk_lines,
        courtyard_lines=court_lines,
        fab_lines=fab_lines,
        fab_circles=fab_circles,
        fab_arcs=fab_arcs,
        texts=texts,
        models_3d=models,
        bbox=bbox,
    )
    return fp, edge_lines, edge_arcs, fp_polys


# ---------------------------------------------------------------------------
# Segment / via parsing
# ---------------------------------------------------------------------------


def _parse_segment(seg_sexpr: SExpNode) -> PcbSegment:
    start_node = sexp.find(seg_sexpr, "start")
    end_node = sexp.find(seg_sexpr, "end")
    width_node = sexp.find(seg_sexpr, "width")
    layer_node = sexp.find(seg_sexpr, "layer")
    if not start_node or not end_node or not width_node or not layer_node:
        msg = "Segment missing required start/end/width/layer"
        raise ValueError(msg)
    start = _xy(start_node)
    end = _xy(end_node)
    width = _float_val(width_node)
    layer = sexp.val(layer_node)
    net_node = sexp.find(seg_sexpr, "net")
    net = int(sexp.num(net_node, 1)) if net_node else 0
    return PcbSegment(start[0], start[1], end[0], end[1], width, layer, net)


def _parse_via(via_sexpr: SExpNode) -> PcbVia:
    at_node = sexp.find(via_sexpr, "at")
    size_node = sexp.find(via_sexpr, "size")
    drill_node = sexp.find(via_sexpr, "drill")
    if not at_node or not size_node or not drill_node:
        msg = "Via missing required at/size/drill"
        raise ValueError(msg)
    x, y = sexp.num(at_node, 1), sexp.num(at_node, 2)
    size = _float_val(size_node)
    drill = _float_val(drill_node)
    layers_node = sexp.find(via_sexpr, "layers")
    via_layers = _layers(layers_node) if layers_node else []
    net_node = sexp.find(via_sexpr, "net")
    net = int(sexp.num(net_node, 1)) if net_node else 0
    return PcbVia(x, y, size, drill, via_layers, net)


# ---------------------------------------------------------------------------
# Board outline parsing
# ---------------------------------------------------------------------------


def _parse_gr_line(item: SExpNode) -> PcbLine | None:
    """Parse a (gr_line ...) if it's on Edge.Cuts."""
    layer_node = sexp.find(item, "layer")
    if not layer_node or sexp.val(layer_node) != "Edge.Cuts":
        return None
    start_node = sexp.find(item, "start")
    end_node = sexp.find(item, "end")
    if not start_node or not end_node:
        return None
    start = _xy(start_node)
    end = _xy(end_node)
    width_node = sexp.find(item, "width")
    stroke_node = sexp.find(item, "stroke")
    if width_node:
        w = _float_val(width_node)
    elif stroke_node:
        sw = sexp.find(stroke_node, "width")
        w = _float_val(sw) if sw else 0.1
    else:
        w = 0.1
    return PcbLine(start[0], start[1], end[0], end[1], "Edge.Cuts", w)


def _parse_gr_arc(item: SExpNode) -> PcbArc | None:
    """Parse a (gr_arc ...) if it's on Edge.Cuts.

    KiCad 6+ uses start/mid/end; KiCad 5 uses start/end/angle where
    start is the centre and end is one endpoint.
    """
    layer_node = sexp.find(item, "layer")
    if not layer_node or sexp.val(layer_node) != "Edge.Cuts":
        return None
    mid_node = sexp.find(item, "mid")
    start_node = sexp.find(item, "start")
    end_node = sexp.find(item, "end")
    if not start_node or not end_node:
        return None
    width_node = sexp.find(item, "width")
    stroke_node = sexp.find(item, "stroke")
    if width_node:
        w = _float_val(width_node)
    elif stroke_node:
        sw = sexp.find(stroke_node, "width")
        w = _float_val(sw) if sw else 0.1
    else:
        w = 0.1
    if mid_node:
        # KiCad 6+: start/mid/end are three points on the arc
        start = _xy(start_node)
        mid = _xy(mid_node)
        end = _xy(end_node)
        return PcbArc(start[0], start[1], mid[0], mid[1], end[0], end[1], "Edge.Cuts", w)
    else:
        # KiCad 5: start=centre, end=one endpoint, angle=sweep
        angle_node = sexp.find(item, "angle")
        if not angle_node:
            return None
        cx, cy = _xy(start_node)
        ex, ey = _xy(end_node)
        angle_deg = _float_val(angle_node)
        # Compute the other endpoint and midpoint
        rad = math.radians(angle_deg)
        half_rad = rad / 2
        dx, dy = ex - cx, ey - cy
        # Midpoint of the arc
        cos_h, sin_h = math.cos(half_rad), math.sin(half_rad)
        mx = cx + dx * cos_h - dy * sin_h
        my = cy + dx * sin_h + dy * cos_h
        # Far endpoint
        cos_f, sin_f = math.cos(rad), math.sin(rad)
        fx = cx + dx * cos_f - dy * sin_f
        fy = cy + dx * sin_f + dy * cos_f
        return PcbArc(ex, ey, mx, my, fx, fy, "Edge.Cuts", w)


# ---------------------------------------------------------------------------
# Zone / polygon / trace-arc parsing
# ---------------------------------------------------------------------------


def _parse_zone_polygons(zone_sexpr: SExpNode) -> list[PcbPolygon]:
    """Extract filled_polygon entries from a zone as PcbPolygon objects."""
    net_node = sexp.find(zone_sexpr, "net")
    net_num = int(sexp.num(net_node, 1)) if net_node and len(net_node) > 1 else 0
    net_name_node = sexp.find(zone_sexpr, "net_name")
    net_name = sexp.val(net_name_node) if net_name_node else ""

    # Zone-level layer (KiCad 5 filled_polygons inherit this)
    zone_layer_node = sexp.find(zone_sexpr, "layer")
    zone_layer = sexp.val(zone_layer_node) if zone_layer_node else ""

    polygons: list[PcbPolygon] = []
    for fp_node in sexp.find_all(zone_sexpr, "filled_polygon"):
        # KiCad 6+ has per-filled_polygon layer; KiCad 5 inherits from zone
        layer_node = sexp.find(fp_node, "layer")
        layer = sexp.val(layer_node) if layer_node else zone_layer
        pts_node = sexp.find(fp_node, "pts")
        if not pts_node:
            continue
        points: list[tuple[float, float]] = []
        for xy_node in sexp.find_all(pts_node, "xy"):
            points.append((sexp.num(xy_node, 1), sexp.num(xy_node, 2)))
        if points:
            polygons.append(
                PcbPolygon(
                    points=points,
                    layer=layer,
                    net_number=net_num,
                    net_name=net_name,
                )
            )
    return polygons


def _parse_gr_poly(item: SExpNode) -> PcbPolygon | None:
    """Parse a (gr_poly ...) as a PcbPolygon."""
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        return None
    layer = sexp.val(layer_node)
    pts_node = sexp.find(item, "pts")
    if not pts_node:
        return None
    points: list[tuple[float, float]] = []
    for xy_node in sexp.find_all(pts_node, "xy"):
        points.append((sexp.num(xy_node, 1), sexp.num(xy_node, 2)))
    if not points:
        return None
    return PcbPolygon(points=points, layer=layer)


def _parse_fp_polys(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    fp_ref: str = "",
) -> list[PcbPolygon]:
    """Parse fp_poly elements matching layer_filter, transform to absolute."""
    polys: list[PcbPolygon] = []
    for item in sexp.find_all(fp_sexpr, "fp_poly"):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        pts_node = sexp.find(item, "pts")
        if not pts_node:
            continue
        points: list[tuple[float, float]] = []
        for xy_node in sexp.find_all(pts_node, "xy"):
            lx, ly = sexp.num(xy_node, 1), sexp.num(xy_node, 2)
            ax, ay = _transform_point(lx, ly, fp_x, fp_y, fp_rot)
            points.append((ax, ay))
        if points:
            polys.append(PcbPolygon(points=points, layer=layer, footprint_ref=fp_ref))
    return polys


def _parse_trace_arc(arc_sexpr: SExpNode) -> PcbTraceArc | None:
    """Parse a top-level (arc ...) copper trace arc."""
    start_node = sexp.find(arc_sexpr, "start")
    mid_node = sexp.find(arc_sexpr, "mid")
    end_node = sexp.find(arc_sexpr, "end")
    if not start_node or not mid_node or not end_node:
        return None
    sx, sy = _xy(start_node)
    mx, my = _xy(mid_node)
    ex, ey = _xy(end_node)
    width_node = sexp.find(arc_sexpr, "width")
    w = _float_val(width_node) if width_node else 0.1
    layer_node = sexp.find(arc_sexpr, "layer")
    layer = sexp.val(layer_node) if layer_node else ""
    net_node = sexp.find(arc_sexpr, "net")
    net = int(sexp.num(net_node, 1)) if net_node and len(net_node) > 1 else 0
    return PcbTraceArc(sx, sy, mx, my, ex, ey, w, layer, net)


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------


def parse_kicad_pcb(path: Path) -> Pcb:
    """Parse a .kicad_pcb file into the PCB domain model."""
    text = path.read_text(encoding="utf-8")
    data: SExpNode = sexpdata.loads(text)
    sexpr: SExpNode = list(data[1:]) if data else []

    # Layer definitions
    layer_defs = _parse_layer_defs(sexpr)

    # Title for the board name
    title_block = sexp.find(sexpr, "title_block")
    title_node = sexp.find(title_block, "title") if title_block else None
    name = sexp.val(title_node) if title_node else path.stem

    nets = _parse_nets(sexpr)

    footprints: list[PcbFootprint] = []
    edge_lines_from_fps: list[PcbLine] = []
    edge_arcs_from_fps: list[PcbArc] = []
    polygons_from_fps: list[PcbPolygon] = []
    # KiCad 6+ uses "footprint", KiCad 5 uses "module"
    for tag in ("footprint", "module"):
        for fp_sexpr in sexp.find_all(sexpr, tag):
            fp, edge_lines, edge_arcs, fp_polys = _parse_footprint(fp_sexpr)
            footprints.append(fp)
            edge_lines_from_fps.extend(edge_lines)
            edge_arcs_from_fps.extend(edge_arcs)
            polygons_from_fps.extend(fp_polys)

    segments = [_parse_segment(s) for s in sexp.find_all(sexpr, "segment")]
    vias = [_parse_via(v) for v in sexp.find_all(sexpr, "via")]

    # Zones — extract filled_polygon geometry
    polygons: list[PcbPolygon] = []
    for zone_sexpr in sexp.find_all(sexpr, "zone"):
        polygons.extend(_parse_zone_polygons(zone_sexpr))
    # Top-level graphic polygons
    for item in sexp.find_all(sexpr, "gr_poly"):
        p = _parse_gr_poly(item)
        if p:
            polygons.append(p)
    # Footprint polygons (fab/silk)
    polygons.extend(polygons_from_fps)

    # Trace arcs (curved copper traces)
    trace_arcs: list[PcbTraceArc] = []
    for item in sexp.find_all(sexpr, "arc"):
        ta = _parse_trace_arc(item)
        if ta:
            trace_arcs.append(ta)

    # Board outline: top-level gr_line/gr_arc on Edge.Cuts + fp-internal ones
    outline_lines: list[PcbLine] = []
    outline_arcs: list[PcbArc] = []
    for item in sexp.find_all(sexpr, "gr_line"):
        ln = _parse_gr_line(item)
        if ln:
            outline_lines.append(ln)
    for item in sexp.find_all(sexpr, "gr_arc"):
        arc = _parse_gr_arc(item)
        if arc:
            outline_arcs.append(arc)
    outline_lines.extend(edge_lines_from_fps)
    outline_arcs.extend(edge_arcs_from_fps)

    return Pcb(
        name=name,
        nets=nets,
        footprints=footprints,
        segments=segments,
        vias=vias,
        outline_lines=outline_lines,
        outline_arcs=outline_arcs,
        polygons=polygons,
        trace_arcs=trace_arcs,
        layers=layer_defs,
    )
