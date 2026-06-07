from __future__ import annotations

from typing import Any

from test_pcb_render import _board

from phosphor_eda.pcb_render_plan import build_derived_render_plan
from phosphor_eda.pcb_render_settings import (
    LayerMatch,
    LayerSelectionRule,
    RenderSettings,
    SourceSelection,
)


class _Profiler:
    def __init__(self) -> None:
        self.metrics: list[tuple[str, dict[str, Any]]] = []

    def metric(self, name: str, **values: Any) -> None:
        self.metrics.append((name, values))

    def span(self, _name: str, **_values: Any) -> _Profiler:
        return self

    def __enter__(self) -> _Profiler:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


def test_render_plan_builds_from_normalized_geometry() -> None:
    settings = RenderSettings(
        render_mode="eda",
        source=SourceSelection(
            layers=[
                LayerSelectionRule(match=LayerMatch(role="copper")),
                LayerSelectionRule(
                    match=LayerMatch(role="silkscreen"), objects=("graphic", "text")
                ),
                LayerSelectionRule(match=LayerMatch(role="edge")),
            ]
        ),
    )

    plan = build_derived_render_plan(
        _board(),
        settings=settings,
        side="front",
        width_px=640,
        annotations=None,
    )

    assert plan.width_px == 640
    assert plan.height_px > 0
    assert {layer.role.function for layer in plan.base_layers} >= {"copper", "silkscreen", "edge"}


def test_render_plan_profiler_counts_geometry_object_types() -> None:
    profiler = _Profiler()
    settings = RenderSettings(
        render_mode="eda",
        source=SourceSelection(layers=[LayerSelectionRule(match=LayerMatch(role="copper"))]),
    )

    build_derived_render_plan(
        _board(),
        settings=settings,
        side="front",
        width_px=640,
        annotations=None,
        profiler=profiler,
    )

    board_metric = next(values for name, values in profiler.metrics if name == "board.input")
    assert board_metric["segments"] == 1
    assert board_metric["trace_arcs"] == 0
    assert board_metric["vias"] == 1
    assert board_metric["pours"] == 0
