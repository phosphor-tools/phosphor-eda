"""Tests for artwork core data structures used by the derived PCB renderer."""

from __future__ import annotations

from shapely import GeometryCollection, Point

from phosphor_eda.pcb_render_artwork import (
    ArtworkItem,
    DerivedLayer,
    artwork_items_from_geometry,
    derived_layer_from_artwork,
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


def _renderable(
    geometry_id: str,
    kind: GeometryKind,
    layer_name: str,
    layer_role: str,
    side: str,
    *,
    geometry: Point | None = None,
) -> RenderableGeometry:
    return RenderableGeometry(
        id=geometry_id,
        kind=kind,
        layer=GeometryLayer(name=layer_name, role=layer_role, side=side),
        tags=GeometryTags(source_collection=kind.value),
        payload=Point(1, 1) if geometry is None else geometry,
    )
