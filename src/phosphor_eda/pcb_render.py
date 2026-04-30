"""Render a PcbBoard as layered SVG with CSS theming.

Emits an SVG with layer groups, data-* attributes on every element, and
a <style> block for theming.  Highlights, layer visibility, and colors
are all controlled via CSS — downstream JS can restyle without re-rendering.

No external dependencies — SVG is built via string formatting.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape as xml_escape

from phosphor_eda.pcb import LayerFunction, PcbLayer

if TYPE_CHECKING:
    from phosphor_eda.pcb import (
        PcbArc,
        PcbBoard,
        PcbCircle,
        PcbFootprint,
        PcbLine,
        PcbPad,
        PcbPolygon,
        PcbSegment,
        PcbTraceArc,
    )

# ---------------------------------------------------------------------------
# SVG builder
# ---------------------------------------------------------------------------


def _fmt_attrs(attrs: dict[str, str] | None) -> str:
    """Format a dict of attributes into an SVG attribute string."""
    if not attrs:
        return ""
    return " " + " ".join(f'{k}="{xml_escape(v, {chr(34): "&quot;"})}"' for k, v in attrs.items())


class _Svg:
    """Tiny SVG string builder with data-attribute support."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def raw(self, s: str) -> None:
        self._parts.append(s)

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        stroke_width: float,
        attrs: dict[str, str] | None = None,
    ) -> None:
        self._parts.append(
            f'<line x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" '
            f'stroke-width="{stroke_width:.4f}"{_fmt_attrs(attrs)}/>'
        )

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        attrs: dict[str, str] | None = None,
    ) -> None:
        self._parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{r:.4f}"{_fmt_attrs(attrs)}/>')

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        rx: float = 0,
        attrs: dict[str, str] | None = None,
    ) -> None:
        s = f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}"'
        if rx > 0:
            s += f' rx="{rx:.4f}"'
        s += f"{_fmt_attrs(attrs)}/>"
        self._parts.append(s)

    def polygon(
        self,
        points: list[tuple[float, float]],
        attrs: dict[str, str] | None = None,
    ) -> None:
        pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        self._parts.append(f'<polygon points="{pts}"{_fmt_attrs(attrs)}/>')

    def text(
        self,
        x: float,
        y: float,
        content: str,
        font_size: float,
        attrs: dict[str, str] | None = None,
        bold: bool = False,
        rotation: float = 0.0,
    ) -> None:
        weight = ' font-weight="bold"' if bold else ""
        rot = f' transform="rotate({rotation:.1f} {x:.4f} {y:.4f})"' if rotation else ""
        self._parts.append(
            f'<text x="{x:.4f}" y="{y:.4f}" font-size="{font_size:.2f}" '
            f'text-anchor="middle" '
            f'dominant-baseline="central" font-family="sans-serif"'
            f"{weight}{rot}{_fmt_attrs(attrs)}>"
            f"{xml_escape(content)}</text>"
        )

    def group_start(
        self,
        transform: str | None = None,
        attrs: dict[str, str] | None = None,
    ) -> None:
        s = "<g"
        if transform:
            s += f' transform="{transform}"'
        s += f"{_fmt_attrs(attrs)}>"
        self._parts.append(s)

    def group_end(self) -> None:
        self._parts.append("</g>")

    def build(self) -> str:
        return "\n".join(self._parts)


# ---------------------------------------------------------------------------
# Arc math — compute SVG arc from three points
# ---------------------------------------------------------------------------


def _circumcircle(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
) -> tuple[float, float, float]:
    """Return (cx, cy, r) for the circle through three points."""
    ax, ay = x1, y1
    bx, by = x2, y2
    cx, cy = x3, y3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        return ((x1 + x3) / 2, (y1 + y3) / 2, 1e6)
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def _arc_svg_params(
    sx: float,
    sy: float,
    mx: float,
    my: float,
    ex: float,
    ey: float,
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
    sx: float,
    sy: float,
    mx: float,
    my: float,
    ex: float,
    ey: float,
) -> str:
    """Return an SVG path `d` attribute for a three-point arc."""
    r, large_arc, sweep = _arc_svg_params(sx, sy, mx, my, ex, ey)
    if r > 1e5:
        return f"M {sx:.4f} {sy:.4f} L {ex:.4f} {ey:.4f}"
    return f"M {sx:.4f} {sy:.4f} A {r:.4f} {r:.4f} 0 {large_arc} {sweep} {ex:.4f} {ey:.4f}"


