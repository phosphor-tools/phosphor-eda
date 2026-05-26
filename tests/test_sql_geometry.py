"""Tests for SQL geometry construction helpers."""

import math
from pathlib import Path

import pytest
from shapely import Polygon

from phosphor_eda.pcb import PcbFootprint, PcbPad, PcbPolygon, PcbSegment, PcbVia
from phosphor_eda.sql.geometry import (
    arc_center_from_three_points,
    arc_sweep_angle,
    arc_to_polyline,
    board_outline_polygon,
    footprint_bbox_polygon,
    footprint_side,
    pad_polygon,
    pad_side,
    polygon_geometry,
    segment_geometry,
    trace_arc_geometry,
    via_geometry,
)

FIXTURES = Path(__file__).parent / "fixtures"
SWD_SWITCH_PCB = FIXTURES / "swd_switch.kicad_pcb"
ORANGECRAB_PCB = FIXTURES / "orangecrab.kicad_pcb"
PI_MX8_PCB = FIXTURES / "altium" / "pi-mx8" / "PCB" / "PiMX8MP_r0.3.PcbDoc"


# ---------------------------------------------------------------------------
# Pad geometry
# ---------------------------------------------------------------------------


def _make_pad(
    shape: str = "circle",
    width: float = 1.0,
    height: float = 1.0,
    rotation: float = 0.0,
    roundrect_rratio: float = 0.25,
    number: str = "1",
    x: float = 0.0,
    y: float = 0.0,
    layers: list[str] | None = None,
    net_number: int = 1,
    net_name: str = "VCC",
    footprint_ref: str = "U1",
) -> PcbPad:
    return PcbPad(
        shape=shape,
        width=width,
        height=height,
        rotation=rotation,
        roundrect_rratio=roundrect_rratio,
        number=number,
        x=x,
        y=y,
        layers=layers if layers is not None else ["F.Cu"],
        net_number=net_number,
        net_name=net_name,
        footprint_ref=footprint_ref,
    )


def test_pad_circle_is_circular() -> None:
    pad = _make_pad(shape="circle", width=2.0, height=2.0)
    geom = pad_polygon(pad)
    expected_area = math.pi * 1.0**2  # radius = 1.0
    assert geom.area == pytest.approx(expected_area, rel=0.01)


def test_pad_rect_dimensions() -> None:
    pad = _make_pad(shape="rect", width=2.0, height=1.0)
    geom = pad_polygon(pad)
    assert geom.area == pytest.approx(2.0, rel=0.001)
    minx, miny, maxx, maxy = geom.bounds
    assert maxx - minx == pytest.approx(2.0, abs=0.001)
    assert maxy - miny == pytest.approx(1.0, abs=0.001)


def test_pad_rect_rotated() -> None:
    pad = _make_pad(shape="rect", width=2.0, height=1.0, rotation=90.0)
    geom = pad_polygon(pad)
    # After 90° rotation, bounds should swap
    minx, miny, maxx, maxy = geom.bounds
    assert maxx - minx == pytest.approx(1.0, abs=0.001)
    assert maxy - miny == pytest.approx(2.0, abs=0.001)


def test_pad_oval_major_axis() -> None:
    pad = _make_pad(shape="oval", width=3.0, height=1.0)
    geom = pad_polygon(pad)
    minx, _, maxx, _ = geom.bounds
    # Oval 3x1: capsule shape, total width ~= 3.0
    assert maxx - minx == pytest.approx(3.0, abs=0.01)


def test_pad_roundrect_smaller_than_rect() -> None:
    rect_pad = _make_pad(shape="rect", width=2.0, height=1.0)
    rr_pad = _make_pad(shape="roundrect", width=2.0, height=1.0, roundrect_rratio=0.25)
    rect_geom = pad_polygon(rect_pad)
    rr_geom = pad_polygon(rr_pad)
    # Rounded rectangle has slightly less area than full rectangle
    assert rr_geom.area < rect_geom.area
    # But not by much (< 10% difference)
    assert rr_geom.area > rect_geom.area * 0.9


# ---------------------------------------------------------------------------
# Segment geometry
# ---------------------------------------------------------------------------


