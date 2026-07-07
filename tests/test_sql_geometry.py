from __future__ import annotations

import math
from pathlib import Path

import pytest
from shapely import MultiPolygon, Polygon

from phosphor_eda.domain.pcb import (
    PadStack,
    PcbArc,
    PcbArtworkKind,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbCircle,
    PcbClosedPath,
    PcbDrill,
    PcbLine,
    PcbPad,
    PcbPadType,
    PcbPolygon,
    PcbVia,
)
from phosphor_eda.geometry.pcb_geometry import (
    arc_center_from_three_points,
    arc_sweep_angle,
    arc_to_polyline,
    board_outline_polygon,
    closed_path_geometry,
    pad_polygon,
    polygon_geometry,
    polygon_shape_geometry,
    segment_geometry,
    trace_arc_geometry,
    via_geometry,
)

FIXTURES = Path(__file__).parent / "fixtures"
SWD_SWITCH_PCB = FIXTURES / "swd_switch.kicad_pcb"
ORANGECRAB_PCB = FIXTURES / "kicad-orangecrab/OrangeCrab.kicad_pcb"
PI_MX8_PCB = FIXTURES / "altium" / "pi-mx8" / "PCB" / "PiMX8MP_r0.3.PcbDoc"


def test_pad_circle_is_circular() -> None:
    pad = _pad("circle", width=2.0, height=2.0)

    geom = pad_polygon(pad)

    assert geom.area == pytest.approx(math.pi, rel=0.01)


def test_pad_rect_rotated_swaps_bounds() -> None:
    pad = _pad("rect", width=2.0, height=1.0, rotation=90.0)

    geom = pad_polygon(pad)
    min_x, min_y, max_x, max_y = geom.bounds

    assert max_x - min_x == pytest.approx(1.0, abs=0.001)
    assert max_y - min_y == pytest.approx(2.0, abs=0.001)


def test_segment_corridor_width_and_centerline_length() -> None:
    seg = PcbLine(0.0, 0.0, 3.0, 4.0, 0.3)

    centerline, corridor = segment_geometry(seg)

    assert centerline.length == pytest.approx(5.0, abs=0.001)
    _, min_y, _, max_y = corridor.bounds
    assert max_y - min_y > 0.0


def test_arc_geometry_has_expected_center_and_width() -> None:
    cx, cy, radius = arc_center_from_three_points(0.0, 0.0, 1.0, 1.0, 2.0, 0.0)
    angle = arc_sweep_angle(0.0, 0.0, 1.0, 1.0, 2.0, 0.0, cx, cy)
    arc = PcbArc(0.0, 0.0, 1.0, 1.0, 2.0, 0.0, 0.2)

    centerline, corridor = trace_arc_geometry(arc)

    assert (cx, cy, radius) == pytest.approx((1.0, 0.0, 1.0), abs=0.001)
    assert abs(angle) == pytest.approx(180.0, abs=1.0)
    assert centerline.length == pytest.approx(math.pi, rel=0.02)
    assert corridor.area > 0


def test_arc_polyline_endpoints() -> None:
    points = arc_to_polyline(0.0, 0.0, 1.0, 1.0, 2.0, 0.0)

    assert points[0] == pytest.approx((0.0, 0.0), abs=0.001)
    assert points[-1] == pytest.approx((2.0, 0.0), abs=0.001)


def test_via_geometry_radii() -> None:
    drill = PcbDrill("drill:via", 5.0, 5.0, 0.4)
    via = PcbVia("via:1", 5.0, 5.0, PadStack.simple("circle", 0.8, 0.8), (), drill)

    copper, drill = via_geometry(via)

    assert copper.area > drill.area
    assert math.sqrt(copper.area / math.pi) == pytest.approx(0.4, rel=0.01)
    assert math.sqrt(drill.area / math.pi) == pytest.approx(0.2, rel=0.01)


