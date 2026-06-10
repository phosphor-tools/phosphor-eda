"""Public render API: parse settings, build a plan, serialize to SVG.

The heavy lifting lives in sibling modules:

- ``render/plan.py`` — derive the layered render plan from board + settings
- ``render/serialize.py`` — turn a plan into SVG (layers, clips, masks)
- ``render/annotation_svg.py`` — annotation CSS and drawing
- ``render/svg.py`` — the low-level SVG string builder

This module re-exports the settings entry points and exposes
``render_pcb_svg``, the one-call rendering function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.render.plan import build_derived_render_plan
from phosphor_eda.render.profiler import profile_span
from phosphor_eda.render.serialize import (
    append_pcb_metadata,
    render_pcb_svg_from_derived_plan,
)
from phosphor_eda.render.settings import (
    HighlightSpec,
    RenderSettings,
    is_json_dict,
    load_bundled_render_settings,
    load_render_settings_file,
    load_render_settings_json,
    parse_render_settings,
    render_settings_schema,
)

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import Pcb
    from phosphor_eda.render.annotations import ResolvedAnnotations
    from phosphor_eda.render.profiler import RenderProfiler

__all__ = [
    "HighlightSpec",
    "RenderResult",
    "RenderSettings",
    "is_json_dict",
    "load_bundled_render_settings",
    "load_render_settings_file",
    "load_render_settings_json",
    "parse_render_settings",
    "render_pcb_svg",
    "render_pcb_svg_from_derived_plan",
    "render_settings_schema",
]


@dataclass(frozen=True)
class RenderResult:
    """Result of rendering a PCB: the SVG plus any non-fatal warnings.

    Warnings capture degradations the user should know about (an
    unresolved highlight target, an unparseable color, a placement solver
    fallback). The CLI prints them to stderr; the SVG is still valid.
    """

    svg: str
    warnings: tuple[str, ...] = ()


def render_pcb_svg(
    board: Pcb,
    settings: RenderSettings,
    *,
    annotations: ResolvedAnnotations | None = None,
    profiler: RenderProfiler | None = None,
) -> RenderResult:
    """Render a Pcb as a layered SVG from fully-resolved render settings.

    Parameters
    ----------
    board:
        Parsed PCB board.
    settings:
        Fully-resolved render settings. ``side``, ``width``, and
        ``font_size`` must be concrete (use
        ``resolve_effective_settings`` to fill defaults and merge CLI
        flags). Highlights and custom CSS are read directly from here.
    annotations:
        Resolved annotations to overlay on the board.
    """
    with profile_span(profiler, "render.build_plan"):
        plan = build_derived_render_plan(
            board,
            settings=settings,
            annotations=annotations,
            profiler=profiler,
        )
    with profile_span(profiler, "render.serialize"):
        svg = render_pcb_svg_from_derived_plan(plan, profiler=profiler)
    with profile_span(profiler, "render.metadata"):
        svg = append_pcb_metadata(svg, board)
    return RenderResult(svg=svg, warnings=plan.warnings)
