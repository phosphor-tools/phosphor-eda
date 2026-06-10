from __future__ import annotations

from test_pcb_render import _board

from phosphor_eda.domain.pcb import PcbClosedPath, PcbKeepout
from phosphor_eda.render.primitives import (
    solder_mask_opening_primitives,
)
from phosphor_eda.render.inventory import (
    InventoryItemKind,
    InventoryPurpose,
    build_inventory,
    select_inventory_items,
)
from phosphor_eda.render.settings import LayerMatch, LayerSelectionRule


def test_source_selection_matches_typed_purposes_and_content_kinds() -> None:
    inventory = build_inventory(_board(), side="front")
    rules = [
        LayerSelectionRule(
            match=LayerMatch(role="silkscreen", side="front"),
            purposes=("silkscreen", "designator"),
            content_kinds=("line", "text"),
        )
    ]

    selected = select_inventory_items(inventory, rules, active_side="front")

    assert {item.item_kind for item in selected} == {InventoryItemKind.ARTWORK}
    assert {item.purpose for item in selected} == {
        InventoryPurpose.SILKSCREEN,
        InventoryPurpose.DESIGNATOR,
    }


def test_empty_source_selection_selects_nothing() -> None:
    inventory = build_inventory(_board(), side="front")

    selected = select_inventory_items(inventory, [], active_side="front")

    assert selected == ()


def test_keepout_overlays_require_explicit_keepout_selection() -> None:
    board = _board()
    copper = board.layer_for("F.Cu")
    assert copper is not None
    board.keepouts.append(
        PcbKeepout(
            id="keepout:1",
            boundary=PcbClosedPath.from_points([(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]),
            layers=(copper,),
        )
    )
    inventory = build_inventory(board, side="front")

    implicit_rules = [LayerSelectionRule(match=LayerMatch(name="F.Cu"), item_kinds=("conductor",))]
    explicit_rules = [LayerSelectionRule(item_kinds=("keepout",), purposes=("keepout",))]

    assert [
        item.id
        for item in select_inventory_items(inventory, implicit_rules, active_side="front")
        if item.item_kind == InventoryItemKind.KEEPOUT
    ] == []
    assert [
        item.source.id
        for item in select_inventory_items(inventory, explicit_rules, active_side="front")
        if item.item_kind == InventoryItemKind.KEEPOUT
    ] == ["keepout:1"]


def test_solder_mask_openings_use_typed_pad_mask_items() -> None:
    inventory = build_inventory(_board(), side="front")

    primitives = solder_mask_opening_primitives(inventory, side="front")

    assert primitives
    assert {primitive.data["purpose"] for primitive in primitives} == {"solder_mask"}
