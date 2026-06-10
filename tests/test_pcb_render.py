from __future__ import annotations

import json
from importlib.resources import as_file, files

import pytest

from phosphor_eda.pcb import (
    LayerRole,
    Pcb,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbConductor,
    PcbConductorKind,
    PcbDrill,
    PcbDrillPlating,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbNet,
    PcbObjectMetadata,
    PcbPad,
    PcbPadType,
    PcbText,
    PcbVia,
)
from phosphor_eda.pcb_render import load_render_settings_json, render_pcb_svg
from phosphor_eda.pcb_render_artwork import select_source_artwork
from phosphor_eda.pcb_render_inventory import (
    InventoryItemKind,
    InventoryPurpose,
    build_inventory,
)
from phosphor_eda.pcb_render_settings import load_render_settings_file


def test_render_svg_uses_typed_inventory_metadata() -> None:
    svg = render_pcb_svg(
        _board(),
        side="front",
        render_settings=load_render_settings_json('{"extends": "phosphor:design"}'),
    )

    assert 'data-kind="pad"' in svg
    assert 'data-kind="via"' in svg
    assert 'data-kind="drill"' in svg
    assert 'data-kind="conductor"' in svg
    assert 'data-kind="artwork"' in svg
    assert 'data-purpose="copper"' in svg
    assert 'data-content-kind="trace"' in svg
    assert 'data-source-collection="pads"' in svg
    assert "display_role" not in svg


def test_inventory_builder_emits_typed_items() -> None:
    inventory = build_inventory(_board(), side="front")

    assert any(
        item.item_kind == InventoryItemKind.BOARD_PROFILE
        and item.purpose == InventoryPurpose.BOARD_MATERIAL
        for item in inventory.items
    )
    assert any(
        item.item_kind == InventoryItemKind.PAD and item.purpose == InventoryPurpose.COPPER
        for item in inventory.items
    )
    assert any(
        item.item_kind == InventoryItemKind.PAD and item.purpose == InventoryPurpose.SOLDER_MASK
        for item in inventory.items
    )
    assert any(item.item_kind == InventoryItemKind.VIA for item in inventory.items)
    assert any(item.item_kind == InventoryItemKind.DRILL for item in inventory.items)
    assert any(
        item.item_kind == InventoryItemKind.CONDUCTOR
        and item.content_kind == PcbConductorKind.TRACE
        for item in inventory.items
    )
    assert any(
        item.item_kind == InventoryItemKind.ARTWORK
        and item.purpose == InventoryPurpose.DESIGNATOR
        and item.content_kind == PcbArtworkKind.TEXT
        for item in inventory.items
    )


def test_inventory_builder_omits_hidden_domain_sources() -> None:
    board = _board()
    board.pads[0].metadata.hidden = True
    board.vias[0].metadata.hidden = True
    board.drills.append(
        PcbDrill(
            "drill:mounting:hidden",
            2.0,
            2.0,
            0.8,
            metadata=PcbObjectMetadata(hidden=True),
        )
    )
    board.conductors[0].metadata.hidden = True
    board.artwork[0].metadata.hidden = True
    assert board.board_profile is not None
    hidden_profile = board.board_profile.elements[0]
    hidden_profile.metadata.hidden = True

    inventory = build_inventory(board, side="front")
    inventory_ids = {item.id for item in inventory.items}

    assert "pad:U1:1:F.Cu:copper" not in inventory_ids
    assert "pad:U1:1:F.Mask:solder_mask" not in inventory_ids
    assert "via:1:F.Cu:copper" not in inventory_ids
    assert "drill:pad:U1:1" not in inventory_ids
    assert "drill:via:1" not in inventory_ids
    assert "drill:mounting:hidden" not in inventory_ids
    assert "trace:1" not in inventory_ids
    assert "silk:1" not in inventory_ids
    assert hidden_profile.id not in inventory_ids
    assert "board:material" in inventory_ids


