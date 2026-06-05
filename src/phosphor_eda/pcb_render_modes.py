"""Render-mode projections from PCB source artwork to derived visual layers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.pcb_render_artwork import (
    DerivedLayer,
    select_source_artwork,
    selected_copper_layers,
    solder_mask_opening_primitives,
    via_layers,
)
from phosphor_eda.pcb_render_geometry import GeometryKind, GeometryLayer
from phosphor_eda.pcb_render_primitives import (
    LayerClip,
    LayerMask,
    SvgPrimitive,
    drill_to_svg_primitive,
    geometry_to_svg_primitive,
    visible_drill_to_svg_primitive,
)
from phosphor_eda.pcb_render_tokens import VisualRole, resolve_layer_style

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from phosphor_eda.pcb_render_geometry import PcbGeometryStore, RenderableGeometry
    from phosphor_eda.pcb_render_profile import RenderProfiler
    from phosphor_eda.pcb_render_settings import HighlightSpec, LayerSelectionRule, RenderSettings


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


@dataclass(frozen=True)
class _PrimitiveLayerItem:
    source: RenderableGeometry
    layer: GeometryLayer


_REALISTIC_LAYER_ORDER = (
    "substrate",
    "solderMask",
    "coveredCopper",
    "exposedSubstrate",
    "exposedCopper",
    "silkscreen",
    "boardOutline",
)


def build_eda_layers(
    store: PcbGeometryStore,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> tuple[DerivedLayer, ...]:
    """Build EDA derived layers from selected PCB source artwork."""
    selected_items = _filter_excluded_components(
        select_source_artwork(store, settings.source.layers, active_side=settings.side),
        settings.source.exclude_components,
    )
    if profiler is not None:
        profiler.metric("eda.selected_items", count=len(selected_items))
    layer_items = _primitive_layer_items(
        store,
        selected_items,
        settings.source.layers,
        active_side=settings.side,
        profiler=profiler,
    )
    if profiler is not None:
        profiler.metric("eda.layer_items", count=len(layer_items))
    layer_mask = _layer_mask(store)
    silkscreen_masks = _silkscreen_masks_by_side(store)
    dimmed = _should_dim_base_layers(store, settings)
    warned_missing_dimmed_tokens: set[str] = set()
    copper_order_by_layer = _copper_order_by_layer(store)

    groups: dict[_LayerGroupKey, list[_PrimitiveLayerItem]] = defaultdict(list)
    for item in layer_items:
        groups[_group_key(item.layer)].append(item)

    layers: list[DerivedLayer] = []
    for key, group in sorted(
        groups.items(),
        key=lambda item: _eda_group_sort_key(item, side=settings.side),
    ):
        primitives = _primitive_layer_primitives(
            key,
            group,
            profiler=profiler,
        )
        if not primitives:
            continue
        role = VisualRole(
            namespace="eda",
            function=key.function,
            side=key.side,
            inner_index=key.inner_index,
            source_layer_name=key.source_layer_name,
        )
        style = resolve_layer_style(
            settings.tokens,
            role,
            dimmed=dimmed,
            warn=warn,
            warned_missing_dimmed_tokens=warned_missing_dimmed_tokens,
            eda_layer_order=copper_order_by_layer.get(key.source_layer_name),
        )
        source_layers = _unique_ordered(primitive.source_layer for primitive in primitives)
        source_ids = _source_ids_by_store_order_for_primitives(store, primitives)
        layers.append(
            DerivedLayer(
                id=_derived_layer_id(role),
                role=role,
                primitives=primitives,
                source_layers=source_layers,
                source_ids=source_ids,
                style=style,
                data={"source-layer": key.source_layer_name},
                mask=_mask_for_group(key, layer_mask, silkscreen_masks),
            )
        )

    return tuple(layers)


def build_realistic_layers(
    store: PcbGeometryStore,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> tuple[DerivedLayer, ...]:
    """Build front/back realistic derived layers from selected source artwork."""
    side = settings.side or "front"
    selected_items = _filter_excluded_components(
        select_source_artwork(store, settings.source.layers, active_side=side),
        settings.source.exclude_components,
    )
    if profiler is not None:
        profiler.metric("realistic.selected_items", count=len(selected_items))
    layer_items = _primitive_layer_items(
        store,
        selected_items,
        settings.source.layers,
        active_side=side,
        profiler=profiler,
    )
    if profiler is not None:
        profiler.metric("realistic.layer_items", count=len(layer_items))
    board_primitives = _board_mask_primitives(store)
    board_clip = LayerClip(board=board_primitives) if board_primitives else None
    drill_primitives = _drill_mask_primitives(store)
    dimmed = _should_dim_base_layers(store, settings)
    warned_missing_dimmed_tokens: set[str] = set()

    mask_openings = solder_mask_opening_primitives(store, side=side)
    copper_primitives = _realistic_side_primitives(
        layer_items,
        role="copper",
        side=side,
        profiler=profiler,
    )
    silkscreen_primitives = _realistic_side_primitives(
        (
            _PrimitiveLayerItem(source=item, layer=item.layer)
            for item in selected_items
            if item.layer.role == "silkscreen" and item.layer.side == side
        ),
        role="silkscreen",
        side=side,
        profiler=profiler,
    )

    if profiler is not None:
        profiler.metric("realistic.mask_opening_primitives", count=len(mask_openings))
        profiler.metric("realistic.copper_primitives", count=len(copper_primitives))
        profiler.metric("realistic.silkscreen_primitives", count=len(silkscreen_primitives))

    board_outline_primitives = _selected_board_outline_primitives(selected_items)
    layer_inputs = {
        "substrate": _RealisticLayerInput(
            primitives=board_primitives,
            source_layers=_board_source_layers(store),
            source_ids=_board_source_ids(store),
            clip=board_clip,
            mask=LayerMask(board=board_primitives, drills=drill_primitives),
        ),
        "solderMask": _RealisticLayerInput(
            primitives=board_primitives,
            source_layers=_source_layers_for_primitives(mask_openings, board_primitives),
            source_ids=_source_ids_for_primitives(mask_openings, board_primitives),
            clip=board_clip,
            mask=LayerMask(
                board=board_primitives,
                drills=drill_primitives,
                openings=mask_openings,
            ),
        ),
        "coveredCopper": _RealisticLayerInput(
            primitives=copper_primitives,
            source_layers=_source_layers_for_primitives(copper_primitives),
            source_ids=_source_ids_by_store_order_for_primitives(store, copper_primitives),
            clip=board_clip,
            mask=LayerMask(board=board_primitives, drills=drill_primitives),
        ),
        "exposedSubstrate": _RealisticLayerInput(
            primitives=board_primitives if mask_openings else (),
            source_layers=_source_layers_for_primitives(mask_openings, board_primitives),
            source_ids=_source_ids_for_primitives(mask_openings, board_primitives),
            clip=board_clip,
            mask=LayerMask(board=mask_openings, drills=drill_primitives),
        ),
        "exposedCopper": _RealisticLayerInput(
            primitives=copper_primitives if mask_openings else (),
            source_layers=_source_layers_for_primitives(copper_primitives),
            source_ids=_source_ids_by_store_order_for_primitives(store, copper_primitives),
            clip=board_clip,
            mask=LayerMask(board=mask_openings, drills=drill_primitives),
        ),
        "silkscreen": _RealisticLayerInput(
            primitives=silkscreen_primitives,
            source_layers=_source_layers_for_primitives(silkscreen_primitives),
            source_ids=_source_ids_by_store_order_for_primitives(store, silkscreen_primitives),
            clip=board_clip,
            mask=LayerMask(
                board=board_primitives,
                drills=drill_primitives,
                openings=mask_openings,
            ),
        ),
        "boardOutline": _RealisticLayerInput(
            primitives=board_outline_primitives,
            source_layers=_board_source_layers(store),
            source_ids=_board_source_ids(store),
            clip=None,
            mask=None,
        ),
    }

    layers: list[DerivedLayer] = []
    for function in _REALISTIC_LAYER_ORDER:
        layer_input = layer_inputs[function]
        if not layer_input.primitives:
            continue

        role = VisualRole(namespace="realistic", function=function)
        style = resolve_layer_style(
            settings.tokens,
            role,
            dimmed=dimmed,
            warn=warn,
            warned_missing_dimmed_tokens=warned_missing_dimmed_tokens,
        )
        layers.append(
            DerivedLayer(
                id=_derived_layer_id(role),
                role=role,
                primitives=layer_input.primitives,
                source_layers=layer_input.source_layers,
                source_ids=layer_input.source_ids,
                style=style,
                clip=layer_input.clip,
                mask=layer_input.mask,
            )
        )
    return tuple(layers)


def build_highlight_layers(
    store: PcbGeometryStore,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> tuple[HighlightGroup, ...]:
    """Build derived highlight overlay groups from selected raw source geometry."""
    layer_mask = _layer_mask(store)
    silkscreen_masks = _silkscreen_masks_by_side(store)
    groups: list[HighlightGroup] = []

    for highlight in settings.highlights:
        selected_items = _selected_highlight_items(store, settings, highlight)
        selected_vias = _selected_highlight_vias(store, settings, highlight)
        layer_items = _primitive_layer_items(
            store,
            selected_items,
            settings.source.layers,
            active_side=settings.side,
            via_items=selected_vias,
            profiler=profiler,
        )
        if profiler is not None:
            profiler.metric(
                "highlight.selected_items",
                target=_highlight_target(highlight),
                items=len(selected_items),
                vias=len(selected_vias),
                grouped=len(layer_items),
            )
        if not layer_items:
            continue

        by_layer: dict[_LayerGroupKey, list[_PrimitiveLayerItem]] = defaultdict(list)
        for item in layer_items:
            by_layer[_group_key(item.layer)].append(item)

        target = _highlight_target(highlight)
        layers: list[DerivedLayer] = []
        for key, layer_group in sorted(by_layer.items(), key=_group_sort_key):
            primitives = _primitive_layer_primitives(
                key,
                layer_group,
                profiler=profiler,
            )
            if not primitives:
                continue
            role = VisualRole(
                namespace="highlight",
                function=key.function,
                side=key.side,
                inner_index=key.inner_index,
                source_layer_name=key.source_layer_name,
            )
            style = resolve_layer_style(
                settings.tokens,
                role,
                dimmed=False,
                warn=warn,
                highlight_color=highlight.color,
            )
            layers.append(
                DerivedLayer(
                    id=_derived_layer_id(role),
                    role=role,
                    primitives=primitives,
                    source_layers=_unique_ordered(
                        primitive.source_layer for primitive in primitives
                    ),
                    source_ids=_source_ids_by_store_order_for_primitives(
                        store,
                        primitives,
                    ),
                    style=style,
                    data={
                        "data-highlight-target": target,
                        "source-layer": key.source_layer_name,
                    },
                    mask=_mask_for_group(key, layer_mask, silkscreen_masks),
                )
            )

        if layers:
            groups.append(HighlightGroup(target=target, layers=tuple(layers)))

    return tuple(groups)


@dataclass(frozen=True)
class _RealisticLayerInput:
    primitives: tuple[SvgPrimitive, ...]
    source_layers: tuple[str, ...]
    source_ids: tuple[str, ...]
    clip: LayerClip | None
    mask: LayerMask | None


def _primitive_layer_items(
    store: PcbGeometryStore,
    selected_items: Iterable[RenderableGeometry],
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str,
    via_items: Iterable[RenderableGeometry] | None = None,
    profiler: RenderProfiler | None = None,
) -> tuple[_PrimitiveLayerItem, ...]:
    selected_non_vias = tuple(item for item in selected_items if item.kind is not GeometryKind.VIA)
    selected_copper_layer_items = selected_copper_layers(store, rules, active_side=active_side)
    selected_via_items = store.by_kind(GeometryKind.VIA) if via_items is None else tuple(via_items)
    items: list[_PrimitiveLayerItem] = [
        _PrimitiveLayerItem(source=item, layer=item.layer) for item in selected_non_vias
    ]

    if profiler is not None:
        profiler.metric(
            "primitive.selected_source_items",
            nonVias=len(selected_non_vias),
            vias=len(selected_via_items),
            copperLayers=len(selected_copper_layer_items),
        )

    for item in selected_via_items:
        item_via_layers = via_layers(item)
        for layer in selected_copper_layer_items:
            if layer.name in item_via_layers or "*.Cu" in item_via_layers:
                items.append(_PrimitiveLayerItem(source=item, layer=layer))
    return tuple(items)


def _should_dim_base_layers(store: PcbGeometryStore, settings: RenderSettings) -> bool:
    if not settings.dimming.enabled:
        return False
    return any(
        _selected_highlight_items(store, settings, highlight)
        or _selected_highlight_vias(store, settings, highlight)
        for highlight in settings.highlights
    )


def _selected_highlight_items(
    store: PcbGeometryStore,
    settings: RenderSettings,
    highlight: HighlightSpec,
) -> tuple[RenderableGeometry, ...]:
    selected_items = _filter_excluded_components(
        select_source_artwork(store, settings.source.layers, active_side=settings.side),
        settings.source.exclude_components,
    )
    return tuple(
        item
        for item in selected_items
        if item.kind is not GeometryKind.VIA and _matches_highlight(item, highlight)
    )


def _selected_highlight_vias(
    store: PcbGeometryStore,
    settings: RenderSettings,
    highlight: HighlightSpec,
) -> tuple[RenderableGeometry, ...]:
    selected_copper_layer_items = selected_copper_layers(
        store,
        settings.source.layers,
        active_side=settings.side,
    )
    if not selected_copper_layer_items:
        return ()
    return tuple(
        item
        for item in store.by_kind(GeometryKind.VIA)
        if _matches_highlight(item, highlight)
        and _via_intersects_selected_layers(item, selected_copper_layer_items)
    )


def _matches_highlight(item: RenderableGeometry, highlight: HighlightSpec) -> bool:
    if highlight.net:
        return item.tags.net_name.casefold() == highlight.net.casefold()
    if highlight.component:
        return item.tags.component_ref.casefold() == highlight.component.casefold()
    if highlight.pad:
        component, _separator, pad_number = highlight.pad.partition(".")
        return (
            item.tags.component_ref.casefold() == component.casefold()
            and item.tags.pad_number.casefold() == pad_number.casefold()
        )
    return False


def _via_intersects_selected_layers(
    item: RenderableGeometry,
    selected_layers: Iterable[GeometryLayer],
) -> bool:
    item_via_layers = via_layers(item)
    return any(
        layer.name in item_via_layers or "*.Cu" in item_via_layers for layer in selected_layers
    )


def _highlight_target(highlight: HighlightSpec) -> str:
    if highlight.net:
        return f"net:{highlight.net}"
    if highlight.component:
        return f"component:{highlight.component}"
    return f"pad:{highlight.pad}"


def _filter_excluded_components(
    items: Iterable[RenderableGeometry],
    excluded_prefixes: tuple[str, ...],
) -> tuple[RenderableGeometry, ...]:
    if not excluded_prefixes:
        return tuple(items)
    return tuple(
        item
        for item in items
        if not item.tags.component_prefix
        or item.tags.component_prefix.upper() not in excluded_prefixes
    )


def _group_key(layer: GeometryLayer) -> _LayerGroupKey:
    side = layer.side
    inner_index = layer.stack_index if side == "inner" else None
    return _LayerGroupKey(
        function=layer.role,
        side=side,
        inner_index=inner_index,
        source_layer_name=layer.name,
    )


def _group_sort_key(
    item: tuple[_LayerGroupKey, list[_PrimitiveLayerItem]],
) -> tuple[int, str]:
    key, group = item
    stack_index = group[0].layer.stack_index if group else 0
    return (stack_index, key.source_layer_name)


def _eda_group_sort_key(
    item: tuple[_LayerGroupKey, list[_PrimitiveLayerItem]],
    *,
    side: str,
) -> tuple[int, int, str]:
    key, group = item
    stack_index = group[0].layer.stack_index if group else 0
    if key.function == "edge":
        return (1, 0, key.source_layer_name)
    if key.function == "drill":
        return (2, 0, key.source_layer_name)
    layer_order = stack_index if side == "back" else -stack_index
    return (0, layer_order, key.source_layer_name)


def _primitive_layer_primitives(
    key: _LayerGroupKey,
    group: list[_PrimitiveLayerItem],
    *,
    profiler: RenderProfiler | None = None,
) -> tuple[SvgPrimitive, ...]:
    if profiler is None:
        return _primitive_layer_primitives_without_profiling(key, group)
    with profiler.span(
        "artwork.convert_primitives",
        layer=key.source_layer_name,
        role=key.function,
        side=key.side,
        items=len(group),
    ):
        primitives = _primitive_layer_primitives_without_profiling(key, group)
    profiler.metric(
        "artwork.converted_primitives",
        layer=key.source_layer_name,
        role=key.function,
        side=key.side,
        sourceItems=len(group),
        primitives=len(primitives),
        pathCharacters=sum(len(primitive.d) for primitive in primitives),
    )
    return primitives


def _primitive_layer_primitives_without_profiling(
    key: _LayerGroupKey,
    group: list[_PrimitiveLayerItem],
) -> tuple[SvgPrimitive, ...]:
    primitives: list[SvgPrimitive] = []
    for item in sorted(group, key=_primitive_layer_item_sort_key):
        if key.function == "drill" and item.source.kind is GeometryKind.DRILL:
            primitive = visible_drill_to_svg_primitive(item.source)
        else:
            primitive = geometry_to_svg_primitive(
                item.source,
                target_layer_name=key.source_layer_name,
            )
        if primitive is not None:
            primitives.append(primitive)
    return tuple(primitives)


_PRIMITIVE_KIND_ORDER = {
    GeometryKind.TRACE: 0,
    GeometryKind.TRACE_ARC: 0,
    GeometryKind.ZONE: 1,
    GeometryKind.SILK_ARC: 1,
    GeometryKind.SILK_POLYGON: 1,
    GeometryKind.FAB_POLYGON: 1,
    GeometryKind.BODY_POLYGON: 1,
    GeometryKind.MASK: 1,
    GeometryKind.PASTE: 1,
    GeometryKind.MECHANICAL: 1,
    GeometryKind.PAD: 2,
    GeometryKind.VIA: 2,
}


def _primitive_layer_item_sort_key(item: _PrimitiveLayerItem) -> tuple[int, str]:
    return (_PRIMITIVE_KIND_ORDER.get(item.source.kind, 3), item.source.id)


def _copper_order_by_layer(store: PcbGeometryStore) -> dict[str, int]:
    layers = {item.layer.name: item.layer for item in store.items if item.layer.role == "copper"}
    return {
        layer.name: index
        for index, layer in enumerate(
            sorted(layers.values(), key=lambda layer: (layer.stack_index, layer.name))
        )
    }


def _realistic_side_primitives(
    layer_items: Iterable[_PrimitiveLayerItem],
    *,
    role: str,
    side: str,
    profiler: RenderProfiler | None = None,
) -> tuple[SvgPrimitive, ...]:
    groups: dict[_LayerGroupKey, list[_PrimitiveLayerItem]] = defaultdict(list)
    for item in layer_items:
        if item.layer.role == role and item.layer.side == side:
            groups[_group_key(item.layer)].append(item)

    primitives: list[SvgPrimitive] = []
    for key, group in sorted(groups.items(), key=_group_sort_key):
        primitives.extend(_primitive_layer_primitives(key, group, profiler=profiler))
    return tuple(primitives)


def _silkscreen_masks_by_side(store: PcbGeometryStore) -> dict[str, LayerMask | None]:
    return {
        "front": _silkscreen_layer_mask(store, side="front"),
        "back": _silkscreen_layer_mask(store, side="back"),
    }


def _silkscreen_layer_mask(store: PcbGeometryStore, *, side: str) -> LayerMask | None:
    board_primitives = _board_mask_primitives(store)
    if not board_primitives:
        return None
    return LayerMask(
        board=board_primitives,
        drills=_drill_mask_primitives(store),
        openings=solder_mask_opening_primitives(store, side=side),
    )


def _mask_for_group(
    key: _LayerGroupKey,
    layer_mask: LayerMask | None,
    silkscreen_masks: dict[str, LayerMask | None],
) -> LayerMask | None:
    if key.function in {"edge", "drill"}:
        return None
    if key.function == "silkscreen" and key.side in silkscreen_masks:
        return silkscreen_masks[key.side]
    return layer_mask


def _selected_board_outline_primitives(
    selected_items: Iterable[RenderableGeometry],
) -> tuple[SvgPrimitive, ...]:
    return tuple(
        primitive
        for item in selected_items
        if item.kind is GeometryKind.BOARD_OUTLINE
        if (
            primitive := geometry_to_svg_primitive(
                item,
                target_layer_name=item.layer.name,
            )
        )
        is not None
    )


def _source_layers_for_primitives(
    primitives: Iterable[SvgPrimitive],
    fallback: Iterable[SvgPrimitive] = (),
) -> tuple[str, ...]:
    source_layers = _unique_ordered(
        primitive.source_layer for primitive in primitives if primitive.source_layer
    )
    if source_layers:
        return source_layers
    return _unique_ordered(
        primitive.source_layer for primitive in fallback if primitive.source_layer
    )


def _source_ids_for_primitives(
    primitives: Iterable[SvgPrimitive],
    fallback: Iterable[SvgPrimitive] = (),
) -> tuple[str, ...]:
    source_ids = _unique_ordered(
        primitive.source_id for primitive in primitives if primitive.source_id
    )
    if source_ids:
        return source_ids
    return _unique_ordered(primitive.source_id for primitive in fallback if primitive.source_id)


def _board_mask_primitives(store: PcbGeometryStore) -> tuple[SvgPrimitive, ...]:
    primitives = tuple(
        primitive
        for item in store.by_kind(GeometryKind.BOARD_MATERIAL)
        if (
            primitive := geometry_to_svg_primitive(
                item,
                target_layer_name=item.layer.name,
            )
        )
        is not None
    )
    if primitives:
        return primitives
    return _board_outline_primitives(store)


def _board_outline_primitives(store: PcbGeometryStore) -> tuple[SvgPrimitive, ...]:
    return tuple(
        primitive
        for item in store.by_kind(GeometryKind.BOARD_OUTLINE)
        if (
            primitive := geometry_to_svg_primitive(
                item,
                target_layer_name=item.layer.name,
            )
        )
        is not None
    )


def _drill_mask_primitives(store: PcbGeometryStore) -> tuple[SvgPrimitive, ...]:
    """Return drill-hole mask primitives visible through board surfaces.

    V1 treats parsed drills and vias as through-board openings. Span-aware
    filtering and tenting semantics can be added behind this helper later.
    """
    return tuple(
        primitive
        for item in store.items
        if item.kind in {GeometryKind.DRILL, GeometryKind.VIA}
        if (primitive := drill_to_svg_primitive(item)) is not None
    )


def _layer_mask(store: PcbGeometryStore) -> LayerMask | None:
    board_primitives = _board_mask_primitives(store)
    if not board_primitives:
        return None
    return LayerMask(
        board=board_primitives,
        drills=_drill_mask_primitives(store),
    )


def _derived_layer_id(role: VisualRole) -> str:
    parts = [role.namespace, role.function]
    if role.side:
        parts.append(role.side)
    if role.side == "inner" and role.inner_index is not None:
        parts.append(str(role.inner_index))
    return ":".join(parts)


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _source_ids_by_store_order_for_primitives(
    store: PcbGeometryStore,
    primitives: Iterable[SvgPrimitive],
) -> tuple[str, ...]:
    selected_ids = {primitive.source_id for primitive in primitives}
    return tuple(item.id for item in store.items if item.id in selected_ids)


def _board_source_layers(store: PcbGeometryStore) -> tuple[str, ...]:
    return _unique_ordered(
        item.layer.name
        for item in store.items
        if item.kind in {GeometryKind.BOARD_MATERIAL, GeometryKind.BOARD_OUTLINE}
    )


def _board_source_ids(store: PcbGeometryStore) -> tuple[str, ...]:
    return tuple(
        item.id
        for item in store.items
        if item.kind in {GeometryKind.BOARD_MATERIAL, GeometryKind.BOARD_OUTLINE}
    )
