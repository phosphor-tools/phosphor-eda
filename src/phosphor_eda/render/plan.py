from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import PcbConductorKind
from phosphor_eda.geometry.pcb_geometry import circle_path_d
from phosphor_eda.render.inventory import InventoryTags, build_inventory
from phosphor_eda.render.modes import (
    DerivedLayer,
    HighlightGroup,
    build_eda_layers,
    build_highlight_layers,
    build_realistic_layers,
)
from phosphor_eda.render.primitives import PaintMode, SvgPrimitive, union_bounds
from phosphor_eda.render.profiler import profile_span
from phosphor_eda.render.tokens import ResolvedStyle, VisualRole
from phosphor_eda.render.view import board_view_transform, rendered_view_board_bbox

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.render.annotations import ResolvedAnnotations
    from phosphor_eda.render.profiler import RenderProfiler
    from phosphor_eda.render.settings import RenderSettings, TokenMap


@dataclass(frozen=True)
class ViewBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class AnnotationLabelStyle:
    """Resolved styling for annotation pill labels (display-pixel units)."""

    fill: str | None = None
    text_halo: str | None = None
    text_halo_width_px: float | None = None
    font_weight: str | None = None
    pill_visible: bool | None = None


@dataclass(frozen=True)
class AnnotationConnectorStyle:
    """Resolved styling for annotation connector lines and end dots."""

    stroke: str | None = None
    stroke_width_px: float | None = None
    dot_visible: bool | None = None


@dataclass(frozen=True)
class AnnotationStyle:
    """Resolved annotation styling, split by element class."""

    label: AnnotationLabelStyle = field(default_factory=AnnotationLabelStyle)
    connector: AnnotationConnectorStyle = field(default_factory=AnnotationConnectorStyle)


@dataclass(frozen=True)
class DimScrim:
    """A translucent wash painted over base layers so highlights pop."""

    fill: str = "#ffffff"
    opacity: float = 0.55


@dataclass(frozen=True)
class DerivedRenderPlan:
    view_box: ViewBox
    width_px: int
    height_px: int
    base_layers: tuple[DerivedLayer, ...]
    highlight_groups: tuple[HighlightGroup, ...]
    annotations: ResolvedAnnotations | None
    warnings: tuple[str, ...]
    annotation_style: AnnotationStyle = field(default_factory=AnnotationStyle)
    custom_css: str = ""
    background: str = ""
    dim_scrim: DimScrim | None = None
    # SVG transform mapping board geometry into the rendered view (back-side
    # mirror and/or rotation); "" for the identity front view.
    board_view_transform: str = ""