def test_polygon_with_holes() -> None:
    poly = PcbPolygon(
        points=[(0, 0), (10, 0), (10, 10), (0, 10)],
        holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]],
    )

    geom = polygon_geometry(poly)

    assert not geom.is_empty
    assert geom.area == pytest.approx(96.0, abs=0.01)


def test_unfilled_polygon_shape_geometry_uses_outline_corridor() -> None:
    poly = PcbPolygon(
        points=[(0, 0), (10, 0), (10, 10), (0, 10)],
        width=1.0,
        fill=False,
    )

    geom = polygon_shape_geometry(poly)

    assert not geom.is_empty
    assert geom.area < polygon_geometry(poly).area
    assert 34.0 <= geom.area <= 46.0


def test_unfilled_polygon_zero_width_shape_geometry_is_min_buffered_ring() -> None:
    # A zero-width unfilled outline paints a hairline ring, not the full
    # enclosed region (which would disagree with the SVG hairline stroke).
    poly = PcbPolygon(
        points=[(0, 0), (10, 0), (10, 10), (0, 10)],
        width=0.0,
        fill=False,
    )

    geom = polygon_shape_geometry(poly)

    assert not geom.is_empty
    # Ring, not the 100mm^2 filled region: perimeter 40mm * 0.05mm ~= 2mm^2.
    assert geom.area < 10.0
    assert geom.area == pytest.approx(40.0 * 0.05, rel=0.2)


def test_polygon_degenerate_returns_empty() -> None:
    # Never raw, possibly-invalid geometry: degenerate input yields an empty
    # geometry (treated as "no shape" by both SQL WKB and SVG serialization).
    assert polygon_geometry(PcbPolygon(points=[(0, 0), (1, 0)])).is_empty


def test_board_outline_from_normalized_fixture_geometry() -> None:
    from phosphor_eda.formats.kicad.board import parse_kicad_pcb

    pcb = parse_kicad_pcb(SWD_SWITCH_PCB)

    assert pcb.board_profile is not None
    geom = board_outline_polygon(pcb.board_profile)

    assert geom is not None
    assert isinstance(geom, Polygon)
    assert geom.is_valid
    assert geom.area > 0


def test_board_outline_closes_orangecrab_fractional_arc_ring() -> None:
    from phosphor_eda.formats.kicad.board import parse_kicad_pcb

    pcb = parse_kicad_pcb(ORANGECRAB_PCB)

    assert pcb.board_profile is not None
    geom = board_outline_polygon(pcb.board_profile)

    assert geom is not None
    assert isinstance(geom, Polygon)
    assert geom.is_valid
    assert geom.area == pytest.approx(1155.74176, rel=1e-6)


def test_board_outline_closes_pi_mx8_altium_fractional_arc_ring() -> None:
    from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb

    pcb = parse_altium_pcb(PI_MX8_PCB)

    assert pcb.board_profile is not None
    geom = board_outline_polygon(pcb.board_profile)

    assert geom is not None
    assert isinstance(geom, Polygon)
    assert geom.is_valid
    assert geom.area == pytest.approx(2192.263, rel=1e-3)


def test_board_outline_polygon_accepts_line_and_arc_geometry_rows() -> None:
    outline = PcbBoardProfile(
        elements=(
            _outline_line("l1", 0.0, 0.0, 2.0, 0.0),
            _outline_line("l2", 2.0, 0.0, 2.0, 2.0),
            _outline_line("l3", 2.0, 2.0, 0.0, 2.0),
            _outline_line("l4", 0.0, 2.0, 0.0, 0.0),
        )
    )

    geom = board_outline_polygon(outline)

    assert geom is not None
    assert geom.area == pytest.approx(4.0)


def test_board_outline_polygon_accepts_profile_polygon_elements() -> None:
    outline = PcbBoardProfile(
        elements=(
            PcbBoardProfileElement(
                id="outline:poly",
                kind=PcbArtworkKind.POLYGON,
                layer=None,
                data=PcbPolygon(points=[(0.0, 0.0), (5.0, 0.0), (5.0, 4.0), (0.0, 4.0)]),
            ),
        )
    )

    geom = board_outline_polygon(outline)

    assert geom is not None
    assert geom.area == pytest.approx(20.0)


