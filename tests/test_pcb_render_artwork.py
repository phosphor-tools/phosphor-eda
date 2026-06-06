from __future__ import annotations

from test_pcb_render import _board

from phosphor_eda.pcb import PcbGeometryObject, PcbGeometryRole
from phosphor_eda.pcb_render_artwork import (
    board_profile_geometry,
    drill_geometry_for_layer,
    select_source_artwork,
    selected_copper_layers,
    solder_mask_opening_primitives,
)
from phosphor_eda.pcb_render_geometry import build_geometry_store
from phosphor_eda.pcb_render_settings import LayerMatch, LayerSelectionRule


def test_source_selection_matches_object_type_and_geometry_roles() -> None:
    store = build_geometry_store(_board(), side="front")
    rules = [
        LayerSelectionRule(
            match=LayerMatch(role="silkscreen", side="front"),
            objects=("graphic", "text"),
        )
    ]

    selected = select_source_artwork(store, rules, active_side="front")

    assert {item.object_type for item in selected} == {
        PcbGeometryObject.GRAPHIC,
        PcbGeometryObject.TEXT,
    }
    assert all(PcbGeometryRole.SILKSCREEN in item.roles for item in selected)


def test_source_selection_rejects_removed_plural_object_aliases() -> None:
    store = build_geometry_store(_board(), side="front")
    rules = [LayerSelectionRule(match=LayerMatch(role="copper"), objects=("pads", "traces"))]

    assert select_source_artwork(store, rules, active_side="front") == ()


def test_selected_copper_layers_projects_vias_from_normalized_layer_rules() -> None:
    store = build_geometry_store(_board(), side="front")
    rules = [LayerSelectionRule(match=LayerMatch(role="copper"), objects=("via",))]

    layers = selected_copper_layers(store, rules, active_side="front")

    assert {layer.name for layer in layers} == {"F.Cu", "B.Cu"}


def test_board_outline_and_drill_geometry_are_derived_from_geometry_store() -> None:
    store = build_geometry_store(_board(), side="front")

    assert not board_profile_geometry(store).is_empty
    assert not drill_geometry_for_layer(store, layer_name="F.Cu").is_empty


def test_solder_mask_openings_use_normalized_solder_mask_role() -> None:
    store = build_geometry_store(_board(), side="front")

    primitives = solder_mask_opening_primitives(store, side="front")

    assert primitives
    assert {primitive.kind for primitive in primitives} == {"solder_mask"}