def test_segment_corridor_width() -> None:
    seg = PcbSegment(
        start_x=0.0, start_y=0.0, end_x=10.0, end_y=0.0, width=0.3, layer="F.Cu", net_number=1
    )
    _centerline, corridor = segment_geometry(seg)
    # Corridor bounds should extend width/2 above and below centerline
    _, miny, _, maxy = corridor.bounds
    assert maxy - miny == pytest.approx(0.3, abs=0.001)


def test_segment_centerline_length() -> None:
    seg = PcbSegment(
        start_x=0.0, start_y=0.0, end_x=3.0, end_y=4.0, width=0.2, layer="F.Cu", net_number=1
    )
    centerline, _ = segment_geometry(seg)
    assert centerline.length == pytest.approx(5.0, abs=0.001)


# ---------------------------------------------------------------------------
# Arc geometry
# ---------------------------------------------------------------------------


def test_arc_center_known_values() -> None:
    # Semicircle: start=(0,0), mid=(1,1), end=(2,0) → center=(1,0), radius=1
    cx, cy, r = arc_center_from_three_points(0.0, 0.0, 1.0, 1.0, 2.0, 0.0)
    assert cx == pytest.approx(1.0, abs=0.001)
    assert cy == pytest.approx(0.0, abs=0.001)
    assert r == pytest.approx(1.0, abs=0.001)


def test_arc_center_degenerate() -> None:
    # Collinear points — should return a fallback (midpoint as center, large radius)
    cx, cy, _r = arc_center_from_three_points(0.0, 0.0, 1.0, 0.0, 2.0, 0.0)
    # Should not raise; returns some result
    assert math.isfinite(cx) and math.isfinite(cy)


def test_arc_polyline_endpoints() -> None:
    # Semicircle from (0,0) through (1,1) to (2,0)
    points = arc_to_polyline(0.0, 0.0, 1.0, 1.0, 2.0, 0.0)
    assert len(points) >= 3
    # First and last points match start and end
    assert points[0][0] == pytest.approx(0.0, abs=0.001)
    assert points[0][1] == pytest.approx(0.0, abs=0.001)
    assert points[-1][0] == pytest.approx(2.0, abs=0.001)
    assert points[-1][1] == pytest.approx(0.0, abs=0.001)


def test_arc_polyline_length() -> None:
    # Semicircle radius=1 → arc length = π
    from shapely import LineString

    points = arc_to_polyline(0.0, 0.0, 1.0, 1.0, 2.0, 0.0)
    line = LineString(points)
    assert line.length == pytest.approx(math.pi, rel=0.01)


def test_arc_sweep_angle_semicircle() -> None:
    # Semicircle: start=(0,0), mid=(1,1), end=(2,0), center=(1,0)
    angle = arc_sweep_angle(0.0, 0.0, 1.0, 1.0, 2.0, 0.0, 1.0, 0.0)
    assert abs(angle) == pytest.approx(180.0, abs=1.0)


def test_trace_arc_geometry_has_width() -> None:
    from phosphor_eda.pcb import PcbTraceArc

    arc = PcbTraceArc(
        start_x=0.0,
        start_y=0.0,
        mid_x=1.0,
        mid_y=1.0,
        end_x=2.0,
        end_y=0.0,
        width=0.2,
        layer="F.Cu",
        net_number=1,
    )
    centerline, corridor = trace_arc_geometry(arc)
    # Corridor should be wider than centerline
    assert corridor.area > 0
    assert centerline.length == pytest.approx(math.pi, rel=0.02)


# ---------------------------------------------------------------------------
# Via geometry
# ---------------------------------------------------------------------------


def test_via_geometry_radii() -> None:
    via = PcbVia(x=5.0, y=5.0, size=0.8, drill=0.4, layers=["F.Cu", "B.Cu"], net_number=1)
    copper, drill = via_geometry(via)
    # Copper area > drill area
    assert copper.area > drill.area
    # Copper radius = 0.4, drill radius = 0.2
    copper_r = math.sqrt(copper.area / math.pi)
    drill_r = math.sqrt(drill.area / math.pi)
    assert copper_r == pytest.approx(0.4, rel=0.01)
    assert drill_r == pytest.approx(0.2, rel=0.01)


