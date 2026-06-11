"""Pure-SVG rendering of resolved annotations (boxes, pointers, labels, legend).

Color parsing, contrast math, the annotation CSS block, and the element
drawing routines live here. The serializer calls ``annotation_css`` and
``render_annotations``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape as xml_escape

from phosphor_eda.geometry.text_metrics import (
    BR_RE,
    EMBEDDED_FONT_FAMILY,
    HTML_TAG_RE,
    baseline_center_offset,
)

if TYPE_CHECKING:
    from phosphor_eda.render.annotations import (
        ResolvedAnnotations,
        ResolvedBox,
        ResolvedCallout,
        ResolvedLabel,
        ResolvedLegend,
        ResolvedPointer,
    )
    from phosphor_eda.render.plan import (
        AnnotationConnectorStyle,
        AnnotationLabelStyle,
        AnnotationStyle,
    )
    from phosphor_eda.render.svg import Svg

_ANNOTATION_FONT_FAMILY = f"{EMBEDDED_FONT_FAMILY}, Inter, system-ui, sans-serif"

# Fallback annotation color (orange) used when a color is absent or
# unparseable.
DEFAULT_ANNOTATION_COLOR = (255, 107, 53)

# Regex for parsing color strings into (r, g, b) 0–255
_HEX3_RE = re.compile(r"^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$")
_HEX6_RE = re.compile(r"^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})")
_RGBA_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _parse_rgb(color: str) -> tuple[int, int, int]:
    """Extract (r, g, b) from a CSS color string, falling back to orange."""
    m = _HEX6_RE.match(color)
    if m:
        return (int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16))
    m = _HEX3_RE.match(color)
    if m:
        return (int(m.group(1), 16) * 17, int(m.group(2), 16) * 17, int(m.group(3), 16) * 17)
    m = _RGBA_RE.match(color)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return DEFAULT_ANNOTATION_COLOR


def _contrast_text_color(bg_color: str) -> str:
    """Return '#000' or '#fff' for best contrast against *bg_color*."""
    r, g, b = _parse_rgb(bg_color)
    # Relative luminance (ITU-R BT.709)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#000" if luminance > 140 else "#fff"


def annotation_css(
    font_size: float,
    *,
    annotation_style: AnnotationStyle,
) -> str:
    """CSS for pure-SVG annotation elements.

    The embedded Inter face (a single shared ``@font-face`` emitted by the
    serializer) backs both annotations and board text, so the rendered font
    matches the font used for measurement.  The annotation group has a
    ``scale()`` transform that maps pixel space onto the SVG viewBox, so all
    sizes here are in display pixels.
    """
    ff = _ANNOTATION_FONT_FAMILY
    label_style = annotation_style.label
    connector_style = annotation_style.connector
    label_rules = [
        f"font-family: {ff}",
        f"font-weight: {label_style.font_weight or '500'}",
        f"font-size: {font_size:.1f}px",
    ]
    if label_style.fill is not None:
        label_rules.append(f"fill: {label_style.fill}")
    if label_style.text_halo is not None:
        label_rules.append(f"stroke: {label_style.text_halo}")
        if label_style.text_halo_width_px is not None:
            label_rules.append(f"stroke-width: {label_style.text_halo_width_px:.1f}px")
        label_rules.append("stroke-linejoin: round")
        label_rules.append("paint-order: stroke fill")
    pill_rules = ["stroke: none"]
    if label_style.pill_visible is False:
        pill_rules.append("display: none")
    connector_rules = ["fill: none", "stroke-linejoin: round"]
    if connector_style.stroke is not None:
        connector_rules.append(f"stroke: {connector_style.stroke}")
    connector_width = (
        f"{connector_style.stroke_width_px:.1f}px"
        if connector_style.stroke_width_px is not None
        else "2"
    )
    connector_rules.append(f"stroke-width: {connector_width}")
    dot_rules: list[str] = []
    if connector_style.dot_visible is False:
        dot_rules.append("display: none")
    return f"""\
.annotation-connector {{ {"; ".join(connector_rules)}; }}
.annotation-box {{ stroke-width: 2; }}
.annotation-pill {{ {"; ".join(pill_rules)}; }}
.annotation-pill--muted {{ {"; ".join(pill_rules)}; }}
.annotation-label-text {{ {"; ".join(label_rules)}; }}
.annotation-dot {{ {"; ".join(dot_rules)}; }}
.legend-bg {{ fill: rgba(12,12,20,0.85); stroke: rgba(255,255,255,0.15);
  stroke-width: 4; paint-order: stroke fill; }}
.legend-title-text {{ fill: #f0f0f0; font-family: {ff};
  font-weight: 600; font-size: {font_size * 0.85:.1f}px;
  opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; }}
