"""Render-mode projections from typed PCB inventory to derived visual layers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import LayerRole
from phosphor_eda.render.inventory import (
    InventoryItem,
    InventoryItemKind,
    InventoryPurpose,
    PcbRenderInventory,
    select_inventory_items,
)
from phosphor_eda.render.primitives import (
    LayerClip,
    LayerMask,
    SvgPrimitive,
    drill_to_svg_primitive,
    inventory_item_to_svg_primitive,
    layer_function_for_item,
    solder_mask_opening_primitives,
    source_layer_name,
)
from phosphor_eda.render.tokens import VisualRole, resolve_layer_style

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from phosphor_eda.render.profiler import RenderProfiler
    from phosphor_eda.render.settings import HighlightSpec, RenderSettings
    from phosphor_eda.render.tokens import ResolvedStyle


def _empty_data() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class DerivedLayer:
    id: str
    role: VisualRole
    primitives: tuple[SvgPrimitive, ...]
    source_layers: tuple[str, ...]
    source_ids: tuple[str, ...]
    style: ResolvedStyle | None = None
    data: Mapping[str, str] = field(default_factory=_empty_data)
    clip: LayerClip | None = None
    mask: LayerMask | None = None


@dataclass(frozen=True)
class _LayerGroupKey:
    function: str
    side: str
    inner_index: int | None
    source_layer_name: str


@dataclass(frozen=True)
class HighlightGroup:
    target: str
    layers: tuple[DerivedLayer, ...]


_EDA_FUNCTION_ORDER = {
    "substrate": 0,
    "copper": 10,
    "solder_mask": 20,
    "solder_paste": 30,
    "silkscreen": 40,
    "designator": 41,
    "value": 42,
    "user_text": 43,
    "fabrication": 50,
    "assembly": 51,
    "courtyard": 52,
    "mechanical": 60,
    "keepout": 70,
    "edge": 80,
    "drill": 90,
}
_SIDE_ORDER = {"back": 0, "inner": 1, "": 2, "front": 3}


def build_eda_layers(
    inventory: PcbRenderInventory,
    settings: RenderSettings,
    *,
    profiler: RenderProfiler | None = None,
) -> tuple[DerivedLayer, ...]:
    """Build EDA derived layers from typed source inventory."""
    selected = _filter_excluded_components(
        select_inventory_items(inventory, settings.source.layers, active_side=settings.side),
        settings.source.exclude_components,
    )
    if profiler is not None:
        profiler.metric("eda.selected_items", count=len(selected))
    board_mask = _board_layer_mask(inventory)
    silkscreen_masks = _silkscreen_layer_masks(inventory, settings.side)
    return _group_inventory_layers(
        inventory,
        selected,
        settings,
        namespace="eda",
        board_mask=board_mask,
        silkscreen_masks=silkscreen_masks,
        profiler=profiler,
    )


def build_realistic_layers(
    inventory: PcbRenderInventory,
    settings: RenderSettings,
    *,
    profiler: RenderProfiler | None = None,
) -> tuple[DerivedLayer, ...]:
    """Build front/back realistic visual layers."""
    side = settings.side
    assert side, "render settings must have a resolved side"
    selected = _filter_excluded_components(
        select_inventory_items(inventory, settings.source.layers, active_side=side),
        settings.source.exclude_components,
    )
    board_primitives = _board_primitives(inventory)
    drill_primitives = _drill_primitives(inventory)
    mask_openings = solder_mask_opening_primitives(inventory, side=side)
    board_clip = LayerClip(board=board_primitives) if board_primitives else None
    copper_items = tuple(
        item
        for item in selected
        if item.purpose == InventoryPurpose.COPPER
        and item.layer is not None
        and item.layer.side in {"", side}
    )
    silkscreen_items = tuple(
        item
        for item in selected
        if _is_silkscreen_projection_item(item)
        and (item.layer is None or item.layer.side in {"", side})
    )
    board_items = tuple(
        item for item in inventory.items if item.purpose == InventoryPurpose.BOARD_MATERIAL
    )
    board_mask = LayerMask(board=board_primitives, drills=drill_primitives)
    solder_mask = LayerMask(
        board=board_primitives,
        drills=drill_primitives,
        openings=mask_openings,
    )
    opening_mask = LayerMask(board=mask_openings, drills=drill_primitives)
    layers: list[DerivedLayer] = []
    layers.extend(
        _realistic_layer(
            settings,
            function="substrate",
            primitives=board_primitives,
            source_items=board_items,
            clip=board_clip,
            mask=board_mask,
        )
    )
    layers.extend(
        _realistic_layer(
            settings,
            function="solder_mask",
            primitives=board_primitives,
            source_items=board_items,
            clip=board_clip,
            mask=solder_mask,
        )
    )
    copper_primitives = _primitives_for_items(copper_items)
    layers.extend(
        _realistic_layer(
            settings,
            function="covered_copper",
            primitives=copper_primitives,
            source_items=copper_items,
            clip=board_clip,
            mask=solder_mask,
        )
    )
    layers.extend(
        _realistic_layer(
            settings,
            function="exposed_substrate",
            primitives=board_primitives,
            source_items=board_items,
            clip=board_clip,
            mask=opening_mask,
        )
    )
    layers.extend(
        _realistic_layer(
            settings,
            function="exposed_copper",
            primitives=copper_primitives,
            source_items=copper_items,
            clip=board_clip,
            mask=opening_mask,
        )
    )
    silkscreen_primitives = _primitives_for_items(silkscreen_items)
    layers.extend(
        _realistic_layer(
            settings,
            function="silkscreen",
            primitives=silkscreen_primitives,
            source_items=silkscreen_items,
            clip=board_clip,
            mask=solder_mask,
        )
    )
    if profiler is not None:
        profiler.metric("realistic.layers", count=len(layers))
    return tuple(layers)


def build_highlight_layers(
    inventory: PcbRenderInventory,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> tuple[HighlightGroup, ...]:
    """Build highlight overlays for net/component/pad targets."""
    groups: list[HighlightGroup] = []
    board_mask = _board_layer_mask(inventory)
    silkscreen_masks = _silkscreen_layer_masks(inventory, settings.side)
    for highlight in settings.highlights:
        selected = tuple(item for item in inventory.items if _matches_highlight(item, highlight))
        if not selected:
            warn(f"Highlight target not found: {_highlight_target(highlight)}")
            continue
        layers = _group_inventory_layers(
            inventory,
            selected,
            settings,
            namespace="highlight",
            highlight_color=highlight.color,
            board_mask=board_mask,
            silkscreen_masks=silkscreen_masks,
            profiler=profiler,
        )
        groups.append(HighlightGroup(target=_highlight_target(highlight), layers=layers))
    return tuple(groups)


def _group_inventory_layers(
    inventory: PcbRenderInventory,
    selected: Iterable[InventoryItem],
    settings: RenderSettings,
    *,
    namespace: str,
    highlight_color: str = "",
    board_mask: LayerMask | None = None,
    silkscreen_masks: dict[str, LayerMask] | None = None,
    profiler: RenderProfiler | None = None,
) -> tuple[DerivedLayer, ...]:
    groups: dict[_LayerGroupKey, list[InventoryItem]] = defaultdict(list)
    for item in selected:
        key = _group_key(item)
        groups[key].append(item)

    layers: list[DerivedLayer] = []
    for key, items in sorted(groups.items(), key=lambda entry: _group_sort_key(entry[0])):
        primitives = _primitives_for_items(tuple(items))
        if not primitives:
            continue
        role = VisualRole(
            namespace=namespace,
            function=key.function,
            side=key.side,
            inner_index=key.inner_index,
            source_layer_name=key.source_layer_name,
        )
        style = resolve_layer_style(
            settings.tokens,
            role,
            highlight_color=highlight_color,
            eda_layer_order=_copper_order(inventory, key.source_layer_name),
        )
        layers.append(
            DerivedLayer(
                id=_derived_layer_id(role),
                role=role,
                primitives=primitives,
                source_layers=_unique_ordered(source_layer_name(item) for item in items),
                source_ids=_unique_ordered(item.id for item in items),
                style=style,
                mask=_mask_for_group(
                    key,
                    board_mask=board_mask,
                    silkscreen_masks=silkscreen_masks or {},
                ),
                data={"source-layer": key.source_layer_name} if key.source_layer_name else {},
            )
        )
    if profiler is not None:
        profiler.metric(f"{namespace}.layers", count=len(layers))
    return tuple(layers)


def _realistic_layer(
    settings: RenderSettings,
    *,
    function: str,
    primitives: tuple[SvgPrimitive, ...],
    source_items: tuple[InventoryItem, ...],
    clip: LayerClip | None,
    mask: LayerMask | None,
) -> tuple[DerivedLayer, ...]:
    if not primitives:
        return ()
    role = VisualRole(namespace="realistic", function=function)
    style = resolve_layer_style(settings.tokens, role)
    return (
        DerivedLayer(
            id=f"realistic:{function}",
            role=role,
            primitives=primitives,
            source_layers=_unique_ordered(source_layer_name(item) for item in source_items),
            source_ids=_unique_ordered(item.id for item in source_items),
            style=style,
            clip=clip,
            mask=mask,
        ),
    )


def _primitives_for_items(items: tuple[InventoryItem, ...]) -> tuple[SvgPrimitive, ...]:
    primitives: list[SvgPrimitive] = []
    for item in items:
        primitive = (
            drill_to_svg_primitive(item)
            if item.item_kind == InventoryItemKind.DRILL
            else inventory_item_to_svg_primitive(item)
        )
        if primitive is not None:
            primitives.append(primitive)
    return tuple(primitives)


def _board_primitives(inventory: PcbRenderInventory) -> tuple[SvgPrimitive, ...]:
    return tuple(
        primitive
        for item in inventory.items
        if item.purpose == InventoryPurpose.BOARD_MATERIAL
        if (primitive := inventory_item_to_svg_primitive(item)) is not None
    )


def _is_silkscreen_projection_item(item: InventoryItem) -> bool:
    if item.purpose == InventoryPurpose.SILKSCREEN:
        return True
    if item.purpose not in {
        InventoryPurpose.DESIGNATOR,
        InventoryPurpose.VALUE,
        InventoryPurpose.USER_TEXT,
    }:
        return False
    return item.layer is not None and item.layer.has_role(LayerRole.SILKSCREEN)


def _drill_primitives(inventory: PcbRenderInventory) -> tuple[SvgPrimitive, ...]:
    return _primitives_for_items(
        tuple(item for item in inventory.items if item.item_kind == InventoryItemKind.DRILL)
    )


def _board_layer_mask(inventory: PcbRenderInventory) -> LayerMask | None:
    board = _board_primitives(inventory)
    if not board:
        return None
    return LayerMask(board=board, drills=_drill_primitives(inventory))


def _silkscreen_layer_masks(
    inventory: PcbRenderInventory,
    active_side: str,
) -> dict[str, LayerMask]:
    board = _board_primitives(inventory)
    if not board:
        return {}
    drills = _drill_primitives(inventory)
    sides = {layer.side for layer in (item.layer for item in inventory.items) if layer is not None}
    sides.update(side for side in ("front", "back", active_side) if side)
    return {
        side: LayerMask(
            board=board,
            drills=drills,
            openings=solder_mask_opening_primitives(inventory, side=side),
        )
        for side in sides
        if side
    }


def _mask_for_group(
    key: _LayerGroupKey,
    *,
    board_mask: LayerMask | None,
    silkscreen_masks: dict[str, LayerMask],
) -> LayerMask | None:
    if board_mask is None:
        return None
    if key.function in {"edge", "drill"}:
        return None
    if key.function in {"silkscreen", "designator", "value", "user_text"}:
        return silkscreen_masks.get(key.side) or board_mask
    return board_mask


def _group_key(item: InventoryItem) -> _LayerGroupKey:
    layer = item.layer
    side = "" if layer is None else layer.side
    inner_index = layer.stack_index if layer is not None and layer.side == "inner" else None
    source_layer = "" if layer is None else layer.name
    return _LayerGroupKey(
        function=layer_function_for_item(item),
        side=side,
        inner_index=inner_index,
        source_layer_name=source_layer,
    )


def _group_sort_key(key: _LayerGroupKey) -> tuple[int, int, str]:
    return (
        _EDA_FUNCTION_ORDER.get(key.function, 500),
        _SIDE_ORDER.get(key.side, 2),
        key.source_layer_name,
    )


def _derived_layer_id(role: VisualRole) -> str:
    parts = [role.namespace, role.function]
    if role.side:
        parts.append(role.side)
    if role.inner_index is not None:
        parts.append(str(role.inner_index))
    if role.source_layer_name:
        parts.append(role.source_layer_name.replace(" ", "_"))
    return ":".join(parts)


def _copper_order(inventory: PcbRenderInventory, source_layer_name: str) -> int | None:
    copper_layers: list[str] = []
    for item in inventory.items:
        if item.layer is None or item.purpose != InventoryPurpose.COPPER:
            continue
        if item.layer.name not in copper_layers:
            copper_layers.append(item.layer.name)
    try:
        return copper_layers.index(source_layer_name)
    except ValueError:
        return None


def _filter_excluded_components(
    items: tuple[InventoryItem, ...],
    excluded_prefixes: tuple[str, ...],
) -> tuple[InventoryItem, ...]:
    if not excluded_prefixes:
        return items
    # Exact prefix match: excluding "R" must not also hide "RV".
    normalized = {prefix.upper() for prefix in excluded_prefixes}
    return tuple(item for item in items if item.tags.component_prefix.upper() not in normalized)


def _matches_highlight(item: InventoryItem, highlight: HighlightSpec) -> bool:
    if item.item_kind == InventoryItemKind.DRILL:
        return False
    if highlight.net:
        return item.tags.net_name.upper() == highlight.net.upper()
    if highlight.component:
        return item.tags.component_ref.upper() == highlight.component.upper()
    if highlight.pad:
        ref, _, pad_number = highlight.pad.partition(".")
        return (
            item.tags.component_ref.upper() == ref.upper()
            and item.tags.pad_number.upper() == pad_number.upper()
        )
    return False


def _highlight_target(highlight: HighlightSpec) -> str:
    if highlight.net:
        return f"net:{highlight.net}"
    if highlight.component:
        return f"component:{highlight.component}"
    return f"pad:{highlight.pad}"


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(value for value in dict.fromkeys(value for value in values if value))
