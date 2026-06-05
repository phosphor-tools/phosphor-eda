"""Parser and dataclasses for PCB render settings."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Literal, TypeGuard, cast

RENDER_MODES = ("eda", "realistic")
SOURCE_LAYER_FUNCTIONS = (
    "copper",
    "silkscreen",
    "solder_mask",
    "solder_paste",
    "fab",
    "courtyard",
    "edge",
    "drill",
    "keepout",
    "mechanical",
    "other",
)
SOURCE_LAYER_SIDES = ("front", "back", "inner", "active", "")

_BUNDLED_SETTINGS_PACKAGE = "phosphor_eda.render_settings"
_SETTINGS_EXTENDS_KEY = "extends"
_PHOSPHOR_SETTINGS_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PAD_TARGET_RE = re.compile(r"^[^.]+\..+$")
_DOT_TOKEN_RE = re.compile(r"^(?:eda|realistic|highlight|annotation)(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
_NATIVE_LAYER_TOKEN_RE = re.compile(
    r"^(?:eda|realistic|highlight)\.layer\[[^\]\r\n]+\]\.[A-Za-z][A-Za-z0-9_]*$"
)
type RenderMode = Literal["eda", "realistic"]
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

    render_mode: RenderMode = "eda"
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
                                "visible": {
                                    "type": "boolean",
                                    "description": (
                                        "Whether this selected source layer is rendered. "
                                        "When extending another settings file, a child rule "
                                        "with the same match identity can set this false to "
                                        "hide an inherited source layer."
                                    ),
                                },
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
                    r"^(eda|realistic|highlight|annotation)(\.[A-Za-z][A-Za-z0-9_]*)+$": {
                        "$ref": "#/$defs/token_value"
                    },
                    r"^(eda|realistic|highlight)\.layer\[[^\]\r\n]+\]\.[A-Za-z][A-Za-z0-9_]*$": {
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
                "renderMode": "eda",
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
                    "eda.copper.front.fill": "#d17a22",
                    "eda.layer[F.Cu].fill": "#d17a22",
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


def _load_render_settings_file_data(path: Path, stack: list[str]) -> dict[str, object]:
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
) -> dict[str, object]:
    source_id = _settings_source_id(source)
    if source_id in stack:
        cycle = " -> ".join([*stack, source_id])
        msg = f"render settings extends cycle detected: {cycle}"
        raise ValueError(msg)

    try:
        raw = cast("object", json.loads(text))
    except json.JSONDecodeError as exc:
        msg = f"Invalid render settings JSON: {exc}"
        raise ValueError(msg) from exc
    if not is_json_dict(raw):
        msg = "top-level JSON value must be an object"
        raise ValueError(msg)

    data = dict(raw)
    _ = parse_render_settings(data)
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
) -> dict[str, object]:
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
    parent: dict[str, object],
    child: dict[str, object],
) -> dict[str, object]:
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
        elif key == "source" and is_json_dict(existing) and is_json_dict(value):
            merged[key] = _merge_source_selection_data(existing, value)
        elif key == "custom_css":
            css_parts = [css for css in (parent_css, child_css) if isinstance(css, str) and css]
            merged[key] = "\n".join(css_parts)
        else:
            merged[key] = value
    return merged


def _merge_source_selection_data(
    parent: dict[str, object],
    child: dict[str, object],
) -> dict[str, object]:
    merged = dict(parent)
    parent_layers = parent.get("layers")
    child_layers = child.get("layers")
    if is_json_list(parent_layers) and is_json_list(child_layers):
        merged["layers"] = _merge_source_layer_rules(parent_layers, child_layers)
    for key, value in child.items():
        if key != "layers":
            merged[key] = value
    return merged


def _merge_source_layer_rules(
    parent_layers: list[object],
    child_layers: list[object],
) -> list[object]:
    merged_layers = list(parent_layers)
    indexes_by_key = {
        key: index
        for index, layer in enumerate(merged_layers)
        if (key := _source_layer_rule_key(layer)) is not None
    }
    for child_layer in child_layers:
        key = _source_layer_rule_key(child_layer)
        if key is None or key not in indexes_by_key:
            merged_layers.append(child_layer)
            if key is not None:
                indexes_by_key[key] = len(merged_layers) - 1
            continue
        index = indexes_by_key[key]
        parent_layer = merged_layers[index]
        if is_json_dict(parent_layer) and is_json_dict(child_layer):
            merged_layers[index] = _deep_merge_json_dicts(parent_layer, child_layer)
        else:
            merged_layers[index] = child_layer
    return merged_layers


def _source_layer_rule_key(layer: object) -> tuple[str, str, str] | None:
    if not is_json_dict(layer):
        return None
    raw_match = layer.get("match", {})
    if not is_json_dict(raw_match):
        return None
    return (
        _string_key_part(raw_match.get("name")),
        _string_key_part(raw_match.get("function")),
        _string_key_part(raw_match.get("side")),
    )


def _string_key_part(value: object) -> str:
    return value if isinstance(value, str) else ""


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
