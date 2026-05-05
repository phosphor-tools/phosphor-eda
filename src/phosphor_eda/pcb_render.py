"""Render a Pcb as layered SVG with CSS theming.

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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeGuard
from xml.sax.saxutils import escape as xml_escape

from phosphor_eda.pcb import LayerFunction, PcbLayer
from phosphor_eda.text_metrics import BASELINE_CENTER_OFFSET, INTER_REGULAR_BASE64

if TYPE_CHECKING:
    from phosphor_eda.pcb import (
        Pcb,
        PcbArc,
        PcbCircle,
        PcbFootprint,
        PcbLine,
        PcbPad,
        PcbPolygon,
        PcbSegment,
        PcbTraceArc,
    )
    from phosphor_eda.pcb_annotations import (
        ResolvedAnnotations,
        ResolvedBox,
        ResolvedLabel,
        ResolvedLegend,
        ResolvedPointer,
    )

# ---------------------------------------------------------------------------
# Render settings
# ---------------------------------------------------------------------------


@dataclass
class HighlightSpec:
    """A single net or component to highlight, with an optional color."""

    net: str = ""
    component: str = ""
    color: str = ""


@dataclass
class RenderSettings:
    """Unified render configuration — theme, highlights, annotations, CSS.

    Parsed from the ``--render-settings`` JSON file.  All fields are optional;
    omitted fields fall back to CLI defaults.
    """

    theme: str = ""
    side: str = ""
    width: int = 0
    highlights: list[HighlightSpec] = field(default_factory=list)
    annotations: dict[str, Any] = field(default_factory=dict)
    custom_css: str = ""


def is_json_dict(v: object) -> TypeGuard[dict[str, object]]:
    """Narrow an object to ``dict[str, object]``.

    ``isinstance(x, dict)`` narrows to ``dict[Unknown, Unknown]`` in
    basedpyright strict.  This TypeGuard gives a properly-typed dict.
    """
    return isinstance(v, dict)


def is_json_list(v: object) -> TypeGuard[list[object]]:
    """Narrow an object to ``list[object]`` (see ``is_json_dict``)."""
    return isinstance(v, list)


def parse_render_settings(data: dict[str, Any]) -> RenderSettings:
    """Parse a render-settings JSON dict into a ``RenderSettings`` object.

    Raises ``ValueError`` on invalid input.
    """
    settings = RenderSettings()

    if "theme" in data:
        theme = data["theme"]
        if not isinstance(theme, str) or theme not in ("design", "review", "clean"):
            msg = f"theme must be 'design', 'review', or 'clean', got {theme!r}"
            raise ValueError(msg)
        settings.theme = theme

    if "side" in data:
        side = data["side"]
        if not isinstance(side, str) or side not in ("front", "back"):
            msg = f"side must be 'front' or 'back', got {side!r}"
            raise ValueError(msg)
        settings.side = side

    if "width" in data:
        width = data["width"]
        if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
            msg = f"width must be a positive integer, got {width!r}"
            raise ValueError(msg)
        settings.width = width

    if "highlights" in data:
        raw_highlights = data["highlights"]
        if not is_json_list(raw_highlights):
            msg = "highlights must be an array"
            raise ValueError(msg)
        for i, item in enumerate(raw_highlights):
            if not is_json_dict(item):
                msg = f"highlights[{i}] must be an object"
                raise ValueError(msg)
            # Validate field types before extracting
            for field in ("net", "component", "color"):
                if field in item and not isinstance(item[field], str):
                    msg = f"highlights[{i}].{field} must be a string"
                    raise ValueError(msg)
            net = str(item.get("net", ""))
            component = str(item.get("component", ""))
            has_net = bool(net)
            has_comp = bool(component)
            if not has_net and not has_comp:
                msg = f"highlights[{i}] must have 'net' or 'component'"
                raise ValueError(msg)
            if has_net and has_comp:
                msg = f"highlights[{i}] cannot have both 'net' and 'component'"
                raise ValueError(msg)
            color = str(item.get("color", ""))
            settings.highlights.append(HighlightSpec(net=net, component=component, color=color))

    if "annotations" in data:
        ann = data["annotations"]
        if not isinstance(ann, dict):
            msg = "annotations must be an object"
            raise ValueError(msg)
        settings.annotations = ann

    if "custom_css" in data:
        css = data["custom_css"]
        if not isinstance(css, str):
            msg = "custom_css must be a string"
            raise ValueError(msg)
        settings.custom_css = css

    return settings


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

    def path(self, d: str, attrs: dict[str, str] | None = None) -> None:
        self._parts.append(f'<path d="{d}"{_fmt_attrs(attrs)}/>')

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
    """Compute (radius, large_arc_flag, sweep_flag) for an SVG arc command.

    Derives sweep direction and arc size from the midpoint — the arc from
    start to end that passes through mid.  SVG's ``sweep-flag=1`` means
    the arc advances in the positive-angle (clockwise in screen coords)
    direction.
    """
    ccx, ccy, r = _circumcircle(sx, sy, mx, my, ex, ey)
    if r > 1e5:
        return (r, 0, 0)

    a_s = math.atan2(sy - ccy, sx - ccx)
    a_m = math.atan2(my - ccy, mx - ccx)
    a_e = math.atan2(ey - ccy, ex - ccx)

    # Compute the angular travel from start→mid and start→end going in
    # the positive-angle (CW in screen coords) direction.
    def _pos(a: float, ref: float) -> float:
        """Angle from *ref* to *a* in [0, 2π) going positive."""
        d = a - ref
        return d % (2 * math.pi)

    cw_to_mid = _pos(a_m, a_s)
    cw_to_end = _pos(a_e, a_s)

    # The arc passes through mid.  If the CW distance to mid is less
    # than the CW distance to end, the arc goes CW (sweep=1).
    # Otherwise it goes CCW (sweep=0).
    if cw_to_mid < cw_to_end:
        # CW path: start → mid → end, total span = cw_to_end
        sweep = 1
        span = cw_to_end
    else:
        # CCW path: start → mid → end, total span = 2π - cw_to_end
        sweep = 0
        span = 2 * math.pi - cw_to_end

    large_arc = 1 if span > math.pi else 0
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


def _net_name(board: Pcb, net_num: int) -> str:
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
# Class helpers — O(1) class selectors replace O(n) attribute selectors
# ---------------------------------------------------------------------------

_PASSIVE_PREFIXES = ("R", "C", "L", "TP")


def _nn_class(net_number: int) -> str:
    """CSS class for a net number: nn-{number}."""
    return f"nn-{net_number}"


_CSS_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _css_safe(s: str) -> str:
    """Replace chars that aren't valid in CSS class names with _XX hex."""
    return _CSS_SAFE_RE.sub(lambda m: f"_{ord(m.group()):02x}", s)


