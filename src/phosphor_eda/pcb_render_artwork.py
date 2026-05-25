"""Artwork core data structures for derived PCB render layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shapely import GeometryCollection
from shapely.geometry.base import BaseGeometry

from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from phosphor_eda.pcb_render_geometry import (
        GeometryTags,
        PcbGeometryStore,
        RenderableGeometry,
    )
    from phosphor_eda.pcb_render_settings import LayerSelectionRule
    from phosphor_eda.pcb_render_tokens import ResolvedStyle, VisualRole


@dataclass(frozen=True)
class ArtworkItem:
    geometry: BaseGeometry
    source_ids: tuple[str, ...]
    source_layers: tuple[str, ...]
    tags: GeometryTags


def _empty_data() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class DerivedLayer:
    id: str
    role: VisualRole
    geometry: BaseGeometry
    source_layers: tuple[str, ...]
    source_ids: tuple[str, ...]
    style: ResolvedStyle | None = None
    data: Mapping[str, str] = field(default_factory=_empty_data)


_FUNCTION_ROLE_ALIASES = {
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

_OBJECT_KIND_ALIASES = {
    "board_material": frozenset({GeometryKind.BOARD_MATERIAL}),
    "board_outline": frozenset({GeometryKind.BOARD_OUTLINE}),
    "drill": frozenset({GeometryKind.DRILL}),
    "drills": frozenset({GeometryKind.DRILL}),
    "pad": frozenset({GeometryKind.PAD}),
    "pads": frozenset({GeometryKind.PAD}),
    "trace": frozenset({GeometryKind.TRACE, GeometryKind.TRACE_ARC}),
    "traces": frozenset({GeometryKind.TRACE, GeometryKind.TRACE_ARC}),
    "trace_arc": frozenset({GeometryKind.TRACE_ARC}),
    "trace_arcs": frozenset({GeometryKind.TRACE_ARC}),
    "zone": frozenset({GeometryKind.ZONE}),
    "zones": frozenset({GeometryKind.ZONE}),
    "via": frozenset({GeometryKind.VIA}),
    "vias": frozenset({GeometryKind.VIA}),
    "silkscreen": frozenset(
        {
            GeometryKind.SILK_LINE,
            GeometryKind.SILK_POLYGON,
            GeometryKind.BOARD_GRAPHIC_TEXT,
        }
    ),
    "silk": frozenset({GeometryKind.SILK_LINE, GeometryKind.SILK_POLYGON}),
    "silk_line": frozenset({GeometryKind.SILK_LINE}),
    "silk_lines": frozenset({GeometryKind.SILK_LINE}),
    "silk_polygon": frozenset({GeometryKind.SILK_POLYGON}),
    "silk_polygons": frozenset({GeometryKind.SILK_POLYGON}),
    "fab": frozenset(
        {
            GeometryKind.FAB_LINE,
            GeometryKind.FAB_ARC,
            GeometryKind.FAB_CIRCLE,
            GeometryKind.FAB_POLYGON,
        }
    ),
    "text": frozenset(
        {
            GeometryKind.REF_TEXT,
            GeometryKind.VALUE_TEXT,
            GeometryKind.USER_TEXT,
            GeometryKind.BOARD_GRAPHIC_TEXT,
        }
    ),
    "reference_text": frozenset({GeometryKind.REF_TEXT}),
    "value_text": frozenset({GeometryKind.VALUE_TEXT}),
    "user_text": frozenset({GeometryKind.USER_TEXT}),
    "board_graphic_text": frozenset({GeometryKind.BOARD_GRAPHIC_TEXT}),
    "mask": frozenset({GeometryKind.MASK}),
    "paste": frozenset({GeometryKind.PASTE}),
    "mechanical": frozenset({GeometryKind.MECHANICAL}),
}


def select_source_artwork(
    store: PcbGeometryStore,
    rules: Iterable[LayerSelectionRule],
) -> tuple[RenderableGeometry, ...]:
    """Select raw renderable geometry using source-layer rules."""
    active_rules = tuple(rule for rule in rules if rule.visible)
    selected: list[RenderableGeometry] = []

    for item in store.items:
        if any(_matches_rule(item, rule) for rule in active_rules):
            selected.append(item)

    return tuple(selected)


def artwork_items_from_geometry(
    items: Iterable[RenderableGeometry],
) -> tuple[ArtworkItem, ...]:
    """Convert selected raw geometry that already carries Shapely artwork."""
    artwork: list[ArtworkItem] = []
    for item in items:
        geometry = _item_shapely_geometry(item)
        artwork.append(
            ArtworkItem(
                geometry=geometry,
                source_ids=(item.id,),
                source_layers=(item.layer.name,),
                tags=item.tags,
            )
        )
    return tuple(artwork)


def derived_layer_from_artwork(
    *,
    role: VisualRole,
    artwork: Sequence[ArtworkItem],
    style: ResolvedStyle | None = None,
    data: Mapping[str, str] | None = None,
) -> DerivedLayer:
    """Create a derived layer record from already-converted artwork."""
    return DerivedLayer(
        id=_derived_layer_id(role),
        role=role,
        geometry=GeometryCollection([item.geometry for item in artwork]),
        source_layers=_unique_ordered(layer for item in artwork for layer in item.source_layers),
        source_ids=tuple(source_id for item in artwork for source_id in item.source_ids),
        style=style,
        data={} if data is None else data,
    )


def _matches_rule(item: RenderableGeometry, rule: LayerSelectionRule) -> bool:
    match = rule.match
    if match.name and item.layer.name != match.name:
        return False
    if match.function and item.layer.role != _function_layer_role(match.function):
        return False
    if match.side and item.layer.side != match.side:
        return False
    return _matches_object_filter(item.kind, rule.objects)


def _function_layer_role(function: str) -> str:
    return _FUNCTION_ROLE_ALIASES.get(function, function)


def _matches_object_filter(kind: GeometryKind, objects: tuple[str, ...]) -> bool:
    if not objects:
        return True
    return any(kind in _object_class_kinds(object_class) for object_class in objects)


def _object_class_kinds(object_class: str) -> frozenset[GeometryKind]:
    normalized = object_class.strip().lower().replace("-", "_")
    if normalized in _OBJECT_KIND_ALIASES:
        return _OBJECT_KIND_ALIASES[normalized]
    return frozenset(kind for kind in GeometryKind if kind.value == normalized)


def _item_shapely_geometry(item: RenderableGeometry) -> BaseGeometry:
    if isinstance(item.payload, BaseGeometry):
        return item.payload
    if isinstance(item.source, BaseGeometry):
        return item.source
    msg = (
        f"Renderable geometry {item.id!r} does not carry Shapely geometry yet; "
        "primitive conversion is implemented by the next renderer task"
    )
    raise ValueError(msg)


def _derived_layer_id(role: VisualRole) -> str:
    parts = [role.namespace, role.function]
    if role.side:
        parts.append(role.side)
    if role.side == "inner" and role.inner_index is not None:
        parts.append(str(role.inner_index))
    return ":".join(parts)


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
