from __future__ import annotations

from test_pcb_render import _board

from phosphor_eda.pcb_render_geometry import build_geometry_store
from phosphor_eda.pcb_render_modes import (
    build_eda_layers,
    build_highlight_layers,
    build_realistic_layers,
)
from phosphor_eda.pcb_render_settings import (
    HighlightSpec,
    LayerMatch,
    LayerSelectionRule,
    RenderSettings,
    SourceSelection,
    load_render_settings_json,
)


def test_eda_layers_are_built_from_normalized_geometry() -> None:
    store = build_geometry_store(_board(), side="front")
    settings = RenderSettings(
        render_mode="eda",
        side="front",
        source=SourceSelection(
            layers=[
                LayerSelectionRule(match=LayerMatch(role="copper")),
                LayerSelectionRule(
                    match=LayerMatch(role="silkscreen", side="front"),
                    objects=("graphic", "text"),
                ),
                LayerSelectionRule(match=LayerMatch(role="edge")),
            ]
        ),
    )

    layers = build_eda_layers(store, settings, warn=lambda _message: None)

    roles = {(layer.role.function, layer.role.side) for layer in layers}
    assert ("copper", "front") in roles
    assert ("copper", "back") in roles
    assert ("silkscreen", "front") in roles
    assert ("edge", "") in roles
    assert any(primitive.kind == "via" for layer in layers for primitive in layer.primitives)


def test_realistic_layers_use_board_material_mask_and_silkscreen() -> None:
    store = build_geometry_store(_board(), side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review"}')
    settings.source = SourceSelection(
        layers=[
            LayerSelectionRule(match=LayerMatch(role="copper")),
            LayerSelectionRule(match=LayerMatch(role="solder_mask", side="front")),
            LayerSelectionRule(
                match=LayerMatch(role="silkscreen", side="front"),
                objects=("graphic", "text"),
            ),
        ]
    )
    settings.render_mode = "realistic"
    settings.side = "front"

    layers = build_realistic_layers(store, settings, warn=lambda _message: None)

    layer_ids = {layer.id for layer in layers}
    assert {
        "realistic:substrate",
        "realistic:solderMask",
        "realistic:coveredCopper",
        "realistic:silkscreen",
    }.issubset(layer_ids)


def test_highlights_match_normalized_geometry_tags() -> None:
    store = build_geometry_store(_board(), side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review"}')
    settings.render_mode = "eda"
    settings.side = "front"
    settings.source = SourceSelection(layers=[LayerSelectionRule(match=LayerMatch(role="copper"))])
    settings.highlights = [HighlightSpec(net="VCC")]

    groups = build_highlight_layers(store, settings, warn=lambda _message: None)

    assert len(groups) == 1
    assert groups[0].target == "net:VCC"
    assert any(layer.primitives for layer in groups[0].layers)
