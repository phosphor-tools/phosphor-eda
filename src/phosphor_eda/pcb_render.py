"""Render a Pcb as layered SVG from structured render settings.

Emits an SVG with layer groups, data-* attributes on every element, and
style blocks for render-time paint rules and custom CSS. Highlights,
layer visibility, and geometry omission are resolved before SVG emission.

No external dependencies — SVG is built via string formatting.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from xml.sax.saxutils import escape as xml_escape

from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon

from phosphor_eda.pcb_render_plan import (
    DerivedRenderPlan,
    build_derived_render_plan,
)
from phosphor_eda.pcb_render_settings import (
    RENDER_MODES,
    SOURCE_LAYER_FUNCTIONS,
    SOURCE_LAYER_SIDES,
    HighlightSpec,
    RenderSettings,
    is_json_dict,
    parse_render_settings,
)
from phosphor_eda.text_metrics import BASELINE_CENTER_OFFSET, INTER_REGULAR_BASE64

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from shapely.coords import CoordinateSequence
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb import (
        Pcb,
    )
    from phosphor_eda.pcb_annotations import (
        ResolvedAnnotations,
        ResolvedBox,
        ResolvedLabel,
        ResolvedLegend,
        ResolvedPointer,
    )
    from phosphor_eda.pcb_render_artwork import DerivedLayer
    from phosphor_eda.pcb_render_profile import RenderProfiler
    from phosphor_eda.pcb_render_tokens import ResolvedStyle, VisualRole

_BUNDLED_SETTINGS_PACKAGE = "phosphor_eda.render_settings"
_PHOSPHOR_SETTINGS_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SETTINGS_EXTENDS_KEY = "extends"
_STYLE_BLOCK_TERMINATOR_RE = re.compile(r"</\s*style\s*>", re.IGNORECASE)


def load_render_settings_file(path: Path) -> RenderSettings:
    """Load, resolve ``extends``, merge, and parse a render-settings JSON file."""
    data = _load_render_settings_file_data(path.resolve(), stack=[])
    return parse_render_settings(data)


def load_render_settings_json(text: str) -> RenderSettings:
    """Load render settings from JSON text.

    Stdin settings can extend packaged ``phosphor:`` settings. Relative file
    extends require a settings file path, so they are rejected for JSON text.
    """
    data = _load_render_settings_text_data(text, source=None, stack=[])
    return parse_render_settings(data)


def _load_render_settings_file_data(path: Path, stack: list[str]) -> dict[str, Any]:
    try:
        text = path.read_text()
    except OSError as exc:
        msg = f"Render settings file not found: {path}"
        raise ValueError(msg) from exc
    return _load_render_settings_text_data(text, source=path, stack=stack)


def _load_render_settings_text_data(
    text: str,
    *,
    source: Path | str | None,
    stack: list[str],
) -> dict[str, Any]:
    source_id = _settings_source_id(source)
    if source_id in stack:
        cycle = " -> ".join([*stack, source_id])
        msg = f"render settings extends cycle detected: {cycle}"
        raise ValueError(msg)

    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"Invalid render settings JSON: {exc}"
        raise ValueError(msg) from exc
    if not is_json_dict(raw):
        msg = "top-level JSON value must be an object"
        raise ValueError(msg)

    data = dict(raw)
    parse_render_settings(data)
    parent_ref = data.pop(_SETTINGS_EXTENDS_KEY, "")
    if not parent_ref:
        return data

    parent_data = _load_parent_render_settings(parent_ref, source=source, stack=[*stack, source_id])
    return _merge_render_settings_data(parent_data, data)


def _settings_source_id(source: Path | str | None) -> str:
    if source is None:
        return "<stdin>"
    if isinstance(source, Path):
        return str(source.resolve())
    return source


def _load_parent_render_settings(
    parent_ref: object,
    *,
    source: Path | str | None,
    stack: list[str],
) -> dict[str, Any]:
    if not isinstance(parent_ref, str):
        msg = "extends must be a string"
        raise ValueError(msg)

    if parent_ref.startswith("phosphor:"):
        name = parent_ref.removeprefix("phosphor:")
        if not _PHOSPHOR_SETTINGS_RE.fullmatch(name):
            msg = f"Invalid phosphor render settings name: {parent_ref!r}"
            raise ValueError(msg)
        resource = files(_BUNDLED_SETTINGS_PACKAGE).joinpath(f"{name}.json")
        if not resource.is_file():
            msg = f"Unknown phosphor render settings: {parent_ref}"
            raise ValueError(msg)
        text = resource.read_text()
        return _load_render_settings_text_data(text, source=parent_ref, stack=stack)

    parent_path = Path(parent_ref)
    if not parent_path.is_absolute():
        if not isinstance(source, Path):
            msg = f"Relative render settings extends requires a file source: {parent_ref}"
            raise ValueError(msg)
        parent_path = source.parent / parent_path
    return _load_render_settings_file_data(parent_path.resolve(), stack=stack)


def _merge_render_settings_data(
    parent: dict[str, Any],
    child: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(parent)
    child_css = child.get("custom_css")
    parent_css = parent.get("custom_css")

    for key, value in child.items():
        existing = merged.get(key)
        if key == "annotations" and is_json_dict(existing) and is_json_dict(value):
            merged[key] = _deep_merge_json_dicts(existing, value)
        elif key == "tokens" and is_json_dict(existing) and is_json_dict(value):
            merged[key] = {**existing, **value}
        elif key == "dimming" and is_json_dict(existing) and is_json_dict(value):
            merged[key] = _deep_merge_json_dicts(existing, value)
        elif key == "custom_css":
            css_parts = [css for css in (parent_css, child_css) if isinstance(css, str) and css]
            merged[key] = "\n".join(css_parts)
        else:
            merged[key] = value
    return merged


def _deep_merge_json_dicts(
    parent: dict[str, object],
    child: dict[str, object],
) -> dict[str, object]:
    merged: dict[str, object] = dict(parent)
    for key, value in child.items():
        existing = merged.get(key)
        if is_json_dict(existing) and is_json_dict(value):
            merged[key] = _deep_merge_json_dicts(existing, value)
        else:
            merged[key] = value
    return merged


def render_settings_schema() -> dict[str, object]:
    """Return the JSON Schema for ``pcb render`` settings."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "phosphor-eda pcb render settings",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "extends": {
                "type": "string",
                "description": (
                    "Base render settings to merge first. Use phosphor:<name> "
                    "for bundled settings or a relative/absolute JSON path."
                ),
            },
            "renderMode": {
                "type": "string",
                "enum": list(RENDER_MODES),
            },
            "side": {
                "type": "string",
                "enum": ["front", "back"],
            },
            "width": {
                "type": "integer",
                "minimum": 1,
            },
            "fontSizePx": {
                "type": "number",
                "minimum": 1,
                "maximum": 500,
                "description": "Annotation label font size in display pixels.",
            },
            "source": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "layers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "match": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "name": {"type": "string", "minLength": 1},
                                        "function": {
                                            "type": "string",
                                            "enum": list(SOURCE_LAYER_FUNCTIONS),
                                        },
                                        "side": {
                                            "type": "string",
                                            "enum": list(SOURCE_LAYER_SIDES),
                                        },
                                    },
                                },
                                "visible": {"type": "boolean"},
                                "objects": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                },
                            },
                        },
                    },
                    "excludeComponents": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
            "tokens": {
                "type": "object",
                "patternProperties": {
                    r"^(cad|realistic|highlight|annotation)(\.[A-Za-z][A-Za-z0-9_]*)+$": {
                        "$ref": "#/$defs/token_value"
                    },
                    r"^(cad|realistic|highlight)\.layer\[[^\]\r\n]+\]\.[A-Za-z][A-Za-z0-9_]*$": {
                        "$ref": "#/$defs/token_value"
                    },
                },
                "additionalProperties": False,
            },
            "dimming": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "enabled": {"type": "boolean"},
                },
            },
            "highlights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "net": {"type": "string", "minLength": 1},
                        "component": {"type": "string", "minLength": 1},
                        "pad": {
                            "type": "string",
                            "minLength": 3,
                            "pattern": r"^[^.]+\..+$",
                        },
                        "color": {"type": "string"},
                    },
                    "oneOf": [
                        {"required": ["net"]},
                        {"required": ["component"]},
                        {"required": ["pad"]},
                    ],
                },
            },
            "annotations": {
                "type": "object",
            },
            "custom_css": {
                "type": "string",
            },
        },
        "$defs": {
            "token_value": {
                "type": ["string", "number", "boolean"],
            },
        },
        "examples": [
            {
                "extends": "phosphor:simplified-high-contrast",
                "renderMode": "cad",
                "width": 3000,
                "fontSizePx": 40,
                "source": {
                    "layers": [
                        {"match": {"function": "copper"}, "visible": True},
                        {
                            "match": {"function": "silkscreen", "side": "front"},
                            "visible": True,
                        },
                    ],
                    "excludeComponents": ["R", "C", "L"],
                },
                "tokens": {
                    "cad.copper.front.fill": "#d17a22",
                    "cad.layer[F.Cu].fill": "#d17a22",
                },
                "dimming": {"enabled": False},
                "highlights": [{"pad": "CN11.30", "color": "#c00000"}],
                "annotations": {
                    "pointers": [{"target": "CN11.30", "label": "PA1 / REF_CLK"}],
                },
                "custom_css": "",
            },
        ],
    }


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