def _cmp_class(ref: str) -> str:
    """CSS class for a component reference: cmp-{sanitized_ref}."""
    return f"cmp-{_css_safe(ref)}"


def _pfx_class(ref: str) -> str | None:
    """CSS class for component type prefix, or None for non-passive refs."""
    for prefix in _PASSIVE_PREFIXES:
        if ref.startswith(prefix):
            return f"pfx-{prefix}"
    return None


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
    for prefix in _PASSIVE_PREFIXES:
        pfx = f"pfx-{prefix}"
        rules.append(f"g.{pfx} {{ display: none; }}")
        rules.append(f".ref-text.{pfx} {{ display: none; }}")

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


def _theme_hidden_layer_classes(theme: str, side: str, layers: list[PcbLayer]) -> set[str]:
    """Return CSS class names for groups hidden by the theme via ``display: none``.

    Includes layer groups, passive-prefix groups, and any other elements
    the theme hides.  The highlight CSS emits ``display: inline !important``
    for each returned class to make highlighted content visible.
    """
    opposite = "back" if side == "front" else "front"
    copper = [lyr for lyr in layers if lyr.function == LayerFunction.COPPER]
    silk = [lyr for lyr in layers if lyr.function == LayerFunction.SILKSCREEN]
    fab = [lyr for lyr in layers if lyr.function == LayerFunction.FAB]

    if theme == "review":
        hidden: set[str] = set()
        for lyr in copper:
            if lyr.side == opposite or not lyr.side:
                hidden.add(_layer_class(lyr.name))
        for lyr in silk + fab:
            if lyr.side == opposite:
                hidden.add(_layer_class(lyr.name))
        return hidden
    if theme == "clean":
        hidden = {_layer_class(lyr.name) for lyr in copper}
        hidden.add("layer-vias")
        for lyr in silk:
            hidden.add(_layer_class(lyr.name))
        for lyr in fab:
            if lyr.side == opposite:
                hidden.add(_layer_class(lyr.name))
        # Passive body groups and ref-text are hidden via pfx-* classes
        for prefix in _PASSIVE_PREFIXES:
            hidden.add(f"pfx-{prefix}")
        return hidden
    if theme == "design":
        hidden = set[str]()
        for lyr in silk:
            if lyr.side == opposite:
                hidden.add(_layer_class(lyr.name))
        for lyr in fab:
            hidden.add(_layer_class(lyr.name))
        return hidden
    return set()


