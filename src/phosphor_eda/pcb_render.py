"""Render a PcbBoard as SVG with optional net/component highlighting.

No external dependencies — SVG is built via string formatting.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape as xml_escape

from phosphor_eda.pcb import PcbArc, PcbBoard, PcbLine, PcbCircle

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

BOARD_FILL = "#1a5c2a"
BOARD_EDGE = "#0d3015"

PAD_COPPER = "#b87333"
PAD_FRONT_HL = "#ff6644"
PAD_BACK_HL = "#4488ff"

TRACE_FRONT_HL = "#ff4444"
TRACE_BACK_HL = "#5577ff"

VIA_NORMAL = "#c0c0c0"
VIA_DRILL = "#1a5c2a"
VIA_HL = "#ffdd44"

SILK = "#ffffffcc"
FAB_TEXT = "#ffffffcc"
COMP_BODY = "#3d3530"       # dark brown/grey — like real component packages
COMP_BODY_EDGE = "#5a504a"  # lighter edge

HIGHLIGHT_BOX = "#ffff00"
LABEL_FILL = "#ffff00"

# ---------------------------------------------------------------------------
# SVG builder
# ---------------------------------------------------------------------------


class _Svg:
    """Tiny SVG string builder."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def raw(self, s: str) -> None:
        self._parts.append(s)

    def line(
        self, x1: float, y1: float, x2: float, y2: float,
        stroke: str, stroke_width: float, opacity: float = 1.0,
    ) -> None:
        self._parts.append(
            f'<line x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" '
            f'stroke="{stroke}" stroke-width="{stroke_width:.4f}" '
            f'stroke-linecap="round" opacity="{opacity}"/>'
        )

    def circle(
        self, cx: float, cy: float, r: float, fill: str,
        stroke: str | None = None, stroke_width: float = 0,
        opacity: float = 1.0,
    ) -> None:
        s = f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{r:.4f}" fill="{fill}" opacity="{opacity}"'
        if stroke:
            s += f' stroke="{stroke}" stroke-width="{stroke_width:.4f}"'
        s += "/>"
        self._parts.append(s)

    def rect(
        self, x: float, y: float, w: float, h: float,
        fill: str | None = None, stroke: str | None = None,
        stroke_width: float = 0, opacity: float = 1.0,
        rx: float = 0, dash: str | None = None,
    ) -> None:
        s = f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}"'
        if rx > 0:
            s += f' rx="{rx:.4f}"'
        if fill:
            s += f' fill="{fill}"'
        else:
            s += ' fill="none"'
        if stroke:
            s += f' stroke="{stroke}" stroke-width="{stroke_width:.4f}"'
        if dash:
            s += f' stroke-dasharray="{dash}"'
        s += f' opacity="{opacity}"/>'
        self._parts.append(s)

    def text(
        self, x: float, y: float, content: str, font_size: float,
        fill: str, anchor: str = "middle", bold: bool = False,
        rotation: float = 0.0,
    ) -> None:
        weight = ' font-weight="bold"' if bold else ""
        rot = f' transform="rotate({rotation:.1f} {x:.4f} {y:.4f})"' if rotation else ""
        self._parts.append(
            f'<text x="{x:.4f}" y="{y:.4f}" font-size="{font_size:.2f}" '
            f'fill="{fill}" text-anchor="{anchor}" '
            f'dominant-baseline="central" font-family="sans-serif"{weight}{rot}>'
            f'{xml_escape(content)}</text>'
        )

    def group_start(self, transform: str | None = None, opacity: float = 1.0) -> None:
        s = "<g"
        if transform:
            s += f' transform="{transform}"'
        if opacity < 1.0:
            s += f' opacity="{opacity}"'
        s += ">"
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
        # Degenerate — treat as straight line
        return ((x1 + x3) / 2, (y1 + y3) / 2, 1e6)
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def _svg_arc_path(arc: PcbArc, stroke: str, stroke_width: float) -> str:
    """Render a PcbArc as an SVG <path> with an arc command."""
    cx, cy, r = _circumcircle(
        arc.start_x, arc.start_y,
        arc.mid_x, arc.mid_y,
        arc.end_x, arc.end_y,
    )
    if r > 1e5:
        # Degenerate arc — render as line
        return (
            f'<line x1="{arc.start_x:.4f}" y1="{arc.start_y:.4f}" '
            f'x2="{arc.end_x:.4f}" y2="{arc.end_y:.4f}" '
            f'stroke="{stroke}" stroke-width="{stroke_width:.4f}" '
            f'stroke-linecap="round"/>'
        )
    # Determine sweep direction using cross product of start→mid and start→end
    dx1 = arc.mid_x - arc.start_x
    dy1 = arc.mid_y - arc.start_y
    dx2 = arc.end_x - arc.start_x
    dy2 = arc.end_y - arc.start_y
    cross = dx1 * dy2 - dy1 * dx2
    sweep = 1 if cross > 0 else 0
    # Determine large-arc flag: check if midpoint angle span > 180°
    angle_start = math.atan2(arc.start_y - cy, arc.start_x - cx)
    angle_mid = math.atan2(arc.mid_y - cy, arc.mid_x - cx)
    angle_end = math.atan2(arc.end_y - cy, arc.end_x - cx)
    # Normalize angles relative to start
    def _norm(a: float, ref: float) -> float:
        d = a - ref
        while d < -math.pi:
            d += 2 * math.pi
        while d > math.pi:
            d -= 2 * math.pi
        return d
    d_mid = _norm(angle_mid, angle_start)
    d_end = _norm(angle_end, angle_start)
    large_arc = 1 if abs(d_end) > math.pi else 0
    # If mid and end are on opposite sides, we need the large arc
    if (d_mid > 0) != (d_end > 0):
        large_arc = 1

    return (
        f'<path d="M {arc.start_x:.4f} {arc.start_y:.4f} '
        f'A {r:.4f} {r:.4f} 0 {large_arc} {sweep} '
        f'{arc.end_x:.4f} {arc.end_y:.4f}" '
        f'fill="none" stroke="{stroke}" stroke-width="{stroke_width:.4f}" '
        f'stroke-linecap="round"/>'
    )


