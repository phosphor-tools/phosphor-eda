"""Style token resolver for derived PCB render layers."""

from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class VisualRole:
    namespace: str
    function: str
    side: str = ""
    inner_index: int | None = None
    source_layer_name: str = ""


@dataclass(frozen=True)
class ResolvedStyle:
    fill: str | None = None
    stroke: str | None = None
    opacity: float | None = None
    stroke_width_mm: float | None = None


@dataclass(frozen=True)
class _TokenResolution:
    token: str
    value: object


_STYLE_PROPERTIES = ("fill", "stroke", "opacity", "strokeWidthMm")
_EDA_NAMESPACE = "eda"
_REALISTIC_NAMESPACE = "realistic"
_EDA_COPPER_ANCHOR_COLORS = {
    "F.Cu": "#cc0000",
    "Top Layer": "#cc0000",
    "Top": "#cc0000",
    "B.Cu": "#0000cc",
    "Bottom Layer": "#0000cc",
    "Bottom": "#0000cc",
}
_EDA_SILKSCREEN_ANCHOR_COLORS = {
    "F.SilkS": "#ffffff",
    "Top Overlay": "#ffffff",
    "B.SilkS": "#ffff00",
    "Bottom Overlay": "#ffff00",
}
_EDA_EDGE_ANCHOR_COLORS = {
    "Edge.Cuts": "#202020",
    "Board Shape": "#202020",
}
_EDA_DRILL_ANCHOR_COLORS = {
    "drills": "#202020",
}
_EDA_COPPER_FRONT_COLOR = "#cc0000"
_EDA_COPPER_BACK_COLOR = "#0000cc"
_EDA_COPPER_OPACITY = 1.0
_EDA_SILKSCREEN_FRONT_COLOR = "#ffffff"
_EDA_SILKSCREEN_BACK_COLOR = "#ffff00"
_EDA_EDGE_COLOR = "#202020"
_EDA_DRILL_COLOR = "#202020"
_EDA_KEEPOUT_COLOR = "#cc2e7f"
_EDA_FABRICATION_COLOR = "#666666"
_EDA_MECHANICAL_COLOR = "#777777"
_EDA_TEXT_COLOR = "#777777"
_HIGHLIGHT_NAMESPACE = "highlight"
_HIGHLIGHT_FILL_BY_SIDE = {
    "front": "#ff8a00",
    "back": "#5aa7ff",
    "bottom": "#5aa7ff",
    "inner": "#ffe066",
}
_HIGHLIGHT_DEFAULT_FILL = "#ff8a00"
_HIGHLIGHT_DEFAULT_OPACITY = 0.85
_REALISTIC_DEFAULTS: dict[tuple[str, str], object] = {
    ("substrate", "fill"): "#b58b55",
    ("solder_mask", "fill"): "#1f7a3a",
    ("covered_copper", "fill"): "#145222",
    ("covered_copper", "opacity"): 0.6,
    ("exposed_copper", "fill"): "#b87333",
    ("exposed_copper", "opacity"): 0.9,
    ("silkscreen", "fill"): "#ffffff",
}
_EDA_STYLE_FALLBACK_FUNCTIONS = {
    "assembly": ("fabrication", "mechanical"),
    "courtyard": ("fabrication", "mechanical"),
    "designator": ("fabrication", "mechanical"),
    "value": ("fabrication", "mechanical"),
    "component_outline": ("fabrication", "mechanical"),
    "component_center": ("fabrication", "mechanical"),
    "dimension": ("fabrication", "mechanical"),
    "board_shape": ("edge", "mechanical"),
    "v_cut": ("mechanical",),
    "route_tool_path": ("mechanical",),
    "keepout": ("mechanical",),
    "sheet": ("mechanical",),
    "coating": ("mechanical",),
    "glue_points": ("mechanical",),
    "gold_plating": ("mechanical",),
    "three_d_body": ("mechanical",),
}