def _highlight_css(
    hl_net_nums: set[int],
    hl_refs: set[str],
    copper_layers: list[PcbLayer],
    hidden_layer_classes: set[str] | None = None,
    net_colors: dict[int, str] | None = None,
    component_colors: dict[str, str] | None = None,
) -> str:
    """Return CSS that dims non-highlighted elements and brightens highlighted.

    Net highlights restore traces, pads, and vias on matching nets.
    Component highlights restore pads, bodies, and ref text for
    matching components.  The two are independent — specify both to see
    a component *and* its connected traces.

    ``hidden_layer_classes`` lists CSS class names for layer ``<g>`` groups
    that the theme hides with ``display: none``.  The highlight block must
    override that to make highlighted content visible — a child's
    ``opacity: 1 !important`` has no effect when the parent is hidden.

    ``net_colors`` maps net numbers to CSS colors.  Nets with an entry get
    that color for traces and pads; nets without fall back to the layer's
    default copper color.

    ``component_colors`` maps component refs to CSS colors.  Components with
    an entry get that color on pads and body elements.
    """
    rules: list[str] = []
    _net_colors = net_colors or {}
    _component_colors = component_colors or {}

    # -- Restore visibility on groups hidden by the theme ----------------------
    if hidden_layer_classes:
        rules.append("/* Restore visibility on theme-hidden groups */")
        for cls in sorted(hidden_layer_classes):
            rules.append(f"g.{cls} {{ display: inline !important; }}")
            # Passive-prefix classes also hide ref-text elements
            if cls.startswith("pfx-"):
                rules.append(f".ref-text.{cls} {{ display: inline !important; }}")
        rules.append("")

    rules.append("/* Dim non-highlighted elements */")
    rules.append("g.lyr .trace, g.lyr .trace-arc { stroke-opacity: 0.12; fill-opacity: 0.12; }")
    rules.append("g.lyr .pad { stroke-opacity: 0.2; fill-opacity: 0.2; }")
    rules.append("g.lyr .zone { stroke-opacity: 0.08; fill-opacity: 0.08; }")
    rules.append("g.layer-vias .via { stroke-opacity: 0.15; fill-opacity: 0.15; }")
    rules.append("g.lyr .silk { stroke-opacity: 0.3; fill-opacity: 0.3; }")
    rules.append(
        "g.lyr .body, g.lyr .body-circle, g.lyr .body-circle-filled, g.lyr .body-arc"
        " { stroke-opacity: 0.3; fill-opacity: 0.3; }"
    )
    rules.append(".ref-text { stroke-opacity: 0.3; fill-opacity: 0.3; }")

    # -- Restore highlighted nets (traces, pads, vias) -------------------------
    if hl_net_nums:
        rules.append("")
        rules.append("/* Restore highlighted nets */")
        nn_sel = ", ".join(f".{_nn_class(nn)}" for nn in sorted(hl_net_nums))
        rules.append(f"{nn_sel} {{ stroke-opacity: 1 !important; fill-opacity: 1 !important; }}")
        # Keep highlighted zones less dominant so they don't flood the view
        zone_sel = ", ".join(f".zone.{_nn_class(nn)}" for nn in sorted(hl_net_nums))
        rules.append(
            f"{zone_sel} {{ stroke-opacity: 0.25 !important; fill-opacity: 0.25 !important; }}"
        )

        # Split into nets with explicit colors vs those using layer defaults
        colored_nets = {nn for nn in hl_net_nums if nn in _net_colors}
        default_nets = hl_net_nums - colored_nets

        # Nets with explicit colors — same color regardless of copper layer
        if colored_nets:
            rules.append("")
            rules.append("/* Per-net highlight colors */")
            for nn in sorted(colored_nets):
                color = _net_colors[nn]
                nn_cls = _nn_class(nn)
                rules.append(
                    f".trace.{nn_cls}, .trace-arc.{nn_cls} {{ stroke: {color} !important; }}"
                )
                rules.append(f".pad.{nn_cls} {{ fill: {color} !important; }}")

        # Nets without colors — restore per-layer copper colors
        if default_nets:
            rules.append("")
            rules.append("/* Restore vibrant copper colors for highlighted traces and pads */")
            inner_idx = 0
            for layer in copper_layers:
                color = _copper_color(layer, inner_idx)
                if not layer.side:
                    inner_idx += 1
                cls = _layer_class(layer.name)
                trace_sel = ", ".join(
                    f"g.{cls} .trace.{_nn_class(nn)}, g.{cls} .trace-arc.{_nn_class(nn)}"
                    for nn in sorted(default_nets)
                )
                rules.append(f"{trace_sel} {{ stroke: {color} !important; }}")
                pad_sel = ", ".join(f"g.{cls} .pad.{_nn_class(nn)}" for nn in sorted(default_nets))
                rules.append(f"{pad_sel} {{ fill: {color} !important; }}")

    # -- Restore highlighted components (pads, bodies, ref text) ---------------
    if hl_refs:
        rules.append("")
        rules.append("/* Restore highlighted components */")
        ref_sel = ", ".join(f".{_cmp_class(ref)}" for ref in sorted(hl_refs))
        rules.append(f"{ref_sel} {{ stroke-opacity: 1 !important; fill-opacity: 1 !important; }}")

        # Per-component colors
        colored_refs = {ref for ref in hl_refs if ref in _component_colors}
        if colored_refs:
            rules.append("")
            rules.append("/* Per-component highlight colors */")
            for ref in sorted(colored_refs):
                color = _component_colors[ref]
                cmp = _cmp_class(ref)
                rules.append(f".pad.{cmp} {{ fill: {color} !important; }}")
                # Stroke for outline-only body geometry
                rules.append(
                    f".body.{cmp}, "
                    f".body-circle.{cmp}, "
                    f".body-arc.{cmp} "
                    f"{{ stroke: {color} !important; }}"
                )
                # Fill for filled body geometry
                rules.append(
                    f".body.{cmp}, .body-circle-filled.{cmp} {{ fill: {color} !important; }}"
                )

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