# ---------------------------------------------------------------------------
# Outline clip path builder
# ---------------------------------------------------------------------------


def _build_outline_clip_path(
    lines: list[PcbLine],
    arcs: list[PcbArc],
) -> str | None:
    """Build an SVG path `d` attribute from board outline geometry.

    Chains lines and arcs into a closed path suitable for a <clipPath>.
    Returns None if the outline can't be chained into a closed loop.
    """
    if not lines and not arcs:
        return None

    EPS = 0.05
    segments: list[tuple[tuple[float, float], tuple[float, float], str]] = []

    for ln in lines:
        s = (ln.start_x, ln.start_y)
        e = (ln.end_x, ln.end_y)
        segments.append((s, e, f"L {e[0]:.4f} {e[1]:.4f}"))

    for arc in arcs:
        s = (arc.start_x, arc.start_y)
        e = (arc.end_x, arc.end_y)
        r, large_arc, sweep = _arc_svg_params(
            arc.start_x,
            arc.start_y,
            arc.mid_x,
            arc.mid_y,
            arc.end_x,
            arc.end_y,
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
        sweep = 1 - sw
        ex, ey = new_end
        return f"A {parts[1]} {parts[2]} {parts[3]} {parts[4]} {sweep} {ex:.4f} {ey:.4f}"

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
            if next_pt is None or next_cmd is None:
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
        key = (round(ln.start_x, 2), round(ln.start_y, 2), round(ln.end_x, 2), round(ln.end_y, 2))
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

# Regex that replaces non-alphanumeric, non-hyphen characters with hyphens.
_CSS_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9-]")


def _layer_class(layer: str) -> str:
    """Sanitize a layer name for use as a CSS class.

    ``"F.Cu"`` → ``"layer-F-Cu"``; ``"Top Layer"`` → ``"layer-Top-Layer"``.
    """
    return "layer-" + _CSS_SANITIZE_RE.sub("-", layer)


def _net_name(board: PcbBoard, net_num: int) -> str:
    """Look up a net name, returning '' for unknown nets."""
    net = board.nets.get(net_num)
    return net.name if net else ""


def _pad_copper_layer(pad: PcbPad, fp_layer: str, layer_lookup: dict[str, PcbLayer]) -> str:
    """Determine which copper layer a pad belongs to for rendering.

    Through-hole pads (``*.Cu``) are placed in the footprint's primary
    layer.  SMD pads are placed in whichever copper layer they specify.
    """
    for ly in pad.layers:
        if ly == "*.Cu":
            return fp_layer
        info = layer_lookup.get(ly)
        if info and info.function == LayerFunction.COPPER:
            return ly
    return fp_layer


def _copper_paint_order(layer: PcbLayer) -> int:
    """Sort key for copper layers: back → inner → front."""
    if layer.side == "back":
        return 0
    if layer.side == "front":
        return 10000
    # Inner layers sort by number (or name as fallback)
    return layer.number if layer.number is not None else 5000


# ---------------------------------------------------------------------------
# CSS theme
# ---------------------------------------------------------------------------

# Colors assigned by layer function + side, not by layer name.
_COPPER_COLOR_FRONT = "#c83434"
_COPPER_COLOR_BACK = "#4d7fc4"
_COPPER_COLOR_INNER = [
    "#7fc87f",
    "#ce7d2c",
    "#4fcbcb",
    "#db628b",
    "#c8c83e",
    "#a18d3e",
    "#3ec8c8",
    "#c83ec8",
]
_SILK_COLOR_FRONT = "#f2eda1"
_SILK_COLOR_BACK = "#e8b2a7"
_FAB_COLOR_FRONT = "#afafaf"
_FAB_COLOR_BACK = "#585d84"
_EDGE_COLOR = "#d0d2cd"
_VIA_COLOR = "#e3b72e"

