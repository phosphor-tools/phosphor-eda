"""Paint-aware payload bounds (T3.15).

Stroked primitives occupy half their width beyond the centerline geometry;
bounds must account for that so mask/clip viewports cover the painted extent.
"""

from __future__ import annotations

import pytest

from phosphor_eda.domain.pcb import PcbArc, PcbCircle, PcbLine, PcbPolygon, extend_shape_bounds
from phosphor_eda.render.primitives import _payload_bounds


def test_stroked_line_bounds_grow_by_half_width() -> None:
    line = PcbLine(0.0, 0.0, 10.0, 0.0, 2.0)

    bounds = _payload_bounds(line)

    assert bounds is not None
    min_x, min_y, max_x, max_y = bounds
    # Half-width (1.0) padding on every side, including past the endpoints.
    assert min_x == pytest.approx(-1.0)
    assert max_x == pytest.approx(11.0)
    assert min_y == pytest.approx(-1.0)
    assert max_y == pytest.approx(1.0)


def test_stroked_circle_bounds_reach_outer_radius() -> None:
    circle = PcbCircle(cx=0.0, cy=0.0, radius=5.0, width=1.0, fill=False)

    bounds = _payload_bounds(circle)

    assert bounds is not None
    # Outer radius = centerline 5.0 + width/2 = 5.5.
    assert bounds == pytest.approx((-5.5, -5.5, 5.5, 5.5))


def test_filled_circle_bounds_are_radius() -> None:
    circle = PcbCircle(cx=0.0, cy=0.0, radius=5.0, width=0.0, fill=True)

    assert _payload_bounds(circle) == pytest.approx((-5.0, -5.0, 5.0, 5.0))


def test_arc_bounds_capture_bulge_beyond_three_points() -> None:
    # Semicircle from (-1,0) to (1,0) bulging up through (0,1); the top of the
    # arc is at y=1, which the three defining points already touch, but a
    # quarter-turn arc's bulge would be missed by a 3-point bbox.
    arc = PcbArc(1.0, 0.0, 0.70710678, 0.70710678, 0.0, 1.0, 0.0)

    bounds = _payload_bounds(arc)

    assert bounds is not None
    _, _, max_x, max_y = bounds
    # The arc bulges to radius 1.0 on both axes; a 3-point bbox would stop at
    # the mid point (~0.707).
    assert max_x == pytest.approx(1.0, abs=0.01)
    assert max_y == pytest.approx(1.0, abs=0.01)


def test_stroked_polygon_bounds_exceed_region_by_half_width() -> None:
    poly = PcbPolygon(
        points=[(0, 0), (10, 0), (10, 10), (0, 10)],
        width=2.0,
        fill=False,
    )

    bounds = _payload_bounds(poly)

    assert bounds is not None
    min_x, min_y, max_x, max_y = bounds
    assert min_x == pytest.approx(-1.0, abs=0.05)
    assert max_x == pytest.approx(11.0, abs=0.05)


def test_extend_shape_bounds_stroked_circle_reaches_outer_radius() -> None:
    xs: list[float] = []
    ys: list[float] = []

    extend_shape_bounds(xs, ys, PcbCircle(cx=0.0, cy=0.0, radius=5.0, width=1.0, fill=False))

    assert min(xs) == pytest.approx(-5.5)
    assert max(xs) == pytest.approx(5.5)


def test_extend_shape_bounds_thick_line_grows_by_half_width() -> None:
    xs: list[float] = []
    ys: list[float] = []

    extend_shape_bounds(xs, ys, PcbLine(0.0, 0.0, 10.0, 0.0, 2.0))

    # Round-capped stroke pads half the width (1.0) on every side, past the ends.
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((-1.0, -1.0, 11.0, 1.0))


def test_extend_shape_bounds_arc_over_180_exceeds_three_point_hull() -> None:
    # 270-degree unit-circle arc: start (1,0) -> mid (-.707,.707) -> end (0,-1).
    # It crosses the +y and -x axes, which lie outside the three-point hull.
    xs: list[float] = []
    ys: list[float] = []

    extend_shape_bounds(xs, ys, PcbArc(1.0, 0.0, -0.70710678, 0.70710678, 0.0, -1.0, 0.0))

    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((-1.0, -1.0, 1.0, 1.0), abs=1e-6)
    # A three-point bbox would stop at x=-0.707 and y=0.707.
    assert min(xs) < -0.9
    assert max(ys) > 0.9


def test_extend_shape_bounds_thick_arc_grows_by_half_width() -> None:
    xs: list[float] = []
    ys: list[float] = []

    extend_shape_bounds(xs, ys, PcbArc(1.0, 0.0, -0.70710678, 0.70710678, 0.0, -1.0, 0.4))

    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((-1.2, -1.2, 1.2, 1.2), abs=1e-6)