def render_pcb_svg_from_derived_plan(
    plan: DerivedRenderPlan,
    *,
    profiler: RenderProfiler | None = None,
) -> str:
    """Serialize a derived-layer render plan to SVG."""
    svg = _Svg()
    view_box = plan.view_box
    svg_open = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{plan.width_px}" '
        + f'height="{plan.height_px}" viewBox="{view_box.x:.4f} {view_box.y:.4f} '
        + f'{view_box.width:.4f} {view_box.height:.4f}">'
    )
    svg.raw(svg_open)
    if plan.custom_css:
        svg.raw('<style id="custom">')
        svg.raw(_escape_style_block_text(plan.custom_css))
        svg.raw("</style>")
    if plan.annotations is not None:
        svg.raw('<style id="annotations">')
        svg.raw(
            _escape_style_block_text(
                _annotation_css(plan.annotations.font_size, annotation_style=plan.annotation_style)
            )
        )
        svg.raw("</style>")

    _render_derived_layers(svg, plan.base_layers, profiler=profiler, group="base")
    for group in plan.highlight_groups:
        svg.group_start(attrs={"class": "highlight-overlay", "data-highlight-target": group.target})
        _render_derived_layers(svg, group.layers, profiler=profiler, group="highlight")
        svg.group_end()

    if plan.annotations is not None:
        _render_annotations(
            svg,
            plan.annotations,
            plan.annotations.font_size,
            annotation_style=plan.annotation_style,
        )

    svg.raw("</svg>")
    rendered = svg.build()
    if profiler is not None:
        profiler.metric(
            "svg.output",
            bytes=len(rendered.encode()),
            characters=len(rendered),
            base_layers=len(plan.base_layers),
            highlight_groups=len(plan.highlight_groups),
            highlight_layers=sum(len(group.layers) for group in plan.highlight_groups),
        )
    return rendered