_COPPER_FALLBACK = "#b87333"


def _copper_color(layer: PcbLayer, inner_index: int) -> str:
    """Assign a copper color by side and inner-layer position."""
    if layer.side == "front":
        return _COPPER_COLOR_FRONT
    if layer.side == "back":
        return _COPPER_COLOR_BACK
    return _COPPER_COLOR_INNER[inner_index % len(_COPPER_COLOR_INNER)]


def _silk_color(layer: PcbLayer) -> str:
    return _SILK_COLOR_FRONT if layer.side == "front" else _SILK_COLOR_BACK


def _fab_color(layer: PcbLayer) -> str:
    return _FAB_COLOR_FRONT if layer.side == "front" else _FAB_COLOR_BACK


def _design_theme_css(side: str, layers: list[PcbLayer]) -> str:
    """Return the design-mode CSS theme for the SVG.

    Design mode shows the board as an EDA editor would: dark background,
    board outline visible, no solder mask fill, distinct colors per layer.
    """
    copper = [lyr for lyr in layers if lyr.function == LayerFunction.COPPER]
    silk = [lyr for lyr in layers if lyr.function == LayerFunction.SILKSCREEN]
    fab = [lyr for lyr in layers if lyr.function == LayerFunction.FAB]
    opposite = "back" if side == "front" else "front"

    rules: list[str] = []
    rules.append("/* Board — design mode uses outline only, no solder mask fill */")
    rules.append(f".board-fill {{ fill: none; stroke: {_EDGE_COLOR}; stroke-width: 0.15; }}")
    rules.append("")

    rules.append("/* Copper layers */")
    inner_idx = 0
    for layer in copper:
        cls = _layer_class(layer.name)
        color = _copper_color(layer, inner_idx)
        if not layer.side:
            inner_idx += 1
        rules.append(f"g.{cls} .trace {{ stroke: {color}; stroke-linecap: round; fill: none; }}")
        rules.append(
            f"g.{cls} .trace-arc {{ stroke: {color}; stroke-linecap: round; fill: none; }}"
        )
        rules.append(f"g.{cls} .pad {{ fill: {color}; }}")
        rules.append(f"g.{cls} .zone {{ fill: {color}; opacity: 0.35; }}")

    rules.append("")
    rules.append("/* Vias */")
    rules.append(f".via .annular {{ fill: {_VIA_COLOR}; }}")
    rules.append(".via .drill { fill: #111111; }")

    rules.append("")
    rules.append("/* Silkscreen */")
    for layer in silk:
        cls = _layer_class(layer.name)
        color = _silk_color(layer)
        rules.append(f"g.{cls} .silk {{ stroke: {color}; stroke-linecap: round; fill: none; }}")

    rules.append("")
    rules.append("/* Fab / component bodies */")
    for layer in fab:
        cls = _layer_class(layer.name)
        color = _fab_color(layer)
        rules.append(
            f"g.{cls} .body {{ fill: none; stroke: {color};"
            f" stroke-width: 0.1; stroke-linejoin: round; }}"
        )
        rules.append(f"g.{cls} .body-circle {{ fill: none; stroke: {color}; }}")
        rules.append(f"g.{cls} .body-circle-filled {{ fill: {color}; }}")
        rules.append(f"g.{cls} .body-arc {{ fill: none; stroke: {color}; stroke-linecap: round; }}")
        rules.append(f"g.{cls} .ref-text {{ fill: {color}; }}")

    rules.append("")
    rules.append("/* Ref text (outside layer groups) */")
    rules.append(f".ref-text {{ fill: {_FAB_COLOR_FRONT}; }}")

    # Design mode: hide opposite-side silk + all fab layers
    rules.append("")
    rules.append("/* Side visibility — design mode hides opposite silk + all fab */")
    for layer in silk:
        if layer.side == opposite:
            rules.append(f"g.{_layer_class(layer.name)} {{ display: none; }}")
    for layer in fab:
        rules.append(f"g.{_layer_class(layer.name)} {{ display: none; }}")

    return "\n".join(rules)