def _body_group_attrs(
    fp: PcbFootprint,
    component_attrs_fn: Callable[[str], dict[str, str]] | None = None,
) -> dict[str, str]:
    """Build attributes for a component body <g>, including model metadata."""
    if component_attrs_fn:
        base = component_attrs_fn(fp.reference)
    else:
        base = {"data-component": fp.reference}
    attrs: dict[str, str] = {"data-type": "body", **base}
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
# Annotation rendering
# ---------------------------------------------------------------------------


_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Matches any HTML tag (for stripping)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


_ANNOTATION_FONT_FAMILY = "InterEmbed, Inter, system-ui, sans-serif"

# Regex for parsing color strings into (r, g, b) 0–255
_HEX3_RE = re.compile(r"^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$")
_HEX6_RE = re.compile(r"^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})")
_RGBA_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _parse_rgb(color: str) -> tuple[int, int, int]:
    """Extract (r, g, b) from a CSS color string. Returns (255,107,53) as fallback."""
    m = _HEX6_RE.match(color)
    if m:
        return (int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16))
    m = _HEX3_RE.match(color)
    if m:
        return (int(m.group(1), 16) * 17, int(m.group(2), 16) * 17, int(m.group(3), 16) * 17)
    m = _RGBA_RE.match(color)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (255, 107, 53)  # default annotation orange


def _contrast_text_color(bg_color: str) -> str:
    """Return '#000' or '#fff' for best contrast against *bg_color*."""
    r, g, b = _parse_rgb(bg_color)
    # Relative luminance (ITU-R BT.709)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#000" if luminance > 140 else "#fff"