def build_derived_render_plan(
    board: Board,
    *,
    settings: RenderSettings,
    annotations: ResolvedAnnotations | None,
    net_expansions: Mapping[str, frozenset[str]] | None = None,
    profiler: RenderProfiler | None = None,
) -> DerivedRenderPlan:
    # Settings must be fully resolved (resolve_effective_settings) before
    # reaching here: side and width are read directly, no defaulting.
    side = settings.side
    width_px = settings.width
    assert side, "render settings must have a resolved side"
    assert width_px > 0, "render settings must have a resolved width"
    board_bbox = board.bbox()
    if board_bbox is None:
        msg = "cannot render an empty board: no board profile and no pads to bound the view"
        raise ValueError(msg)
    # The viewBox frames the rendered view: rotation swaps the board extents.
    view_bbox = rendered_view_board_bbox(board_bbox, settings.rotation)
    bx0, by0, bx1, by1 = view_bbox
    pad_mm = 2.0
    vb_x = bx0 - pad_mm
    vb_y = by0 - pad_mm
    vb_w = (bx1 - bx0) + 2 * pad_mm
    vb_h = (by1 - by0) + 2 * pad_mm
    if annotations is not None:
        vb_x, vb_y, vb_w, vb_h = _expand_view_box_for_annotations(
            vb_x,
            vb_y,
            vb_w,
            vb_h,
            annotations,
        )
    height_px = int(width_px * vb_h / vb_w) if vb_w > 0 else width_px
    warnings: list[str] = []
    if annotations is not None:
        warnings.extend(annotations.warnings)

    if profiler is not None:
        profiler.metric(
            "board.input",
            footprints=len(board.footprints),
            segments=len(
                [
                    item
                    for item in board.conductors
                    if item.kind in {PcbConductorKind.TRACE, PcbConductorKind.TRACE_ARC}
                ]
            ),
            trace_arcs=len(
                [item for item in board.conductors if item.kind == PcbConductorKind.TRACE_ARC]
            ),
            vias=len(board.vias),
            pours=len(board.pours),
            layers=len(board.layers),
        )
    with profile_span(profiler, "plan.build_inventory"):
        inventory = build_inventory(board, side=side)
    if profiler is not None:
        profiler.metric("inventory.items", count=len(inventory.items))
    if settings.render_mode == "realistic":
        with profile_span(profiler, "plan.build_realistic_layers"):
            base_layers = build_realistic_layers(
                inventory,
                settings,
                profiler=profiler,
            )
    else:
        with profile_span(profiler, "plan.build_eda_layers"):
            base_layers = build_eda_layers(
                inventory,
                settings,
                profiler=profiler,
            )
    with profile_span(profiler, "plan.build_highlight_layers"):
        highlight_groups = build_highlight_layers(
            inventory,
            settings,
            warn=warnings.append,
            net_expansions=net_expansions,
            profiler=profiler,
        )
    px_per_mm = width_px / vb_w if vb_w > 0 else 1.0
    highlight_groups = _append_pad_marker_rings(settings, highlight_groups, px_per_mm)

    return DerivedRenderPlan(
        view_box=ViewBox(vb_x, vb_y, vb_w, vb_h),
        width_px=width_px,
        height_px=height_px,
        base_layers=base_layers,
        highlight_groups=highlight_groups,
        annotations=annotations,
        warnings=tuple(warnings),
        annotation_style=annotation_style_for_settings(settings),
        custom_css=settings.custom_css,
        background=_resolved_background(settings),
        dim_scrim=_dim_scrim_for_settings(settings, highlight_groups),
        board_view_transform=board_view_transform(board_bbox, side, settings.rotation),
    )


_MARKER_DEFAULT_MIN_DIAMETER_PX = 28.0
_MARKER_DEFAULT_STROKE_WIDTH_PX = 2.5
_MARKER_DEFAULT_COLOR = "#ff8a00"
# Ring radius relative to the pad's half extent, so the ring clears the pad.
_MARKER_PAD_CLEARANCE = 1.6


def _append_pad_marker_rings(
    settings: RenderSettings,
    groups: tuple[HighlightGroup, ...],
    px_per_mm: float,
) -> tuple[HighlightGroup, ...]:
    """Draw a ring around each highlighted pad with a minimum on-screen size.

    A highlighted 0402 pad is invisible at print scale; the ring keeps the
    location findable without redrawing the pad at a false size.
    """
    enabled = _token_bool(settings.tokens, "highlight.marker.enabled")
    if not enabled:
        return groups
    min_diameter_token = _token_float(settings.tokens, "highlight.marker.minDiameterPx")
    min_diameter_px = (
        min_diameter_token if min_diameter_token is not None else _MARKER_DEFAULT_MIN_DIAMETER_PX
    )
    stroke_width_token = _token_float(settings.tokens, "highlight.marker.strokeWidthPx")
    stroke_width_px = (
        stroke_width_token if stroke_width_token is not None else _MARKER_DEFAULT_STROKE_WIDTH_PX
    )

    result: list[HighlightGroup] = []
    for group in groups:
        if not group.target.startswith("pad:"):
            result.append(group)
            continue
        bounds = union_bounds(
            tuple(primitive for layer in group.layers for primitive in layer.primitives)
        )
        if bounds is None:
            result.append(group)
            continue
        min_x, min_y, max_x, max_y = bounds
        cx = (min_x + max_x) / 2
        cy = (min_y + max_y) / 2
        half_extent = max(max_x - min_x, max_y - min_y) / 2
        radius = max(half_extent * _MARKER_PAD_CLEARANCE, min_diameter_px / 2 / px_per_mm)
        marker = DerivedLayer(
            id=f"highlight:marker:{group.target}",
            role=VisualRole(namespace="highlight", function="marker"),
            primitives=(
                SvgPrimitive(
                    d=circle_path_d(cx, cy, radius),
                    source_id=f"marker:{group.target}",
                    source_layer="",
                    kind="marker",
                    tags=InventoryTags(),
                    bbox=(cx - radius, cy - radius, cx + radius, cy + radius),
                    paint=PaintMode.STROKE,
                    stroke_width=stroke_width_px / px_per_mm,
                ),
            ),
            source_layers=(),
            source_ids=(),
            style=ResolvedStyle(fill=_marker_color(group)),
        )
        result.append(HighlightGroup(target=group.target, layers=(*group.layers, marker)))
    return tuple(result)