_SOLDER_MASK_GREEN = "#1a5c2a"
_BODY_FILL = "#3d3530"
_BODY_STROKE = "#5a504a"


def _review_theme_css(side: str, layers: list[PcbLayer]) -> str:
    """Return the review-mode CSS theme for the SVG.

    Realistic top-down view: green solder mask over copper, exposed pads
    in copper color, white silkscreen, dark component bodies.
    """
    copper = [lyr for lyr in layers if lyr.function == LayerFunction.COPPER]
    silk = [lyr for lyr in layers if lyr.function == LayerFunction.SILKSCREEN]
    fab = [lyr for lyr in layers if lyr.function == LayerFunction.FAB]
    opposite = "back" if side == "front" else "front"

    rules: list[str] = []

    rules.append("/* Board — green solder mask */")
    rules.append(f".board-fill {{ fill: {_SOLDER_MASK_GREEN}; stroke: none; }}")
    rules.append("")

    rules.append("/* Copper under solder mask — dimmed */")
    for layer in copper:
        cls = _layer_class(layer.name)
        rules.append(
            f"g.{cls} .trace, g.{cls} .trace-arc "
            f"{{ stroke: #145222; stroke-linecap: round; fill: none; opacity: 0.6; }}"
        )
        rules.append(f"g.{cls} .pad {{ fill: #b87333; }}")
        rules.append(f"g.{cls} .zone {{ fill: #145222; opacity: 0.3; }}")

    rules.append("")
    rules.append("/* Vias */")
    rules.append(".via .annular { fill: #c0c0c0; }")
    rules.append(f".via .drill {{ fill: {_SOLDER_MASK_GREEN}; }}")

    rules.append("")
    rules.append("/* Silkscreen — white */")
    for layer in silk:
        cls = _layer_class(layer.name)
        rules.append(f"g.{cls} .silk {{ stroke: #ffffffcc; stroke-linecap: round; fill: none; }}")

    rules.append("")
    rules.append("/* Component bodies */")
    for layer in fab:
        cls = _layer_class(layer.name)
        rules.append(
            f"g.{cls} .body {{ fill: {_BODY_FILL}; stroke: {_BODY_STROKE}; "
            f"stroke-width: 0.06; stroke-linejoin: round; }}"
        )
        rules.append(f"g.{cls} .body-circle {{ fill: none; stroke: {_BODY_STROKE}; }}")
        rules.append(f"g.{cls} .body-circle-filled {{ fill: {_BODY_STROKE}; }}")
        rules.append(
            f"g.{cls} .body-arc {{ fill: none; stroke: {_BODY_STROKE}; stroke-linecap: round; }}"
        )
    rules.append(".ref-text { fill: #ffffffcc; }")

    # Hide opposite-side copper, inner copper, opposite silk, opposite fab
    rules.append("")
    rules.append("/* Layer visibility — single side + fab */")
    for layer in copper:
        if layer.side == opposite or not layer.side:
            rules.append(f"g.{_layer_class(layer.name)} {{ display: none; }}")
    for layer in silk:
        if layer.side == opposite:
            rules.append(f"g.{_layer_class(layer.name)} {{ display: none; }}")
    for layer in fab:
        if layer.side == opposite:
            rules.append(f"g.{_layer_class(layer.name)} {{ display: none; }}")

    return "\n".join(rules)