# ---------------------------------------------------------------------------
# Polygon geometry
# ---------------------------------------------------------------------------


def test_polygon_with_holes() -> None:
    poly = PcbPolygon(
        points=[(0, 0), (10, 0), (10, 10), (0, 10)],
        layer="F.Cu",
        holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]],
    )
    geom = polygon_geometry(poly)
    assert geom is not None
    # Area should be outer (100) minus hole (4)
    assert geom.area == pytest.approx(96.0, abs=0.01)


def test_polygon_degenerate_returns_none() -> None:
    poly = PcbPolygon(points=[(0, 0), (1, 0)], layer="F.Cu")
    geom = polygon_geometry(poly)
    assert geom is None


# ---------------------------------------------------------------------------
# Board outline
# ---------------------------------------------------------------------------


def test_board_outline_closed() -> None:
    """Board outline from swd_switch fixture forms a valid closed polygon."""
    from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb

    if not SWD_SWITCH_PCB.exists():
        pytest.skip("Fixture not available")
    pcb = parse_kicad_pcb(SWD_SWITCH_PCB)
    geom = board_outline_polygon(pcb.outline_lines, pcb.outline_arcs)
    assert geom is not None
    assert isinstance(geom, Polygon)
    assert geom.is_valid
    assert geom.area > 0


def test_board_outline_closes_orangecrab_fractional_arc_ring() -> None:
    """KiCad outline arcs can miss line endpoints by floating-point noise."""
    from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb

    pcb = parse_kicad_pcb(ORANGECRAB_PCB)
    geom = board_outline_polygon(pcb.outline_lines, pcb.outline_arcs)

    assert geom is not None
    assert isinstance(geom, Polygon)
    assert geom.is_valid
    assert geom.area == pytest.approx(1155.74176, rel=1e-6)


def test_board_outline_closes_pi_mx8_altium_fractional_arc_ring() -> None:
    """Altium mil conversion leaves outline arc endpoints a few nanometres apart."""
    from phosphor_eda.altium.pcb_parser import parse_altium_pcb

    pcb = parse_altium_pcb(PI_MX8_PCB)
    geom = board_outline_polygon(pcb.outline_lines, pcb.outline_arcs)

    assert geom is not None
    assert isinstance(geom, Polygon)
    assert geom.is_valid
    assert geom.area == pytest.approx(2192.26296, rel=1e-6)


# ---------------------------------------------------------------------------
# Pad side / footprint side
# ---------------------------------------------------------------------------


def test_pad_side_smd_front() -> None:
    pad = _make_pad(layers=["F.Cu", "F.Paste", "F.Mask"])
    assert pad_side(pad) == "front"


def test_pad_side_smd_back() -> None:
    pad = _make_pad(layers=["B.Cu", "B.Paste", "B.Mask"])
    assert pad_side(pad) == "back"


def test_pad_side_through_hole() -> None:
    pad = _make_pad(layers=["*.Cu", "*.Mask"])
    assert pad_side(pad) == "through"


def test_pad_side_explicit_both() -> None:
    pad = _make_pad(layers=["F.Cu", "B.Cu", "F.Mask", "B.Mask"])
    assert pad_side(pad) == "through"


def test_footprint_side_front() -> None:
    fp = PcbFootprint(reference="U1", footprint_lib="lib", x=0, y=0, rotation=0, layer="F.Cu")
    assert footprint_side(fp) == "front"


def test_footprint_side_back() -> None:
    fp = PcbFootprint(reference="U1", footprint_lib="lib", x=0, y=0, rotation=0, layer="B.Cu")
    assert footprint_side(fp) == "back"


# ---------------------------------------------------------------------------
# Footprint bbox
# ---------------------------------------------------------------------------


def test_footprint_bbox_from_bbox_field() -> None:
    fp = PcbFootprint(
        reference="U1",
        footprint_lib="lib",
        x=5.0,
        y=5.0,
        rotation=0,
        layer="F.Cu",
        bbox=(1.0, 2.0, 3.0, 4.0),
    )
    geom = footprint_bbox_polygon(fp)
    assert geom is not None
    assert geom.area == pytest.approx(4.0, abs=0.001)  # 2x2
