"""Parser and dataclasses for PCB render settings."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal, TypeGuard

RENDER_MODES = ("cad", "realistic")
SOURCE_LAYER_FUNCTIONS = (
    "copper",
    "silkscreen",
    "solder_mask",
    "solder_paste",
    "fab",
    "courtyard",
    "edge",
    "mechanical",
    "other",
)
SOURCE_LAYER_SIDES = ("front", "back", "inner", "")

_SETTINGS_EXTENDS_KEY = "extends"
_PAD_TARGET_RE = re.compile(r"^[^.]+\..+$")
_DOT_TOKEN_RE = re.compile(r"^(?:cad|realistic|highlight|annotation)(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
_NATIVE_LAYER_TOKEN_RE = re.compile(
    r"^(?:cad|realistic|highlight)\.layer\[[^\]\r\n]+\]\.[A-Za-z][A-Za-z0-9_]*$"
)
type RenderMode = Literal["cad", "realistic"]
type TokenValue = str | int | float | bool
type TokenMap = dict[str, TokenValue]


@dataclass
class HighlightSpec:
    """A single net, component, or pad to highlight, with an optional color."""

    net: str = ""
    component: str = ""
    pad: str = ""
    color: str = ""


@dataclass
class LayerMatch:
    name: str = ""
    function: str = ""
    side: str = ""


@dataclass
class LayerSelectionRule:
    match: LayerMatch = field(default_factory=LayerMatch)
    visible: bool = True
    objects: tuple[str, ...] = ()


@dataclass
class SourceSelection:
    layers: list[LayerSelectionRule] = field(default_factory=list)
    exclude_components: tuple[str, ...] = ()


@dataclass
class DimmingSettings:
    enabled: bool = False


@dataclass
class RenderSettings:
    """Unified render configuration parsed from render-settings JSON."""

    render_mode: RenderMode = "cad"
    side: str = ""
    width: int = 0
    font_size: float = 0.0
    source: SourceSelection = field(default_factory=SourceSelection)
    tokens: TokenMap = field(default_factory=dict)
    dimming: DimmingSettings = field(default_factory=DimmingSettings)
    highlights: list[HighlightSpec] = field(default_factory=list)
    annotations: dict[str, object] = field(default_factory=dict)
    custom_css: str = ""


def is_json_dict(v: object) -> TypeGuard[dict[str, object]]:
    """Narrow an object to ``dict[str, object]``."""
    return isinstance(v, dict)


def is_json_list(v: object) -> TypeGuard[list[object]]:
    """Narrow an object to ``list[object]``."""
    return isinstance(v, list)


def parse_render_settings(data: dict[str, object]) -> RenderSettings:
    """Parse a render-settings JSON dict into a ``RenderSettings`` object.

    Raises ``ValueError`` on invalid input.
    """
    settings = RenderSettings()

    if _SETTINGS_EXTENDS_KEY in data:
        extends = data[_SETTINGS_EXTENDS_KEY]
        if not isinstance(extends, str):
            msg = "extends must be a string"
            raise ValueError(msg)

    if "theme" in data:
        msg = "theme is no longer supported; use extends instead"
        raise ValueError(msg)

    if "renderMode" in data:
        render_mode = data["renderMode"]
        if not isinstance(render_mode, str) or render_mode not in RENDER_MODES:
            msg = f"renderMode must be one of {', '.join(RENDER_MODES)}, got {render_mode!r}"
            raise ValueError(msg)
        settings.render_mode = render_mode

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

    if "font_size" in data:
        msg = "font_size is no longer supported; use fontSizePx"
        raise ValueError(msg)

    if "font_size_px" in data:
        msg = "font_size_px is no longer supported; use fontSizePx"
        raise ValueError(msg)

    if "include" in data:
        msg = "include is no longer supported; use source layer selection"
        raise ValueError(msg)

    if "highlight_behavior" in data:
        msg = "highlight_behavior is no longer supported; use dimming and highlight tokens"
        raise ValueError(msg)

    if "style_rules" in data:
        msg = "style_rules is no longer supported; use semantic tokens"
        raise ValueError(msg)

    if "exclude_component_prefixes" in data:
        msg = "exclude_component_prefixes is no longer supported; use source.excludeComponents"
        raise ValueError(msg)

    if "fontSizePx" in data:
        settings.font_size = _parse_font_size(data["fontSizePx"], "fontSizePx")

    if "source" in data:
        settings.source = _parse_source_selection(data["source"])

    if "tokens" in data:
        settings.tokens = _parse_tokens(data["tokens"])

    if "dimming" in data:
        settings.dimming = _parse_dimming_settings(data["dimming"])

    if "highlights" in data:
        raw_highlights = data["highlights"]
        if not is_json_list(raw_highlights):
            msg = "highlights must be an array"
            raise ValueError(msg)
        for i, item in enumerate(raw_highlights):
            settings.highlights.append(_parse_highlight(item, i))

    if "annotations" in data:
        ann = data["annotations"]
        if not is_json_dict(ann):
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


def _parse_source_selection(raw_source: object) -> SourceSelection:
    if not is_json_dict(raw_source):
        msg = "source must be an object"
        raise ValueError(msg)

    source = SourceSelection()
    if "layers" in raw_source:
        layers = raw_source["layers"]
        if not is_json_list(layers):
            msg = "source.layers must be an array"
            raise ValueError(msg)
        source.layers = [_parse_layer_selection_rule(layer, i) for i, layer in enumerate(layers)]

    if "excludeComponents" in raw_source:
        source.exclude_components = _parse_string_tuple(
            raw_source["excludeComponents"],
            "source.excludeComponents",
        )

    return source


def _parse_layer_selection_rule(raw_layer: object, index: int) -> LayerSelectionRule:
    if not is_json_dict(raw_layer):
        msg = f"source.layers[{index}] must be an object"
        raise ValueError(msg)

    raw_match = raw_layer.get("match", {})
    if not is_json_dict(raw_match):
        msg = f"source.layers[{index}].match must be an object"
        raise ValueError(msg)

    rule = LayerSelectionRule(match=_parse_layer_match(raw_match, index))
    if "visible" in raw_layer:
        visible = raw_layer["visible"]
        if not isinstance(visible, bool):
            msg = f"source.layers[{index}].visible must be a boolean"
            raise ValueError(msg)
        rule.visible = visible

    if "objects" in raw_layer:
        rule.objects = _parse_string_tuple(raw_layer["objects"], f"source.layers[{index}].objects")

    return rule


def _parse_layer_match(raw_match: dict[str, object], index: int) -> LayerMatch:
    match = LayerMatch()
    if "name" in raw_match:
        name = raw_match["name"]
        if not isinstance(name, str) or not name:
            msg = f"source.layers[{index}].match.name must be a non-empty string"
            raise ValueError(msg)
        match.name = name

    if "function" in raw_match:
        function = raw_match["function"]
        if not isinstance(function, str) or function not in SOURCE_LAYER_FUNCTIONS:
            msg = (
                f"source.layers[{index}].match.function must be one of "
                f"{', '.join(SOURCE_LAYER_FUNCTIONS)}"
            )
            raise ValueError(msg)
        match.function = function

    if "side" in raw_match:
        side = raw_match["side"]
        if not isinstance(side, str) or side not in SOURCE_LAYER_SIDES:
            msg = (
                f"source.layers[{index}].match.side must be one of "
                f"{', '.join(repr(side) for side in SOURCE_LAYER_SIDES)}"
            )
            raise ValueError(msg)
        match.side = side

    return match


def _parse_string_tuple(value: object, path: str) -> tuple[str, ...]:
    if not is_json_list(value):
        msg = f"{path} must be an array"
        raise ValueError(msg)
    parsed: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            msg = f"{path}[{index}] must be a non-empty string"
            raise ValueError(msg)
        parsed.append(item)
    return tuple(parsed)


def _parse_tokens(raw_tokens: object) -> TokenMap:
    if not is_json_dict(raw_tokens):
        msg = "tokens must be an object"
        raise ValueError(msg)

    tokens: TokenMap = {}
    for key, value in raw_tokens.items():
        if not _DOT_TOKEN_RE.fullmatch(key) and not _NATIVE_LAYER_TOKEN_RE.fullmatch(key):
            msg = f"tokens key must be a dot token or native layer token, got {key!r}"
            raise ValueError(msg)
        if not isinstance(value, str | int | float | bool) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            msg = f"tokens[{key!r}] must be a string, number, or boolean"
            raise ValueError(msg)
        tokens[key] = value
    return tokens


def _parse_dimming_settings(raw_dimming: object) -> DimmingSettings:
    if not is_json_dict(raw_dimming):
        msg = "dimming must be an object"
        raise ValueError(msg)

    dimming = DimmingSettings()
    if "enabled" in raw_dimming:
        enabled = raw_dimming["enabled"]
        if not isinstance(enabled, bool):
            msg = "dimming.enabled must be a boolean"
            raise ValueError(msg)
        dimming.enabled = enabled
    return dimming


def _parse_font_size(value: object, field_name: str) -> float:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 1
        or value > 500
    ):
        msg = f"{field_name} must be a number from 1 to 500, got {value!r}"
        raise ValueError(msg)
    return float(value)


def _parse_highlight(item: object, index: int) -> HighlightSpec:
    if not is_json_dict(item):
        msg = f"highlights[{index}] must be an object"
        raise ValueError(msg)
    for field_name in ("net", "component", "pad", "color"):
        if field_name in item and not isinstance(item[field_name], str):
            msg = f"highlights[{index}].{field_name} must be a string"
            raise ValueError(msg)
    net = str(item.get("net", ""))
    component = str(item.get("component", ""))
    pad = str(item.get("pad", ""))
    has_net = bool(net)
    has_comp = bool(component)
    has_pad = bool(pad)
    if sum((has_net, has_comp, has_pad)) != 1:
        msg = f"highlights[{index}] must have exactly one of 'net', 'component', or 'pad'"
        raise ValueError(msg)
    if has_pad and not _PAD_TARGET_RE.fullmatch(pad):
        msg = f"highlights[{index}].pad must be '<component>.<pad>', got {pad!r}"
        raise ValueError(msg)
    color = str(item.get("color", ""))
    return HighlightSpec(net=net, component=component, pad=pad, color=color)