def _append_pcb_metadata(svg: str, board: Pcb) -> str:
    metadata_json = _pcb_metadata_json(board)
    if not metadata_json:
        return svg
    metadata_block = (
        f'<script type="application/json" id="pcb-metadata">\n{metadata_json}\n</script>'
    )
    if svg.endswith("</svg>"):
        return f"{svg[:-6]}{metadata_block}\n</svg>"
    return f"{svg}\n{metadata_block}"


def _pcb_metadata_json(board: Pcb) -> str:
    meta = {
        fp.reference: {"lib": fp.footprint_lib, "value": fp.value}
        for fp in sorted(board.footprints, key=lambda footprint: footprint.reference)
        if fp.footprint_lib or fp.value
    }
    return _escape_json_for_script(json.dumps(meta, separators=(",", ":"))) if meta else ""


def _escape_json_for_script(value: str) -> str:
    return value.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _escape_style_block_text(value: str) -> str:
    return _STYLE_BLOCK_TERMINATOR_RE.sub(r"<\\/style>", value)


def _render_derived_layers(
    svg: _Svg,
    layers: tuple[DerivedLayer, ...],
    *,
    profiler: RenderProfiler | None = None,
    group: str,
) -> None:
    clip_ids_by_object: dict[int, str] = {}
    for layer in layers:
        path_d = _geometry_to_svg_path_d(layer.geometry)
        if not path_d:
            continue
        clip_key = id(layer.clip) if layer.clip is not None else 0
        clip_already_rendered = clip_key in clip_ids_by_object
        clip_id = ""
        if layer.clip is not None:
            clip_id = clip_ids_by_object.setdefault(
                clip_key,
                _layer_clip_id(group, len(clip_ids_by_object), layer),
            )
        if layer.clip is not None:
            _render_layer_clip_mask(
                svg,
                clip_id,
                layer,
                already_rendered=clip_already_rendered,
            )
        if profiler is not None:
            profiler.metric(
                "svg.layer_path",
                group=group,
                layer=layer.id,
                geometry_type=layer.geometry.geom_type,
                source_ids=len(layer.source_ids),
                path_characters=len(path_d),
                move_commands=path_d.count("M "),
                line_commands=path_d.count("L "),
            )
        attrs = _derived_layer_group_attrs(layer)
        if clip_id:
            attrs["mask"] = f"url(#{clip_id})"
        svg.group_start(attrs=attrs)
        svg.path(path_d, attrs=_derived_layer_path_attrs(layer.style))
        svg.group_end()


