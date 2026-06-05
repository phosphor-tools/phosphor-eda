"""Tests for artwork selection and derived PCB render layer records."""

from __future__ import annotations

import math

import pytest
from shapely import Point, Polygon

from phosphor_eda.pcb import PcbArc, PcbLine, PcbPad, PcbVia
from phosphor_eda.pcb_render_artwork import (
    DerivedLayer,
    board_outline_geometry,
    drill_geometry_for_layer,
    select_source_artwork,
)
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometryLayer,
    GeometryTags,
    PcbGeometryStore,
    RenderableGeometry,
)
from phosphor_eda.pcb_render_primitives import SvgPrimitive
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


def test_select_source_artwork_resolves_active_side_from_rendered_side() -> None:
    front_silk = _renderable("silk-1", GeometryKind.SILK_LINE, "F.SilkS", "silkscreen", "front")
    back_silk = _renderable("silk-2", GeometryKind.SILK_LINE, "B.SilkS", "silkscreen", "back")
    front_copper = _renderable("pad-1", GeometryKind.PAD, "F.Cu", "copper", "front")
    store = PcbGeometryStore(items=(front_silk, back_silk, front_copper))

    selected = select_source_artwork(
        store,
        (LayerSelectionRule(match=LayerMatch(function="silkscreen", side="active")),),
        active_side="back",
    )

    assert selected == (back_silk,)


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


def test_select_source_artwork_requires_explicit_keepout_opt_in() -> None:
    front_pad = _renderable("pad-1", GeometryKind.PAD, "F.Cu", "copper", "front")
    front_keepout = _renderable("keepout-1", GeometryKind.KEEPOUT, "F.Cu", "keepout", "front")
    store = PcbGeometryStore(items=(front_pad, front_keepout))

    selected_by_layer_name = select_source_artwork(
        store,
        (LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
    )
    selected_by_function = select_source_artwork(
        store,
        (LayerSelectionRule(match=LayerMatch(function="keepout")),),
    )
    selected_by_object = select_source_artwork(
        store,
        (LayerSelectionRule(match=LayerMatch(name="F.Cu"), objects=("keepout",)),),
    )

    assert selected_by_layer_name == (front_pad,)
    assert selected_by_function == (front_keepout,)
    assert selected_by_object == (front_keepout,)


def test_derived_layer_is_keyed_by_visual_role_and_carries_primitive_provenance() -> None:
    role = VisualRole(namespace="cad", function="copper", side="front")
    style = ResolvedStyle(fill="#d17a22", opacity=0.35)
    primitive = SvgPrimitive(
        d="M 0.0000 0.0000 L 1.0000 0.0000 L 1.0000 1.0000 Z",
        source_id="pad-1",
        source_layer="F.Cu",
        kind=GeometryKind.PAD,
        tags=GeometryTags(source_collection="pads"),
    )

    layer = DerivedLayer(
        id="cad:copper:front",
        role=role,
        primitives=(primitive,),
        source_layers=("F.Cu",),
        source_ids=("pad-1",),
        style=style,
        data={"selected": "true"},
    )

    assert layer.id == "cad:copper:front"
    assert layer.primitives == (primitive,)
    assert layer.source_layers == ("F.Cu",)
    assert layer.source_ids == ("pad-1",)
    assert layer.style == style
    assert layer.data == {"selected": "true"}


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


def test_board_outline_falls_back_to_board_material_when_outline_reconstruction_is_empty() -> None:
    empty_outline_geometry: tuple[list[tuple[float, float]], list[list[tuple[float, float]]]] = (
        [],
        [],
    )
    material = _renderable(
        "board-material",
        GeometryKind.BOARD_MATERIAL,
        "Edge.Cuts",
        "edge",
        "",
        geometry=(0.0, 0.0, 12.0, 8.0),
    )
    empty_outline = _renderable(
        "board-outline",
        GeometryKind.BOARD_OUTLINE,
        "Edge.Cuts",
        "edge",
        "",
        geometry=empty_outline_geometry,
    )
    store = PcbGeometryStore(items=(material, empty_outline))

    geometry = board_outline_geometry(store)

    assert isinstance(geometry, Polygon)
    _assert_close(float(geometry.area), 96.0)


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


def test_slotted_pad_drills_convert_to_subtractive_slot_geometry() -> None:
    pad_drill = _renderable(
        "drill-1",
        GeometryKind.DRILL,
        "drills",
        "drill",
        "",
        geometry=_pad(
            shape="oval",
            x=0.0,
            y=0.0,
            drill=0.6,
            drill_shape="oval",
            drill_width=0.6,
            drill_height=1.6,
        ),
    )
    store = PcbGeometryStore(items=(pad_drill,))

    geometry = drill_geometry_for_layer(store, layer_name="F.Cu")

    min_x, min_y, max_x, max_y = geometry.bounds
    assert max_x - min_x == pytest.approx(0.6, abs=0.02)
    assert max_y - min_y == pytest.approx(1.6, abs=0.02)
    assert geometry.contains(Point(0.0, 0.0))
    assert geometry.contains(Point(0.0, 0.7))
    assert not geometry.contains(Point(0.7, 0.0))


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
    drill_shape: str = "circle",
    drill_width: float = 0.0,
    drill_height: float = 0.0,
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
        drill_shape=drill_shape,
        drill_width=drill_width,
        drill_height=drill_height,
    )


def _assert_close(actual: float, expected: float, *, rel: float = 1e-9) -> None:
    tolerance = max(abs(expected) * rel, 1e-9)
    assert abs(actual - expected) <= tolerance
