"""Native drill rendering (plan 11 step 4)."""

from __future__ import annotations

import math

from phosphor_eda.domain.pcb import PcbDrill, PcbDrillShape
from phosphor_eda.render.drills import drill_render


def test_round_drill_is_native_circle() -> None:
    render = drill_render(PcbDrill(id="d", x=1.0, y=2.0, diameter=0.8))
    assert render is not None
    assert render.stroke_width is None
    assert render.d.count(" A ") == 2
    assert render.d.endswith("Z")
    assert render.d.startswith("M 1.4000 2.0000")


def test_horizontal_slot_is_stroked_centerline() -> None:
    render = drill_render(
        PcbDrill(
            id="s", x=0.0, y=0.0, diameter=0.0, shape=PcbDrillShape.SLOT, width=3.0, height=1.0
        )
    )
    assert render is not None
    assert render.stroke_width == 1.0
    assert " A " not in render.d
    assert render.d == "M -1.0000 0.0000 L 1.0000 0.0000"


def test_rotated_slot_bakes_rotation_into_endpoints() -> None:
    render = drill_render(
        PcbDrill(
            id="sr",
            x=0.0,
            y=0.0,
            diameter=0.0,
            shape=PcbDrillShape.SLOT,
            width=3.0,
            height=1.0,
            rotation=90.0,
        )
    )
    assert render is not None
    assert render.stroke_width == 1.0
    numbers = [float(t) for t in render.d.replace("M", "").replace("L", "").split()]
    (x1, y1, x2, y2) = numbers
    # A 90-degree rotation maps the horizontal slot onto the vertical axis.
    assert math.isclose(abs(y1), 1.0, abs_tol=1e-6)
    assert math.isclose(abs(y2), 1.0, abs_tol=1e-6)
    assert math.isclose(x1, 0.0, abs_tol=1e-6)
    assert math.isclose(x2, 0.0, abs_tol=1e-6)


def test_square_slot_falls_back_to_circle() -> None:
    render = drill_render(
        PcbDrill(
            id="sq", x=0.0, y=0.0, diameter=0.0, shape=PcbDrillShape.SLOT, width=1.0, height=1.0
        )
    )
    assert render is not None
    assert render.stroke_width is None
    assert render.d.count(" A ") == 2


def test_degenerate_drill_returns_none() -> None:
    assert drill_render(PcbDrill(id="z", x=0.0, y=0.0, diameter=0.0, width=0.0, height=0.0)) is None