def eda_default_copper_color(source_layer_name: str, copper_order: int | None) -> str:
    """Return a deterministic default color for an EDA copper layer."""
    if source_layer_name in _EDA_COPPER_ANCHOR_COLORS:
        return _EDA_COPPER_ANCHOR_COLORS[source_layer_name]

    color_index = copper_order
    if color_index is None:
        digest = hashlib.blake2s(source_layer_name.encode(), digest_size=4).digest()
        color_index = int.from_bytes(digest, byteorder="big", signed=False)

    hue = ((color_index * 137) % 360) / 360
    saturation = 0.56 + (color_index % 4) * 0.08
    lightness = 0.46 + ((color_index // 4) % 3) * 0.08
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def resolve_layer_style(
    tokens: Mapping[str, object],
    role: VisualRole,
    *,
    highlight_color: str = "",
    highlight_stroke: str = "",
    highlight_stroke_width_mm: float = 0.0,
    eda_layer_order: int | None = None,
) -> ResolvedStyle:
    """Resolve paint style tokens for a visual layer role.

    Per-highlight overrides (``highlight_color``, ``highlight_stroke``,
    ``highlight_stroke_width_mm``) win over token-resolved values.
    """
    style_values: dict[str, object] = {}
    if highlight_color:
        style_values["fill"] = highlight_color
    else:
        fill_resolution = _resolve_optional_token(tokens, role, "fill")
        if fill_resolution is not None:
            style_values["fill"] = fill_resolution.value
        else:
            default_fill = _resolve_default_value(tokens, role, "fill", eda_layer_order)
            if default_fill is None:
                _raise_missing_token(role, "fill")
            style_values["fill"] = default_fill

    for prop in _STYLE_PROPERTIES:
        if prop == "fill":
            continue
        resolution = _resolve_optional_token(tokens, role, prop)
        if resolution is not None:
            style_values[prop] = resolution.value
        else:
            default_value = _resolve_default_value(tokens, role, prop, eda_layer_order)
            if default_value is not None:
                style_values[prop] = default_value

    if highlight_stroke:
        style_values["stroke"] = highlight_stroke
    if highlight_stroke_width_mm > 0:
        style_values["strokeWidthMm"] = highlight_stroke_width_mm

    return ResolvedStyle(
        fill=_as_optional_string(style_values.get("fill"), "fill"),
        stroke=_as_optional_string(style_values.get("stroke"), "stroke"),
        opacity=_as_optional_float(style_values.get("opacity"), "opacity"),
        stroke_width_mm=_as_optional_float(style_values.get("strokeWidthMm"), "strokeWidthMm"),
    )


def _raise_missing_token(role: VisualRole, prop: str) -> NoReturn:
    role_label = _role_label(role)
    msg = f"Missing style token for {role_label}.{prop}"
    raise ValueError(msg)


def _resolve_optional_token(
    tokens: Mapping[str, object],
    role: VisualRole,
    prop: str,
) -> _TokenResolution | None:
    for token in _candidate_tokens(role, prop):
        if token in tokens:
            return _TokenResolution(token=token, value=tokens[token])
    return None


def _candidate_tokens(role: VisualRole, prop: str) -> tuple[str, ...]:
    candidates: list[str] = []
    if role.source_layer_name:
        candidates.append(f"{role.namespace}.layer[{role.source_layer_name}].{prop}")

    role_parts = _role_parts(role)
    candidates.append(".".join((*role_parts, prop)))

    if role.side == "inner" and role.inner_index is not None:
        candidates.append(f"{role.namespace}.{role.function}.inner.{role.inner_index}.{prop}")
    if role.side:
        candidates.append(f"{role.namespace}.{role.function}.{role.side}.default.{prop}")

    fallback_functions = _EDA_STYLE_FALLBACK_FUNCTIONS.get(role.function, ())
    if role.namespace == _EDA_NAMESPACE:
        for fallback_function in fallback_functions:
            if role.side:
                candidates.append(f"{role.namespace}.{fallback_function}.{role.side}.{prop}")
                candidates.append(
                    f"{role.namespace}.{fallback_function}.{role.side}.default.{prop}"
                )
            candidates.append(f"{role.namespace}.{fallback_function}.{prop}")

    candidates.append(f"{role.namespace}.layer.default.{prop}")
    return tuple(dict.fromkeys(candidates))


def _resolve_default_value(
    tokens: Mapping[str, object],
    role: VisualRole,
    prop: str,
    eda_layer_order: int | None,
) -> object | None:
    if role.namespace == _REALISTIC_NAMESPACE:
        return _resolve_realistic_default_value(tokens, role, prop)
    if role.namespace == _HIGHLIGHT_NAMESPACE:
        return _resolve_highlight_default_value(role, prop)
    return _resolve_eda_default_value(role, prop, eda_layer_order)


def _resolve_realistic_default_value(
    tokens: Mapping[str, object],
    role: VisualRole,
    prop: str,
) -> object | None:
    if role.function == "exposed_substrate" and prop == "fill":
        substrate_fill = tokens.get("realistic.substrate.fill")
        if substrate_fill is not None:
            return substrate_fill
        return _REALISTIC_DEFAULTS[("substrate", "fill")]
    return _REALISTIC_DEFAULTS.get((role.function, prop))


def _resolve_highlight_default_value(role: VisualRole, prop: str) -> object | None:
    if prop == "fill":
        return _HIGHLIGHT_FILL_BY_SIDE.get(role.side, _HIGHLIGHT_DEFAULT_FILL)
    if prop == "opacity":
        return _HIGHLIGHT_DEFAULT_OPACITY
    if prop == "stroke":
        return "none"
    return None


def _resolve_eda_default_value(
    role: VisualRole,
    prop: str,
    eda_layer_order: int | None,
) -> object | None:
    if role.namespace != _EDA_NAMESPACE:
        return None

    if role.function == "copper":
        return _resolve_eda_copper_default(role, prop, eda_layer_order)
    if role.function == "silkscreen":
        return _resolve_eda_silkscreen_default(role, prop)
    if role.function == "edge":
        return _resolve_eda_edge_default(role, prop)
    if role.function == "drill":
        return _resolve_eda_drill_default(role, prop)
    if role.function == "keepout":
        return _resolve_eda_keepout_default(prop)
    if role.function in {"designator", "value", "user_text"}:
        return _resolve_eda_text_default(prop)
    if role.function in {"fabrication", "assembly", "courtyard"}:
        return _resolve_eda_fabrication_default(prop)
    if role.function in {"mechanical", "unknown"}:
        return _resolve_eda_mechanical_default(prop)
    return None


def _resolve_eda_fabrication_default(prop: str) -> object | None:
    if prop == "fill":
        return "none"
    if prop == "stroke":
        return _EDA_FABRICATION_COLOR
    if prop == "strokeWidthMm":
        return 0.08
    if prop == "opacity":
        return 0.8
    return None


def _resolve_eda_copper_default(
    role: VisualRole,
    prop: str,
    eda_layer_order: int | None,
) -> object | None:
    if prop == "fill":
        if role.side == "front":
            return _EDA_COPPER_FRONT_COLOR
        if role.side in {"back", "bottom"}:
            return _EDA_COPPER_BACK_COLOR
        return eda_default_copper_color(role.source_layer_name, eda_layer_order)
    if prop == "opacity":
        return _EDA_COPPER_OPACITY
    if prop == "stroke":
        return "none"
    return None


def _resolve_eda_silkscreen_default(role: VisualRole, prop: str) -> object | None:
    if prop == "fill":
        if role.source_layer_name in _EDA_SILKSCREEN_ANCHOR_COLORS:
            return _EDA_SILKSCREEN_ANCHOR_COLORS[role.source_layer_name]
        if role.side == "back":
            return _EDA_SILKSCREEN_BACK_COLOR
        return _EDA_SILKSCREEN_FRONT_COLOR
    if prop == "opacity":
        return 1.0
    if prop == "stroke":
        return "none"
    return None


def _resolve_eda_edge_default(role: VisualRole, prop: str) -> object | None:
    if prop == "fill":
        return "none"
    if prop == "stroke":
        return _EDA_EDGE_ANCHOR_COLORS.get(role.source_layer_name, _EDA_EDGE_COLOR)
    if prop == "strokeWidthMm":
        return 0.08
    return None


def _resolve_eda_drill_default(role: VisualRole, prop: str) -> object | None:
    if prop == "fill":
        return "none"
    if prop == "stroke":
        return _EDA_DRILL_ANCHOR_COLORS.get(role.source_layer_name, _EDA_DRILL_COLOR)
    if prop == "strokeWidthMm":
        return 0.06
    return None


def _resolve_eda_keepout_default(prop: str) -> object | None:
    if prop == "fill":
        return "none"
    if prop == "stroke":
        return _EDA_KEEPOUT_COLOR
    if prop == "strokeWidthMm":
        return 0.08
    if prop == "opacity":
        return 0.8
    return None


def _resolve_eda_text_default(prop: str) -> object | None:
    if prop == "fill":
        return _EDA_TEXT_COLOR
    if prop == "stroke":
        return "none"
    if prop == "opacity":
        return 0.8
    return None


def _resolve_eda_mechanical_default(prop: str) -> object | None:
    if prop == "fill":
        return "none"
    if prop == "stroke":
        return _EDA_MECHANICAL_COLOR
    if prop == "strokeWidthMm":
        return 0.08
    if prop == "opacity":
        return 0.8
    return None


def _role_label(role: VisualRole) -> str:
    return ".".join(_role_parts(role))


def _role_parts(role: VisualRole) -> tuple[str, ...]:
    if role.side:
        return (role.namespace, role.function, role.side)
    return (role.namespace, role.function)


def _as_optional_string(value: object, prop: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"Style token {prop} must be a string, got {value!r}"
        raise ValueError(msg)
    return value


def _as_optional_float(value: object, prop: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"Style token {prop} must be a number, got {value!r}"
        raise ValueError(msg)
    return float(value)
