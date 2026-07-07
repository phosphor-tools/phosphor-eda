"""Parser and dataclasses for PCB render settings."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from importlib.resources import files
from pathlib import Path
from typing import Literal, TypeGuard, cast

from phosphor_eda.domain.pcb import LayerRole, PcbArtworkKind, PcbConductorKind
from phosphor_eda.render.inventory import InventoryItemKind, InventoryPurpose
from phosphor_eda.render.view import VIEW_ROTATIONS

RENDER_MODES = ("eda", "realistic")
SOURCE_LAYER_ROLES = tuple(role.value for role in LayerRole)
SOURCE_LAYER_SIDES = ("front", "back", "inner", "active", "")
SOURCE_ITEM_KINDS = tuple(kind.value for kind in InventoryItemKind)
SOURCE_PURPOSES = tuple(purpose.value for purpose in InventoryPurpose)
SOURCE_CONTENT_KINDS = tuple(
    dict.fromkeys(
        (
            *(kind.value for kind in PcbArtworkKind),
            *(kind.value for kind in PcbConductorKind),
        )
    )
)

_BUNDLED_SETTINGS_PACKAGE = "phosphor_eda.render.profiles"

# Bundled presets, addressed as ``extends: "phosphor:<name>"``. Adding a
# preset file requires registering it here and documenting it in skill.md
# (guarded by tests/test_pcb_render_presets.py).
BUNDLED_PRESETS = ("realistic", "design", "print", "documentation")
DEFAULT_PRESET = "realistic"
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

# Upper bound on user-supplied custom CSS (repo rule: every string bounded).
MAX_CUSTOM_CSS_LENGTH = 100_000

# Settings keys that were removed; map each to its replacement guidance so a
# stale settings file fails with a clear migration message.
_REMOVED_KEYS: dict[str, str] = {
    "theme": "use extends instead",
    "font_size": "use fontSizePt",
    "font_size_px": "use fontSizePt",
    "fontSizePx": "use fontSizePt (points at the standard display width)",
    "include": "use source layer selection",
    "highlight_behavior": "use dimming and highlight tokens",
    "style_rules": "use semantic tokens",
    "exclude_component_prefixes": "use source.excludeComponents",
}

# Token names that changed unit from display pixels to points; map each to
# its replacement so a stale settings file fails with a clear message.
_REMOVED_TOKENS: dict[str, str] = {
    "annotation.label.textHaloWidthPx": "annotation.label.textHaloWidthPt",
    "annotation.connector.strokeWidthPx": "annotation.connector.strokeWidthPt",
    "highlight.marker.minDiameterPx": "highlight.marker.minDiameterPt",
    "highlight.marker.strokeWidthPx": "highlight.marker.strokeWidthPt",
}


@dataclass
class HighlightSpec:
    """A single net, component, or pad to highlight, with optional styling.

    ``color`` overrides the highlight fill; ``stroke``/``stroke_width_mm``
    outline this highlight's artwork so it reads over other highlights.
    Net highlights follow the signal through series passives by default
    (schematic-bridged closure); ``exact`` restricts the match to the named
    net only.
    """

    net: str = ""
    component: str = ""
    pad: str = ""
    color: str = ""
    stroke: str = ""
    stroke_width_mm: float = 0.0
    exact: bool = False


@dataclass
class LayerMatch:
    name: str = ""
    role: str = ""
    side: str = ""


@dataclass
class LayerSelectionRule:
    match: LayerMatch = field(default_factory=LayerMatch)
    visible: bool = True
    item_kinds: tuple[str, ...] = ()
    purposes: tuple[str, ...] = ()
    content_kinds: tuple[str, ...] = ()


@dataclass
class SourceSelection:
    layers: list[LayerSelectionRule] = field(default_factory=list)
    exclude_components: tuple[str, ...] = ()


DIMMING_MODES = ("off", "on", "auto")
type DimmingMode = Literal["off", "on", "auto"]


@dataclass
class DimmingSettings:
    """Highlight-driven base-layer dimming.

    ``auto`` dims base layers only when at least one highlight resolves;
    ``on`` always paints the dim scrim; ``off`` never does.
    """

    mode: DimmingMode = "auto"


@dataclass(frozen=True)
class RenderSettings:
    """Unified render configuration parsed from render-settings JSON."""

    render_mode: RenderMode = "eda"
    side: str = ""
    rotation: int = 0
    width: int = 0
    font_size: float = 0.0
    background: str = ""
    debug_attributes: bool = False
    source: SourceSelection = field(default_factory=SourceSelection)
    tokens: TokenMap = field(default_factory=dict)
    dimming: DimmingSettings = field(default_factory=DimmingSettings)
    highlights: list[HighlightSpec] = field(default_factory=list)
    annotations: dict[str, object] = field(default_factory=dict)
    custom_css: str = ""


DEFAULT_SIDE = "front"
DEFAULT_WIDTH = 800
# Annotation font size in points (screen-relative; see render/view.py).
DEFAULT_FONT_SIZE = 20.0
DEFAULT_BACKGROUND = "#ffffff"

# Upper bound on a CSS color value for the canvas background.
MAX_BACKGROUND_LENGTH = 64


@dataclass(frozen=True)
class CliOverrides:
    """Explicitly-set CLI render flags layered over a base settings object.

    Each field is ``None`` when the flag was left at its default, so the
    base (settings-file / bundled) value wins. ``highlights`` are always
    merged additively, so an empty tuple is a no-op.
    """

    side: str | None = None
    rotation: int | None = None
    width: int | None = None
    font_size: float | None = None
    custom_css: str | None = None
    debug_attributes: bool | None = None
    highlights: tuple[HighlightSpec, ...] = ()


def resolve_effective_settings(
    base: RenderSettings,
    overrides: CliOverrides,
) -> RenderSettings:
    """Fold CLI overrides into *base* and fill defaults for unset values.

    The result is a fully-resolved ``RenderSettings``: ``side``, ``width``,
    and ``font_size`` are concrete (never empty/zero), CLI highlights are
    merged with the base highlights, and ``custom_css`` is composed
    base-then-CLI. An explicitly empty CLI ``custom_css`` clears the base
    CSS.
    """
    side = overrides.side or base.side or DEFAULT_SIDE
    rotation = overrides.rotation if overrides.rotation is not None else base.rotation
    debug_attributes = (
        overrides.debug_attributes
        if overrides.debug_attributes is not None
        else base.debug_attributes
    )
    width = overrides.width or base.width or DEFAULT_WIDTH
    font_size = overrides.font_size or base.font_size or DEFAULT_FONT_SIZE
    background = base.background or DEFAULT_BACKGROUND

    highlights = list(base.highlights)
    for highlight in overrides.highlights:
        if highlight not in highlights:
            highlights.append(highlight)

    if overrides.custom_css is None:
        custom_css = base.custom_css
    elif not overrides.custom_css:
        custom_css = ""
    else:
        css_parts = [css for css in (base.custom_css, overrides.custom_css) if css]
        custom_css = "\n".join(css_parts)
    if len(custom_css) > MAX_CUSTOM_CSS_LENGTH:
        msg = f"custom_css must be at most {MAX_CUSTOM_CSS_LENGTH} characters"
        raise ValueError(msg)

    return replace(
        base,
        side=side,
        rotation=rotation,
        width=width,
        font_size=font_size,
        background=background,
        debug_attributes=debug_attributes,
        highlights=highlights,
        custom_css=custom_css,
    )


def parse_highlight_target(target: str) -> HighlightSpec:
    """Parse a CLI ``--highlight-pad`` value into a ``HighlightSpec``.

    Accepts a ``<component>.<pad>`` pad target. Raises ``ValueError`` on a
    malformed value.
    """
    return _parse_highlight({"pad": target}, 0)


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


def load_bundled_render_settings(name: str) -> RenderSettings:
    """Load a bundled ``phosphor:<name>`` render settings profile.

    Raises ``ValueError`` for an unknown or malformed bundled name.
    """
    if not _PHOSPHOR_SETTINGS_RE.fullmatch(name):
        msg = f"Invalid phosphor render settings name: {name!r}"
        raise ValueError(msg)
    data = _load_parent_render_settings(f"phosphor:{name}", source=None, stack=[])
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
            "rotation": {
                "type": "integer",
                "enum": list(VIEW_ROTATIONS),
                "description": (
                    "Clockwise view rotation in degrees, applied after the "
                    "back-side mirror. Annotation labels stay upright."
                ),
            },
            "width": {
                "type": "integer",
                "minimum": 1,
            },
            "fontSizePt": {
                "type": "number",
                "minimum": 1,
                "maximum": 500,
                "description": (
                    "Annotation label font size in points, as seen when the "
                    "image is viewed at a standard content-column width "
                    "(~1000 px). Independent of render width and board size."
                ),
            },
            "debugAttributes": {
                "type": "boolean",
                "description": (
                    "Emit per-element data-* provenance attributes "
                    "(component/net/pad identity on every path) for CSS "
                    "targeting and debugging. Off by default: they multiply "
                    "file size several-fold."
                ),
            },
            "background": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_BACKGROUND_LENGTH,
                "description": (
                    "Canvas background CSS color (default '#ffffff'). "
                    "Use 'none' for a transparent canvas."
                ),
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
                                        "role": {
                                            "type": "string",
                                            "enum": list(SOURCE_LAYER_ROLES),
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
                                "itemKinds": {
                                    "type": "array",
                                    "items": {"type": "string", "enum": list(SOURCE_ITEM_KINDS)},
                                },
                                "purposes": {
                                    "type": "array",
                                    "items": {"type": "string", "enum": list(SOURCE_PURPOSES)},
                                },
                                "contentKinds": {
                                    "type": "array",
                                    "items": {"type": "string", "enum": list(SOURCE_CONTENT_KINDS)},
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
                    "mode": {
                        "type": "string",
                        "enum": list(DIMMING_MODES),
                        "description": (
                            "off: never dim; on: always dim base layers; "
                            "auto (default): dim base layers when a highlight resolves."
                        ),
                    },
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
                        "stroke": {
                            "type": "string",
                            "description": "Outline color for this highlight's artwork.",
                        },
                        "strokeWidthMm": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": "Outline width in board mm.",
                        },
                        "exact": {
                            "type": "boolean",
                            "description": (
                                "Match only the named net instead of following "
                                "the signal through series passives."
                            ),
                        },
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
                "maxLength": MAX_CUSTOM_CSS_LENGTH,
            },
        },
        "$defs": {
            "token_value": {
                "type": ["string", "number", "boolean"],
            },
        },
        "examples": [
            {
                "extends": "phosphor:documentation",
                "renderMode": "eda",
                "fontSizePt": 12,
                "source": {
                    "layers": [
                        {"match": {"role": "copper"}, "visible": True},
                        {
                            "match": {"role": "silkscreen", "side": "front"},
                            "visible": True,
                        },
                    ],
                    "excludeComponents": ["R*", "C*", "L*"],
                },
                "tokens": {
                    "eda.copper.front.fill": "#d17a22",
                    "eda.layer[F.Cu].fill": "#d17a22",
                },
                "dimming": {"mode": "auto"},
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
    if _SETTINGS_EXTENDS_KEY in data:
        extends = data[_SETTINGS_EXTENDS_KEY]
        if not isinstance(extends, str):
            msg = "extends must be a string"
            raise ValueError(msg)

    for removed_key, guidance in _REMOVED_KEYS.items():
        if removed_key in data:
            msg = f"{removed_key} is no longer supported; {guidance}"
            raise ValueError(msg)

    render_mode: RenderMode = "eda"
    if "renderMode" in data:
        raw_mode = data["renderMode"]
        if not isinstance(raw_mode, str) or raw_mode not in RENDER_MODES:
            msg = f"renderMode must be one of {', '.join(RENDER_MODES)}, got {raw_mode!r}"
            raise ValueError(msg)
        render_mode = raw_mode

    side = ""
    if "side" in data:
        raw_side = data["side"]
        if not isinstance(raw_side, str) or raw_side not in ("front", "back"):
            msg = f"side must be 'front' or 'back', got {raw_side!r}"
            raise ValueError(msg)
        side = raw_side

    rotation = 0
    if "rotation" in data:
        raw_rotation = data["rotation"]
        if (
            not isinstance(raw_rotation, int)
            or isinstance(raw_rotation, bool)
            or raw_rotation not in VIEW_ROTATIONS
        ):
            allowed = ", ".join(str(value) for value in VIEW_ROTATIONS)
            msg = f"rotation must be one of {allowed}, got {raw_rotation!r}"
            raise ValueError(msg)
        rotation = raw_rotation

    width = 0
    if "width" in data:
        raw_width = data["width"]
        if not isinstance(raw_width, int) or isinstance(raw_width, bool) or raw_width <= 0:
            msg = f"width must be a positive integer, got {raw_width!r}"
            raise ValueError(msg)
        width = raw_width

    font_size = 0.0
    if "fontSizePt" in data:
        font_size = _parse_font_size(data["fontSizePt"], "fontSizePt")

    debug_attributes = False
    if "debugAttributes" in data:
        raw_debug = data["debugAttributes"]
        if not isinstance(raw_debug, bool):
            msg = f"debugAttributes must be a boolean, got {raw_debug!r}"
            raise ValueError(msg)
        debug_attributes = raw_debug

    background = ""
    if "background" in data:
        raw_background = data["background"]
        if (
            not isinstance(raw_background, str)
            or not raw_background
            or len(raw_background) > MAX_BACKGROUND_LENGTH
        ):
            msg = (
                "background must be a CSS color string of at most "
                f"{MAX_BACKGROUND_LENGTH} characters (or 'none'), got {raw_background!r}"
            )
            raise ValueError(msg)
        background = raw_background

    source = _parse_source_selection(data["source"]) if "source" in data else SourceSelection()
    tokens = _parse_tokens(data["tokens"]) if "tokens" in data else {}
    dimming = _parse_dimming_settings(data["dimming"]) if "dimming" in data else DimmingSettings()

    highlights: list[HighlightSpec] = []
    if "highlights" in data:
        raw_highlights = data["highlights"]
        if not is_json_list(raw_highlights):
            msg = "highlights must be an array"
            raise ValueError(msg)
        highlights = [_parse_highlight(item, i) for i, item in enumerate(raw_highlights)]

    annotations: dict[str, object] = {}
    if "annotations" in data:
        ann = data["annotations"]
        if not is_json_dict(ann):
            msg = "annotations must be an object"
            raise ValueError(msg)
        # Validate the annotation block at parse time so a bad settings file
        # fails at load rather than during rendering. Lazy import: annotations
        # pulls in ortools (CP-SAT solver), too heavy for every CLI invocation.
        from phosphor_eda.render.annotations import parse_annotations

        _ = parse_annotations(ann)
        annotations = ann

    custom_css = ""
    if "custom_css" in data:
        css = data["custom_css"]
        if not isinstance(css, str):
            msg = "custom_css must be a string"
            raise ValueError(msg)
        if len(css) > MAX_CUSTOM_CSS_LENGTH:
            msg = f"custom_css must be at most {MAX_CUSTOM_CSS_LENGTH} characters"
            raise ValueError(msg)
        custom_css = css

    return RenderSettings(
        render_mode=render_mode,
        side=side,
        rotation=rotation,
        width=width,
        font_size=font_size,
        background=background,
        debug_attributes=debug_attributes,
        source=source,
        tokens=tokens,
        dimming=dimming,
        highlights=highlights,
        annotations=annotations,
        custom_css=custom_css,
    )


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
        msg = (
            f"source.layers[{index}].objects is no longer supported; "
            "use itemKinds, purposes, or contentKinds"
        )
        raise ValueError(msg)
    if "itemKinds" in raw_layer:
        rule.item_kinds = _parse_string_tuple(
            raw_layer["itemKinds"],
            f"source.layers[{index}].itemKinds",
            allowed=SOURCE_ITEM_KINDS,
        )
    if "purposes" in raw_layer:
        rule.purposes = _parse_string_tuple(
            raw_layer["purposes"],
            f"source.layers[{index}].purposes",
            allowed=SOURCE_PURPOSES,
        )
    if "contentKinds" in raw_layer:
        rule.content_kinds = _parse_string_tuple(
            raw_layer["contentKinds"],
            f"source.layers[{index}].contentKinds",
            allowed=SOURCE_CONTENT_KINDS,
        )

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
        msg = f"source.layers[{index}].match.function is no longer supported; use match.role"
        raise ValueError(msg)

    if "role" in raw_match:
        role = raw_match["role"]
        if not isinstance(role, str) or role not in SOURCE_LAYER_ROLES:
            msg = (
                f"source.layers[{index}].match.role must be one of {', '.join(SOURCE_LAYER_ROLES)}"
            )
            raise ValueError(msg)
        match.role = role

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


def _parse_string_tuple(
    value: object,
    path: str,
    *,
    allowed: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    if not is_json_list(value):
        msg = f"{path} must be an array"
        raise ValueError(msg)
    parsed: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            msg = f"{path}[{index}] must be a non-empty string"
            raise ValueError(msg)
        if allowed is not None and item not in allowed:
            msg = f"{path}[{index}] must be one of {', '.join(allowed)}, got {item!r}"
            raise ValueError(msg)
        parsed.append(item)
    return tuple(parsed)


def _parse_tokens(raw_tokens: object) -> TokenMap:
    if not is_json_dict(raw_tokens):
        msg = "tokens must be an object"
        raise ValueError(msg)

    tokens: TokenMap = {}
    for key, value in raw_tokens.items():
        if key in _REMOVED_TOKENS:
            msg = f"token {key!r} is no longer supported; use {_REMOVED_TOKENS[key]!r} (points)"
            raise ValueError(msg)
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

    if "enabled" in raw_dimming:
        msg = "dimming.enabled is no longer supported; use dimming.mode (off, on, auto)"
        raise ValueError(msg)

    unknown_keys = sorted(set(raw_dimming) - {"mode"})
    if unknown_keys:
        msg = f"unknown dimming key(s): {', '.join(unknown_keys)} (supported: mode)"
        raise ValueError(msg)

    dimming = DimmingSettings()
    if "mode" in raw_dimming:
        mode = raw_dimming["mode"]
        if mode not in ("off", "on", "auto"):
            msg = f"dimming.mode must be one of {', '.join(DIMMING_MODES)}, got {mode!r}"
            raise ValueError(msg)
        dimming.mode = mode
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
    for field_name in ("net", "component", "pad", "color", "stroke"):
        if field_name in item and not isinstance(item[field_name], str):
            msg = f"highlights[{index}].{field_name} must be a string"
            raise ValueError(msg)
    stroke_width_mm = 0.0
    if "strokeWidthMm" in item:
        raw_width = item["strokeWidthMm"]
        if (
            not isinstance(raw_width, int | float)
            or isinstance(raw_width, bool)
            or not math.isfinite(raw_width)
            or raw_width <= 0
        ):
            msg = f"highlights[{index}].strokeWidthMm must be a positive number, got {raw_width!r}"
            raise ValueError(msg)
        stroke_width_mm = float(raw_width)
    exact = item.get("exact", False)
    if not isinstance(exact, bool):
        msg = f"highlights[{index}].exact must be a boolean"
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
    stroke = str(item.get("stroke", ""))
    return HighlightSpec(
        net=net,
        component=component,
        pad=pad,
        color=color,
        stroke=stroke,
        stroke_width_mm=stroke_width_mm,
        exact=exact,
    )


def _load_render_settings_file_data(path: Path, stack: list[str]) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
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
        if name not in BUNDLED_PRESETS:
            msg = (
                f"Unknown phosphor render settings: {parent_ref} "
                f"(available: {', '.join(BUNDLED_PRESETS)})"
            )
            raise ValueError(msg)
        resource = files(_BUNDLED_SETTINGS_PACKAGE).joinpath(f"{name}.json")
        if not resource.is_file():
            msg = f"Bundled render settings file missing for registered preset: {name}"
            raise ValueError(msg)
        text = resource.read_text(encoding="utf-8")
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
        _string_key_part(raw_match.get("role")),
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
