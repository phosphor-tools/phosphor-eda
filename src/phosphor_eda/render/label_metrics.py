"""Annotation label and legend measurement.

Shared by the placement solver (to size non-overlap intervals) and the SVG
emitter (to align text inside the pill). Kept free of solver dependencies so
the emitter can import it without pulling in ortools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.geometry.text_metrics import measure_text

if TYPE_CHECKING:
    from phosphor_eda.render.annotation_spec import LegendSpec

# Label pill padding (pixels on each side). Horizontal padding doubles as the
# text inset for start/end-anchored labels.
LABEL_PAD_H_PX = 6.0
_PAD_V_PX = 4.0


def measure_label(text: str, font_size: float) -> tuple[float, float]:
    """Measure a label pill including padding.

    Returns (width, height) in the same units as ``font_size``.
    """
    if not text:
        return (0.0, 0.0)
    tw, th = measure_text(text, font_size)
    return (tw + 2 * LABEL_PAD_H_PX, th + 2 * _PAD_V_PX)


def measure_legend(
    spec: LegendSpec,
    font_size: float,
) -> tuple[float, float]:
    """Compute legend box size from title and entries.

    Returns (width, height) in the same units as ``font_size``.
    """
    title_w = 0.0
    title_h = 0.0
    if spec.title:
        tw, th = measure_text(spec.title, font_size * 0.85)
        title_w = tw
        title_h = th + font_size * 0.3  # gap below title

    swatch_size = font_size * 0.8
    swatch_gap = font_size * 0.4
    entry_gap = font_size * 0.2
    max_entry_w = 0.0
    total_entry_h = 0.0
    for i, entry in enumerate(spec.entries):
        ew, eh = measure_text(entry.label, font_size)
        row_w = (swatch_size + swatch_gap + ew) if entry.color else ew
        max_entry_w = max(max_entry_w, row_w)
        total_entry_h += max(eh, swatch_size)
        if i > 0:
            total_entry_h += entry_gap

    pad_h = font_size * 0.6
    pad_v = font_size * 0.5
    width = max(title_w, max_entry_w) + 2 * pad_h
    height = title_h + total_entry_h + 2 * pad_v

    return (width, height)