def _annotation_css(font_size: float) -> str:
    """CSS for pure-SVG annotation elements.

    Embeds a subset of Inter-Regular via @font-face so the rendered
    font exactly matches the font used for text measurement.  The
    annotation group has a ``scale()`` transform that maps pixel space
    onto the SVG viewBox, so all sizes here are in display pixels.
    """
    ff = _ANNOTATION_FONT_FAMILY
    return f"""\
@font-face {{ font-family: "InterEmbed"; font-weight: 400;
  src: url("data:font/truetype;base64,{INTER_REGULAR_BASE64}") format("truetype"); }}
.annotation-connector {{ stroke-width: 2; fill: none; }}
.annotation-box {{ stroke-width: 2; }}
.annotation-pill {{ stroke: none; }}
.annotation-pill--muted {{ stroke: none; }}
.annotation-label-text {{ font-family: {ff};
  font-weight: 500; font-size: {font_size:.1f}px; }}
.annotation-dot {{}}
.legend-bg {{ fill: rgba(12,12,20,0.85); stroke: rgba(255,255,255,0.15);
  stroke-width: 4; paint-order: stroke fill; }}
.legend-title-text {{ fill: #f0f0f0; font-family: {ff};
  font-weight: 600; font-size: {font_size * 0.85:.1f}px;
  opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; }}
.legend-entry-text {{ fill: #f0f0f0; font-family: {ff};
  font-weight: 500; font-size: {font_size:.1f}px; }}"""


def _render_annotations(
    svg: _Svg,
    annotations: ResolvedAnnotations,
    font_size: float,
) -> None:
    """Emit all annotation elements as pure SVG.

    The annotation group gets ``transform="scale(px_scale)"`` so that
    coordinates authored in pixel space map correctly onto the board-mm
    viewBox.  All sizes (font, stroke, padding) are in display pixels.
    """
    s = annotations.px_scale
    svg.group_start(
        attrs={"class": "annotations"},
        transform=f"scale({s:.6f})",
    )
    for box in annotations.boxes:
        _render_box(svg, box, font_size)
    for pointer in annotations.pointers:
        _render_pointer(svg, pointer, font_size)
    for label in annotations.labels:
        _render_label(svg, label, font_size)
    if annotations.legend is not None:
        _render_legend(svg, annotations.legend, font_size)
    svg.group_end()


def _connector_path_d(points: list[tuple[float, float]]) -> str:
    """Build an SVG path d attribute from a list of waypoints."""
    if len(points) < 2:
        return ""
    parts = [f"M {points[0][0]:.4f} {points[0][1]:.4f}"]
    for x, y in points[1:]:
        parts.append(f"L {x:.4f} {y:.4f}")
    return " ".join(parts)


def _render_connector(
    svg: _Svg,
    path: list[tuple[float, float]],
    color: str,
    *,
    dot: bool = True,
) -> None:
    """Render an orthogonal connector path with an optional dot at the end."""
    if len(path) < 2:
        return
    d = _connector_path_d(path)
    svg.path(d, attrs={"class": "annotation-connector", "style": f"stroke: {color}"})
    if dot:
        tx, ty = path[-1]
        dot_r = 2.5  # pixels
        svg.circle(tx, ty, dot_r, attrs={"class": "annotation-dot", "style": f"fill: {color}"})


def _split_label_lines(text: str) -> list[str]:
    """Split label text on <br> tags and strip HTML tags."""
    lines = _BR_RE.split(text)
    return [_HTML_TAG_RE.sub("", line) for line in lines]


def _render_pill_label(
    svg: _Svg,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    font_size: float,
    color: str,
    css_class: str = "annotation-pill",
) -> None:
    """Render a pill-shaped label with solid color fill and contrast text."""
    rx = height / 2
    text_color = _contrast_text_color(color)
    svg.rect(
        x,
        y,
        width,
        height,
        rx=rx,
        attrs={"class": css_class, "style": f"fill: {color}"},
    )

    # Render text lines centered in the pill
    lines = _split_label_lines(text)
    line_height = font_size * 1.2
    total_text_h = len(lines) * line_height
    cx = x + width / 2
    center_y = y + height / 2
    start_y = center_y - total_text_h / 2 + line_height / 2 + BASELINE_CENTER_OFFSET * font_size

    for i, line in enumerate(lines):
        ty = start_y + i * line_height
        svg.raw(
            f'<text x="{cx:.4f}" y="{ty:.4f}" text-anchor="middle" '
            f'class="annotation-label-text" fill="{text_color}">'
            f"{xml_escape(line)}</text>"
        )