# ---------------------------------------------------------------------------
# Layer helpers
# ---------------------------------------------------------------------------


def _is_front(layer: str) -> bool:
    return layer.startswith("F.")


def _is_back(layer: str) -> bool:
    return layer.startswith("B.")


def _chain_lines_to_polygon(lines: list[PcbLine]) -> list[tuple[float, float]] | None:
    """Try to chain line segments into a closed polygon.

    Returns a list of (x, y) vertices if the lines form a closed loop,
    or None if they can't be chained.
    """
    if len(lines) < 3:
        return None

    EPS = 0.05  # tolerance for matching endpoints (mm)

    # Deduplicate lines (some footprints have the same edge drawn twice)
    seen: set[tuple[float, float, float, float]] = set()
    deduped: list[PcbLine] = []
    for ln in lines:
        # Normalize so (A->B) and (B->A) are treated as the same edge
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
    # Start from the start point of the first line
    vertices = [(lines[chain[0]].start_x, lines[chain[0]].start_y)]
    cx, cy = lines[chain[0]].end_x, lines[chain[0]].end_y
    vertices.append((cx, cy))

    while remaining:
        found = False
        for i, idx in enumerate(remaining):
            ln = lines[idx]
            # Check if start matches current point
            if abs(ln.start_x - cx) < EPS and abs(ln.start_y - cy) < EPS:
                cx, cy = ln.end_x, ln.end_y
                vertices.append((cx, cy))
                remaining.pop(i)
                found = True
                break
            # Check if end matches current point (reversed line)
            if abs(ln.end_x - cx) < EPS and abs(ln.end_y - cy) < EPS:
                cx, cy = ln.start_x, ln.start_y
                vertices.append((cx, cy))
                remaining.pop(i)
                found = True
                break
        if not found:
            return None  # Can't chain — disjoint lines

    # Check if the polygon closes
    if abs(cx - vertices[0][0]) < EPS and abs(cy - vertices[0][1]) < EPS:
        return vertices[:-1]  # Drop the duplicate closing point
    return None


