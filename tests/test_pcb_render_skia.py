from __future__ import annotations

from test_pcb_render import _board

from phosphor_eda.pcb_render_inventory import InventoryItemKind, build_inventory
from phosphor_eda.pcb_render_skia import inventory_item_to_skia_artwork, skia_path_to_svg_d


def test_path_adapter_converts_typed_inventory_items() -> None:
    inventory = build_inventory(_board(), side="front")

    for item in inventory.items:
        artwork = inventory_item_to_skia_artwork(item)
        if artwork is not None:
            assert skia_path_to_svg_d(artwork.path).startswith("M ")


def test_path_adapter_converts_conductor_items() -> None:
    inventory = build_inventory(_board(), side="front")
    conductor = next(
        item for item in inventory.items if item.item_kind == InventoryItemKind.CONDUCTOR
    )

    artwork = inventory_item_to_skia_artwork(conductor)

    assert artwork is not None
    assert "L " in skia_path_to_svg_d(artwork.path)
