"""Tests for artwork core data structures used by the derived PCB renderer."""

from __future__ import annotations

import math

from shapely import GeometryCollection, Point, Polygon

from phosphor_eda.pcb import PcbArc, PcbLine, PcbPad, PcbPolygon, PcbSegment, PcbTraceArc, PcbVia
from phosphor_eda.pcb_render_artwork import (
    ArtworkItem,
    DerivedLayer,
    artwork_items_from_geometry,
    board_outline_geometry,
    derived_layer_from_artwork,
    drill_geometry_for_layer,
    geometry_to_artwork,
    select_source_artwork,
)
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometryLayer,
    GeometryTags,
    PcbGeometryStore,
    RenderableGeometry,
)
from phosphor_eda.pcb_render_settings import LayerMatch, LayerSelectionRule
from phosphor_eda.pcb_render_tokens import ResolvedStyle, VisualRole


def test_select_source_artwork_matches_layer_function_side_and_object_classes() -> None:
    front_pad = _renderable("pad-1", GeometryKind.PAD, "F.Cu", "copper", "front")
    front_trace = _renderable("trace-1", GeometryKind.TRACE, "F.Cu", "copper", "front")
    back_pad = _renderable("pad-2", GeometryKind.PAD, "B.Cu", "copper", "back")
    store = PcbGeometryStore(items=(front_pad, front_trace, back_pad))

    selected = select_source_artwork(
        store,
        (
            LayerSelectionRule(
                match=LayerMatch(function="copper", side="front"),
                objects=("pads",),
            ),
        ),
    )

    assert selected == (front_pad,)