def test_render_settings_accept_typed_source_filters() -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "source": {
                    "layers": [
                        {
                            "match": {"role": "copper"},
                            "itemKinds": ["pad", "via", "conductor"],
                            "purposes": ["copper"],
                        },
                        {
                            "match": {"role": "silkscreen"},
                            "purposes": ["silkscreen", "designator", "value", "user_text"],
                            "contentKinds": ["line", "text"],
                        },
                    ]
                }
            }
        )
    )

    assert settings.source.layers[0].item_kinds == ("pad", "via", "conductor")
    assert settings.source.layers[0].purposes == ("copper",)
    assert settings.source.layers[1].content_kinds == ("line", "text")


def test_render_settings_reject_old_objects_filter() -> None:
    with pytest.raises(ValueError, match="objects is no longer supported"):
        load_render_settings_json(
            json.dumps({"source": {"layers": [{"match": {"role": "copper"}, "objects": ["pad"]}]}})
        )


@pytest.mark.parametrize("name", ["design", "review", "clean", "high-contrast"])
def test_builtin_render_settings_use_typed_source_filters(name: str) -> None:
    settings_file = files("phosphor_eda.render_settings").joinpath(f"{name}.json")
    with as_file(settings_file) as path:
        settings = load_render_settings_file(path)

    assert all(
        rule.item_kinds or rule.purposes or rule.content_kinds or rule.match
        for rule in settings.source.layers
    )


@pytest.mark.parametrize(
    "name",
    [
        "clean",
        "design",
        "high-contrast",
        "print",
        "print-callout",
        "review",
        "review-callout",
        "simplified-high-contrast",
    ],
)
def test_builtin_render_settings_hide_non_silkscreen_footprint_text(name: str) -> None:
    board = _board()
    copper_layer = next(layer for layer in board.layers if layer.name == "F.Cu")
    edge_layer = next(layer for layer in board.layers if layer.has_role(LayerRole.EDGE))
    fab_layer = PcbLayer(
        "F.Fab",
        (LayerRole.FABRICATION, LayerRole.FRONT),
        number=45,
    )
    mechanical_layer = PcbLayer(
        "Mechanical 1",
        (LayerRole.MECHANICAL, LayerRole.FRONT),
        number=46,
    )
    footprint = board.footprints[0]
    board.layers.extend([fab_layer, mechanical_layer])
    board.artwork.extend(
        [
            PcbArtwork(
                id="text:U1:copper-user",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.USER_TEXT,
                layer=copper_layer,
                data=PcbText("DEBUG", 3.0, 8.0, 0.0, 1.0),
                footprint=footprint,
            ),
            PcbArtwork(
                id="text:board:edge-user",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.USER_TEXT,
                layer=edge_layer,
                data=PcbText("OUTLINE NOTE", 3.0, 9.0, 0.0, 1.0),
                footprint=None,
            ),
            PcbArtwork(
                id="text:U1:fab-ref",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.DESIGNATOR,
                layer=fab_layer,
                data=PcbText("U1", 5.0, 3.0, 0.0, 1.0),
                footprint=footprint,
            ),
            PcbArtwork(
                id="text:U1:mech-user",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.USER_TEXT,
                layer=mechanical_layer,
                data=PcbText("PN123", 5.0, 8.0, 0.0, 1.0),
                footprint=footprint,
            ),
        ]
    )

    settings_file = files("phosphor_eda.render_settings").joinpath(f"{name}.json")
    with as_file(settings_file) as path:
        settings = load_render_settings_file(path)

    selected = select_source_artwork(
        build_inventory(board, side="front"),
        settings.source.layers,
        active_side="front",
    )
    selected_ids = {item.id for item in selected}

    assert "text:U1:copper-user" not in selected_ids
    assert "text:board:edge-user" not in selected_ids
    assert "text:U1:fab-ref" not in selected_ids
    assert "text:U1:mech-user" not in selected_ids


