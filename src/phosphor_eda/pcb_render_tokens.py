"""Style token resolver for derived PCB render layers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

type WarningCallback = Callable[[str], None]


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


def resolve_layer_style(
    tokens: Mapping[str, object],
    role: VisualRole,
    *,
    dimmed: bool,
    warn: WarningCallback,
    highlight_color: str = "",
) -> ResolvedStyle:
    """Resolve paint style tokens for a visual layer role."""
    fill_resolution = _resolve_required_token(tokens, role, "fill")
    style_values: dict[str, object] = {
        "fill": _resolve_dimmed_value(tokens, fill_resolution, dimmed, warn)
    }

    for prop in _STYLE_PROPERTIES:
        if prop == "fill":
            continue
        resolution = _resolve_optional_token(tokens, role, prop)
        if resolution is not None:
            style_values[prop] = _resolve_dimmed_value(tokens, resolution, dimmed, warn)

    if highlight_color:
        style_values["fill"] = highlight_color

    return ResolvedStyle(
        fill=_as_optional_string(style_values.get("fill"), "fill"),
        stroke=_as_optional_string(style_values.get("stroke"), "stroke"),
        opacity=_as_optional_float(style_values.get("opacity"), "opacity"),
        stroke_width_mm=_as_optional_float(style_values.get("strokeWidthMm"), "strokeWidthMm"),
    )


def _resolve_required_token(
    tokens: Mapping[str, object],
    role: VisualRole,
    prop: str,
) -> _TokenResolution:
    resolution = _resolve_optional_token(tokens, role, prop)
    if resolution is not None:
        return resolution

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

    candidates.append(f"{role.namespace}.layer.default.{prop}")
    return tuple(dict.fromkeys(candidates))


def _resolve_dimmed_value(
    tokens: Mapping[str, object],
    resolution: _TokenResolution,
    dimmed: bool,
    warn: WarningCallback,
) -> object:
    if not dimmed:
        return resolution.value

    dimmed_token = _dimmed_token(resolution.token)
    if dimmed_token in tokens:
        return tokens[dimmed_token]

    warn(f"Missing dimmed style token {dimmed_token}; using {resolution.token}")
    return resolution.value


def _dimmed_token(token: str) -> str:
    namespace, separator, rest = token.partition(".")
    if not separator:
        return f"{token}.dimmed"
    return f"{namespace}.dimmed.{rest}"


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