def _clean_theme_css(side: str, layers: list[PcbLayer]) -> str:
    """Return the clean/documentation CSS theme for the SVG.

    Documentation view: green board, no copper/vias/silkscreen, hides
    passive components (R/C/L/TP), shows only ICs and connectors with
    ref labels.
    """
    copper = [lyr for lyr in layers if lyr.function == LayerFunction.COPPER]
    silk = [lyr for lyr in layers if lyr.function == LayerFunction.SILKSCREEN]
    fab = [lyr for lyr in layers if lyr.function == LayerFunction.FAB]
    opposite = "back" if side == "front" else "front"

    rules: list[str] = []

    rules.append("/* Board — green solder mask */")
    rules.append(f".board-fill {{ fill: {_SOLDER_MASK_GREEN}; stroke: none; }}")
    rules.append("")

    rules.append("/* Hide copper, vias, silkscreen */")
    for layer in copper:
        rules.append(f"g.{_layer_class(layer.name)} {{ display: none; }}")
    rules.append("g.layer-vias { display: none; }")
    for layer in silk:
        rules.append(f"g.{_layer_class(layer.name)} {{ display: none; }}")

    rules.append("")
    rules.append("/* Hide passive component bodies and labels */")
    for prefix in ("R", "C", "L", "TP"):
        rules.append(f'g[data-component^="{prefix}"] {{ display: none; }}')
        rules.append(f'.ref-text[data-component^="{prefix}"] {{ display: none; }}')

    rules.append("")
    rules.append("/* Component bodies — dark */")
    for layer in fab:
        cls = _layer_class(layer.name)
        rules.append(
            f"g.{cls} .body {{ fill: {_BODY_FILL}; stroke: {_BODY_STROKE}; "
            f"stroke-width: 0.06; stroke-linejoin: round; }}"
        )
    rules.append(".ref-text { fill: #ffffffee; }")

    # Hide opposite-side fab
    rules.append("")
    rules.append("/* Layer visibility — only same-side fab */")
    for layer in fab:
        if layer.side == opposite:
            rules.append(f"g.{_layer_class(layer.name)} {{ display: none; }}")

    return "\n".join(rules)


_ThemeFn = Callable[[str, list[PcbLayer]], str]

_THEME_CSS_FN: dict[str, _ThemeFn] = {
    "design": _design_theme_css,
    "review": _review_theme_css,
    "clean": _clean_theme_css,
}

THEME_NAMES: list[str] = list(_THEME_CSS_FN.keys())


def _theme_css(theme: str, side: str, layers: list[PcbLayer]) -> str:
    """Dispatch to the appropriate theme CSS function."""
    fn = _THEME_CSS_FN.get(theme, _design_theme_css)
    return fn(side, layers)


def _highlight_css(
    hl_net_nums: set[int],
    hl_refs: set[str],
    copper_layers: list[PcbLayer],
) -> str:
    """Return CSS that dims non-highlighted elements and brightens highlighted.

    Net highlights (``-n``) restore traces, pads, and vias on matching nets.
    Component highlights (``-c``) restore pads, bodies, and ref text for
    matching components.  The two are independent — specify both to see
    a component *and* its connected traces.
    """
    rules: list[str] = []
    rules.append("/* Dim non-highlighted elements */")
    rules.append("g[data-layer] .trace, g[data-layer] .trace-arc { opacity: 0.12; }")
    rules.append("g[data-layer] .pad { opacity: 0.2; }")
    rules.append("g[data-layer] .zone { opacity: 0.08; }")
    rules.append("g.layer-vias .via { opacity: 0.15; }")
    rules.append("g[data-layer] .silk { opacity: 0.3; }")
    rules.append(
        "g[data-layer] .body, g[data-layer] .body-circle, "
        "g[data-layer] .body-circle-filled, g[data-layer] .body-arc "
        "{ opacity: 0.3; }"
    )
    rules.append(".ref-text { opacity: 0.3; }")

    # -- Restore highlighted nets (traces, pads, vias) -------------------------
    if hl_net_nums:
        rules.append("")
        rules.append("/* Restore highlighted nets */")
        nn_sel = ", ".join(f'[data-net-number="{nn}"]' for nn in sorted(hl_net_nums))
        rules.append(f"{nn_sel} {{ opacity: 1 !important; }}")
        # Keep highlighted zones less dominant so they don't flood the view
        zone_sel = ", ".join(f'.zone[data-net-number="{nn}"]' for nn in sorted(hl_net_nums))
        rules.append(f"{zone_sel} {{ opacity: 0.25 !important; }}")
        rules.append("")
        rules.append("/* Restore vibrant copper colors for highlighted traces and pads */")
        inner_idx = 0
        for layer in copper_layers:
            color = _copper_color(layer, inner_idx)
            if not layer.side:
                inner_idx += 1
            cls = _layer_class(layer.name)
            trace_sel = ", ".join(
                f'g.{cls} .trace[data-net-number="{nn}"], '
                f'g.{cls} .trace-arc[data-net-number="{nn}"]'
                for nn in sorted(hl_net_nums)
            )
            rules.append(f"{trace_sel} {{ stroke: {color} !important; }}")
            pad_sel = ", ".join(
                f'g.{cls} .pad[data-net-number="{nn}"]' for nn in sorted(hl_net_nums)
            )
            rules.append(f"{pad_sel} {{ fill: {color} !important; }}")

    # -- Restore highlighted components (pads, bodies, ref text) ---------------
    if hl_refs:
        rules.append("")
        rules.append("/* Restore highlighted components */")
        ref_sel = ", ".join(f'[data-component="{ref}"]' for ref in sorted(hl_refs))
        rules.append(f"{ref_sel} {{ opacity: 1 !important; }}")

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


