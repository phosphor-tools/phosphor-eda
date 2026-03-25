"""Parse a KiCad .kicad_pcb file into the PCB domain model.

Uses sexpdata and the same helper pattern as to_schematic.py.
Handles both KiCad 6 (fp_text reference) and KiCad 8 (property
"Reference") formats.
"""

from __future__ import annotations

import math
from pathlib import Path

import sexpdata

from phosphor_eda.kicad.to_schematic import _find, _find_all, _property, _tag, _val
from phosphor_eda.pcb import (
    PcbArc,
    PcbBoard,
    PcbFootprint,
    PcbLine,
    PcbNet,
    PcbPad,
    PcbSegment,
    PcbText,
    PcbVia,
)


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------


def _xy(item: list) -> tuple[float, float]:
    """Extract (x, y) from an S-expression like (start 1.0 2.0)."""
    return (float(item[1]), float(item[2]))


def _float_val(item: list) -> float:
    """Extract a single float from item[1]."""
    return float(item[1])


def _at(item: list) -> tuple[float, float, float]:
    """Extract (x, y, rotation) from (at X Y [ROT]).

    The rotation field may be absent, or followed by keywords like
    ``unlocked`` which must be skipped.
    """
    x = float(item[1])
    y = float(item[2])
    rot = 0.0
    if len(item) > 3:
        try:
            rot = float(item[3])
        except (ValueError, TypeError):
            pass  # e.g. Symbol('unlocked')
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


def _layers(item: list) -> list[str]:
    """Extract layer names from (layers "F.Cu" "B.Cu" ...)."""
    result: list[str] = []
    for v in item[1:]:
        if isinstance(v, str):
            result.append(v)
        elif isinstance(v, sexpdata.Symbol):
            result.append(v.value())
    return result


# ---------------------------------------------------------------------------
# Net parsing
# ---------------------------------------------------------------------------


def _parse_nets(sexpr: list) -> dict[int, PcbNet]:
    """Parse top-level (net N "name") entries."""
    nets: dict[int, PcbNet] = {}
    for item in _find_all(sexpr, "net"):
        if len(item) >= 3:
            num = int(item[1])
            name = str(item[2])
            nets[num] = PcbNet(number=num, name=name)
    return nets


# ---------------------------------------------------------------------------
# Footprint / pad parsing
# ---------------------------------------------------------------------------


def _extract_reference(fp_sexpr: list) -> str:
    """Get reference designator, handling both KiCad 6 and 8 formats."""
    # KiCad 8: (property "Reference" "R1" ...)
    ref = _property(fp_sexpr, "Reference")
    if ref:
        return ref
    # KiCad 6: (fp_text reference "R1" ...)
    for item in fp_sexpr:
        if _tag(item) == "fp_text" and len(item) > 2:
            v = item[1]
            if isinstance(v, sexpdata.Symbol) and v.value() == "reference":
                return str(item[2])
    return "?"


def _parse_pad(
    pad_sexpr: list,
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

    at_node = _find(pad_sexpr, "at")
    local_x, local_y, pad_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

    size_node = _find(pad_sexpr, "size")
    width = float(size_node[1]) if size_node else 0.0
    height = float(size_node[2]) if size_node and len(size_node) > 2 else width

    layers_node = _find(pad_sexpr, "layers")
    pad_layers = _layers(layers_node) if layers_node else []

    net_node = _find(pad_sexpr, "net")
    net_num = int(net_node[1]) if net_node and len(net_node) > 1 else 0
    net_name = str(net_node[2]) if net_node and len(net_node) > 2 else ""

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
    )


