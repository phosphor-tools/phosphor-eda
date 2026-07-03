"""Stroke-width fallthrough floor (T3.10).

A stroke-mode primitive with no explicit width and no layer stroke-width token
must floor to ``MIN_STROKE_WIDTH_MM`` rather than falling through to the SVG
default 1mm stroke, in both the derived-layer path and the layer-mask path.
"""

from __future__ import annotations

from phosphor_eda.geometry.pcb_geometry import MIN_STROKE_WIDTH_MM
from phosphor_eda.render.inventory import InventoryTags
from phosphor_eda.render.primitives import PaintMode, SvgPrimitive
from phosphor_eda.render.serialize import (
    _layer_mask_path_attrs,
    _stroke_primitive_style_attrs,
)
from phosphor_eda.render.tokens import ResolvedStyle

_FLOOR = f"stroke-width: {MIN_STROKE_WIDTH_MM:.4f}"


def _stroke_primitive(width: float | None) -> SvgPrimitive:
    return SvgPrimitive(
        d="M 0 0 L 1 0",
        source_id="s",
        source_layer="copper",
        kind="artwork",
        tags=InventoryTags(),
        paint=PaintMode.STROKE,
        stroke_width=width,
    )


def test_stroke_style_floors_when_no_width_source() -> None:
    attrs = _stroke_primitive_style_attrs(ResolvedStyle(fill="#f00"), _stroke_primitive(None))

    assert _FLOOR in attrs["style"]
    assert "stroke-width: 1.0000" not in attrs["style"]


def test_stroke_style_prefers_layer_token_over_floor() -> None:
    style = ResolvedStyle(fill="#f00", stroke_width_mm=0.08)

    attrs = _stroke_primitive_style_attrs(style, _stroke_primitive(None))

    assert "stroke-width: 0.0800" in attrs["style"]


def test_layer_mask_stroke_floors_when_widthless() -> None:
    attrs = _layer_mask_path_attrs(_stroke_primitive(None), fill="white")

    assert attrs["stroke-width"] == f"{MIN_STROKE_WIDTH_MM:.4f}"
