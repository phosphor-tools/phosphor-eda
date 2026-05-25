"""Parser and dataclasses for PCB render settings."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TypeGuard

INCLUDE_STATES = ("visible", "hidden", "when-highlighted", "never")
LAYER_SIDES = ("front", "back", "inner", "active", "opposite", "any")
LAYER_ROLES = ("copper", "silkscreen", "fabrication", "mask", "paste", "mechanical", "unknown")

_SETTINGS_EXTENDS_KEY = "extends"
_PAD_TARGET_RE = re.compile(r"^[^.]+\..+$")
_SIZE_STYLE_BASE_KEYS = (
    "stroke_width",
    "font_size",
    "connector_width",
    "text_halo_width",
    "pad_expansion",
)
_STYLE_UNIT_SUFFIXES = ("_mm", "_mil", "_px", "_scale")


@dataclass
class HighlightSpec:
    """A single net, component, or pad to highlight, with an optional color."""

    net: str = ""
    component: str = ""
    pad: str = ""
    color: str = ""


@dataclass
class LayerIncludeRule:
    role: str = ""
    side: str = "any"
    name: str = ""
    objects: dict[str, str] = field(default_factory=dict)


@dataclass
class IncludePolicy:
    board_outline: str = "visible"
    drills: str = "visible"
    vias: str = "visible"
    layers: list[LayerIncludeRule] = field(default_factory=list)


@dataclass
class StyleRule:
    match: dict[str, object]
    style: dict[str, object]


@dataclass
class RenderSettings:
    """Unified render configuration parsed from render-settings JSON."""

    side: str = ""
    width: int = 0
    font_size: float = 0.0
    highlights: list[HighlightSpec] = field(default_factory=list)
    include: IncludePolicy = field(default_factory=IncludePolicy)
    highlight_behavior: dict[str, object] = field(default_factory=dict)
    exclude_component_prefixes: tuple[str, ...] = ()
    style_rules: list[StyleRule] = field(default_factory=list)
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
        msg = "font_size is no longer supported; use font_size_px"
        raise ValueError(msg)

    if "font_size_px" in data:
        settings.font_size = _parse_font_size(data["font_size_px"], "font_size_px")

    if "highlights" in data:
        raw_highlights = data["highlights"]
        if not is_json_list(raw_highlights):
            msg = "highlights must be an array"
            raise ValueError(msg)
        for i, item in enumerate(raw_highlights):
            settings.highlights.append(_parse_highlight(item, i))

    if "include" in data:
        settings.include = _parse_include_policy(data["include"])

    if "highlight_behavior" in data:
        behavior = data["highlight_behavior"]
        if not is_json_dict(behavior):
            msg = "highlight_behavior must be an object"
            raise ValueError(msg)
        settings.highlight_behavior = behavior

    if "exclude_component_prefixes" in data:
        prefixes = data["exclude_component_prefixes"]
        if not is_json_list(prefixes):
            msg = "exclude_component_prefixes must be an array"
            raise ValueError(msg)
        parsed_prefixes: list[str] = []
        for index, prefix in enumerate(prefixes):
            if not isinstance(prefix, str) or not prefix:
                msg = f"exclude_component_prefixes[{index}] must be a non-empty string"
                raise ValueError(msg)
            parsed_prefixes.append(prefix.upper())
        settings.exclude_component_prefixes = tuple(parsed_prefixes)

    if "style_rules" in data:
        settings.style_rules = _parse_style_rules(data["style_rules"])

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


def _parse_include_policy(raw_include: object) -> IncludePolicy:
    if not is_json_dict(raw_include):
        msg = "include must be an object"
        raise ValueError(msg)

    include = IncludePolicy()
    for field_name in ("board_outline", "drills", "vias"):
        if field_name in raw_include:
            state = _parse_include_state(raw_include[field_name], f"include.{field_name}")
            setattr(include, field_name, state)

    if "layers" in raw_include:
        layers = raw_include["layers"]
        if not is_json_list(layers):
            msg = "include.layers must be an array"
            raise ValueError(msg)
        include.layers = [_parse_layer_include_rule(layer, i) for i, layer in enumerate(layers)]

    return include


def _parse_layer_include_rule(raw_layer: object, index: int) -> LayerIncludeRule:
    if not is_json_dict(raw_layer):
        msg = f"include.layers[{index}] must be an object"
        raise ValueError(msg)

    rule = LayerIncludeRule()
    if "role" in raw_layer:
        role = raw_layer["role"]
        if not isinstance(role, str) or role not in LAYER_ROLES:
            msg = f"include.layers[{index}].role must be one of {', '.join(LAYER_ROLES)}"
            raise ValueError(msg)
        rule.role = role
    if "side" in raw_layer:
        side = raw_layer["side"]
        if not isinstance(side, str) or side not in LAYER_SIDES:
            msg = f"include.layers[{index}].side must be one of {', '.join(LAYER_SIDES)}"
            raise ValueError(msg)
        rule.side = side
    if "name" in raw_layer:
        name = raw_layer["name"]
        if not isinstance(name, str):
            msg = f"include.layers[{index}].name must be a string"
            raise ValueError(msg)
        rule.name = name
    if "objects" in raw_layer:
        objects_path = f"include.layers[{index}].objects"
        rule.objects = _parse_layer_objects(raw_layer["objects"], objects_path)
    return rule


def _parse_layer_objects(raw_objects: object, path: str) -> dict[str, str]:
    if isinstance(raw_objects, str):
        return {"*": _parse_include_state(raw_objects, path)}
    if not is_json_dict(raw_objects):
        msg = f"{path} must be an object or include state"
        raise ValueError(msg)

    parsed: dict[str, str] = {}
    for object_name, state in raw_objects.items():
        parsed[object_name] = _parse_include_state(state, f"{path}.{object_name}")
    return parsed


def _parse_include_state(value: object, path: str) -> str:
    if not isinstance(value, str) or value not in INCLUDE_STATES:
        msg = f"{path} must be one of {', '.join(INCLUDE_STATES)}, got {value!r}"
        raise ValueError(msg)
    return value


def _parse_style_rules(raw_rules: object) -> list[StyleRule]:
    if not is_json_list(raw_rules):
        msg = "style_rules must be an array"
        raise ValueError(msg)

    rules: list[StyleRule] = []
    for index, raw_rule in enumerate(raw_rules):
        if not is_json_dict(raw_rule):
            msg = f"style_rules[{index}] must be an object"
            raise ValueError(msg)
        match = raw_rule.get("match", {})
        style = raw_rule.get("style", {})
        if not is_json_dict(match):
            msg = f"style_rules[{index}].match must be an object"
            raise ValueError(msg)
        if not is_json_dict(style):
            msg = f"style_rules[{index}].style must be an object"
            raise ValueError(msg)
        _validate_style_units(style, f"style_rules[{index}].style")
        rules.append(StyleRule(match=match, style=style))
    return rules


def _validate_style_units(style: dict[str, object], path: str) -> None:
    for base_key in _SIZE_STYLE_BASE_KEYS:
        if base_key in style:
            msg = f"{path}.{base_key} must use an explicit unit suffix"
            raise ValueError(msg)

        present_suffixes = [
            suffix for suffix in _STYLE_UNIT_SUFFIXES if f"{base_key}{suffix}" in style
        ]
        if len(present_suffixes) > 1:
            msg = f"{path}.{base_key} must not use duplicate unit variants"
            raise ValueError(msg)