def _parse_fp_lines(
    fp_sexpr: list,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
) -> list[PcbLine]:
    """Parse fp_line elements matching layer_filter, transform to absolute."""
    lines: list[PcbLine] = []
    for item in _find_all(fp_sexpr, "fp_line"):
        layer_node = _find(item, "layer")
        if not layer_node:
            continue
        layer = _val(layer_node)
        if layer not in layer_filter:
            continue
        start_node = _find(item, "start")
        end_node = _find(item, "end")
        if not start_node or not end_node:
            continue
        sx, sy = _xy(start_node)
        ex, ey = _xy(end_node)
        abs_s = _transform_point(sx, sy, fp_x, fp_y, fp_rot)
        abs_e = _transform_point(ex, ey, fp_x, fp_y, fp_rot)
        width_node = _find(item, "width")
        stroke_node = _find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = _find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        lines.append(PcbLine(abs_s[0], abs_s[1], abs_e[0], abs_e[1], layer, w))
    return lines


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
    fp_sexpr: list,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    fp_ref: str,
) -> list[PcbText]:
    """Parse fp_text elements into PcbText with absolute coords."""
    texts: list[PcbText] = []
    for item in _find_all(fp_sexpr, "fp_text"):
        if len(item) < 3:
            continue
        kind_sym = item[1]
        kind = kind_sym.value() if isinstance(kind_sym, sexpdata.Symbol) else str(kind_sym)
        raw_text = str(item[2])

        # Resolve ${REFERENCE} placeholder
        if "${REFERENCE}" in raw_text:
            raw_text = raw_text.replace("${REFERENCE}", fp_ref)

        # Check hidden flag
        hidden = any(
            isinstance(x, sexpdata.Symbol) and x.value() == "hide" for x in item
        )

        layer_node = _find(item, "layer")
        layer = _val(layer_node) if layer_node else ""

        at_node = _find(item, "at")
        local_x, local_y, text_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

        # Font size
        effects = _find(item, "effects")
        font = _find(effects, "font") if effects else None
        size_node = _find(font, "size") if font else None
        font_size = float(size_node[1]) if size_node else 1.0

        abs_x, abs_y = _transform_point(local_x, local_y, fp_x, fp_y, fp_rot)
        abs_rot = fp_rot + text_rot

        texts.append(PcbText(
            text=raw_text,
            x=abs_x,
            y=abs_y,
            rotation=abs_rot,
            layer=layer,
            font_size=font_size,
            hidden=hidden,
        ))
    return texts


def _parse_footprint(fp_sexpr: list) -> tuple[PcbFootprint, list[PcbLine]]:
    """Parse a footprint, returning (PcbFootprint, edge_cuts_lines)."""
    lib_name = str(fp_sexpr[1])

    layer_node = _find(fp_sexpr, "layer")
    layer = _val(layer_node) if layer_node else "F.Cu"

    at_node = _find(fp_sexpr, "at")
    fp_x, fp_y, fp_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

    ref = _extract_reference(fp_sexpr)

    pads = [
        _parse_pad(p, fp_x, fp_y, fp_rot, ref)
        for p in _find_all(fp_sexpr, "pad")
    ]

    silk_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _SILK_LAYERS)
    court_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _COURTYARD_LAYERS)
    fab_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS)
    edge_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _EDGE_LAYERS)

    texts = _parse_fp_texts(fp_sexpr, fp_x, fp_y, fp_rot, ref)

    bbox = _compute_bbox(pads, court_lines)

    fp = PcbFootprint(
        reference=ref,
        footprint_lib=lib_name,
        x=fp_x,
        y=fp_y,
        rotation=fp_rot,
        layer=layer,
        pads=pads,
        silkscreen_lines=silk_lines,
        courtyard_lines=court_lines,
        fab_lines=fab_lines,
        texts=texts,
        bbox=bbox,
    )
    return fp, edge_lines


# ---------------------------------------------------------------------------
# Segment / via parsing
# ---------------------------------------------------------------------------


def _parse_segment(seg_sexpr: list) -> PcbSegment:
    start = _xy(_find(seg_sexpr, "start"))
    end = _xy(_find(seg_sexpr, "end"))
    width = _float_val(_find(seg_sexpr, "width"))
    layer = _val(_find(seg_sexpr, "layer"))
    net_node = _find(seg_sexpr, "net")
    net = int(net_node[1]) if net_node else 0
    return PcbSegment(start[0], start[1], end[0], end[1], width, layer, net)