.legend-entry-text {{ fill: #f0f0f0; font-family: {ff};
  font-weight: 500; font-size: {font_size:.1f}px; }}"""


def render_annotations(
    svg: Svg,
    annotations: ResolvedAnnotations,
    font_size: float,
    *,
    annotation_style: AnnotationStyle,
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
    connector_style = annotation_style.connector
    label_style = annotation_style.label
    for box in annotations.boxes:
        _render_box(svg, box, font_size, connector_style=connector_style, label_style=label_style)
    for pointer in annotations.pointers:
        _render_pointer(
            svg,
            pointer,
            font_size,
            connector_style=connector_style,
            label_style=label_style,
        )
    for label in annotations.labels:
        _render_label(
            svg,
            label,
            font_size,
            connector_style=connector_style,
            label_style=label_style,
        )
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
    svg: Svg,
    path: list[tuple[float, float]],
    color: str,
    *,
    dot: bool = True,
    connector_style: AnnotationConnectorStyle,
) -> None:
    """Render an orthogonal connector path with an optional dot at the end."""
    if len(path) < 2:
        return
    d = _connector_path_d(path)
    stroke_color = connector_style.stroke if connector_style.stroke is not None else color
    svg.path(d, attrs={"class": "annotation-connector", "style": f"stroke: {stroke_color}"})
    if dot:
        tx, ty = path[-1]
        dot_r = 2.5  # pixels
        svg.circle(
            tx,
            ty,
            dot_r,
            attrs={"class": "annotation-dot", "style": f"fill: {stroke_color}"},
        )


def _split_label_lines(text: str) -> list[str]:
    """Split label text on <br> tags and strip HTML tags."""
    lines = BR_RE.split(text)
    return [HTML_TAG_RE.sub("", line) for line in lines]


def _render_pill_label(
    svg: Svg,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    font_size: float,
    color: str,
    *,
    label_style: AnnotationLabelStyle,
    text_anchor: str = "middle",
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
    start_y = center_y - total_text_h / 2 + line_height / 2 + baseline_center_offset() * font_size

    for i, line in enumerate(lines):
        ty = start_y + i * line_height
        if label_style.fill is not None:
            fill_attr = label_style.fill
        elif label_style.pill_visible is False:
            # The pill behind the text is hidden, so contrast-against-pill is
            # meaningless; default to dark text for the light default canvas.
            fill_attr = "#000"
        else:
            fill_attr = text_color
        svg.raw(
            "".join(
                (
                    f'<text x="{cx:.4f}" y="{ty:.4f}" text-anchor="{text_anchor}" ',
                    f'class="annotation-label-text" fill="{fill_attr}">',
                    f"{xml_escape(line)}</text>",
                )
            )
        )


def _render_box(
    svg: Svg,
    box: ResolvedBox,
    font_size: float,
    *,
    connector_style: AnnotationConnectorStyle,
    label_style: AnnotationLabelStyle,
) -> None:
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
    if box.callout is not None:
        _render_connector(
            svg,
            box.callout.connector_path,
            box.color,
            dot=False,
            connector_style=connector_style,
        )
        _render_callout_pill(
            svg,
            box.callout,
            font_size,
            color=box.color,
            label_style=label_style,
        )


def _render_pointer(
    svg: Svg,
    pointer: ResolvedPointer,
    font_size: float,
    *,
    connector_style: AnnotationConnectorStyle,
    label_style: AnnotationLabelStyle,
) -> None:
    """Render a pointer with connector and margin label."""
    if pointer.callout is not None:
        _render_connector(
            svg,
            pointer.callout.connector_path,
            pointer.color,
            connector_style=connector_style,
        )
        _render_callout_pill(
            svg,
            pointer.callout,
            font_size,
            color=pointer.color,
            label_style=label_style,
        )


def _render_label(
    svg: Svg,
    label: ResolvedLabel,
    font_size: float,
    *,
    connector_style: AnnotationConnectorStyle,
    label_style: AnnotationLabelStyle,
) -> None:
    """Render a label with optional connector to its target."""
    if label.callout is None:
        return
    if label.callout.connector_path:
        _render_connector(
            svg,
            label.callout.connector_path,
            "rgba(180,180,200,0.5)",
            connector_style=connector_style,
        )
    _render_callout_pill(
        svg,
        label.callout,
        font_size,
        color="rgba(60,60,80,0.9)",
        label_style=label_style,
        css_class="annotation-pill annotation-pill--muted",
    )


def _render_callout_pill(
    svg: Svg,
    callout: ResolvedCallout,
    font_size: float,
    *,
    color: str,
    label_style: AnnotationLabelStyle,
    css_class: str = "annotation-pill",
) -> None:
    _render_pill_label(
        svg,
        callout.x,
        callout.y,
        callout.width,
        callout.height,
        callout.text,
        font_size,
        color=color,
        text_anchor=callout.text_anchor,
        label_style=label_style,
        css_class=css_class,
    )


def _render_legend(svg: Svg, legend: ResolvedLegend, font_size: float) -> None:
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
        cursor_y += title_fs / 2 + baseline_center_offset() * title_fs
        svg.raw(
            "".join(
                (
                    f'<text x="{legend.x + pad_h:.4f}" y="{cursor_y:.4f}" ',
                    f'class="legend-title-text">{xml_escape(legend.title)}</text>',
                )
            )
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
        text_y = cursor_y + font_size / 2 + baseline_center_offset() * font_size
        svg.raw(
            "".join(
                (
                    f'<text x="{text_x:.4f}" y="{text_y:.4f}" ',
                    f'class="legend-entry-text">{xml_escape(entry.label)}</text>',
                )
            )
        )
        cursor_y += max(font_size, swatch_size)
