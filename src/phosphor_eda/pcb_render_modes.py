"""Render-mode projections from PCB source artwork to derived visual layers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shapely import GeometryCollection
from shapely.ops import unary_union

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

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb_render_geometry import PcbGeometryStore, RenderableGeometry
    from phosphor_eda.pcb_render_settings import LayerSelectionRule, RenderSettings


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


def build_cad_layers(
    store: PcbGeometryStore,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
) -> tuple[DerivedLayer, ...]:
    """Build CAD derived layers from selected PCB source artwork."""
    selected_items = _filter_excluded_components(
        select_source_artwork(store, settings.source.layers),
        settings.source.exclude_components,
    )
    grouped_artwork = _groupable_artwork(store, selected_items, settings.source.layers)
    board = board_outline_geometry(store)

    groups: dict[_LayerGroupKey, list[_GroupedArtwork]] = defaultdict(list)
    for item in grouped_artwork:
        groups[_group_key(item.layer)].append(item)

    layers: list[DerivedLayer] = []
    for key, group in sorted(groups.items(), key=_group_sort_key):
        geometry = _resolved_group_geometry(store, group, board)
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
            dimmed=settings.dimming.enabled,
            warn=warn,
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


def _groupable_artwork(
    store: PcbGeometryStore,
    selected_items: Iterable[RenderableGeometry],
    rules: Iterable[LayerSelectionRule],
) -> tuple[_GroupedArtwork, ...]:
    grouped: list[_GroupedArtwork] = []
    selected_non_vias = tuple(item for item in selected_items if item.kind is not GeometryKind.VIA)
    for item in selected_non_vias:
        artwork = geometry_to_artwork(item)
        if artwork is None:
            continue
        grouped.append(_GroupedArtwork(artwork=artwork, layer=item.layer))

    selected_copper_layers = _selected_copper_layers(store, rules)
    for item in store.by_kind(GeometryKind.VIA):
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
    return tuple(grouped)


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
) -> tuple[GeometryLayer, ...]:
    rule_tuple = tuple(rules)
    known_layers = {
        item.layer.name: item.layer for item in store.items if item.layer.role == "copper"
    }
    layers: dict[str, GeometryLayer] = {}
    for item in store.items:
        if item.layer.role != "copper":
            continue
        if not any(_rule_selects_layer(rule, item.layer, object_name="via") for rule in rule_tuple):
            continue
        layers[item.layer.name] = item.layer

    for item in store.by_kind(GeometryKind.VIA):
        for layer_name in _via_layers(item):
            if layer_name == "*.Cu":
                continue
            layer = known_layers.get(layer_name, _copper_layer_for_name(layer_name))
            if any(_rule_selects_layer(rule, layer, object_name="via") for rule in rule_tuple):
                layers[layer.name] = layer
    return tuple(layers.values())


def _rule_selects_layer(
    rule: LayerSelectionRule,
    layer: GeometryLayer,
    *,
    object_name: str,
) -> bool:
    if not rule.visible:
        return False
    if rule.match.name and layer.name != rule.match.name:
        return False
    if rule.match.function and layer.role != _source_function_layer_role(rule.match.function):
        return False
    if rule.match.side and layer.side != rule.match.side:
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
    return frozenset(payload.layers)


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
) -> BaseGeometry:
    layer = group[0].layer
    if layer.role == "edge":
        return _outline_geometry(board)

    geometry = _union_or_empty(item.artwork.geometry for item in group)
    if not board.is_empty:
        geometry = geometry.intersection(board)
    if layer.role == "copper":
        drills = drill_geometry_for_layer(store, layer_name=layer.name)
        if not drills.is_empty:
            geometry = geometry.difference(drills)
    return geometry


def _outline_geometry(board: BaseGeometry) -> BaseGeometry:
    if board.is_empty:
        return GeometryCollection()
    return board.boundary


def _union_or_empty(geometries: Iterable[BaseGeometry]) -> BaseGeometry:
    geometry_tuple = tuple(geometries)
    if not geometry_tuple:
        return GeometryCollection()
    if len(geometry_tuple) == 1:
        return geometry_tuple[0]
    return unary_union(geometry_tuple)


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