def _render_layer_clip_mask(
    svg: _Svg,
    clip_id: str,
    layer: DerivedLayer,
    *,
    already_rendered: bool,
) -> None:
    if already_rendered:
        return
    if layer.clip is None:
        return
    board_path = _geometry_to_svg_path_d(layer.clip.board)
    if not board_path:
        return
    min_x, min_y, max_x, max_y = layer.clip.board.bounds
    pad = max(max_x - min_x, max_y - min_y, 1.0) * 0.05
    svg.raw(
        f'<defs><mask id="{xml_escape(clip_id)}" maskUnits="userSpaceOnUse" '
        f'x="{min_x - pad:.4f}" y="{min_y - pad:.4f}" '
        f'width="{(max_x - min_x) + 2 * pad:.4f}" '
        f'height="{(max_y - min_y) + 2 * pad:.4f}">'
    )
    svg.path(board_path, attrs={"fill": "white", "fill-rule": "evenodd"})
    for circle in layer.clip.drill_circles:
        svg.raw(
            f'<circle cx="{circle.cx:.4f}" cy="{circle.cy:.4f}" '
            f'r="{circle.radius:.4f}" fill="black"/>'
        )
    if not layer.clip.drill_circles and (drill_path := _geometry_to_svg_path_d(layer.clip.drills)):
        svg.path(drill_path, attrs={"fill": "black", "fill-rule": "evenodd"})
    svg.raw("</mask></defs>")


def _layer_clip_id(group: str, index: int, layer: DerivedLayer) -> str:
    raw = f"layer-clip-{group}-{index}-{layer.id}"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw)


def _derived_layer_group_attrs(layer: DerivedLayer) -> dict[str, str]:
    attrs = {
        "data-role": _visual_role_name(layer.role),
        "data-source-layers": ",".join(layer.source_layers),
    }
    if layer.source_ids:
        attrs["data-source-ids"] = ",".join(layer.source_ids)
    for key, value in layer.data.items():
        attr_name = key if key.startswith("data-") else f"data-{key}"
        attrs[attr_name] = value
    return attrs


