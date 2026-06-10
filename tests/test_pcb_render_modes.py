from __future__ import annotations

from pathlib import Path

from test_pcb_render import _board

from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb_render_inventory import build_inventory
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

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_eda_layers_are_built_from_typed_inventory() -> None:
    inventory = build_inventory(_board(), side="front")
    settings = RenderSettings(
        render_mode="eda",
        side="front",
        source=SourceSelection(
            layers=[
                LayerSelectionRule(match=LayerMatch(role="copper")),
                LayerSelectionRule(
                    match=LayerMatch(role="silkscreen", side="front"),
                    purposes=("silkscreen", "designator"),
                ),
                LayerSelectionRule(match=LayerMatch(role="edge")),
                LayerSelectionRule(item_kinds=("drill",), purposes=("drill",)),
            ]
        ),
    )

    layers = build_eda_layers(inventory, settings, warn=lambda _message: None)

    roles = {(layer.role.function, layer.role.side) for layer in layers}
    assert ("copper", "front") in roles
    assert ("copper", "back") in roles
    assert ("silkscreen", "front") in roles
    assert ("edge", "") in roles
    assert any(primitive.kind == "drill" for layer in layers for primitive in layer.primitives)


def test_realistic_layers_use_board_material_copper_and_silkscreen() -> None:
    inventory = build_inventory(_board(), side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review"}')
    settings.side = "front"

    layers = build_realistic_layers(inventory, settings, warn=lambda _message: None)

    layer_ids = {layer.id for layer in layers}
    assert {
        "realistic:substrate",
        "realistic:solderMask",
        "realistic:coveredCopper",
        "realistic:exposedSubstrate",
        "realistic:exposedCopper",
        "realistic:silkscreen",
    }.issubset(layer_ids)
    assert "realistic:boardOutline" not in layer_ids
    layers_by_id = {layer.id: layer for layer in layers}
    assert layers_by_id["realistic:solderMask"].mask is not None
    assert layers_by_id["realistic:solderMask"].mask.openings
    assert layers_by_id["realistic:exposedCopper"].mask is not None
    assert layers_by_id["realistic:exposedCopper"].mask.board
    assert layers_by_id["realistic:silkscreen"].mask is not None
    assert layers_by_id["realistic:silkscreen"].mask.openings


def test_eda_layers_use_board_drill_and_solder_mask_cutouts() -> None:
    inventory = build_inventory(_board(), side="front")
    settings = RenderSettings(
        render_mode="eda",
        side="front",
        source=SourceSelection(
            layers=[
                LayerSelectionRule(match=LayerMatch(role="copper", side="front")),
                LayerSelectionRule(
                    match=LayerMatch(role="silkscreen", side="front"),
                    purposes=("silkscreen", "designator"),
                ),
            ]
        ),
    )

    layers = build_eda_layers(inventory, settings, warn=lambda _message: None)

    copper = next(layer for layer in layers if layer.role.function == "copper")
    silkscreen = next(layer for layer in layers if layer.role.function == "silkscreen")
    assert copper.mask is not None
    assert copper.mask.board
    assert copper.mask.drills
    assert silkscreen.mask is not None
    assert silkscreen.mask.board
    assert silkscreen.mask.drills
    assert silkscreen.mask.openings


def test_realistic_board_material_uses_profile_path_for_orangecrab() -> None:
    board = parse_kicad_pcb(FIXTURES / "orangecrab.kicad_pcb")
    inventory = build_inventory(board, side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review", "side": "front"}')

    layers = build_realistic_layers(inventory, settings, warn=lambda _message: None)
    substrate = next(layer for layer in layers if layer.id == "realistic:substrate")
    board_path = substrate.primitives[0].d

    assert board_path.startswith("M ")
    assert board_path.count("Z") >= 1


def test_highlights_match_typed_inventory_tags() -> None:
    inventory = build_inventory(_board(), side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review"}')
    settings.render_mode = "eda"
    settings.side = "front"
    settings.source = SourceSelection(layers=[LayerSelectionRule(match=LayerMatch(role="copper"))])
    settings.highlights = [HighlightSpec(net="VCC")]

    groups = build_highlight_layers(inventory, settings, warn=lambda _message: None)

    assert len(groups) == 1
    assert groups[0].target == "net:VCC"
    assert any(layer.primitives for layer in groups[0].layers)


def test_highlight_layers_use_board_and_drill_cutouts() -> None:
    inventory = build_inventory(_board(), side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review"}')
    settings.render_mode = "eda"
    settings.side = "front"
    settings.highlights = [HighlightSpec(net="VCC")]

    groups = build_highlight_layers(inventory, settings, warn=lambda _message: None)

    copper = next(
        layer
        for layer in groups[0].layers
        if layer.role.function == "copper" and layer.role.side == "front"
    )
    assert copper.mask is not None
    assert copper.mask.board
    assert copper.mask.drills


def test_highlights_do_not_select_drills_from_net_tags() -> None:
    inventory = build_inventory(_board(), side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review"}')
    settings.render_mode = "eda"
    settings.side = "front"
    settings.highlights = [HighlightSpec(net="VCC")]

    groups = build_highlight_layers(inventory, settings, warn=lambda _message: None)

    assert all(layer.role.function != "drill" for layer in groups[0].layers)
    assert all(
        not source_id.startswith("drill:")
        for layer in groups[0].layers
        for source_id in layer.source_ids
    )