def test_select_source_artwork_rule_without_object_filter_selects_all_matched_layers() -> None:
    front_pad = _renderable("pad-1", GeometryKind.PAD, "F.Cu", "copper", "front")
    front_trace = _renderable("trace-1", GeometryKind.TRACE, "F.Cu", "copper", "front")
    back_trace = _renderable("trace-2", GeometryKind.TRACE, "B.Cu", "copper", "back")
    store = PcbGeometryStore(items=(front_pad, front_trace, back_trace))

    selected = select_source_artwork(
        store,
        (LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
    )

    assert selected == (front_pad, front_trace)


def test_selected_renderable_geometry_becomes_artwork_items_with_shapely_geometry() -> None:
    geometry = Point(1, 1)
    raw = _renderable(
        "pad-1",
        GeometryKind.PAD,
        "F.Cu",
        "copper",
        "front",
        geometry=geometry,
    )

    artwork = artwork_items_from_geometry((raw,))

    assert artwork == (
        ArtworkItem(
            geometry=geometry,
            source_ids=("pad-1",),
            source_layers=("F.Cu",),
            tags=raw.tags,
        ),
    )
    assert artwork[0].geometry.equals(geometry)


def test_derived_layer_is_keyed_by_visual_role_and_carries_source_layer_provenance() -> None:
    first = ArtworkItem(
        geometry=Point(1, 1),
        source_ids=("pad-1",),
        source_layers=("F.Cu",),
        tags=GeometryTags(source_collection="pads"),
    )
    second = ArtworkItem(
        geometry=Point(2, 2),
        source_ids=("trace-1",),
        source_layers=("F.Cu",),
        tags=GeometryTags(source_collection="segments"),
    )
    role = VisualRole(namespace="cad", function="copper", side="front")
    style = ResolvedStyle(fill="#d17a22", opacity=0.35)

    layer = derived_layer_from_artwork(
        role=role,
        artwork=(first, second),
        style=style,
        data={"selected": "true"},
    )

    assert layer == DerivedLayer(
        id="cad:copper:front",
        role=role,
        geometry=layer.geometry,
        source_layers=("F.Cu",),
        source_ids=("pad-1", "trace-1"),
        style=style,
        data={"selected": "true"},
    )
    assert isinstance(layer.geometry, GeometryCollection)
    assert layer.geometry.equals(GeometryCollection([Point(1, 1), Point(2, 2)]))


def test_pad_shapes_convert_to_polygons() -> None:
    rect = _renderable(
        "pad-rect",
        GeometryKind.PAD,
        "F.Cu",
        "copper",
        "front",
        geometry=_pad(shape="rect", width=2.0, height=1.0),
    )
    circle = _renderable(
        "pad-circle",
        GeometryKind.PAD,
        "F.Cu",
        "copper",
        "front",
        geometry=_pad(shape="circle", width=2.0, height=2.0),
    )

    rect_artwork = geometry_to_artwork(rect)
    circle_artwork = geometry_to_artwork(circle)

    assert rect_artwork is not None
    assert circle_artwork is not None
    assert isinstance(rect_artwork.geometry, Polygon)
    _assert_close(float(rect_artwork.geometry.area), 2.0)
    _assert_close(float(circle_artwork.geometry.area), math.pi, rel=0.01)


def test_traces_convert_to_buffered_line_polygons() -> None:
    trace = _renderable(
        "trace-1",
        GeometryKind.TRACE,
        "F.Cu",
        "copper",
        "front",
        geometry=PcbSegment(0.0, 0.0, 10.0, 0.0, 0.5, "F.Cu", 1),
    )

    artwork = geometry_to_artwork(trace)

    assert artwork is not None
    assert isinstance(artwork.geometry, Polygon)
    _assert_close(float(artwork.geometry.area), 5.0)
    _assert_bounds_close(artwork.geometry.bounds, (0.0, -0.25, 10.0, 0.25))


def test_trace_arcs_convert_to_buffered_arc_polygons() -> None:
    trace_arc = _renderable(
        "trace-arc-1",
        GeometryKind.TRACE_ARC,
        "F.Cu",
        "copper",
        "front",
        geometry=PcbTraceArc(0.0, 0.0, 1.0, 1.0, 2.0, 0.0, 0.2, "F.Cu", 1),
    )

    artwork = geometry_to_artwork(trace_arc)

    assert artwork is not None
    assert isinstance(artwork.geometry, Polygon)
    _assert_close(float(artwork.geometry.area), math.pi * 0.2, rel=0.03)
    assert artwork.geometry.bounds[3] > 0.9


def test_zones_and_polygons_preserve_holes() -> None:
    polygon = _renderable(
        "zone-1",
        GeometryKind.ZONE,
        "F.Cu",
        "copper",
        "front",
        geometry=PcbPolygon(
            points=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
            layer="F.Cu",
            holes=[[(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0)]],
        ),
    )

    artwork = geometry_to_artwork(polygon)

    assert artwork is not None
    assert isinstance(artwork.geometry, Polygon)
    _assert_close(float(artwork.geometry.area), 96.0)
    assert len(artwork.geometry.interiors) == 1


def test_vias_convert_to_annular_copper_and_drill_geometry_separately() -> None:
    via = _renderable(
        "via-1",
        GeometryKind.VIA,
        "vias",
        "via",
        "",
        geometry=PcbVia(5.0, 5.0, 1.0, 0.4, ["F.Cu", "B.Cu"], 1),
    )
    store = PcbGeometryStore(items=(via,))

    artwork = geometry_to_artwork(via)
    drills = drill_geometry_for_layer(store, layer_name="F.Cu")

    assert artwork is not None
    assert isinstance(artwork.geometry, Polygon)
    assert len(artwork.geometry.interiors) == 1
    _assert_close(float(artwork.geometry.area), math.pi * (0.5**2 - 0.2**2), rel=0.01)
    _assert_close(float(drills.area), math.pi * 0.2**2, rel=0.01)


def test_silkscreen_and_fab_lines_convert_to_buffered_line_polygons() -> None:
    silk = _renderable(
        "silk-1",
        GeometryKind.SILK_LINE,
        "F.SilkS",
        "silkscreen",
        "front",
        geometry=PcbLine(0.0, 0.0, 4.0, 0.0, "F.SilkS", 0.2),
    )
    fab = _renderable(
        "fab-1",
        GeometryKind.FAB_LINE,
        "F.Fab",
        "fabrication",
        "front",
        geometry=PcbLine(0.0, 1.0, 4.0, 1.0, "F.Fab", 0.1),
    )

    silk_artwork = geometry_to_artwork(silk)
    fab_artwork = geometry_to_artwork(fab)

    assert silk_artwork is not None
    assert fab_artwork is not None
    assert isinstance(silk_artwork.geometry, Polygon)
    assert isinstance(fab_artwork.geometry, Polygon)
    _assert_close(float(silk_artwork.geometry.area), 0.8)
    _assert_close(float(fab_artwork.geometry.area), 0.4)


def test_board_outline_converts_to_a_polygon() -> None:
    outline_arcs: list[PcbArc] = []
    outline = _renderable(
        "board-outline",
        GeometryKind.BOARD_OUTLINE,
        "Edge.Cuts",
        "edge",
        "",
        geometry=(
            [
                PcbLine(0.0, 0.0, 10.0, 0.0, "Edge.Cuts", 0.1),
                PcbLine(10.0, 0.0, 10.0, 5.0, "Edge.Cuts", 0.1),
                PcbLine(10.0, 5.0, 0.0, 5.0, "Edge.Cuts", 0.1),
                PcbLine(0.0, 5.0, 0.0, 0.0, "Edge.Cuts", 0.1),
            ],
            outline_arcs,
        ),
    )
    store = PcbGeometryStore(items=(outline,))

    geometry = board_outline_geometry(store)

    assert isinstance(geometry, Polygon)
    _assert_close(float(geometry.area), 50.0)


def test_drill_holes_convert_to_subtractive_geometry() -> None:
    pad_drill = _renderable(
        "drill-1",
        GeometryKind.DRILL,
        "drills",
        "drill",
        "",
        geometry=_pad(shape="circle", x=0.0, y=0.0, drill=0.6),
    )
    via = _renderable(
        "via-1",
        GeometryKind.VIA,
        "vias",
        "via",
        "",
        geometry=PcbVia(2.0, 0.0, 1.0, 0.4, ["F.Cu", "B.Cu"], 1),
    )
    store = PcbGeometryStore(items=(pad_drill, via))

    geometry = drill_geometry_for_layer(store, layer_name="F.Cu")

    _assert_close(float(geometry.area), math.pi * 0.3**2 + math.pi * 0.2**2, rel=0.01)
    assert geometry.contains(Point(0.0, 0.0))
    assert geometry.contains(Point(2.0, 0.0))


def test_drill_holes_are_not_additive_artwork_items() -> None:
    pad_drill = _renderable(
        "drill-1",
        GeometryKind.DRILL,
        "drills",
        "drill",
        "",
        geometry=_pad(shape="circle", x=0.0, y=0.0, drill=0.6),
    )

    assert geometry_to_artwork(pad_drill) is None
    assert artwork_items_from_geometry((pad_drill,)) == ()


def _renderable(
    geometry_id: str,
    kind: GeometryKind,
    layer_name: str,
    layer_role: str,
    side: str,
    *,
    geometry: object | None = None,
) -> RenderableGeometry:
    return RenderableGeometry(
        id=geometry_id,
        kind=kind,
        layer=GeometryLayer(name=layer_name, role=layer_role, side=side),
        tags=GeometryTags(source_collection=kind.value),
        payload=Point(1, 1) if geometry is None else geometry,
        source=geometry,
    )


def _pad(
    *,
    shape: str,
    width: float = 1.0,
    height: float = 1.0,
    x: float = 0.0,
    y: float = 0.0,
    drill: float = 0.0,
) -> PcbPad:
    return PcbPad(
        number="1",
        x=x,
        y=y,
        width=width,
        height=height,
        shape=shape,
        layers=["F.Cu", "B.Cu"],
        net_number=1,
        net_name="GND",
        footprint_ref="J1",
        drill=drill,
    )


def _assert_close(actual: float, expected: float, *, rel: float = 1e-9) -> None:
    tolerance = max(abs(expected) * rel, 1e-9)
    assert abs(actual - expected) <= tolerance


def _assert_bounds_close(
    actual: tuple[float, float, float, float],
    expected: tuple[float, float, float, float],
) -> None:
    for actual_value, expected_value in zip(actual, expected, strict=True):
        _assert_close(actual_value, expected_value)