def _pad_on_side(pad_layers: list[str], side: str) -> bool:
    """Check if a pad is visible on the given side (or on all layers)."""
    for ly in pad_layers:
        if ly.startswith("*."):
            return True
        if side == "front" and _is_front(ly):
            return True
        if side == "back" and _is_back(ly):
            return True
    return False


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
) -> str:
    """Render a PcbBoard as an SVG string.

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
    svg.raw(f'<rect x="{vb_x:.4f}" y="{vb_y:.4f}" width="{vb_w:.4f}" height="{vb_h:.4f}" fill="#111111"/>')

    # Back-side mirror
    if side == "back":
        svg.group_start(transform=f"translate({bx0 + bx1:.4f}, 0) scale(-1, 1)")

    # -- Board fill --------------------------------------------------------
    svg.rect(bx0, by0, bx1 - bx0, by1 - by0, fill=BOARD_FILL)

    # -- Board outline (Edge.Cuts) -----------------------------------------
    for ln in board.outline_lines:
        svg.line(ln.start_x, ln.start_y, ln.end_x, ln.end_y, BOARD_EDGE, max(ln.width, 0.15))
    for arc in board.outline_arcs:
        svg.raw(_svg_arc_path(arc, BOARD_EDGE, max(arc.width, 0.15)))

    # -- Pads (non-highlighted) --------------------------------------------
    for fp in board.footprints:
        for pad in fp.pads:
            if has_hl and pad.net_number in hl_net_nums:
                continue  # Draw highlighted pads later
            if not _pad_on_side(pad.layers, side):
                continue
            _draw_pad(svg, pad, PAD_COPPER, 0.8)

    # -- Vias (non-highlighted) --------------------------------------------
    for via in board.vias:
        if has_hl and via.net_number in hl_net_nums:
            continue
        r = via.drill / 2 + 0.05  # annular ring just outside drill
        svg.circle(via.x, via.y, r, VIA_NORMAL, opacity=0.7)
        svg.circle(via.x, via.y, via.drill / 2, VIA_DRILL)

    # -- Highlighted traces (both sides) -----------------------------------
    if has_hl:
        for seg in board.segments:
            if seg.net_number not in hl_net_nums:
                continue
            color = TRACE_FRONT_HL if _is_front(seg.layer) else TRACE_BACK_HL
            svg.line(seg.start_x, seg.start_y, seg.end_x, seg.end_y, color, seg.width)

    # -- Highlighted vias --------------------------------------------------
    if has_hl:
        for via in board.vias:
            if via.net_number not in hl_net_nums:
                continue
            r = via.drill / 2 + 0.05
            svg.circle(via.x, via.y, r, VIA_HL)
            svg.circle(via.x, via.y, via.drill / 2, VIA_DRILL)

    # -- Highlighted pads (both sides) -------------------------------------
    if has_hl:
        for fp in board.footprints:
            for pad in fp.pads:
                if pad.net_number not in hl_net_nums:
                    continue
                on_front = any(_is_front(ly) or ly.startswith("*.") for ly in pad.layers)
                color = PAD_FRONT_HL if on_front else PAD_BACK_HL
                _draw_pad(svg, pad, color, 1.0)

    # -- Component bodies (opaque fab-layer geometry, drawn ON TOP of pads) ---
    active_fab = {"F.Fab"} if side == "front" else {"B.Fab"}
    for fp in board.footprints:
        fab_lines_side = [ln for ln in fp.fab_lines if ln.layer in active_fab]
        fab_circles_side = [c for c in fp.fab_circles if c.layer in active_fab]
        fab_arcs_side = [a for a in fp.fab_arcs if a.layer in active_fab]
        if not fab_lines_side and not fab_circles_side and not fab_arcs_side:
            continue
        # Try to build a filled polygon from the fab lines
        poly = _chain_lines_to_polygon(fab_lines_side)
        if poly:
            pts = " ".join(f"{x:.4f},{y:.4f}" for x, y in poly)
            svg.raw(
                f'<polygon points="{pts}" fill="{COMP_BODY}" '
                f'stroke="{COMP_BODY_EDGE}" stroke-width="0.06" '
                f'stroke-linejoin="round"/>'
            )
        # Draw circles (e.g. pin-1 dots) on top
        for circ in fab_circles_side:
            if circ.fill:
                svg.circle(circ.cx, circ.cy, circ.radius, COMP_BODY_EDGE)
            else:
                svg.circle(circ.cx, circ.cy, circ.radius, "none",
                           stroke=COMP_BODY_EDGE, stroke_width=max(circ.width, 0.08))
        # Draw arcs on top
        for arc in fab_arcs_side:
            svg.raw(_svg_arc_path(arc, COMP_BODY_EDGE, max(arc.width, 0.08)))

    # -- Silkscreen on active side -----------------------------------------
    active_silk = {"F.SilkS", "F.Silkscreen"} if side == "front" else {"B.SilkS", "B.Silkscreen"}
    for fp in board.footprints:
        for ln in fp.silkscreen_lines:
            if ln.layer in active_silk:
                svg.line(ln.start_x, ln.start_y, ln.end_x, ln.end_y, SILK, max(ln.width, 0.1))

    # -- Collect ref designator texts to render outside mirror group ----------
    deferred_texts: list[tuple[float, float, str, float, float]] = []
    active_text_layers = active_fab | active_silk
    for fp in board.footprints:
        # Find the best reference text for this footprint
        best_ref_txt = None
        for txt in fp.texts:
            if txt.hidden or txt.layer not in active_text_layers:
                continue
            # Only show reference designators, not values or other text
            if txt.kind == "value":
                continue
            if txt.text == fp.reference or txt.kind in ("reference", "user"):
                if best_ref_txt is None or txt.font_size < best_ref_txt.font_size:
                    best_ref_txt = txt  # prefer smaller (user ${REFERENCE}) over large ref
        if best_ref_txt is not None:
            # Cap font size relative to board
            fs = min(best_ref_txt.font_size, 0.8)
            deferred_texts.append((
                best_ref_txt.x, best_ref_txt.y,
                fp.reference,  # always use the actual reference, not raw text
                fs, best_ref_txt.rotation,
            ))

    # -- Component highlight boxes (inside mirror group) ---------------------
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
                stroke=HIGHLIGHT_BOX, stroke_width=0.3,
                dash="0.5,0.3", opacity=0.9,
            )
            hl_labels.append((mx0, my0, mx1, my1, fp.reference))

    if side == "back":
        svg.group_end()

    # -- Text labels drawn outside mirror so they read correctly -------------
    for tx, ty, ttext, tsize, trot in deferred_texts:
        if side == "back":
            tx = (bx0 + bx1) - tx
            # Back-side text in KiCad is stored with justify=mirror, meaning
            # the rotation assumes viewing from the back.  After our X-mirror,
            # we need to flip the rotation sense: negate and add 180°.
            trot = 180.0 - trot
        svg.text(tx, ty, ttext, tsize, fill=FAB_TEXT, rotation=trot)

    # -- Highlight labels drawn outside mirror so they read correctly -------
    for mx0, my0, mx1, my1, ref in hl_labels:
        margin = 0.5
        label_y = my0 - margin - 0.4
        label_x = (mx0 + mx1) / 2
        if side == "back":
            label_x = (bx0 + bx1) - label_x
        svg.text(label_x, label_y, ref, font_size=1.8, fill=LABEL_FILL, bold=True)

    svg.raw("</svg>")
    return svg.build()


# ---------------------------------------------------------------------------
# Pad drawing helper
# ---------------------------------------------------------------------------


def _draw_pad(svg: _Svg, pad, color: str, opacity: float) -> None:
    """Draw a single pad as a shape."""
    hw, hh = pad.width / 2, pad.height / 2
    if pad.shape == "circle":
        svg.circle(pad.x, pad.y, hw, color, opacity=opacity)
    elif pad.shape == "roundrect":
        rx = min(hw, hh) * 0.25
        svg.rect(pad.x - hw, pad.y - hh, pad.width, pad.height, fill=color, rx=rx, opacity=opacity)
    elif pad.shape == "oval":
        rx = min(hw, hh)
        svg.rect(pad.x - hw, pad.y - hh, pad.width, pad.height, fill=color, rx=rx, opacity=opacity)
    else:
        # rect, trapezoid, custom — draw as rect
        svg.rect(pad.x - hw, pad.y - hh, pad.width, pad.height, fill=color, opacity=opacity)
