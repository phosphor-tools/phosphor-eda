from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from phosphor_eda.pcb_render_geometry import build_geometry_store
from phosphor_eda.pcb_render_modes import (
    HighlightGroup,
    build_cad_layers,
    build_highlight_layers,
    build_realistic_layers,
)

if TYPE_CHECKING:
    from phosphor_eda.pcb import Pcb
    from phosphor_eda.pcb_annotations import ResolvedAnnotations
    from phosphor_eda.pcb_render_artwork import DerivedLayer
    from phosphor_eda.pcb_render_profile import RenderProfiler
    from phosphor_eda.pcb_render_settings import RenderSettings


@dataclass(frozen=True)
class ViewBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class DerivedRenderPlan:
    view_box: ViewBox
    width_px: int
    height_px: int
    base_layers: tuple[DerivedLayer, ...]
    highlight_groups: tuple[HighlightGroup, ...]
    annotations: ResolvedAnnotations | None
    warnings: tuple[str, ...]
    annotation_style: dict[str, object] = field(default_factory=dict)
    custom_css: str = ""


def build_derived_render_plan(
    board: Pcb,
    *,
    settings: RenderSettings,
    side: str,
    width_px: int,
    annotations: ResolvedAnnotations | None,
    profiler: RenderProfiler | None = None,
) -> DerivedRenderPlan:
    bx0, by0, bx1, by1 = board.bbox()
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

    if profiler is not None:
        profiler.metric(
            "board.input",
            footprints=len(board.footprints),
            segments=len(board.segments),
            trace_arcs=len(board.trace_arcs),
            vias=len(board.vias),
            zones=len(board.zones),
            layers=len(board.layers),
        )
    if profiler is None:
        store = build_geometry_store(board, side=side)
    else:
        with profiler.span("plan.build_geometry_store"):
            store = build_geometry_store(board, side=side)
        profiler.metric("geometry_store.items", count=len(store.items))
    mode_settings = replace(settings, side=side)
    if mode_settings.render_mode == "realistic":
        if profiler is None:
            base_layers = build_realistic_layers(store, mode_settings, warn=warnings.append)
        else:
            with profiler.span("plan.build_realistic_layers"):
                base_layers = build_realistic_layers(
                    store,
                    mode_settings,
                    warn=warnings.append,
                    profiler=profiler,
                )
    else:
        if profiler is None:
            base_layers = build_cad_layers(store, mode_settings, warn=warnings.append)
        else:
            with profiler.span("plan.build_cad_layers"):
                base_layers = build_cad_layers(
                    store,
                    mode_settings,
                    warn=warnings.append,
                    profiler=profiler,
                )
    if profiler is None:
        highlight_groups = build_highlight_layers(store, mode_settings, warn=warnings.append)
    else:
        with profiler.span("plan.build_highlight_layers"):
            highlight_groups = build_highlight_layers(
                store,
                mode_settings,
                warn=warnings.append,
                profiler=profiler,
            )

    return DerivedRenderPlan(
        view_box=ViewBox(vb_x, vb_y, vb_w, vb_h),
        width_px=width_px,
        height_px=height_px,
        base_layers=base_layers,
        highlight_groups=highlight_groups,
        annotations=annotations,
        warnings=tuple(warnings),
        annotation_style=_annotation_style_for_settings(settings),
        custom_css=settings.custom_css,
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


def _annotation_style_for_settings(settings: RenderSettings) -> dict[str, object]:
    label_style = _annotation_token_style(
        settings,
        "annotation.label",
        {
            "fill": "fill",
            "textHalo": "text_halo",
            "textHaloWidthPx": "text_halo_width_px",
            "fontWeight": "font_weight",
            "pillVisible": "pill_visible",
        },
    )
    connector_style = _annotation_token_style(
        settings,
        "annotation.connector",
        {
            "stroke": "stroke",
            "strokeWidthPx": "stroke_width_px",
            "dotVisible": "dot_visible",
        },
    )
    styles: dict[str, object] = {}
    if label_style:
        styles["label"] = label_style
    if connector_style:
        styles["connector"] = connector_style
    return styles


def _annotation_token_style(
    settings: RenderSettings,
    prefix: str,
    field_map: dict[str, str],
) -> dict[str, object]:
    style: dict[str, object] = {}
    for token_name, style_name in field_map.items():
        key = f"{prefix}.{token_name}"
        if key in settings.tokens:
            style[style_name] = settings.tokens[key]
    return style