def test_board_outline_polygon_subtracts_profile_cutouts() -> None:
    outline = PcbBoardProfile(
        elements=(
            PcbBoardProfileElement(
                id="outline:poly",
                kind=PcbArtworkKind.POLYGON,
                layer=None,
                data=PcbPolygon(points=[(0.0, 0.0), (5.0, 0.0), (5.0, 4.0), (0.0, 4.0)]),
            ),
            PcbBoardProfileElement(
                id="cutout:poly",
                kind=PcbArtworkKind.POLYGON,
                layer=None,
                data=PcbPolygon(points=[(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]),
                is_cutout=True,
            ),
        )
    )

    geom = board_outline_polygon(outline)

    assert geom is not None
    assert geom.area == pytest.approx(19.0)


def test_board_outline_polygon_keeps_all_panelized_outlines() -> None:
    """Two disjoint outlines (a panel) must both survive, not just the largest."""
    outline = PcbBoardProfile(
        elements=(
            PcbBoardProfileElement(
                id="outline:big",
                kind=PcbArtworkKind.POLYGON,
                layer=None,
                data=PcbPolygon(points=[(0.0, 0.0), (5.0, 0.0), (5.0, 4.0), (0.0, 4.0)]),
            ),
            PcbBoardProfileElement(
                id="outline:small",
                kind=PcbArtworkKind.POLYGON,
                layer=None,
                data=PcbPolygon(points=[(10.0, 0.0), (12.0, 0.0), (12.0, 2.0), (10.0, 2.0)]),
            ),
        )
    )

    geom = board_outline_polygon(outline)

    assert geom is not None
    assert isinstance(geom, MultiPolygon)
    assert len(geom.geoms) == 2
    assert geom.area == pytest.approx(24.0)


def test_board_outline_polygon_keeps_repaired_self_intersecting_outline() -> None:
    """A self-intersecting outline polygon repairs into a MultiPolygon; both
    lobes must survive as board material instead of being dropped."""
    outline = PcbBoardProfile(
        elements=(
            PcbBoardProfileElement(
                id="outline:bowtie",
                kind=PcbArtworkKind.POLYGON,
                layer=None,
                data=PcbPolygon(points=[(0.0, 0.0), (4.0, 4.0), (4.0, 0.0), (0.0, 4.0)]),
            ),
        )
    )

    geom = board_outline_polygon(outline)

    assert geom is not None
    assert geom.area == pytest.approx(8.0)


def test_board_outline_polygon_subtracts_repaired_self_intersecting_cutout() -> None:
    outline = PcbBoardProfile(
        elements=(
            PcbBoardProfileElement(
                id="outline:poly",
                kind=PcbArtworkKind.POLYGON,
                layer=None,
                data=PcbPolygon(points=[(-1.0, -1.0), (5.0, -1.0), (5.0, 5.0), (-1.0, 5.0)]),
            ),
            PcbBoardProfileElement(
                id="cutout:bowtie",
                kind=PcbArtworkKind.POLYGON,
                layer=None,
                data=PcbPolygon(points=[(0.0, 0.0), (4.0, 4.0), (4.0, 0.0), (0.0, 4.0)]),
                is_cutout=True,
            ),
        )
    )

    geom = board_outline_polygon(outline)

    assert geom is not None
    assert geom.area == pytest.approx(36.0 - 8.0)


def test_pad_polygon_octagon_area_and_bounds() -> None:
    pad = _pad("octagon", width=4.0, height=4.0)

    geom = pad_polygon(pad)

    # Regular octagon = box area minus four 45-degree corner triangles with
    # legs (half-extent * (2 - sqrt2)): w*h - (w*h/2) * (2 - sqrt2)^2.
    expected = 16.0 - 0.5 * 16.0 * (2.0 - math.sqrt(2.0)) ** 2
    assert geom.area == pytest.approx(expected, rel=0.001)
    min_x, min_y, max_x, max_y = geom.bounds
    assert (max_x - min_x, max_y - min_y) == pytest.approx((4.0, 4.0), abs=0.001)
    assert len(geom.exterior.coords) == 9  # 8 vertices + closing point


def test_pad_polygon_diamond_is_half_box_area() -> None:
    pad = _pad("diamond", width=4.0, height=2.0)

    geom = pad_polygon(pad)

    assert geom.area == pytest.approx(0.5 * 4.0 * 2.0, rel=0.001)
    assert len(geom.exterior.coords) == 5  # 4 vertices + closing point


def test_pad_polygon_octagon_honors_rotation() -> None:
    upright = pad_polygon(_pad("octagon", width=4.0, height=2.0))
    rotated = pad_polygon(_pad("octagon", width=4.0, height=2.0, rotation=90.0))

    assert rotated.area == pytest.approx(upright.area, rel=0.001)
    # 90-degree rotation swaps the bounding-box extents.
    ux0, uy0, ux1, uy1 = upright.bounds
    rx0, ry0, rx1, ry1 = rotated.bounds
    assert (rx1 - rx0) == pytest.approx(uy1 - uy0, abs=0.001)
    assert (ry1 - ry0) == pytest.approx(ux1 - ux0, abs=0.001)


def test_pad_polygon_custom_annular_circle_uses_centerline_radius() -> None:
    # PcbCircle.radius is the stroke centerline; an unfilled circle paints an
    # annulus spanning radius +/- width/2 (outer 2.25, inner 1.75).
    pad = PcbPad(
        id="pad:ring",
        number="1",
        x=0.0,
        y=0.0,
        stack=PadStack.simple("custom", 4.0, 4.0),
        pad_type=PcbPadType.SMD,
        layers=(),
        custom_shapes=(PcbCircle(cx=0.0, cy=0.0, radius=2.0, width=0.5, fill=False),),
    )

    geom = pad_polygon(pad)

    _, _, max_x, _ = geom.bounds
    assert max_x == pytest.approx(2.25, abs=0.02)
    assert geom.area == pytest.approx(math.pi * (2.25**2 - 1.75**2), rel=0.02)


def test_pad_polygon_custom_honors_dimension_overrides() -> None:
    pad = PcbPad(
        id="pad:custom",
        number="1",
        x=0.0,
        y=0.0,
        stack=PadStack.simple("custom", 2.0, 2.0),
        pad_type=PcbPadType.SMD,
        layers=(),
        custom_shapes=(PcbCircle(cx=0.0, cy=0.0, radius=1.0, width=0.0, fill=True),),
    )

    # A +0.5 margin per side dilates the r=1 copper circle to r=1.5.
    geom = pad_polygon(pad, width=3.0, height=3.0)

    assert geom.area == pytest.approx(math.pi * 1.5**2, rel=0.01)


def _pad(
    shape: str,
    *,
    width: float,
    height: float,
    rotation: float = 0.0,
) -> PcbPad:
    return PcbPad(
        id="pad:1",
        number="1",
        x=0.0,
        y=0.0,
        stack=PadStack.simple(shape, width, height),
        pad_type=PcbPadType.SMD,
        layers=(),
        rotation=rotation,
    )


def _outline_line(
    id_: str,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
) -> PcbBoardProfileElement:
    return PcbBoardProfileElement(
        id=id_,
        kind=PcbArtworkKind.LINE,
        layer=None,
        data=PcbLine(start_x, start_y, end_x, end_y, 0.1),
    )


def test_closed_path_geometry_repairs_self_intersecting_boundary() -> None:
    """A bowtie boundary must yield the valid repaired area, never raw invalid
    geometry - invalid WKB corrupts spatial queries and SVG serialization."""
    bowtie = PcbClosedPath.from_points([(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)])

    geometry = closed_path_geometry(bowtie)

    assert geometry is not None
    assert geometry.is_valid
    assert geometry.area > 0.0