def _render_box(svg: _Svg, box: ResolvedBox, font_size: float) -> None:
    """Render a solid box with semi-transparent fill and a margin label."""
    r, g, b = _parse_rgb(box.color)
    fill = f"rgba({r},{g},{b},0.15)"
    svg.rect(
        box.x,
        box.y,
        box.width,
        box.height,
        attrs={"class": "annotation-box", "style": f"stroke: {box.color}; fill: {fill}"},
    )
    if box.label_text:
        _render_connector(svg, box.connector_path, box.color, dot=False)
        _render_pill_label(
            svg,
            box.label_x,
            box.label_y,
            box.label_width,
            box.label_height,
            box.label_text,
            font_size,
            color=box.color,
        )


def _render_pointer(svg: _Svg, pointer: ResolvedPointer, font_size: float) -> None:
    """Render a pointer with connector and margin label."""
    if pointer.label_text:
        _render_connector(svg, pointer.connector_path, pointer.color)
        _render_pill_label(
            svg,
            pointer.label_x,
            pointer.label_y,
            pointer.label_width,
            pointer.label_height,
            pointer.label_text,
            font_size,
            color=pointer.color,
        )
    elif pointer.connector_path:
        _render_connector(svg, pointer.connector_path, pointer.color)


def _render_label(svg: _Svg, label: ResolvedLabel, font_size: float) -> None:
    """Render a label with optional connector to its target."""
    if label.connector_path:
        _render_connector(svg, label.connector_path, "rgba(180,180,200,0.5)")
    if label.label_text:
        _render_pill_label(
            svg,
            label.label_x,
            label.label_y,
            label.label_width,
            label.label_height,
            label.label_text,
            font_size,
            color="rgba(60,60,80,0.9)",
            css_class="annotation-pill annotation-pill--muted",
        )


