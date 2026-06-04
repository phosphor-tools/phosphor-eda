"""Artwork selection and derived PCB render layer data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, Point, Polygon
from shapely.geometry.base import BaseGeometry

from phosphor_eda.pcb import (
    Pcb,
    PcbArc,
    PcbLine,
    PcbPad,
    PcbVia,
)
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
)
from phosphor_eda.shapely_geometry import normalize_geometry, robust_union
from phosphor_eda.sql.geometry import (
    VIA_DRILL_QUAD_SEGS,
    board_outline_polygon,
    via_geometry,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from phosphor_eda.pcb_render_geometry import (
        PcbGeometryStore,
        RenderableGeometry,
    )
    from phosphor_eda.pcb_render_primitives import LayerClip, LayerMask, SvgPrimitive
    from phosphor_eda.pcb_render_settings import LayerSelectionRule
    from phosphor_eda.pcb_render_tokens import ResolvedStyle, VisualRole


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
    *,
    active_side: str = "",
) -> tuple[RenderableGeometry, ...]:
    """Select raw renderable geometry using source-layer rules."""
    active_rules = tuple(rule for rule in rules if rule.visible)
    selected: list[RenderableGeometry] = []

    for item in store.items:
        if any(_matches_rule(item, rule, active_side=active_side) for rule in active_rules):
            selected.append(item)

    return tuple(selected)


def board_outline_geometry(store: PcbGeometryStore) -> BaseGeometry:
    """Return the board outline polygon assembled from edge-cut primitives."""
    outlines: list[BaseGeometry] = []
    for item in store.by_kind(GeometryKind.BOARD_OUTLINE):
        geometry = _board_outline_from_item(item)
        if geometry is not None and not geometry.is_empty:
            outlines.append(geometry)
    outline = _union_or_empty(outlines)
    if not outline.is_empty:
        return outline
    for item in store.by_kind(GeometryKind.BOARD_MATERIAL):
        geometry = _board_material_geometry(_item_payload(item))
        if geometry is not None and not geometry.is_empty:
            return geometry
    return outline


def drill_geometry_for_layer(
    store: PcbGeometryStore,
    *,
    layer_name: str | None = None,
) -> BaseGeometry:
    """Return subtractive drill geometry relevant to a source copper layer.

    V1 includes through-hole pad drills and via drills when they intersect the
    requested layer. Tenting/blind/buried filtering can extend this API later.
    """
    drills: list[BaseGeometry] = []
    for item in store.items:
        if item.kind is GeometryKind.DRILL:
            drill = _pad_drill_geometry(_item_payload(item), layer_name=layer_name)
        elif item.kind is GeometryKind.VIA:
            drill = _via_drill_geometry(_item_payload(item), layer_name=layer_name)
        else:
            drill = None
        if drill is not None and not drill.is_empty:
            drills.append(drill)
    return _union_or_empty(drills)


def _matches_rule(
    item: RenderableGeometry,
    rule: LayerSelectionRule,
    *,
    active_side: str,
) -> bool:
    match = rule.match
    if match.name and item.layer.name != match.name:
        return False
    if match.function and item.layer.role != _function_layer_role(match.function):
        return False
    match_side = active_side if match.side == "active" else match.side
    if match_side and item.layer.side != match_side:
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


def _item_payload(item: RenderableGeometry) -> object:
    return item.payload if item.payload is not None else item.source


def _pad_drill_geometry(payload: object, *, layer_name: str | None) -> BaseGeometry | None:
    if not isinstance(payload, PcbPad) or payload.drill <= 0:
        return None
    if layer_name is not None and not _layer_in_stack(layer_name, payload.layers):
        return None
    return Point(payload.x, payload.y).buffer(payload.drill / 2, quad_segs=VIA_DRILL_QUAD_SEGS)


def _via_drill_geometry(payload: object, *, layer_name: str | None) -> BaseGeometry | None:
    if not isinstance(payload, PcbVia) or payload.drill <= 0:
        return None
    if layer_name is not None and not _layer_in_stack(layer_name, payload.layers):
        return None
    _copper, drill = via_geometry(payload)
    return drill


def _layer_in_stack(layer_name: str, layers: list[str]) -> bool:
    normalized_layers = {str(layer) for layer in layers}
    return layer_name in normalized_layers or "*.Cu" in normalized_layers


def _board_outline_from_item(item: RenderableGeometry) -> BaseGeometry | None:
    payload = _item_payload(item)
    if isinstance(payload, BaseGeometry):
        return payload
    if isinstance(payload, Pcb):
        return board_outline_polygon(payload.outline_lines, payload.outline_arcs)
    outline_payload = _outline_payload(payload)
    if outline_payload is not None:
        lines, arcs = outline_payload
        return board_outline_polygon(lines, arcs)
    if isinstance(item.source, Pcb):
        return board_outline_polygon(item.source.outline_lines, item.source.outline_arcs)
    return None


def _outline_payload(payload: object) -> tuple[list[PcbLine], list[PcbArc]] | None:
    if not isinstance(payload, tuple):
        return None
    payload_tuple = cast("tuple[object, ...]", payload)
    if len(payload_tuple) != 2:
        return None
    lines_object, arcs_object = payload_tuple
    if not isinstance(lines_object, list) or not isinstance(arcs_object, list):
        return None
    raw_lines = cast("list[object]", lines_object)
    raw_arcs = cast("list[object]", arcs_object)
    lines: list[PcbLine] = []
    for line in raw_lines:
        if not isinstance(line, PcbLine):
            return None
        lines.append(line)
    arcs: list[PcbArc] = []
    for arc in raw_arcs:
        if not isinstance(arc, PcbArc):
            return None
        arcs.append(arc)
    return lines, arcs


def _board_material_geometry(payload: object) -> BaseGeometry | None:
    bbox = _bbox(payload)
    if bbox is None:
        return None
    min_x, min_y, max_x, max_y = bbox
    return Polygon([(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)])


def _bbox(payload: object) -> tuple[float, float, float, float] | None:
    if not isinstance(payload, tuple):
        return None
    payload_tuple = cast("tuple[object, ...]", payload)
    if len(payload_tuple) != 4:
        return None
    values: list[float] = []
    for value in payload_tuple:
        if not isinstance(value, int | float):
            return None
        values.append(float(value))
    return values[0], values[1], values[2], values[3]


def _union_or_empty(geometries: list[BaseGeometry]) -> BaseGeometry:
    if not geometries:
        return GeometryCollection()
    if len(geometries) == 1:
        return normalize_geometry(geometries[0])
    return robust_union(geometries)
