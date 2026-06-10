"""Native SVG path builders for PCB primitives (plan 11).

These builders replace shapely polygonization. The tests assert exact path
structure (arc/line commands) and key coordinates so curves stay precise.
"""

from __future__ import annotations

import math

import pytest

from phosphor_eda.domain.pcb import PcbCircle, PcbLine, PcbPad, PcbPadType
from phosphor_eda.geometry.pcb_geometry import (
    circle_path_d,
    oval_path_d,
    pad_path_d,
    rect_path_d,
    roundrect_path_d,
)


def _coords(d: str) -> list[float]:
    return [float(token) for token in d.replace(",", " ").split() if _is_number(token)]


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def test_circle_path_is_two_arcs_closed() -> None:
    d = circle_path_d(10.0, 20.0, 5.0)
    assert d.count(" A ") == 2
    assert d.endswith("Z")
    assert d.startswith("M 15.0000 20.0000")


def test_circle_path_degenerate_radius_empty() -> None:
    assert circle_path_d(0.0, 0.0, 0.0) == ""


def test_rect_path_unrotated_corners() -> None:
    d = rect_path_d(0.0, 0.0, 4.0, 2.0)
    assert d.count(" L ") == 3
    assert d.endswith("Z")
    assert " A " not in d
    # Four distinct corners at +/- half-extents.
    assert "M -2.0000 -1.0000" in d
    assert "L 2.0000 -1.0000" in d
    assert "L 2.0000 1.0000" in d
    assert "L -2.0000 1.0000" in d


def test_rect_path_rotation_baked_into_coordinates() -> None:
    d = rect_path_d(0.0, 0.0, 2.0, 2.0, rotation=90.0)
    coords = _coords(d)
    # A 90-degree rotation of a square keeps corners on the unit-ish ring.
    xs = coords[0::2]
    ys = coords[1::2]
    for x, y in zip(xs, ys, strict=True):
        assert math.isclose(math.hypot(x, y), math.hypot(1.0, 1.0), rel_tol=1e-6)


def test_oval_path_horizontal_capsule_structure() -> None:
    d = oval_path_d(0.0, 0.0, 6.0, 2.0)
    # Two straight edges + two semicircle caps.
    assert d.count(" L ") == 2
    assert d.count(" A ") == 2
    assert d.endswith("Z")
    # Cap radius is half the minor axis.
    assert "A 1.0000 1.0000" in d


def test_oval_path_square_falls_back_to_circle() -> None:
    assert oval_path_d(0.0, 0.0, 4.0, 4.0) == circle_path_d(0.0, 0.0, 2.0)


def test_roundrect_path_has_four_arcs_and_four_lines() -> None:
    d = roundrect_path_d(0.0, 0.0, 10.0, 6.0, corner_radius=1.5)
    assert d.count(" A ") == 4
    assert d.count(" L ") == 4
    assert d.endswith("Z")
    assert "A 1.5000 1.5000" in d


def test_roundrect_zero_radius_is_plain_rect() -> None:
    assert roundrect_path_d(0.0, 0.0, 4.0, 2.0, corner_radius=0.0) == rect_path_d(
        0.0, 0.0, 4.0, 2.0
    )


def test_roundrect_radius_clamped_to_half_extent() -> None:
    # Corner radius larger than half the short side is clamped.
    d = roundrect_path_d(0.0, 0.0, 4.0, 2.0, corner_radius=5.0)
    assert "A 1.0000 1.0000" in d


def _pad(shape: str, *, roundrect_rratio: float = 0.0) -> PcbPad:
    return PcbPad(
        id="p",
        number="1",
        x=0.0,
        y=0.0,
        width=4.0,
        height=2.0,
        shape=shape,
        pad_type=PcbPadType.SMD,
        layers=(),
        roundrect_rratio=roundrect_rratio,
    )


def test_pad_path_dispatches_on_shape() -> None:
    assert pad_path_d(_pad("circle")).count(" A ") == 2
    assert " A " not in pad_path_d(_pad("rect"))
    assert pad_path_d(_pad("oval")).count(" A ") == 2
    assert pad_path_d(_pad("roundrect", roundrect_rratio=0.5)).count(" A ") == 4


def test_pad_path_expansion_overrides_dimensions() -> None:
    base = pad_path_d(_pad("rect"))
    expanded = pad_path_d(_pad("rect"), width=6.0, height=4.0)
    assert base != expanded
    assert "M -3.0000 -2.0000" in expanded


def test_custom_pad_concatenates_subpaths() -> None:
    pad = PcbPad(
        id="c",
        number="1",
        x=0.0,
        y=0.0,
        width=4.0,
        height=4.0,
        shape="custom",
        pad_type=PcbPadType.SMD,
        layers=(),
        custom_shapes=(
            PcbCircle(cx=0.0, cy=0.0, radius=1.0, width=0.0, fill=True),
            PcbLine(start_x=-2.0, start_y=0.0, end_x=2.0, end_y=0.0, width=0.5),
        ),
    )
    d = pad_path_d(pad)
    # One circle subpath (2 arcs) plus the buffered line outline.
    assert d.count("M ") >= 2
    assert " A " in d


@pytest.mark.parametrize("shape", ["circle", "rect", "oval", "roundrect"])
def test_pad_path_nonempty(shape: str) -> None:
    assert pad_path_d(_pad(shape, roundrect_rratio=0.25)) != ""
