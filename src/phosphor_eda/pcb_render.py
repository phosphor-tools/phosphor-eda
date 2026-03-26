"""Render a PcbBoard as layered SVG with CSS theming.

Emits an SVG with layer groups, data-* attributes on every element, and
a <style> block for theming.  Highlights, layer visibility, and colors
are all controlled via CSS — downstream JS can restyle without re-rendering.

No external dependencies — SVG is built via string formatting.
"""

from __future__ import annotations

import math
from collections import defaultdict
from xml.sax.saxutils import escape as xml_escape

from phosphor_eda.pcb import (
    PcbArc,
    PcbBoard,
    PcbCircle,
    PcbLine,
    PcbNet,
    PcbPad,
    PcbTraceArc,
)

# ---------------------------------------------------------------------------
# SVG builder
# ---------------------------------------------------------------------------


def _fmt_attrs(attrs: dict[str, str] | None) -> str:
    """Format a dict of attributes into an SVG attribute string."""
    if not attrs:
        return ""
    return " " + " ".join(f'{k}="{v}"' for k, v in attrs.items())


class _Svg:
    """Tiny SVG string builder with data-attribute support."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def raw(self, s: str) -> None:
        self._parts.append(s)

    def line(
        self, x1: float, y1: float, x2: float, y2: float,
        stroke_width: float, attrs: dict[str, str] | None = None,
    ) -> None:
        self._parts.append(
            f'<line x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" '
            f'stroke-width="{stroke_width:.4f}"{_fmt_attrs(attrs)}/>'
        )

    def circle(
        self, cx: float, cy: float, r: float,
        attrs: dict[str, str] | None = None,
    ) -> None:
        self._parts.append(
            f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{r:.4f}"'
            f'{_fmt_attrs(attrs)}/>'
        )

    def rect(
        self, x: float, y: float, w: float, h: float,
        rx: float = 0, attrs: dict[str, str] | None = None,
    ) -> None:
        s = f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}"'
        if rx > 0:
            s += f' rx="{rx:.4f}"'
        s += f'{_fmt_attrs(attrs)}/>'
        self._parts.append(s)

    def polygon(
        self, points: list[tuple[float, float]],
        attrs: dict[str, str] | None = None,
    ) -> None:
        pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        self._parts.append(f'<polygon points="{pts}"{_fmt_attrs(attrs)}/>')

    def text(
        self, x: float, y: float, content: str, font_size: float,
        attrs: dict[str, str] | None = None,
        bold: bool = False, rotation: float = 0.0,
    ) -> None:
        weight = ' font-weight="bold"' if bold else ""
        rot = (
            f' transform="rotate({rotation:.1f} {x:.4f} {y:.4f})"'
            if rotation else ""
        )
        self._parts.append(
            f'<text x="{x:.4f}" y="{y:.4f}" font-size="{font_size:.2f}" '
            f'text-anchor="middle" '
            f'dominant-baseline="central" font-family="sans-serif"'
            f'{weight}{rot}{_fmt_attrs(attrs)}>'
            f'{xml_escape(content)}</text>'
        )

    def group_start(
        self, transform: str | None = None,
        attrs: dict[str, str] | None = None,
    ) -> None:
        s = "<g"
        if transform:
            s += f' transform="{transform}"'
        s += f'{_fmt_attrs(attrs)}>'
        self._parts.append(s)

    def group_end(self) -> None:
        self._parts.append("</g>")

    def build(self) -> str:
        return "\n".join(self._parts)


# ---------------------------------------------------------------------------
# Arc math — compute SVG arc from three points
# ---------------------------------------------------------------------------


def _circumcircle(
    x1: float, y1: float, x2: float, y2: float, x3: float, y3: float,
) -> tuple[float, float, float]:
    """Return (cx, cy, r) for the circle through three points."""
    ax, ay = x1, y1
    bx, by = x2, y2
    cx, cy = x3, y3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        return ((x1 + x3) / 2, (y1 + y3) / 2, 1e6)
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def _arc_svg_params(
    sx: float, sy: float, mx: float, my: float, ex: float, ey: float,
) -> tuple[float, int, int]:
    """Compute (radius, large_arc_flag, sweep_flag) for an SVG arc command."""
    _, _, r = _circumcircle(sx, sy, mx, my, ex, ey)
    if r > 1e5:
        return (r, 0, 0)
    ccx, ccy, _ = _circumcircle(sx, sy, mx, my, ex, ey)
    dx1 = mx - sx
    dy1 = my - sy
    dx2 = ex - sx
    dy2 = ey - sy
    cross = dx1 * dy2 - dy1 * dx2
    sweep = 1 if cross > 0 else 0
    angle_start = math.atan2(sy - ccy, sx - ccx)
    angle_mid = math.atan2(my - ccy, mx - ccx)
    angle_end = math.atan2(ey - ccy, ex - ccx)

    def _norm(a: float, ref: float) -> float:
        dd = a - ref
        while dd < -math.pi:
            dd += 2 * math.pi
        while dd > math.pi:
            dd -= 2 * math.pi
        return dd

    d_mid = _norm(angle_mid, angle_start)
    d_end = _norm(angle_end, angle_start)
    large_arc = 1 if abs(d_end) > math.pi else 0
    if (d_mid > 0) != (d_end > 0):
        large_arc = 1
    return (r, large_arc, sweep)


def _svg_arc_path_d(
    sx: float, sy: float, mx: float, my: float, ex: float, ey: float,
) -> str:
    """Return an SVG path `d` attribute for a three-point arc."""
    r, large_arc, sweep = _arc_svg_params(sx, sy, mx, my, ex, ey)
    if r > 1e5:
        return f"M {sx:.4f} {sy:.4f} L {ex:.4f} {ey:.4f}"
    return (
        f"M {sx:.4f} {sy:.4f} "
        f"A {r:.4f} {r:.4f} 0 {large_arc} {sweep} {ex:.4f} {ey:.4f}"
    )


# ---------------------------------------------------------------------------
# Outline clip path builder
# ---------------------------------------------------------------------------


def _build_outline_clip_path(
    lines: list[PcbLine], arcs: list[PcbArc],
) -> str | None:
    """Build an SVG path `d` attribute from board outline geometry.

    Chains lines and arcs into a closed path suitable for a <clipPath>.
    Returns None if the outline can't be chained into a closed loop.
    """
    if not lines and not arcs:
        return None

    EPS = 0.05
    Seg = tuple[tuple[float, float], tuple[float, float], str]
    segments: list[Seg] = []

    for ln in lines:
        s = (ln.start_x, ln.start_y)
        e = (ln.end_x, ln.end_y)
        segments.append((s, e, f"L {e[0]:.4f} {e[1]:.4f}"))

    for arc in arcs:
        s = (arc.start_x, arc.start_y)
        e = (arc.end_x, arc.end_y)
        r, large_arc, sweep = _arc_svg_params(
            arc.start_x, arc.start_y,
            arc.mid_x, arc.mid_y,
            arc.end_x, arc.end_y,
        )
        if r > 1e5:
            segments.append((s, e, f"L {e[0]:.4f} {e[1]:.4f}"))
            continue
        cmd = f"A {r:.4f} {r:.4f} 0 {large_arc} {sweep} {e[0]:.4f} {e[1]:.4f}"
        segments.append((s, e, cmd))

    if not segments:
        return None

    def _reverse_cmd(cmd: str, new_end: tuple[float, float]) -> str:
        if cmd.startswith("L"):
            return f"L {new_end[0]:.4f} {new_end[1]:.4f}"
        parts = cmd.split()
        sw = int(parts[5])
        return f"A {parts[1]} {parts[2]} {parts[3]} {parts[4]} {1 - sw} {new_end[0]:.4f} {new_end[1]:.4f}"

    def _find_loop(
        start: tuple[float, float],
        cur: tuple[float, float],
        used: set[int],
        cmds: list[str],
    ) -> list[str] | None:
        if len(cmds) >= 3 and abs(cur[0] - start[0]) < EPS and abs(cur[1] - start[1]) < EPS:
            return list(cmds)
        best: list[str] | None = None
        for idx in range(len(segments)):
            if idx in used:
                continue
            seg_s, seg_e, cmd = segments[idx]
            next_pt = None
            next_cmd = None
            if abs(seg_s[0] - cur[0]) < EPS and abs(seg_s[1] - cur[1]) < EPS:
                next_pt = seg_e
                next_cmd = cmd
            elif abs(seg_e[0] - cur[0]) < EPS and abs(seg_e[1] - cur[1]) < EPS:
                next_pt = seg_s
                next_cmd = _reverse_cmd(cmd, seg_s)
            if next_pt is None:
                continue
            used.add(idx)
            cmds.append(next_cmd)
            result = _find_loop(start, next_pt, used, cmds)
            if result is not None and (best is None or len(result) > len(best)):
                best = result
            cmds.pop()
            used.discard(idx)
        return best

    best_path: str | None = None
    best_len = 0
    for si in range(len(segments)):
        start_pt = segments[si][0]
        cur_pt = segments[si][1]
        loop = _find_loop(start_pt, cur_pt, {si}, [segments[si][2]])
        if loop and len(loop) > best_len:
            best_len = len(loop)
            best_path = f"M {start_pt[0]:.4f} {start_pt[1]:.4f} " + " ".join(loop) + " Z"
        start_pt = segments[si][1]
        cur_pt = segments[si][0]
        rev_cmd = _reverse_cmd(segments[si][2], cur_pt)
        loop = _find_loop(start_pt, cur_pt, {si}, [rev_cmd])
        if loop and len(loop) > best_len:
            best_len = len(loop)
            best_path = f"M {start_pt[0]:.4f} {start_pt[1]:.4f} " + " ".join(loop) + " Z"

    return best_path


# ---------------------------------------------------------------------------
# Component body polygon builder
# ---------------------------------------------------------------------------


def _chain_lines_to_polygon(lines: list[PcbLine]) -> list[tuple[float, float]] | None:
    """Try to chain line segments into a closed polygon.

    Returns a list of (x, y) vertices if the lines form a closed loop,
    or None if they can't be chained.
    """
    if len(lines) < 3:
        return None

    EPS = 0.05

    seen: set[tuple[float, float, float, float]] = set()
    deduped: list[PcbLine] = []
    for ln in lines:
        key = (round(ln.start_x, 2), round(ln.start_y, 2),
               round(ln.end_x, 2), round(ln.end_y, 2))
        key_rev = (key[2], key[3], key[0], key[1])
        if key not in seen and key_rev not in seen:
            seen.add(key)
            deduped.append(ln)
    lines = deduped

    if len(lines) < 3:
        return None

    remaining = list(range(len(lines)))
    chain = [remaining.pop(0)]
    vertices = [(lines[chain[0]].start_x, lines[chain[0]].start_y)]
    cx, cy = lines[chain[0]].end_x, lines[chain[0]].end_y
    vertices.append((cx, cy))

    while remaining:
        found = False
        for i, idx in enumerate(remaining):
            ln = lines[idx]
            if abs(ln.start_x - cx) < EPS and abs(ln.start_y - cy) < EPS:
                cx, cy = ln.end_x, ln.end_y
                vertices.append((cx, cy))
                remaining.pop(i)
                found = True
                break
            if abs(ln.end_x - cx) < EPS and abs(ln.end_y - cy) < EPS:
                cx, cy = ln.start_x, ln.start_y
                vertices.append((cx, cy))
                remaining.pop(i)
                found = True
                break
        if not found:
            return None

    if abs(cx - vertices[0][0]) < EPS and abs(cy - vertices[0][1]) < EPS:
        return vertices[:-1]
    return None


# ---------------------------------------------------------------------------
# Layer helpers
# ---------------------------------------------------------------------------

_SILK_NAMES = {"F.SilkS", "F.Silkscreen", "B.SilkS", "B.Silkscreen"}
_FAB_NAMES = {"F.Fab", "B.Fab"}

# Canonical layer paint order (bottom-up).  The renderer discovers which
# layers actually have content and emits only those, in this order.
_LAYER_ORDER = [
    "B.Cu",
    "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu",
    "In5.Cu", "In6.Cu", "In7.Cu", "In8.Cu",
    "F.Cu",
]


def _layer_class(layer: str) -> str:
    """Sanitize a layer name for use as a CSS class: 'F.Cu' -> 'layer-F-Cu'."""
    return "layer-" + layer.replace(".", "-")


def _net_name(board: PcbBoard, net_num: int) -> str:
    """Look up a net name, returning '' for unknown nets."""
    net = board.nets.get(net_num)
    return net.name if net else ""


def _pad_copper_layer(pad: PcbPad, fp_layer: str) -> str:
    """Determine which copper layer a pad belongs to for rendering.

    Through-hole pads (*.Cu) are placed in the footprint's primary layer.
    SMD pads are placed in whichever copper layer they specify.
    """
    for ly in pad.layers:
        if ly.startswith("*."):
            return fp_layer
        if ly.endswith(".Cu"):
            return ly
    return fp_layer


# ---------------------------------------------------------------------------
# CSS theme
# ---------------------------------------------------------------------------

# Copper layer color palette (hue-shifted for visual distinction)
_COPPER_COLORS: dict[str, str] = {
    "F.Cu": "#ff4444",
    "B.Cu": "#4488ff",
    "In1.Cu": "#ffaa00",
    "In2.Cu": "#44cc44",
    "In3.Cu": "#cc44cc",
    "In4.Cu": "#44cccc",
    "In5.Cu": "#ff8844",
    "In6.Cu": "#88cc44",
    "In7.Cu": "#8844ff",
    "In8.Cu": "#cc8844",
}


def _default_theme_css(side: str, copper_layers: list[str]) -> str:
    """Return the default CSS theme for the SVG."""
    rules: list[str] = []
    rules.append("/* Board */")
    rules.append(".board-fill { fill: #1a5c2a; }")
    rules.append(".background { fill: #111111; }")
    rules.append("")

    rules.append("/* Copper layers */")
    for layer in copper_layers:
        cls = _layer_class(layer)
        color = _COPPER_COLORS.get(layer, "#b87333")
        rules.append(f"g.{cls} .trace {{ stroke: {color}; stroke-linecap: round; fill: none; }}")
        rules.append(f"g.{cls} .trace-arc {{ stroke: {color}; stroke-linecap: round; fill: none; }}")
        rules.append(f"g.{cls} .pad {{ fill: #b87333; }}")
        rules.append(f"g.{cls} .zone {{ fill: {color}; opacity: 0.35; }}")

    rules.append("")
    rules.append("/* Vias */")
    rules.append(".via .annular { fill: #c0c0c0; }")
    rules.append(".via .drill { fill: #1a5c2a; }")

    rules.append("")
    rules.append("/* Silkscreen */")
    rules.append("g.layer-F-SilkS .silk, g.layer-F-Silkscreen .silk { stroke: #ffffffcc; stroke-linecap: round; fill: none; }")
    rules.append("g.layer-B-SilkS .silk, g.layer-B-Silkscreen .silk { stroke: #ffffffcc; stroke-linecap: round; fill: none; }")

    rules.append("")
    rules.append("/* Fab / component bodies */")
    rules.append(".body { fill: #3d3530; stroke: #5a504a; stroke-width: 0.06; stroke-linejoin: round; }")
    rules.append(".body-circle { fill: none; stroke: #5a504a; }")
    rules.append(".body-circle-filled { fill: #5a504a; }")
    rules.append(".body-arc { fill: none; stroke: #5a504a; stroke-linecap: round; }")

    rules.append("")
    rules.append("/* Text */")
    rules.append(".ref-text { fill: #ffffffcc; }")

    rules.append("")
    rules.append("/* Highlights */")
    rules.append(".highlight-box { fill: none; stroke: #ffff00; stroke-width: 0.3; stroke-dasharray: 0.5,0.3; }")
    rules.append(".highlight-label { fill: #ffff00; }")

    # Hide opposite-side layers by default
    opposite_silk = "B" if side == "front" else "F"
    opposite_fab = opposite_silk
    rules.append("")
    rules.append("/* Side visibility */")
    rules.append(f"g.layer-{opposite_silk}-SilkS, g.layer-{opposite_silk}-Silkscreen {{ display: none; }}")
    rules.append(f"g.layer-{opposite_fab}-Fab {{ display: none; }}")

    return "\n".join(rules)


def _highlight_css(hl_net_nums: set[int], hl_refs: set[str]) -> str:
    """Return CSS that dims non-highlighted elements and brightens highlighted."""
    rules: list[str] = []
    rules.append("/* Dim everything */")
    rules.append("g[data-layer] .trace, g[data-layer] .trace-arc, "
                 "g[data-layer] .pad, g[data-layer] .zone, "
                 "g.layer-vias .via, "
                 "g[data-layer] .silk, "
                 "g[data-layer] .body, g[data-layer] .body-circle, "
                 "g[data-layer] .body-circle-filled, g[data-layer] .body-arc, "
                 ".ref-text "
                 "{ opacity: 0.15; }")
    rules.append("")
    rules.append("/* Restore highlighted nets */")
    for nn in sorted(hl_net_nums):
        rules.append(f'[data-net-number="{nn}"] {{ opacity: 1 !important; }}')
    return "\n".join(rules)


# ---------------------------------------------------------------------------
# Pad drawing helper
# ---------------------------------------------------------------------------


def _draw_pad(svg: _Svg, pad: PcbPad, attrs: dict[str, str]) -> None:
    """Draw a single pad shape with the given attributes."""
    hw, hh = pad.width / 2, pad.height / 2
    if pad.shape == "circle":
        svg.circle(pad.x, pad.y, hw, attrs=attrs)
    elif pad.shape == "roundrect":
        rx = min(hw, hh) * 0.25
        svg.rect(pad.x - hw, pad.y - hh, pad.width, pad.height, rx=rx, attrs=attrs)
    elif pad.shape == "oval":
        rx = min(hw, hh)
        svg.rect(pad.x - hw, pad.y - hh, pad.width, pad.height, rx=rx, attrs=attrs)
    else:
        svg.rect(pad.x - hw, pad.y - hh, pad.width, pad.height, attrs=attrs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_pcb_svg(
    board: PcbBoard,
    *,
    side: str = "front",
    highlight_nets: list[str] | None = None,
    highlight_components: list[str] | None = None,
    width_px: int = 800,
    theme: str = "default",
) -> str:
    """Render a PcbBoard as a layered SVG string with CSS theming.

    Parameters
    ----------
    board:
        Parsed PCB board.
    side:
        "front" or "back".  Back view mirrors horizontally.
    highlight_nets:
        Net names to highlight (case-insensitive substring match).
    highlight_components:
        Component references to highlight.  Also highlights their nets.
    width_px:
        Pixel width of the SVG.
    theme:
        CSS theme name (currently only "default").
    """
    # -- Resolve highlights ------------------------------------------------
    hl_net_nums: set[int] = set()
    hl_refs: set[str] = set()

    if highlight_nets:
        for name in highlight_nets:
            hl_net_nums |= board.net_numbers_by_name(name)

    if highlight_components:
        for ref in highlight_components:
            fp = board.footprint_by_ref(ref)
            if fp:
                hl_refs.add(fp.reference)
                hl_net_nums |= board.nets_for_component(fp.reference)

    has_hl = bool(hl_net_nums) or bool(hl_refs)

    # -- Discover which copper layers have content -------------------------
    copper_layers_present: set[str] = set()
    for seg in board.segments:
        copper_layers_present.add(seg.layer)
    for ta in board.trace_arcs:
        copper_layers_present.add(ta.layer)
    for poly in board.polygons:
        if poly.layer.endswith(".Cu"):
            copper_layers_present.add(poly.layer)
    for fp in board.footprints:
        for pad in fp.pads:
            copper_layers_present.add(_pad_copper_layer(pad, fp.layer))
    # Ordered subset of _LAYER_ORDER that actually has content
    copper_layers = [ly for ly in _LAYER_ORDER if ly in copper_layers_present]
    # Add any extra layers not in _LAYER_ORDER (unlikely but defensive)
    for ly in sorted(copper_layers_present - set(_LAYER_ORDER)):
        copper_layers.append(ly)

    # -- Build indexes for per-layer rendering ----------------------------
    # Segments by layer
    segs_by_layer: dict[str, list] = defaultdict(list)
    for seg in board.segments:
        segs_by_layer[seg.layer].append(seg)

    # Trace arcs by layer
    tarcs_by_layer: dict[str, list] = defaultdict(list)
    for ta in board.trace_arcs:
        tarcs_by_layer[ta.layer].append(ta)

    # Zone polygons by layer
    zones_by_layer: dict[str, list] = defaultdict(list)
    for poly in board.polygons:
        if poly.layer.endswith(".Cu"):
            zones_by_layer[poly.layer].append(poly)

    # Pads by copper layer
    pads_by_layer: dict[str, list[tuple[PcbPad, str]]] = defaultdict(list)
    for fp in board.footprints:
        for pad in fp.pads:
            ly = _pad_copper_layer(pad, fp.layer)
            pads_by_layer[ly].append((pad, fp.reference))

    # Silkscreen lines by layer
    silk_by_layer: dict[str, list[PcbLine]] = defaultdict(list)
    for fp in board.footprints:
        for ln in fp.silkscreen_lines:
            if ln.layer in _SILK_NAMES:
                silk_by_layer[ln.layer].append(ln)

    # -- ViewBox -----------------------------------------------------------
    pad_mm = 2.0
    bx0, by0, bx1, by1 = board.bbox()
    vb_x = bx0 - pad_mm
    vb_y = by0 - pad_mm
    vb_w = (bx1 - bx0) + 2 * pad_mm
    vb_h = (by1 - by0) + 2 * pad_mm
    height_px = int(width_px * vb_h / vb_w) if vb_w > 0 else width_px

    svg = _Svg()
    svg.raw(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width_px}" height="{height_px}" '
        f'viewBox="{vb_x:.4f} {vb_y:.4f} {vb_w:.4f} {vb_h:.4f}">'
    )

    # -- CSS theme ---------------------------------------------------------
    svg.raw('<style id="theme">')
    svg.raw(_default_theme_css(side, copper_layers))
    svg.raw('</style>')

    if has_hl:
        svg.raw('<style id="highlight">')
        svg.raw(_highlight_css(hl_net_nums, hl_refs))
        svg.raw('</style>')

    # -- Background --------------------------------------------------------
    svg.raw(
        f'<rect x="{vb_x:.4f}" y="{vb_y:.4f}" '
        f'width="{vb_w:.4f}" height="{vb_h:.4f}" class="background"/>'
    )

    # -- Back-side mirror --------------------------------------------------
    if side == "back":
        svg.group_start(transform=f"translate({bx0 + bx1:.4f}, 0) scale(-1, 1)")

    # -- Clip paths --------------------------------------------------------
    clip_d = _build_outline_clip_path(board.outline_lines, board.outline_arcs)

    hole_circles: list[tuple[float, float, float]] = []
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.drill > 0:
                hole_circles.append((pad.x, pad.y, pad.drill / 2))
    _MASK_LAYERS = {"F.Mask", "B.Mask", "*.Mask"}
    for via in board.vias:
        via_layers = {
            ly.value() if hasattr(ly, "value") else str(ly)
            for ly in via.layers
        }
        if via_layers & _MASK_LAYERS:
            hole_circles.append((via.x, via.y, via.drill / 2))

    holes_d = ""
    for hx, hy, hr in hole_circles:
        holes_d += (
            f" M {hx - hr:.4f} {hy:.4f}"
            f" A {hr:.4f} {hr:.4f} 0 1 0 {hx + hr:.4f} {hy:.4f}"
            f" A {hr:.4f} {hr:.4f} 0 1 0 {hx - hr:.4f} {hy:.4f} Z"
        )

    board_clip_d = clip_d or (
        f"M {bx0:.4f} {by0:.4f} L {bx1:.4f} {by0:.4f} "
        f"L {bx1:.4f} {by1:.4f} L {bx0:.4f} {by1:.4f} Z"
    )
    svg.raw('<defs>')
    svg.raw(f'<clipPath id="board-clip"><path d="{board_clip_d}"/></clipPath>')
    if holes_d:
        cover_d = (
            f"M {vb_x:.4f} {vb_y:.4f} L {vb_x + vb_w:.4f} {vb_y:.4f} "
            f"L {vb_x + vb_w:.4f} {vb_y + vb_h:.4f} L {vb_x:.4f} {vb_y + vb_h:.4f} Z"
        )
        svg.raw(
            f'<clipPath id="drill-clip" clip-path="url(#board-clip)">'
            f'<path d="{cover_d}{holes_d}" clip-rule="evenodd"/>'
            f'</clipPath>'
        )
        active_clip = "drill-clip"
    else:
        active_clip = "board-clip"
    svg.raw('</defs>')

    # -- Board fill (clipped) ----------------------------------------------
    svg.raw(f'<g clip-path="url(#{active_clip})">')
    if clip_d:
        svg.raw(f'<path d="{clip_d}" class="board-fill"/>')
    else:
        svg.rect(bx0, by0, bx1 - bx0, by1 - by0, attrs={"class": "board-fill"})
    svg.group_end()

    # -- Content group (clipped) -------------------------------------------
    svg.raw(f'<g clip-path="url(#{active_clip})">')

    # -- Copper layer groups (paint order: back → inner → front) -----------
    for layer in copper_layers:
        cls = _layer_class(layer)
        svg.group_start(attrs={"data-layer": layer, "class": cls})

        # Zones
        for poly in zones_by_layer.get(layer, []):
            net_nm = poly.net_name or _net_name(board, poly.net_number)
            svg.polygon(poly.points, attrs={
                "class": "zone",
                "data-type": "zone",
                "data-net": net_nm,
                "data-net-number": str(poly.net_number),
            })

        # Traces
        for seg in segs_by_layer.get(layer, []):
            net_nm = _net_name(board, seg.net_number)
            svg.line(seg.start_x, seg.start_y, seg.end_x, seg.end_y, seg.width, attrs={
                "class": "trace",
                "data-type": "trace",
                "data-net": net_nm,
                "data-net-number": str(seg.net_number),
            })

        # Trace arcs
        for ta in tarcs_by_layer.get(layer, []):
            net_nm = _net_name(board, ta.net_number)
            d = _svg_arc_path_d(ta.start_x, ta.start_y, ta.mid_x, ta.mid_y, ta.end_x, ta.end_y)
            svg.raw(
                f'<path d="{d}" stroke-width="{ta.width:.4f}" '
                f'class="trace-arc" data-type="trace" '
                f'data-net="{xml_escape(net_nm)}" data-net-number="{ta.net_number}"/>'
            )

        # Pads
        for pad, fp_ref in pads_by_layer.get(layer, []):
            net_nm = pad.net_name or _net_name(board, pad.net_number)
            _draw_pad(svg, pad, {
                "class": "pad",
                "data-type": "pad",
                "data-component": fp_ref,
                "data-pad": pad.number,
                "data-net": net_nm,
                "data-net-number": str(pad.net_number),
            })

        svg.group_end()

    # -- Vias (span layers, get their own group) ---------------------------
    svg.group_start(attrs={"data-layer": "vias", "class": "layer-vias"})
    for via in board.vias:
        net_nm = _net_name(board, via.net_number)
        r_annular = via.drill / 2 + 0.05
        via_attrs_base = {
            "data-type": "via",
            "data-net": net_nm,
            "data-net-number": str(via.net_number),
        }
        svg.group_start(attrs={**via_attrs_base, "class": "via"})
        svg.circle(via.x, via.y, r_annular, attrs={"class": "annular"})
        svg.circle(via.x, via.y, via.drill / 2, attrs={"class": "drill"})
        svg.group_end()
    svg.group_end()

    # -- Silkscreen layer groups -------------------------------------------
    for silk_layer in sorted(silk_by_layer.keys()):
        cls = _layer_class(silk_layer)
        svg.group_start(attrs={"data-layer": silk_layer, "class": cls})
        for ln in silk_by_layer[silk_layer]:
            svg.line(
                ln.start_x, ln.start_y, ln.end_x, ln.end_y,
                max(ln.width, 0.1),
                attrs={"class": "silk"},
            )
        svg.group_end()

    # -- Fab layer groups (component bodies) -------------------------------
    for fab_layer in ("B.Fab", "F.Fab"):
        fab_content: list[tuple[str, list[PcbLine], list[PcbCircle], list[PcbArc]]] = []
        for fp in board.footprints:
            fab_lines = [ln for ln in fp.fab_lines if ln.layer == fab_layer]
            fab_circles = [c for c in fp.fab_circles if c.layer == fab_layer]
            fab_arcs = [a for a in fp.fab_arcs if a.layer == fab_layer]
            if fab_lines or fab_circles or fab_arcs:
                fab_content.append((fp.reference, fab_lines, fab_circles, fab_arcs))

        if not fab_content:
            continue

        cls = _layer_class(fab_layer)
        svg.group_start(attrs={"data-layer": fab_layer, "class": cls})
        for ref, fab_lines, fab_circles, fab_arcs in fab_content:
            svg.group_start(attrs={"data-type": "body", "data-component": ref})
            # Try to build filled polygon from fab lines
            poly = _chain_lines_to_polygon(fab_lines)
            if poly:
                svg.polygon(poly, attrs={"class": "body"})
            # Circles
            for circ in fab_circles:
                if circ.fill:
                    svg.circle(circ.cx, circ.cy, circ.radius,
                               attrs={"class": "body-circle-filled"})
                else:
                    svg.circle(circ.cx, circ.cy, circ.radius,
                               attrs={"class": "body-circle",
                                      "stroke-width": f"{max(circ.width, 0.08):.4f}"})
            # Arcs
            for arc in fab_arcs:
                d = _svg_arc_path_d(
                    arc.start_x, arc.start_y,
                    arc.mid_x, arc.mid_y,
                    arc.end_x, arc.end_y,
                )
                svg.raw(
                    f'<path d="{d}" stroke-width="{max(arc.width, 0.08):.4f}" '
                    f'class="body-arc"/>'
                )
            svg.group_end()
        svg.group_end()

    # -- Collect ref designator texts for rendering outside mirror ---------
    active_fab = {"F.Fab"} if side == "front" else {"B.Fab"}
    active_silk = (
        {"F.SilkS", "F.Silkscreen"} if side == "front"
        else {"B.SilkS", "B.Silkscreen"}
    )
    active_text_layers = active_fab | active_silk
    deferred_texts: list[tuple[float, float, str, float, float]] = []
    for fp in board.footprints:
        best_ref_txt = None
        for txt in fp.texts:
            if txt.hidden or txt.layer not in active_text_layers:
                continue
            if txt.kind == "value":
                continue
            if txt.text == fp.reference or txt.kind in ("reference", "user"):
                if best_ref_txt is None or txt.font_size < best_ref_txt.font_size:
                    best_ref_txt = txt
        if best_ref_txt is not None:
            fs = min(best_ref_txt.font_size, 0.8)
            deferred_texts.append((
                best_ref_txt.x, best_ref_txt.y,
                fp.reference, fs, best_ref_txt.rotation,
            ))

    # -- Close content clip group ------------------------------------------
    svg.group_end()

    # -- Highlight boxes (inside mirror, outside clip) ---------------------
    hl_labels: list[tuple[float, float, float, float, str]] = []
    if hl_refs:
        for fp in board.footprints:
            if fp.reference not in hl_refs:
                continue
            bbox = fp.bbox
            if not bbox:
                continue
            mx0, my0, mx1, my1 = bbox
            margin = 0.5
            svg.rect(
                mx0 - margin, my0 - margin,
                (mx1 - mx0) + 2 * margin, (my1 - my0) + 2 * margin,
                attrs={"class": "highlight-box", "data-component": fp.reference},
            )
            hl_labels.append((mx0, my0, mx1, my1, fp.reference))

    # -- Close mirror group ------------------------------------------------
    if side == "back":
        svg.group_end()

    # -- Text labels (outside mirror so they read correctly) ---------------
    for tx, ty, ttext, tsize, trot in deferred_texts:
        if side == "back":
            tx = (bx0 + bx1) - tx
            trot = 180.0 - trot
        svg.text(tx, ty, ttext, tsize, rotation=trot, attrs={
            "class": "ref-text",
            "data-component": ttext,
        })

    # -- Highlight labels (outside mirror) ---------------------------------
    for mx0, my0, mx1, my1, ref in hl_labels:
        margin = 0.5
        label_y = my0 - margin - 0.4
        label_x = (mx0 + mx1) / 2
        if side == "back":
            label_x = (bx0 + bx1) - label_x
        svg.text(label_x, label_y, ref, font_size=1.8, bold=True, attrs={
            "class": "highlight-label",
        })

    svg.raw("</svg>")
    return svg.build()