def _parse_via(via_sexpr: list) -> PcbVia:
    at_node = _find(via_sexpr, "at")
    x, y = float(at_node[1]), float(at_node[2])
    size = _float_val(_find(via_sexpr, "size"))
    drill = _float_val(_find(via_sexpr, "drill"))
    layers_node = _find(via_sexpr, "layers")
    via_layers = _layers(layers_node) if layers_node else []
    net_node = _find(via_sexpr, "net")
    net = int(net_node[1]) if net_node else 0
    return PcbVia(x, y, size, drill, via_layers, net)


# ---------------------------------------------------------------------------
# Board outline parsing
# ---------------------------------------------------------------------------


def _parse_gr_line(item: list) -> PcbLine | None:
    """Parse a (gr_line ...) if it's on Edge.Cuts."""
    layer_node = _find(item, "layer")
    if not layer_node or _val(layer_node) != "Edge.Cuts":
        return None
    start = _xy(_find(item, "start"))
    end = _xy(_find(item, "end"))
    width_node = _find(item, "width")
    stroke_node = _find(item, "stroke")
    if width_node:
        w = _float_val(width_node)
    elif stroke_node:
        sw = _find(stroke_node, "width")
        w = _float_val(sw) if sw else 0.1
    else:
        w = 0.1
    return PcbLine(start[0], start[1], end[0], end[1], "Edge.Cuts", w)


def _parse_gr_arc(item: list) -> PcbArc | None:
    """Parse a (gr_arc ...) if it's on Edge.Cuts."""
    layer_node = _find(item, "layer")
    if not layer_node or _val(layer_node) != "Edge.Cuts":
        return None
    start = _xy(_find(item, "start"))
    mid = _xy(_find(item, "mid"))
    end = _xy(_find(item, "end"))
    width_node = _find(item, "width")
    stroke_node = _find(item, "stroke")
    if width_node:
        w = _float_val(width_node)
    elif stroke_node:
        sw = _find(stroke_node, "width")
        w = _float_val(sw) if sw else 0.1
    else:
        w = 0.1
    return PcbArc(start[0], start[1], mid[0], mid[1], end[0], end[1], "Edge.Cuts", w)


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------


def parse_kicad_pcb(path: Path) -> PcbBoard:
    """Parse a .kicad_pcb file into the PCB domain model."""
    text = path.read_text(encoding="utf-8")
    data = sexpdata.loads(text)
    sexpr = data[1:] if data else []

    # Title for the board name
    title_block = _find(sexpr, "title_block")
    title_node = _find(title_block, "title") if title_block else None
    name = _val(title_node) if title_node else path.stem

    nets = _parse_nets(sexpr)

    footprints: list[PcbFootprint] = []
    edge_lines_from_fps: list[PcbLine] = []
    for fp_sexpr in _find_all(sexpr, "footprint"):
        fp, edge_lines = _parse_footprint(fp_sexpr)
        footprints.append(fp)
        edge_lines_from_fps.extend(edge_lines)

    segments = [_parse_segment(s) for s in _find_all(sexpr, "segment")]
    vias = [_parse_via(v) for v in _find_all(sexpr, "via")]

    # Board outline: top-level gr_line/gr_arc on Edge.Cuts + fp-internal ones
    outline_lines: list[PcbLine] = []
    outline_arcs: list[PcbArc] = []
    for item in _find_all(sexpr, "gr_line"):
        ln = _parse_gr_line(item)
        if ln:
            outline_lines.append(ln)
    for item in _find_all(sexpr, "gr_arc"):
        arc = _parse_gr_arc(item)
        if arc:
            outline_arcs.append(arc)
    outline_lines.extend(edge_lines_from_fps)

    return PcbBoard(
        name=name,
        nets=nets,
        footprints=footprints,
        segments=segments,
        vias=vias,
        outline_lines=outline_lines,
        outline_arcs=outline_arcs,
    )