def _derived_layer_path_attrs(style: ResolvedStyle | None) -> dict[str, str]:
    attrs = _resolved_style_svg_attrs(style)
    attrs["fill-rule"] = "evenodd"
    return attrs


def _visual_role_name(role: VisualRole) -> str:
    parts = [role.namespace, role.function]
    if role.side:
        parts.append(role.side)
    if role.side == "inner" and role.inner_index is not None:
        parts.append(str(role.inner_index))
    return ".".join(parts)


def _resolved_style_svg_attrs(style: ResolvedStyle | None) -> dict[str, str]:
    if style is None:
        return {}
    declarations: list[str] = []
    if style.fill is not None:
        declarations.append(f"fill: {style.fill}")
    if style.stroke is not None:
        declarations.append(f"stroke: {style.stroke}")
    if style.opacity is not None:
        declarations.append(f"opacity: {style.opacity:.4f}")
    if style.stroke_width_mm is not None:
        declarations.append(f"stroke-width: {style.stroke_width_mm:.4f}")
    return {"style": "; ".join(declarations)} if declarations else {}


def _geometry_to_svg_path_d(geometry: object) -> str:
    if not isinstance(
        geometry,
        (GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon),
    ):
        return ""
    if geometry.is_empty:
        return ""
    if isinstance(geometry, Polygon):
        return _polygon_to_svg_path_d(geometry)
    if isinstance(geometry, MultiPolygon):
        return " ".join(
            path_d
            for polygon in geometry.geoms
            for path_d in (_polygon_to_svg_path_d(polygon),)
            if path_d
        )
    if isinstance(geometry, LineString):
        return _line_string_to_svg_path_d(geometry)
    if isinstance(geometry, MultiLineString):
        return " ".join(
            path_d
            for line in geometry.geoms
            for path_d in (_line_string_to_svg_path_d(line),)
            if path_d
        )
    return _geometry_collection_to_svg_path_d(cast("GeometryCollection[BaseGeometry]", geometry))


def _geometry_collection_to_svg_path_d(geometry: GeometryCollection[BaseGeometry]) -> str:
    parts = cast("Iterable[BaseGeometry]", geometry.geoms)
    return " ".join(
        path_d for part in parts for path_d in (_geometry_to_svg_path_d(part),) if path_d
    )


def _polygon_to_svg_path_d(polygon: Polygon) -> str:
    rings = [_ring_to_svg_path_d(polygon.exterior.coords)]
    rings.extend(_ring_to_svg_path_d(interior.coords) for interior in polygon.interiors)
    return " ".join(ring for ring in rings if ring)