@pytest.mark.parametrize(
    "name",
    ["high-contrast", "print", "print-callout", "simplified-high-contrast"],
)
def test_high_contrast_presets_hide_mechanical_artwork_by_default(name: str) -> None:
    board = _board()
    mechanical_layer = PcbLayer(
        "Top 3D Body",
        (LayerRole.MECHANICAL, LayerRole.THREE_D_BODY, LayerRole.FRONT),
        number=69,
    )
    footprint = board.footprints[0]
    board.layers.append(mechanical_layer)
    board.artwork.append(
        PcbArtwork(
            id="line:U1:body",
            kind=PcbArtworkKind.LINE,
            purpose=PcbArtworkPurpose.COMPONENT_BODY,
            layer=mechanical_layer,
            data=PcbLine(1.0, 1.0, 4.0, 1.0, 0.1),
            footprint=footprint,
        )
    )

    settings_file = files("phosphor_eda.render_settings").joinpath(f"{name}.json")
    with as_file(settings_file) as path:
        settings = load_render_settings_file(path)

    selected = select_source_artwork(
        build_inventory(board, side="front"),
        settings.source.layers,
        active_side="front",
    )

    assert "line:U1:body" not in {item.id for item in selected}


def _board() -> Pcb:
    front_cu = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT, LayerRole.OUTER), number=0)
    back_cu = PcbLayer("B.Cu", (LayerRole.COPPER, LayerRole.BACK, LayerRole.OUTER), number=31)
    front_mask = PcbLayer("F.Mask", (LayerRole.SOLDER_MASK, LayerRole.FRONT), number=37)
    front_silk = PcbLayer("F.SilkS", (LayerRole.SILKSCREEN, LayerRole.FRONT), number=33)
    edge = PcbLayer("Edge.Cuts", (LayerRole.EDGE,), number=44)
    net = PcbNet(1, "VCC")
    footprint = PcbFootprint("U1", "Package", 5.0, 5.0, 0.0, front_cu, value="MCU")
    pad_drill = PcbDrill(
        "drill:pad:U1:1",
        5.0,
        5.0,
        0.4,
        plating=PcbDrillPlating.PLATED,
        layers=(front_cu, back_cu),
    )
    via_drill = PcbDrill(
        "drill:via:1",
        8.0,
        5.0,
        0.35,
        plating=PcbDrillPlating.PLATED,
        layers=(front_cu, back_cu),
    )
    pad = PcbPad(
        id="pad:U1:1",
        number="1",
        x=5.0,
        y=5.0,
        width=1.4,
        height=1.4,
        shape="circle",
        pad_type=PcbPadType.THROUGH_HOLE,
        layers=(front_cu, front_mask),
        net=net,
        footprint=footprint,
        drill=pad_drill,
        mask_expansion=0.05,
    )
    via = PcbVia(
        id="via:1",
        x=8.0,
        y=5.0,
        diameter=0.8,
        layers=(front_cu, back_cu),
        drill=via_drill,
        net=net,
    )
    return Pcb(
        name="render-test",
        layers=[front_cu, back_cu, front_mask, front_silk, edge],
        nets={1: net},
        footprints=[footprint],
        pads=[pad],
        vias=[via],
        drills=[pad_drill, via_drill],
        conductors=[
            PcbConductor(
                id="trace:1",
                kind=PcbConductorKind.TRACE,
                layer=front_cu,
                data=PcbLine(5.0, 5.0, 8.0, 5.0, 0.25),
                net=net,
            )
        ],
        artwork=[
            PcbArtwork(
                id="silk:1",
                kind=PcbArtworkKind.LINE,
                purpose=PcbArtworkPurpose.SILKSCREEN,
                layer=front_silk,
                data=PcbLine(4.0, 7.0, 6.0, 7.0, 0.12),
                footprint=footprint,
            ),
            PcbArtwork(
                id="text:U1:ref",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.DESIGNATOR,
                layer=front_silk,
                data=PcbText("U1", 5.0, 3.5, 0.0, 1.0),
                footprint=footprint,
            ),
        ],
        pours=[],
        keepouts=[],
        board_profile=PcbBoardProfile(
            elements=tuple(
                PcbBoardProfileElement(
                    id=f"edge:{index}",
                    kind=PcbArtworkKind.LINE,
                    layer=edge,
                    data=PcbLine(x1, y1, x2, y2, 0.1),
                )
                for index, ((x1, y1), (x2, y2)) in enumerate(
                    zip(
                        [(0.0, 0.0), (12.0, 0.0), (12.0, 10.0), (0.0, 10.0)],
                        [(12.0, 0.0), (12.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
                        strict=False,
                    )
                )
            )
        ),
    )