def _marker_color(group: HighlightGroup) -> str:
    """The ring inherits the group's resolved highlight fill."""
    for layer in group.layers:
        if layer.style is not None and layer.style.fill not in (None, "none"):
            return layer.style.fill
    return _MARKER_DEFAULT_COLOR


def _resolved_background(settings: RenderSettings) -> str:
    """Map the background setting to a paintable fill ('' = no background)."""
    if settings.background in ("none", "transparent"):
        return ""
    return settings.background


def _dim_scrim_for_settings(
    settings: RenderSettings,
    highlight_groups: tuple[HighlightGroup, ...],
) -> DimScrim | None:
    mode = settings.dimming.mode
    if mode == "off" or (mode == "auto" and not highlight_groups):
        return None
    fill = _token_str(settings.tokens, "highlight.dim.fill")
    opacity = _token_float(settings.tokens, "highlight.dim.opacity")
    defaults = DimScrim()
    return DimScrim(
        fill=fill if fill is not None else defaults.fill,
        opacity=opacity if opacity is not None else defaults.opacity,
    )


def _expand_view_box_for_annotations(
    vb_x: float,
    vb_y: float,
    vb_w: float,
    vb_h: float,
    annotations: ResolvedAnnotations,
) -> tuple[float, float, float, float]:
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
    return vb_x, vb_y, vb_w, vb_h


def annotation_style_for_settings(settings: RenderSettings) -> AnnotationStyle:
    tokens = settings.tokens
    label = AnnotationLabelStyle(
        fill=_token_str(tokens, "annotation.label.fill"),
        text_halo=_token_str(tokens, "annotation.label.textHalo"),
        text_halo_width_px=_token_float(tokens, "annotation.label.textHaloWidthPx"),
        font_weight=_token_css_value(tokens, "annotation.label.fontWeight"),
        pill_visible=_token_bool(tokens, "annotation.label.pillVisible"),
    )
    connector = AnnotationConnectorStyle(
        stroke=_token_str(tokens, "annotation.connector.stroke"),
        stroke_width_px=_token_float(tokens, "annotation.connector.strokeWidthPx"),
        dot_visible=_token_bool(tokens, "annotation.connector.dotVisible"),
    )
    return AnnotationStyle(label=label, connector=connector)


def _token_str(tokens: TokenMap, key: str) -> str | None:
    if key not in tokens:
        return None
    value = tokens[key]
    if not isinstance(value, str):
        msg = f"token {key!r} must be a string, got {value!r}"
        raise ValueError(msg)
    return value


def _token_float(tokens: TokenMap, key: str) -> float | None:
    if key not in tokens:
        return None
    value = tokens[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"token {key!r} must be a number, got {value!r}"
        raise ValueError(msg)
    return float(value)


def _token_bool(tokens: TokenMap, key: str) -> bool | None:
    if key not in tokens:
        return None
    value = tokens[key]
    if not isinstance(value, bool):
        msg = f"token {key!r} must be a boolean, got {value!r}"
        raise ValueError(msg)
    return value


def _token_css_value(tokens: TokenMap, key: str) -> str | None:
    """Resolve a token that maps to a CSS value, accepting numbers as strings."""
    if key not in tokens:
        return None
    # Widen to object: token maps built from unvalidated JSON can carry
    # values outside TokenValue at runtime, which must not leak into CSS.
    value: object = tokens[key]
    if not isinstance(value, bool) and isinstance(value, int | float):
        return f"{float(value):g}"
    if isinstance(value, str):
        return value
    msg = f"token {key!r} must be a string or number, got {value!r}"
    raise ValueError(msg)