def _ring_to_svg_path_d(coords: CoordinateSequence) -> str:
    points = [(float(x), float(y)) for x, y in coords]
    if len(points) < 2:
        return ""
    if points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 2:
        return ""
    commands = [f"M {points[0][0]:.4f} {points[0][1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in points[1:])
    commands.append("Z")
    return " ".join(commands)


def _line_string_to_svg_path_d(line: LineString) -> str:
    points = [(float(x), float(y)) for x, y in line.coords]
    if len(points) < 2:
        return ""
    commands = [f"M {points[0][0]:.4f} {points[0][1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in points[1:])
    return " ".join(commands)


def _settings_for_plan(
    render_settings: RenderSettings,
    *,
    highlight_nets: list[str] | None,
    highlight_components: list[str] | None,
    highlight_specs: list[HighlightSpec] | None,
    custom_css: str,
) -> RenderSettings:
    highlights = list(render_settings.highlights)
    for net in highlight_nets or []:
        highlight = HighlightSpec(net=net)
        if highlight not in highlights:
            highlights.append(highlight)
    for component in highlight_components or []:
        highlight = HighlightSpec(component=component)
        if highlight not in highlights:
            highlights.append(highlight)
    for highlight in highlight_specs or []:
        if highlight not in highlights:
            highlights.append(highlight)
    return replace(
        render_settings,
        highlights=highlights,
        custom_css=custom_css or render_settings.custom_css,
    )


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


def _annotation_css(
    font_size: float,
    *,
    annotation_style: dict[str, object] | None = None,
) -> str:
    """CSS for pure-SVG annotation elements.

    Embeds a subset of Inter-Regular via @font-face so the rendered
    font exactly matches the font used for text measurement.  The
    annotation group has a ``scale()`` transform that maps pixel space
    onto the SVG viewBox, so all sizes here are in display pixels.
    """
    ff = _ANNOTATION_FONT_FAMILY
    label_style = _annotation_part_style(annotation_style, "label")
    connector_style = _annotation_part_style(annotation_style, "connector")
    label_rules = [
        f"font-family: {ff}",
        f"font-weight: {_css_style_value(label_style.get('font_weight'), '500')}",
        f"font-size: {font_size:.1f}px",
    ]
    label_fill = label_style.get("fill")
    if isinstance(label_fill, str):
        label_rules.append(f"fill: {label_fill}")
    text_halo = label_style.get("text_halo")
    if isinstance(text_halo, str):
        label_rules.append(f"stroke: {text_halo}")
        halo_width = _css_px_value(label_style.get("text_halo_width_px"))
        if halo_width:
            label_rules.append(f"stroke-width: {halo_width}")
        label_rules.append("stroke-linejoin: round")
        label_rules.append("paint-order: stroke fill")
    pill_rules = ["stroke: none"]
    if label_style.get("pill_visible") is False:
        pill_rules.append("display: none")
    connector_rules = ["fill: none", "stroke-linejoin: round"]
    connector_stroke = connector_style.get("stroke")
    if isinstance(connector_stroke, str):
        connector_rules.append(f"stroke: {connector_stroke}")
    connector_width = _css_px_value(connector_style.get("stroke_width_px"))
    connector_rules.append(f"stroke-width: {connector_width or '2'}")
    dot_rules: list[str] = []
    if connector_style.get("dot_visible") is False:
        dot_rules.append("display: none")
    return f"""\
@font-face {{ font-family: "InterEmbed"; font-weight: 400;
  src: url("data:font/truetype;base64,{INTER_REGULAR_BASE64}") format("truetype"); }}
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


def _annotation_part_style(
    annotation_style: dict[str, object] | None,
    part: str,
) -> dict[str, object]:
    if annotation_style is None:
        return {}
    style = annotation_style.get(part)
    if not is_json_dict(style):
        return {}
    return dict(style)


def _css_px_value(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.1f}px"
    return value if isinstance(value, str) else ""


def _css_style_value(value: object, default: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):g}"
    return value if isinstance(value, str) else default


def _render_annotations(
    svg: _Svg,
    annotations: ResolvedAnnotations,
    font_size: float,
    *,
    annotation_style: dict[str, object] | None = None,
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
    connector_style = _annotation_part_style(annotation_style, "connector")
    label_style = _annotation_part_style(annotation_style, "label")
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
    svg: _Svg,
    path: list[tuple[float, float]],
    color: str,
    *,
    dot: bool = True,
    connector_style: dict[str, object] | None = None,
) -> None:
    """Render an orthogonal connector path with an optional dot at the end."""
    if len(path) < 2:
        return
    d = _connector_path_d(path)
    stroke = connector_style.get("stroke") if connector_style is not None else None
    stroke_color = stroke if isinstance(stroke, str) else color
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
    text_anchor: str = "middle",
    css_class: str = "annotation-pill",
    label_style: dict[str, object] | None = None,
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
        fill = label_style.get("fill") if label_style is not None else None
        fill_attr = fill if isinstance(fill, str) else text_color
        svg.raw(
            f'<text x="{cx:.4f}" y="{ty:.4f}" text-anchor="{text_anchor}" '
            f'class="annotation-label-text" fill="{fill_attr}">'
            f"{xml_escape(line)}</text>"
        )


def _render_box(
    svg: _Svg,
    box: ResolvedBox,
    font_size: float,
    *,
    connector_style: dict[str, object],
    label_style: dict[str, object],
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
    if box.label_text:
        _render_connector(
            svg,
            box.connector_path,
            box.color,
            dot=False,
            connector_style=connector_style,
        )
        _render_pill_label(
            svg,
            box.label_x,
            box.label_y,
            box.label_width,
            box.label_height,
            box.label_text,
            font_size,
            color=box.color,
            text_anchor=box.text_anchor,
            label_style=label_style,
        )


def _render_pointer(
    svg: _Svg,
    pointer: ResolvedPointer,
    font_size: float,
    *,
    connector_style: dict[str, object],
    label_style: dict[str, object],
) -> None:
    """Render a pointer with connector and margin label."""
    if pointer.label_text:
        _render_connector(
            svg,
            pointer.connector_path,
            pointer.color,
            connector_style=connector_style,
        )
        _render_pill_label(
            svg,
            pointer.label_x,
            pointer.label_y,
            pointer.label_width,
            pointer.label_height,
            pointer.label_text,
            font_size,
            color=pointer.color,
            text_anchor=pointer.text_anchor,
            label_style=label_style,
        )
    elif pointer.connector_path:
        _render_connector(
            svg,
            pointer.connector_path,
            pointer.color,
            connector_style=connector_style,
        )


def _render_label(
    svg: _Svg,
    label: ResolvedLabel,
    font_size: float,
    *,
    connector_style: dict[str, object],
    label_style: dict[str, object],
) -> None:
    """Render a label with optional connector to its target."""
    if label.connector_path:
        _render_connector(
            svg,
            label.connector_path,
            "rgba(180,180,200,0.5)",
            connector_style=connector_style,
        )
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
            text_anchor=label.text_anchor,
            css_class="annotation-pill annotation-pill--muted",
            label_style=label_style,
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
    custom_css: str = "",
    annotations: ResolvedAnnotations | None = None,
    render_settings: RenderSettings | None = None,
    profiler: RenderProfiler | None = None,
) -> str:
    """Render a Pcb as a layered SVG string from structured render settings.

    Parameters
    ----------
    board:
        Parsed PCB board.
    side:
        "front" or "back".  Back view mirrors horizontally.
    highlight_nets:
        Net names to highlight (case-insensitive exact match).
    highlight_components:
        Component references to highlight (footprint only, not nets).
    highlight_specs:
        Structured highlights with optional per-net/component colors.
        Merged with ``highlight_nets``/``highlight_components``.
    width_px:
        Pixel width of the SVG.
    custom_css:
        Extra CSS injected after structured render styles.
        Overrides any built-in rule.  Useful for board mask recoloring,
        layer visibility, etc.
    annotations:
        Resolved annotations to overlay on the board.
    """
    with _profile_span(profiler, "render.settings"):
        effective_settings = render_settings or load_render_settings_json(
            '{"extends": "phosphor:review"}'
        )
        plan_settings = _settings_for_plan(
            effective_settings,
            highlight_nets=highlight_nets,
            highlight_components=highlight_components,
            highlight_specs=highlight_specs,
            custom_css=custom_css,
        )
    with _profile_span(profiler, "render.build_plan"):
        if profiler is None:
            plan = build_derived_render_plan(
                board,
                settings=plan_settings,
                side=side,
                width_px=width_px,
                annotations=annotations,
            )
        else:
            plan = build_derived_render_plan(
                board,
                settings=plan_settings,
                side=side,
                width_px=width_px,
                annotations=annotations,
                profiler=profiler,
            )
    with _profile_span(profiler, "render.serialize"):
        svg = render_pcb_svg_from_derived_plan(plan, profiler=profiler)
    with _profile_span(profiler, "render.metadata"):
        return _append_pcb_metadata(svg, board)


@contextmanager
def _profile_span(profiler: RenderProfiler | None, name: str) -> Iterator[None]:
    if profiler is None:
        yield
        return
    with profiler.span(name):
        yield