def _body_group_attrs(fp: PcbFootprint) -> dict[str, str]:
    """Build attributes for a component body <g>, including model metadata."""
    attrs: dict[str, str] = {"data-type": "body", "data-component": fp.reference}
    cached_models = [m for m in fp.models_3d if m.cache_key]
    if cached_models:
        models_json = json.dumps(
            [
                {
                    "key": m.cache_key,
                    "offset": list(m.offset),
                    "rotation": list(m.rotation),
                    "scale": list(m.scale),
                }
                for m in cached_models
            ],
            separators=(",", ":"),
        )
        attrs["data-models"] = models_json
    return attrs


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
    theme: str = "design",
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
        CSS theme: "design" (EDA view), "review" (realistic), or
        "clean" (documentation — hides copper/passives).
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

    has_hl = bool(hl_net_nums) or bool(hl_refs)

    # -- Layer lookup from board definitions --------------------------------
    layer_lookup: dict[str, PcbLayer] = {lyr.name: lyr for lyr in board.layers}
    all_copper = sorted(
        [lyr for lyr in board.layers if lyr.function == LayerFunction.COPPER],
        key=_copper_paint_order,
    )
    silk_layer_names = {
        lyr.name for lyr in board.layers if lyr.function == LayerFunction.SILKSCREEN
    }
    fab_layer_defs = [lyr for lyr in board.layers if lyr.function == LayerFunction.FAB]

    # -- Discover which copper layers have content -------------------------
    copper_layers_present: set[str] = set()
    for seg in board.segments:
        copper_layers_present.add(seg.layer)
    for ta in board.trace_arcs:
        copper_layers_present.add(ta.layer)
    for poly in board.polygons:
        info = layer_lookup.get(poly.layer)
        if info and info.function == LayerFunction.COPPER:
            copper_layers_present.add(poly.layer)
    for fp in board.footprints:
        for pad in fp.pads:
            copper_layers_present.add(_pad_copper_layer(pad, fp.layer, layer_lookup))
    # Ordered subset of all_copper that actually has content
    copper_layers = [lyr for lyr in all_copper if lyr.name in copper_layers_present]

    # -- Build indexes for per-layer rendering ----------------------------
    # Segments by layer
    segs_by_layer: dict[str, list[PcbSegment]] = defaultdict(list)
    for seg in board.segments:
        segs_by_layer[seg.layer].append(seg)

    # Trace arcs by layer
    tarcs_by_layer: dict[str, list[PcbTraceArc]] = defaultdict(list)
    for ta in board.trace_arcs:
        tarcs_by_layer[ta.layer].append(ta)

    # Zone polygons by layer (copper only)
    zones_by_layer: dict[str, list[PcbPolygon]] = defaultdict(list)
    for poly in board.polygons:
        info = layer_lookup.get(poly.layer)
        if info and info.function == LayerFunction.COPPER:
            zones_by_layer[poly.layer].append(poly)

    # Pads by copper layer
    pads_by_layer: dict[str, list[tuple[PcbPad, str]]] = defaultdict(list)
    for fp in board.footprints:
        for pad in fp.pads:
            ly = _pad_copper_layer(pad, fp.layer, layer_lookup)
            pads_by_layer[ly].append((pad, fp.reference))

    # Silkscreen lines by layer
    silk_by_layer: dict[str, list[PcbLine]] = defaultdict(list)
    for fp in board.footprints:
        for ln in fp.silkscreen_lines:
            if ln.layer in silk_layer_names:
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
    svg.raw(_theme_css(theme, side, board.layers))
    svg.raw("</style>")

    if has_hl:
        svg.raw('<style id="highlight">')
        svg.raw(_highlight_css(hl_net_nums, hl_refs, copper_layers))
        svg.raw("</style>")

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
        via_layers = set(via.layers)
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
        f"M {bx0:.4f} {by0:.4f} L {bx1:.4f} {by0:.4f} L {bx1:.4f} {by1:.4f} L {bx0:.4f} {by1:.4f} Z"
    )
    svg.raw("<defs>")
    svg.raw(f'<clipPath id="board-clip"><path d="{board_clip_d}"/></clipPath>')
    if holes_d:
        cover_d = (
            f"M {vb_x:.4f} {vb_y:.4f} L {vb_x + vb_w:.4f} {vb_y:.4f} "
            f"L {vb_x + vb_w:.4f} {vb_y + vb_h:.4f} L {vb_x:.4f} {vb_y + vb_h:.4f} Z"
        )
        svg.raw(
            f'<clipPath id="drill-clip" clip-path="url(#board-clip)">'
            f'<path d="{cover_d}{holes_d}" clip-rule="evenodd"/>'
            f"</clipPath>"
        )
        active_clip = "drill-clip"
    else:
        active_clip = "board-clip"
    svg.raw("</defs>")

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
    for cu_layer in copper_layers:
        layer = cu_layer.name
        cls = _layer_class(layer)
        svg.group_start(attrs={"data-layer": layer, "class": cls})

        # Zones
        for poly in zones_by_layer.get(layer, []):
            net_nm = poly.net_name or _net_name(board, poly.net_number)
            svg.polygon(
                poly.points,
                attrs={
                    "class": "zone",
                    "data-type": "zone",
                    "data-net": net_nm,
                    "data-net-number": str(poly.net_number),
                },
            )

        # Traces
        for seg in segs_by_layer.get(layer, []):
            net_nm = _net_name(board, seg.net_number)
            svg.line(
                seg.start_x,
                seg.start_y,
                seg.end_x,
                seg.end_y,
                seg.width,
                attrs={
                    "class": "trace",
                    "data-type": "trace",
                    "data-net": net_nm,
                    "data-net-number": str(seg.net_number),
                },
            )

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
            _draw_pad(
                svg,
                pad,
                {
                    "class": "pad",
                    "data-type": "pad",
                    "data-component": fp_ref,
                    "data-pad": pad.number,
                    "data-net": net_nm,
                    "data-net-number": str(pad.net_number),
                },
            )

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
            attrs: dict[str, str] = {"class": "silk"}
            if ln.footprint_ref:
                attrs["data-component"] = ln.footprint_ref
            svg.line(
                ln.start_x,
                ln.start_y,
                ln.end_x,
                ln.end_y,
                max(ln.width, 0.1),
                attrs=attrs,
            )
        svg.group_end()

    # -- Fab layer groups (component bodies) -------------------------------
    # Paint order: back fab first, then front fab (same as copper).
    fab_ordered = sorted(fab_layer_defs, key=lambda lyr: 0 if lyr.side == "back" else 1)

    # Track which footprints have been emitted in a body group so we can
    # emit model-only groups for footprints with 3D models but no fab geometry.
    emitted_refs: set[str] = set()

    for fab_def in fab_ordered:
        fab_layer = fab_def.name
        fab_content: list[tuple[PcbFootprint, list[PcbLine], list[PcbCircle], list[PcbArc]]] = []
        for fp in board.footprints:
            fab_lines = [ln for ln in fp.fab_lines if ln.layer == fab_layer]
            fab_circles = [c for c in fp.fab_circles if c.layer == fab_layer]
            fab_arcs = [a for a in fp.fab_arcs if a.layer == fab_layer]
            if fab_lines or fab_circles or fab_arcs:
                fab_content.append((fp, fab_lines, fab_circles, fab_arcs))

        if not fab_content:
            continue

        cls = _layer_class(fab_layer)
        svg.group_start(attrs={"data-layer": fab_layer, "class": cls})
        for fp, fab_lines, fab_circles, fab_arcs in fab_content:
            body_attrs = _body_group_attrs(fp)
            svg.group_start(attrs=body_attrs)
            emitted_refs.add(fp.reference)
            ref = fp.reference
            # Try to build filled polygon from fab lines
            poly = _chain_lines_to_polygon(fab_lines)
            if poly:
                svg.polygon(poly, attrs={"class": "body", "data-component": ref})
            # Circles
            for circ in fab_circles:
                if circ.fill:
                    svg.circle(
                        circ.cx,
                        circ.cy,
                        circ.radius,
                        attrs={"class": "body-circle-filled", "data-component": ref},
                    )
                else:
                    svg.circle(
                        circ.cx,
                        circ.cy,
                        circ.radius,
                        attrs={
                            "class": "body-circle",
                            "data-component": ref,
                            "stroke-width": f"{max(circ.width, 0.08):.4f}",
                        },
                    )
            # Arcs
            for arc in fab_arcs:
                d = _svg_arc_path_d(
                    arc.start_x,
                    arc.start_y,
                    arc.mid_x,
                    arc.mid_y,
                    arc.end_x,
                    arc.end_y,
                )
                svg.raw(
                    f'<path d="{d}" stroke-width="{max(arc.width, 0.08):.4f}"'
                    f' class="body-arc" data-component="{xml_escape(ref)}"/>'
                )
            svg.group_end()
        svg.group_end()

    # Emit empty body groups for footprints that have 3D models but no fab
    # geometry. The renderer needs these to place 3D models.
    model_only_fps = [
        fp
        for fp in board.footprints
        if fp.reference not in emitted_refs and any(m.cache_key for m in fp.models_3d)
    ]
    if model_only_fps:
        svg.group_start(attrs={"data-layer": "models", "class": "models"})
        for fp in model_only_fps:
            svg.group_start(attrs=_body_group_attrs(fp))
            svg.group_end()
        svg.group_end()

    # -- Collect ref designator texts for rendering outside mirror ---------
    active_side = "front" if side == "front" else "back"
    active_fab = {lyr.name for lyr in fab_layer_defs if lyr.side == active_side}
    active_silk = {
        lyr.name
        for lyr in board.layers
        if lyr.function == LayerFunction.SILKSCREEN and lyr.side == active_side
    }
    active_text_layers = active_fab | active_silk
    deferred_texts: list[tuple[float, float, str, float, float]] = []
    for fp in board.footprints:
        best_ref_txt = None
        for txt in fp.texts:
            if txt.hidden or txt.layer not in active_text_layers:
                continue
            if txt.kind == "value":
                continue
            if (txt.text == fp.reference or txt.kind in ("reference", "user")) and (
                best_ref_txt is None or txt.font_size < best_ref_txt.font_size
            ):
                best_ref_txt = txt
        if best_ref_txt is not None:
            fs = min(best_ref_txt.font_size, 0.8)
            deferred_texts.append(
                (
                    best_ref_txt.x,
                    best_ref_txt.y,
                    fp.reference,
                    fs,
                    best_ref_txt.rotation,
                )
            )

    # -- Close content clip group ------------------------------------------
    svg.group_end()

    # -- Close mirror group ------------------------------------------------
    if side == "back":
        svg.group_end()

    # -- Text labels (outside mirror so they read correctly) ---------------
    for tx, ty, ttext, tsize, trot in deferred_texts:
        if side == "back":
            tx = (bx0 + bx1) - tx
            trot = 180.0 - trot
        svg.text(
            tx,
            ty,
            ttext,
            tsize,
            rotation=trot,
            attrs={
                "class": "ref-text",
                "data-component": ttext,
            },
        )

    svg.raw("</svg>")
    return svg.build()
