"""Render-mode projections from PCB source artwork to derived visual layers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shapely import GeometryCollection

from phosphor_eda.pcb import PcbVia
from phosphor_eda.pcb_render_artwork import (
    ArtworkItem,
    DerivedLayer,
    board_outline_geometry,
    drill_geometry_for_layer,
    geometry_to_artwork,
    select_source_artwork,
)
from phosphor_eda.pcb_render_geometry import GeometryKind, GeometryLayer
from phosphor_eda.pcb_render_tokens import VisualRole, resolve_layer_style
from phosphor_eda.shapely_geometry import (
    robust_difference,
    robust_intersection,
    robust_union,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from shapely.geometry.base import BaseGeometry

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
class _GroupedArtwork:
    artwork: ArtworkItem
    layer: GeometryLayer


@dataclass(frozen=True)
class HighlightGroup:
    target: str
    layers: tuple[DerivedLayer, ...]


_SOURCE_FUNCTION_TO_LAYER_ROLE = {
    "copper": "copper",
    "silkscreen": "silkscreen",
    "solder_mask": "mask",
    "solder_paste": "paste",
    "fab": "fabrication",
    "courtyard": "courtyard",
    "edge": "edge",
    "mechanical": "mechanical",
    "other": "unknown",
}

_REALISTIC_LAYER_ORDER = (
    "substrate",
    "solderMask",
    "coveredCopper",
    "exposedCopper",
    "silkscreen",
    "boardOutline",
)


def build_cad_layers(
    store: PcbGeometryStore,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> tuple[DerivedLayer, ...]:
    """Build CAD derived layers from selected PCB source artwork."""
    selected_items = _filter_excluded_components(
        select_source_artwork(store, settings.source.layers, active_side=settings.side),
        settings.source.exclude_components,
    )
    if profiler is not None:
        profiler.metric("cad.selected_items", count=len(selected_items))
    grouped_artwork = _groupable_artwork(
        store,
        selected_items,
        settings.source.layers,
        active_side=settings.side,
        profiler=profiler,
    )
    if profiler is not None:
        profiler.metric("cad.grouped_artwork", count=len(grouped_artwork))
    board = board_outline_geometry(store)
    dimmed = _should_dim_base_layers(store, settings)
    warned_missing_dimmed_tokens: set[str] = set()
    drill_cache: dict[str, BaseGeometry] = {}

    groups: dict[_LayerGroupKey, list[_GroupedArtwork]] = defaultdict(list)
    for item in grouped_artwork:
        groups[_group_key(item.layer)].append(item)

    layers: list[DerivedLayer] = []
    for key, group in sorted(groups.items(), key=_group_sort_key):
        if profiler is None:
            geometry = _resolved_group_geometry(
                store,
                group,
                board,
                drill_cache=drill_cache,
            )
        else:
            with profiler.span(
                "cad.resolve_group",
                layer=key.source_layer_name,
                function=key.function,
                side=key.side,
                items=len(group),
            ):
                geometry = _resolved_group_geometry(
                    store,
                    group,
                    board,
                    drill_cache=drill_cache,
                )
        if geometry.is_empty:
            continue
        role = VisualRole(
            namespace="cad",
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
        )
        source_layers = _unique_ordered(
            layer for item in group for layer in item.artwork.source_layers
        )
        source_ids = _source_ids_by_store_order(store, group)
        layers.append(
            DerivedLayer(
                id=_derived_layer_id(role),
                role=role,
                geometry=geometry,
                source_layers=source_layers,
                source_ids=source_ids,
                style=style,
                data={"source-layer": key.source_layer_name},
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
    grouped_artwork = _groupable_artwork(
        store,
        selected_items,
        settings.source.layers,
        active_side=side,
        profiler=profiler,
    )
    if profiler is not None:
        profiler.metric("realistic.grouped_artwork", count=len(grouped_artwork))
    board = board_outline_geometry(store)
    surface_drills = _surface_drill_geometry(store)
    dimmed = _should_dim_base_layers(store, settings)
    warned_missing_dimmed_tokens: set[str] = set()

    mask_artwork = _side_artwork(selected_items, role="mask", side=side)
    copper_artwork = tuple(
        item.artwork
        for item in grouped_artwork
        if item.layer.role == "copper" and item.layer.side == side
    )
    silkscreen_artwork = _side_artwork(selected_items, role="silkscreen", side=side)

    if profiler is not None:
        profiler.metric("realistic.mask_artwork", count=len(mask_artwork))
        profiler.metric("realistic.copper_artwork", count=len(copper_artwork))
        profiler.metric("realistic.silkscreen_artwork", count=len(silkscreen_artwork))
    if profiler is None:
        mask_openings = _clip_to_board(
            _union_or_empty(
                (item.geometry for item in mask_artwork),
                prefer_disjoint_subsets=True,
            ),
            board,
        )
        outer_copper = _clip_to_board(
            _union_or_empty(
                (item.geometry for item in copper_artwork),
                prefer_disjoint_subsets=True,
            ),
            board,
        )
        silkscreen = _clip_to_board(
            _union_or_empty(
                (item.geometry for item in silkscreen_artwork),
                prefer_disjoint_subsets=True,
            ),
            board,
        )
    else:
        with profiler.span("realistic.union_mask_openings", items=len(mask_artwork)):
            mask_openings = _clip_to_board(
                _union_or_empty(
                    (item.geometry for item in mask_artwork),
                    prefer_disjoint_subsets=True,
                ),
                board,
            )
        with profiler.span("realistic.union_outer_copper", items=len(copper_artwork)):
            outer_copper = _clip_to_board(
                _union_or_empty(
                    (item.geometry for item in copper_artwork),
                    prefer_disjoint_subsets=True,
                ),
                board,
            )
        with profiler.span("realistic.union_silkscreen", items=len(silkscreen_artwork)):
            silkscreen = _clip_to_board(
                _union_or_empty(
                    (item.geometry for item in silkscreen_artwork),
                    prefer_disjoint_subsets=True,
                ),
                board,
            )

    layer_inputs = {
        "substrate": _RealisticLayerInput(
            geometry=board,
            source_layers=_board_source_layers(store),
            source_ids=_board_source_ids(store),
        ),
        "solderMask": _RealisticLayerInput(
            geometry=_difference(board, mask_openings),
            source_layers=_source_layers_from_artwork(mask_artwork),
            source_ids=_source_ids_from_artwork(mask_artwork),
        ),
        "coveredCopper": _RealisticLayerInput(
            geometry=outer_copper,
            source_layers=_source_layers_from_artwork(copper_artwork),
            source_ids=_source_ids_by_store_order_for_artwork(store, copper_artwork),
        ),
        "exposedCopper": _RealisticLayerInput(
            geometry=_intersection(outer_copper, mask_openings),
            source_layers=_unique_ordered(
                (
                    *_source_layers_from_artwork(copper_artwork),
                    *_source_layers_from_artwork(mask_artwork),
                )
            ),
            source_ids=_source_ids_by_store_order_for_artwork(
                store,
                (*copper_artwork, *mask_artwork),
            ),
        ),
        "silkscreen": _RealisticLayerInput(
            geometry=_difference(silkscreen, mask_openings),
            source_layers=_source_layers_from_artwork(silkscreen_artwork),
            source_ids=_source_ids_by_store_order_for_artwork(store, silkscreen_artwork),
        ),
        "boardOutline": _RealisticLayerInput(
            geometry=_outline_geometry(board),
            source_layers=_board_source_layers(store),
            source_ids=_board_source_ids(store),
        ),
    }

    layers: list[DerivedLayer] = []
    for function in _REALISTIC_LAYER_ORDER:
        layer_input = layer_inputs[function]
        geometry = layer_input.geometry
        if function != "boardOutline":
            geometry = _difference(geometry, surface_drills)
        if geometry.is_empty:
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
                geometry=geometry,
                source_layers=layer_input.source_layers,
                source_ids=layer_input.source_ids,
                style=style,
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
    board = board_outline_geometry(store)
    groups: list[HighlightGroup] = []
    drill_cache: dict[str, BaseGeometry] = {}

    for highlight in settings.highlights:
        selected_items = _selected_highlight_items(store, settings, highlight)
        selected_vias = _selected_highlight_vias(store, settings, highlight)
        grouped_artwork = _groupable_artwork(
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
                grouped=len(grouped_artwork),
            )
        if not grouped_artwork:
            continue

        by_layer: dict[_LayerGroupKey, list[_GroupedArtwork]] = defaultdict(list)
        for item in grouped_artwork:
            by_layer[_group_key(item.layer)].append(item)

        target = _highlight_target(highlight)
        layers: list[DerivedLayer] = []
        for key, layer_group in sorted(by_layer.items(), key=_group_sort_key):
            if profiler is None:
                geometry = _resolved_group_geometry(
                    store,
                    layer_group,
                    board,
                    drill_cache=drill_cache,
                )
            else:
                with profiler.span(
                    "highlight.resolve_group",
                    target=target,
                    layer=key.source_layer_name,
                    function=key.function,
                    side=key.side,
                    items=len(layer_group),
                ):
                    geometry = _resolved_group_geometry(
                        store,
                        layer_group,
                        board,
                        drill_cache=drill_cache,
                    )
            if geometry.is_empty:
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
                    geometry=geometry,
                    source_layers=_unique_ordered(
                        layer for item in layer_group for layer in item.artwork.source_layers
                    ),
                    source_ids=_source_ids_by_store_order(store, layer_group),
                    style=style,
                    data={
                        "data-highlight-target": target,
                        "source-layer": key.source_layer_name,
                    },
                )
            )

        if layers:
            groups.append(HighlightGroup(target=target, layers=tuple(layers)))

    return tuple(groups)


@dataclass(frozen=True)
class _RealisticLayerInput:
    geometry: BaseGeometry
    source_layers: tuple[str, ...]
    source_ids: tuple[str, ...]


def _groupable_artwork(
    store: PcbGeometryStore,
    selected_items: Iterable[RenderableGeometry],
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str,
    via_items: Iterable[RenderableGeometry] | None = None,
    profiler: RenderProfiler | None = None,
) -> tuple[_GroupedArtwork, ...]:
    grouped: list[_GroupedArtwork] = []
    selected_non_vias = tuple(item for item in selected_items if item.kind is not GeometryKind.VIA)
    if profiler is None:
        for item in selected_non_vias:
            artwork = geometry_to_artwork(item)
            if artwork is None:
                continue
            grouped.append(_GroupedArtwork(artwork=artwork, layer=item.layer))
    else:
        with profiler.span("artwork.convert_non_vias", items=len(selected_non_vias)):
            for item in selected_non_vias:
                artwork = geometry_to_artwork(item)
                if artwork is None:
                    continue
                grouped.append(_GroupedArtwork(artwork=artwork, layer=item.layer))

    selected_copper_layers = _selected_copper_layers(store, rules, active_side=active_side)
    selected_via_items = store.by_kind(GeometryKind.VIA) if via_items is None else tuple(via_items)

    def append_vias() -> None:
        for item in selected_via_items:
            artwork = geometry_to_artwork(item)
            if artwork is None:
                continue
            via_layers = _via_layers(item)
            for layer in selected_copper_layers:
                if layer.name not in via_layers and "*.Cu" not in via_layers:
                    continue
                grouped.append(
                    _GroupedArtwork(
                        artwork=ArtworkItem(
                            geometry=artwork.geometry,
                            source_ids=artwork.source_ids,
                            source_layers=(layer.name,),
                            tags=artwork.tags,
                        ),
                        layer=layer,
                    )
                )

    if profiler is None:
        append_vias()
    else:
        with profiler.span(
            "artwork.convert_vias",
            vias=len(selected_via_items),
            copper_layers=len(selected_copper_layers),
        ):
            append_vias()
    return tuple(grouped)


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
    selected_copper_layers = _selected_copper_layers(
        store,
        settings.source.layers,
        active_side=settings.side,
    )
    if not selected_copper_layers:
        return ()
    return tuple(
        item
        for item in store.by_kind(GeometryKind.VIA)
        if _matches_highlight(item, highlight)
        and _via_intersects_selected_layers(item, selected_copper_layers)
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
    via_layers = _via_layers(item)
    return any(layer.name in via_layers or "*.Cu" in via_layers for layer in selected_layers)


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


def _selected_copper_layers(
    store: PcbGeometryStore,
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str,
) -> tuple[GeometryLayer, ...]:
    rule_tuple = tuple(rules)
    known_layers = {
        item.layer.name: item.layer for item in store.items if item.layer.role == "copper"
    }
    layers: dict[str, GeometryLayer] = {}
    for item in store.items:
        if item.layer.role != "copper":
            continue
        if not any(
            _rule_selects_layer(
                rule,
                item.layer,
                object_name="via",
                active_side=active_side,
            )
            for rule in rule_tuple
        ):
            continue
        layers[item.layer.name] = item.layer

    for item in store.by_kind(GeometryKind.VIA):
        for layer_name in _via_layers(item):
            if layer_name == "*.Cu":
                continue
            layer = known_layers.get(layer_name, _copper_layer_for_name(layer_name))
            if any(
                _rule_selects_layer(
                    rule,
                    layer,
                    object_name="via",
                    active_side=active_side,
                )
                for rule in rule_tuple
            ):
                layers[layer.name] = layer
    return tuple(layers.values())


def _rule_selects_layer(
    rule: LayerSelectionRule,
    layer: GeometryLayer,
    *,
    object_name: str,
    active_side: str,
) -> bool:
    if not rule.visible:
        return False
    if rule.match.name and layer.name != rule.match.name:
        return False
    if rule.match.function and layer.role != _source_function_layer_role(rule.match.function):
        return False
    match_side = active_side if rule.match.side == "active" else rule.match.side
    if match_side and layer.side != match_side:
        return False
    return not rule.objects or object_name in _normalized_objects(rule.objects)


def _source_function_layer_role(function: str) -> str:
    return _SOURCE_FUNCTION_TO_LAYER_ROLE.get(function, function)


def _normalized_objects(objects: Iterable[str]) -> frozenset[str]:
    return frozenset(obj.strip().lower().replace("-", "_") for obj in objects)


def _via_layers(item: RenderableGeometry) -> frozenset[str]:
    payload = item.payload if item.payload is not None else item.source
    if not isinstance(payload, PcbVia):
        return frozenset()
    return frozenset(str(layer) for layer in payload.layers)


def _copper_layer_for_name(name: str) -> GeometryLayer:
    if name in {"F.Cu", "Top Layer"}:
        return GeometryLayer(name=name, role="copper", side="front", stack_index=10_000)
    if name in {"B.Cu", "Bottom Layer"}:
        return GeometryLayer(name=name, role="copper", side="back", stack_index=0)
    return GeometryLayer(
        name=name,
        role="copper",
        side="inner",
        stack_index=_inner_layer_index(name),
    )


def _inner_layer_index(name: str) -> int:
    digits = "".join(character for character in name if character.isdigit())
    if not digits:
        return 5_000
    return int(digits)


def _group_key(layer: GeometryLayer) -> _LayerGroupKey:
    side = layer.side
    inner_index = layer.stack_index if side == "inner" else None
    return _LayerGroupKey(
        function=layer.role,
        side=side,
        inner_index=inner_index,
        source_layer_name=layer.name,
    )


def _group_sort_key(item: tuple[_LayerGroupKey, list[_GroupedArtwork]]) -> tuple[int, int, str]:
    key, group = item
    stack_index = group[0].layer.stack_index if group else 0
    return (stack_index, len(group), key.source_layer_name)


def _resolved_group_geometry(
    store: PcbGeometryStore,
    group: list[_GroupedArtwork],
    board: BaseGeometry,
    *,
    drill_cache: dict[str, BaseGeometry] | None = None,
) -> BaseGeometry:
    layer = group[0].layer
    if layer.role == "edge":
        return _outline_geometry(board)

    geometry = _union_or_empty(item.artwork.geometry for item in group)
    if not board.is_empty:
        geometry = _intersection(geometry, board)
    if layer.role == "copper":
        drills = _drill_geometry_for_layer(store, layer.name, drill_cache=drill_cache)
        if not drills.is_empty:
            geometry = _difference(geometry, drills)
    return geometry


def _side_artwork(
    selected_items: Iterable[RenderableGeometry],
    *,
    role: str,
    side: str,
) -> tuple[ArtworkItem, ...]:
    artwork: list[ArtworkItem] = []
    for item in selected_items:
        if item.layer.role != role or item.layer.side != side:
            continue
        artwork_item = geometry_to_artwork(item)
        if artwork_item is not None:
            artwork.append(artwork_item)
    return tuple(artwork)


def _surface_drill_geometry(store: PcbGeometryStore) -> BaseGeometry:
    """Return drills visible through board surfaces.

    V1 treats parsed drills and vias as through-board openings. Span-aware
    filtering and tenting semantics can be added behind this helper later.
    """
    return drill_geometry_for_layer(store)


def _drill_geometry_for_layer(
    store: PcbGeometryStore,
    layer_name: str,
    *,
    drill_cache: dict[str, BaseGeometry] | None,
) -> BaseGeometry:
    if drill_cache is None:
        return drill_geometry_for_layer(store, layer_name=layer_name)
    if layer_name not in drill_cache:
        drill_cache[layer_name] = drill_geometry_for_layer(store, layer_name=layer_name)
    return drill_cache[layer_name]


def _clip_to_board(geometry: BaseGeometry, board: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty or board.is_empty:
        return geometry
    return _intersection(geometry, board)


def _difference(geometry: BaseGeometry, subtractive: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty or subtractive.is_empty:
        return geometry
    return robust_difference(geometry, subtractive)


def _intersection(left: BaseGeometry, right: BaseGeometry) -> BaseGeometry:
    if left.is_empty or right.is_empty:
        return GeometryCollection()
    return robust_intersection(left, right)


def _outline_geometry(board: BaseGeometry) -> BaseGeometry:
    if board.is_empty:
        return GeometryCollection()
    return board.boundary


def _union_or_empty(
    geometries: Iterable[BaseGeometry],
    *,
    prefer_disjoint_subsets: bool = False,
) -> BaseGeometry:
    geometry_tuple = tuple(geometries)
    if not geometry_tuple:
        return GeometryCollection()
    if len(geometry_tuple) == 1:
        return geometry_tuple[0]
    return robust_union(geometry_tuple, prefer_disjoint_subsets=prefer_disjoint_subsets)


def _derived_layer_id(role: VisualRole) -> str:
    parts = [role.namespace, role.function]
    if role.side:
        parts.append(role.side)
    if role.side == "inner" and role.inner_index is not None:
        parts.append(str(role.inner_index))
    return ":".join(parts)


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _source_ids_by_store_order(
    store: PcbGeometryStore,
    group: Iterable[_GroupedArtwork],
) -> tuple[str, ...]:
    selected_ids = {source_id for item in group for source_id in item.artwork.source_ids}
    return tuple(item.id for item in store.items if item.id in selected_ids)


def _source_layers_from_artwork(artwork: Iterable[ArtworkItem]) -> tuple[str, ...]:
    return _unique_ordered(layer for item in artwork for layer in item.source_layers)


def _source_ids_from_artwork(artwork: Iterable[ArtworkItem]) -> tuple[str, ...]:
    return _unique_ordered(source_id for item in artwork for source_id in item.source_ids)


def _source_ids_by_store_order_for_artwork(
    store: PcbGeometryStore,
    artwork: Iterable[ArtworkItem],
) -> tuple[str, ...]:
    selected_ids = {source_id for item in artwork for source_id in item.source_ids}
    return tuple(item.id for item in store.items if item.id in selected_ids)


def _board_source_layers(store: PcbGeometryStore) -> tuple[str, ...]:
    return _unique_ordered(item.layer.name for item in store.by_kind(GeometryKind.BOARD_OUTLINE))


def _board_source_ids(store: PcbGeometryStore) -> tuple[str, ...]:
    return tuple(item.id for item in store.by_kind(GeometryKind.BOARD_OUTLINE))