def _render_legend(svg: _Svg, legend: ResolvedLegend, font_size: float) -> None:
    """Render a legend box with color swatches using pure SVG."""
    rx = 5.0  # corner radius in pixels
    svg.rect(
        legend.x,
        legend.y,
        legend.width,
        legend.height,
        rx=rx,
        attrs={"class": "legend-bg"},
    )

    pad_h = font_size * 0.6
    pad_v = font_size * 0.5
    cursor_y = legend.y + pad_v

    # Title
    if legend.title:
        title_fs = font_size * 0.85
        cursor_y += title_fs / 2 + BASELINE_CENTER_OFFSET * title_fs
        svg.raw(
            f'<text x="{legend.x + pad_h:.4f}" y="{cursor_y:.4f}" '
            f'class="legend-title-text">{xml_escape(legend.title)}</text>'
        )
        cursor_y += title_fs * 0.5  # gap after title

    # Entries
    swatch_size = font_size * 0.8
    swatch_gap = font_size * 0.4
    entry_gap = font_size * 0.2
    for i, entry in enumerate(legend.entries):
        if i > 0:
            cursor_y += entry_gap
        if entry.color:
            # Color swatch + label
            swatch_x = legend.x + pad_h
            swatch_y = cursor_y + (font_size - swatch_size) * 0.3
            swatch_rx = swatch_size * 0.2
            svg.rect(
                swatch_x,
                swatch_y,
                swatch_size,
                swatch_size,
                rx=swatch_rx,
                attrs={"style": f"fill: {entry.color}; stroke: none"},
            )
            text_x = swatch_x + swatch_size + swatch_gap
        else:
            # Text-only entry (no swatch)
            text_x = legend.x + pad_h
        text_y = cursor_y + font_size / 2 + BASELINE_CENTER_OFFSET * font_size
        svg.raw(
            f'<text x="{text_x:.4f}" y="{text_y:.4f}" '
            f'class="legend-entry-text">{xml_escape(entry.label)}</text>'
        )
        cursor_y += max(font_size, swatch_size)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_pcb_svg(
    board: Pcb,
    *,
    side: str = "front",
    highlight_nets: list[str] | None = None,
    highlight_components: list[str] | None = None,
    highlight_specs: list[HighlightSpec] | None = None,
    width_px: int = 800,
    theme: str = "review",
    custom_css: str = "",
    annotations: ResolvedAnnotations | None = None,
) -> str:
    """Render a Pcb as a layered SVG string with CSS theming.

    Parameters
    ----------
    board:
        Parsed PCB board.
    side:
        "front" or "back".  Back view mirrors horizontally.
    highlight_nets:
        Net names to highlight (case-insensitive substring match).
    highlight_components:
        Component references to highlight (footprint only, not nets).
    highlight_specs:
        Structured highlights with optional per-net/component colors.
        Merged with ``highlight_nets``/``highlight_components``.
    width_px:
        Pixel width of the SVG.
    theme:
        CSS theme: "design" (EDA view), "review" (realistic), or
        "clean" (documentation — hides copper/passives).
    custom_css:
        Extra CSS injected after the theme and highlight styles.
        Overrides any built-in rule.  Useful for board mask recoloring,
        layer visibility, etc.
    annotations:
        Resolved annotations to overlay on the board.
    """
    # -- Resolve highlights ------------------------------------------------
    hl_net_nums: set[int] = set()
    hl_refs: set[str] = set()
    net_colors: dict[int, str] = {}
    component_colors: dict[str, str] = {}

    if highlight_nets:
        for name in highlight_nets:
            hl_net_nums |= board.net_numbers_by_name(name)

    if highlight_components:
        for ref in highlight_components:
            fp = board.footprint_by_ref(ref)
            if fp:
                hl_refs.add(fp.reference)

    if highlight_specs:
        for spec in highlight_specs:
            if spec.net:
                nums = board.net_numbers_by_name(spec.net)
                hl_net_nums |= nums
                if spec.color:
                    for nn in nums:
                        net_colors[nn] = spec.color
            if spec.component:
                fp = board.footprint_by_ref(spec.component)
                if fp:
                    hl_refs.add(fp.reference)
                    if spec.color:
                        component_colors[fp.reference] = spec.color

    has_hl = bool(hl_net_nums) or bool(hl_refs)

    # -- Component metadata lookup (ref → lib, value) ----------------------
    fp_meta: dict[str, tuple[str, str]] = {}
    for fp in board.footprints:
        fp_meta[fp.reference] = (fp.footprint_lib, fp.value)

    def _component_class_tokens(ref: str) -> str:
        """Return space-separated class tokens for a component ref."""
        pfx = _pfx_class(ref)
        return f"{_cmp_class(ref)} {pfx}" if pfx else _cmp_class(ref)

    def _component_attrs(ref: str) -> dict[str, str]:
        """Build data-component/data-footprint-lib/data-value for a ref."""
        attrs: dict[str, str] = {"data-component": ref}
        lib, val = fp_meta.get(ref, ("", ""))
        if lib:
            attrs["data-footprint-lib"] = lib
        if val:
            attrs["data-value"] = val
        return attrs

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

    # Expand viewBox to include annotation content
    if annotations is not None:
        ax0, ay0, ax1, ay1 = annotations.content_bbox
        if ax0 < vb_x:
            vb_w += vb_x - ax0
            vb_x = ax0
        if ay0 < vb_y:
            vb_h += vb_y - ay0
            vb_y = ay0
        if ax1 > vb_x + vb_w:
            vb_w = ax1 - vb_x
        if ay1 > vb_y + vb_h:
            vb_h = ay1 - vb_y

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
        hidden = _theme_hidden_layer_classes(theme, side, board.layers)
        svg.raw('<style id="highlight">')
        svg.raw(
            _highlight_css(
                hl_net_nums,
                hl_refs,
                copper_layers,
                hidden,
                net_colors=net_colors or None,
                component_colors=component_colors or None,
            )
        )
        svg.raw("</style>")

    if custom_css:
        svg.raw('<style id="custom">')
        svg.raw(custom_css)
        svg.raw("</style>")

    if annotations is not None:
        svg.raw('<style id="annotations">')
        svg.raw(_annotation_css(annotations.font_size))
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
        svg.group_start(attrs={"data-layer": layer, "class": f"{cls} lyr"})

        # Zones
        for poly in zones_by_layer.get(layer, []):
            net_nm = poly.net_name or _net_name(board, poly.net_number)
            svg.polygon(
                poly.points,
                attrs={
                    "class": f"zone {_nn_class(poly.net_number)}",
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
                    "class": f"trace {_nn_class(seg.net_number)}",
                    "data-type": "trace",
                    "data-net": net_nm,
                    "data-net-number": str(seg.net_number),
                },
            )

        # Trace arcs
        for ta in tarcs_by_layer.get(layer, []):
            net_nm = _net_name(board, ta.net_number)
            nn = _nn_class(ta.net_number)
            d = _svg_arc_path_d(ta.start_x, ta.start_y, ta.mid_x, ta.mid_y, ta.end_x, ta.end_y)
            svg.raw(
                f'<path d="{d}" stroke-width="{ta.width:.4f}" '
                f'class="trace-arc {nn}" data-type="trace" '
                f'data-net="{xml_escape(net_nm)}" data-net-number="{ta.net_number}"/>'
            )

        # Pads
        for pad, fp_ref in pads_by_layer.get(layer, []):
            net_nm = pad.net_name or _net_name(board, pad.net_number)
            cmp_tokens = _component_class_tokens(fp_ref)
            pad_attrs = {
                "class": f"pad {_nn_class(pad.net_number)} {cmp_tokens}",
                "data-type": "pad",
                **_component_attrs(fp_ref),
                "data-pad": pad.number,
                "data-net": net_nm,
                "data-net-number": str(pad.net_number),
            }
            _draw_pad(svg, pad, pad_attrs)

        svg.group_end()

    # -- Vias (span layers, get their own group) ---------------------------
    svg.group_start(attrs={"data-layer": "vias", "class": "layer-vias lyr"})
    for via in board.vias:
        net_nm = _net_name(board, via.net_number)
        r_annular = via.size / 2
        nn = _nn_class(via.net_number)
        via_attrs_base = {
            "data-type": "via",
            "data-net": net_nm,
            "data-net-number": str(via.net_number),
        }
        svg.group_start(attrs={**via_attrs_base, "class": f"via {nn}"})
        svg.circle(via.x, via.y, r_annular, attrs={"class": "annular"})
        svg.circle(via.x, via.y, via.drill / 2, attrs={"class": "drill"})
        svg.group_end()
    svg.group_end()

    # -- Silkscreen layer groups -------------------------------------------
    for silk_layer in sorted(silk_by_layer.keys()):
        cls = _layer_class(silk_layer)
        svg.group_start(attrs={"data-layer": silk_layer, "class": f"{cls} lyr"})
        for ln in silk_by_layer[silk_layer]:
            silk_cls = "silk"
            if ln.footprint_ref:
                silk_cls = f"silk {_component_class_tokens(ln.footprint_ref)}"
            attrs: dict[str, str] = {"class": silk_cls}
            if ln.footprint_ref:
                attrs.update(_component_attrs(ln.footprint_ref))
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
        svg.group_start(attrs={"data-layer": fab_layer, "class": f"{cls} lyr"})
        for fp, fab_lines, fab_circles, fab_arcs in fab_content:
            body_attrs = _body_group_attrs(fp, _component_attrs)
            cmp_tokens = _component_class_tokens(fp.reference)
            body_attrs["class"] = cmp_tokens
            svg.group_start(attrs=body_attrs)
            emitted_refs.add(fp.reference)
            ref = fp.reference
            comp_attrs = _component_attrs(ref)
            # Try to build filled polygon from fab lines
            poly = _chain_lines_to_polygon(fab_lines)
            if poly:
                svg.polygon(poly, attrs={"class": f"body {cmp_tokens}", **comp_attrs})
            # Circles
            for circ in fab_circles:
                if circ.fill:
                    svg.circle(
                        circ.cx,
                        circ.cy,
                        circ.radius,
                        attrs={"class": f"body-circle-filled {cmp_tokens}", **comp_attrs},
                    )
                else:
                    svg.circle(
                        circ.cx,
                        circ.cy,
                        circ.radius,
                        attrs={
                            "class": f"body-circle {cmp_tokens}",
                            **comp_attrs,
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
                    f' class="body-arc {cmp_tokens}"{_fmt_attrs(comp_attrs)}/>'
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
            model_attrs = _body_group_attrs(fp, _component_attrs)
            model_attrs["class"] = _component_class_tokens(fp.reference)
            svg.group_start(attrs=model_attrs)
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
                "class": f"ref-text {_component_class_tokens(ttext)}",
                **_component_attrs(ttext),
            },
        )

    # -- Annotations (outside clip and mirror, always read left-to-right) ---
    if annotations is not None:
        _render_annotations(svg, annotations, annotations.font_size)

    # -- Component metadata (embedded JSON for downstream tooling) ----------
    meta = {
        ref: {"lib": lib, "value": val} for ref, (lib, val) in sorted(fp_meta.items()) if lib or val
    }
    if meta:
        svg.raw('<script type="application/json" id="pcb-metadata">')
        svg.raw(json.dumps(meta, separators=(",", ":")))
        svg.raw("</script>")

    svg.raw("</svg>")
    return svg.build()
