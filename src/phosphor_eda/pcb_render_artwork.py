"""Artwork core data structures for derived PCB render layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from phosphor_eda.pcb import (
    Pcb,
    PcbArc,
    PcbCircle,
    PcbLine,
    PcbPad,
    PcbPolygon,
    PcbSegment,
    PcbTraceArc,
    PcbVia,
    PcbZone,
)
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
)
from phosphor_eda.sql.geometry import (
    arc_to_polyline,
    board_outline_polygon,
    pad_polygon,
    polygon_geometry,
    segment_geometry,
    trace_arc_geometry,
    via_geometry,
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
    """Convert selected raw geometry into Shapely artwork items."""
    artwork: list[ArtworkItem] = []
    for item in items:
        artwork_item = geometry_to_artwork(item)
        if artwork_item is not None:
            artwork.append(artwork_item)
    return tuple(artwork)


def geometry_to_artwork(item: RenderableGeometry) -> ArtworkItem | None:
    """Convert one raw renderable PCB primitive into Shapely artwork."""
    geometry = _item_shapely_geometry(item)
    if geometry is None or geometry.is_empty:
        return None
    return ArtworkItem(
        geometry=geometry,
        source_ids=(item.id,),
        source_layers=(item.layer.name,),
        tags=item.tags,
    )


def board_outline_geometry(store: PcbGeometryStore) -> BaseGeometry:
    """Return the board outline polygon assembled from edge-cut primitives."""
    outlines: list[BaseGeometry] = []
    for item in store.by_kind(GeometryKind.BOARD_OUTLINE):
        geometry = _board_outline_from_item(item)
        if geometry is not None and not geometry.is_empty:
            outlines.append(geometry)
    return _union_or_empty(outlines)


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


def _item_shapely_geometry(item: RenderableGeometry) -> BaseGeometry | None:
    payload = _item_payload(item)
    if isinstance(payload, BaseGeometry):
        return payload
    if item.kind is GeometryKind.PAD and isinstance(payload, PcbPad):
        return pad_polygon(payload)
    if item.kind is GeometryKind.TRACE and isinstance(payload, PcbSegment):
        _centerline, corridor = segment_geometry(payload)
        return corridor
    if item.kind is GeometryKind.TRACE_ARC and isinstance(payload, PcbTraceArc):
        _centerline, corridor = trace_arc_geometry(payload)
        return corridor
    if item.kind in _POLYGON_KINDS and isinstance(payload, PcbPolygon):
        return polygon_geometry(payload)
    if item.kind is GeometryKind.ZONE and isinstance(payload, PcbZone):
        return _zone_geometry(payload)
    if item.kind is GeometryKind.VIA and isinstance(payload, PcbVia):
        copper, drill = via_geometry(payload)
        return copper.difference(drill)
    if item.kind in _LINE_KINDS and isinstance(payload, PcbLine):
        return _line_geometry(payload)
    if item.kind in _ARC_KINDS and isinstance(payload, PcbArc):
        return _arc_geometry(payload)
    if item.kind in _CIRCLE_KINDS and isinstance(payload, PcbCircle):
        return _circle_geometry(payload)
    if item.kind is GeometryKind.BOARD_OUTLINE:
        return _board_outline_from_item(item)
    if item.kind is GeometryKind.BOARD_MATERIAL:
        return _board_material_geometry(payload)
    return None


_POLYGON_KINDS = frozenset(
    {
        GeometryKind.ZONE,
        GeometryKind.SILK_POLYGON,
        GeometryKind.FAB_POLYGON,
        GeometryKind.BODY_POLYGON,
        GeometryKind.MASK,
        GeometryKind.PASTE,
        GeometryKind.MECHANICAL,
    }
)

_LINE_KINDS = frozenset(
    {
        GeometryKind.SILK_LINE,
        GeometryKind.FAB_LINE,
        GeometryKind.BODY_LINE,
    }
)

_ARC_KINDS = frozenset(
    {
        GeometryKind.FAB_ARC,
        GeometryKind.BODY_ARC,
    }
)

_CIRCLE_KINDS = frozenset(
    {
        GeometryKind.FAB_CIRCLE,
        GeometryKind.BODY_CIRCLE,
    }
)


def _item_payload(item: RenderableGeometry) -> object:
    return item.payload if item.payload is not None else item.source


def _line_geometry(line: PcbLine) -> BaseGeometry:
    return LineString([(line.start_x, line.start_y), (line.end_x, line.end_y)]).buffer(
        line.width / 2,
        cap_style="flat",
    )


def _arc_geometry(arc: PcbArc) -> BaseGeometry:
    points = arc_to_polyline(
        arc.start_x,
        arc.start_y,
        arc.mid_x,
        arc.mid_y,
        arc.end_x,
        arc.end_y,
        num_points=32,
    )
    return LineString(points).buffer(arc.width / 2, cap_style="flat")


def _circle_geometry(circle: PcbCircle) -> BaseGeometry:
    geometry = Point(circle.cx, circle.cy).buffer(circle.radius, quad_segs=32)
    if circle.fill:
        return geometry
    return geometry.boundary.buffer(circle.width / 2, cap_style="flat")


def _zone_geometry(zone: PcbZone) -> BaseGeometry | None:
    if len(zone.boundary) < 3:
        return None
    return Polygon(zone.boundary)


def _pad_drill_geometry(payload: object, *, layer_name: str | None) -> BaseGeometry | None:
    if not isinstance(payload, PcbPad) or payload.drill <= 0:
        return None
    if layer_name is not None and not _layer_in_stack(layer_name, payload.layers):
        return None
    return Point(payload.x, payload.y).buffer(payload.drill / 2, quad_segs=32)


def _via_drill_geometry(payload: object, *, layer_name: str | None) -> BaseGeometry | None:
    if not isinstance(payload, PcbVia) or payload.drill <= 0:
        return None
    if layer_name is not None and not _layer_in_stack(layer_name, payload.layers):
        return None
    _copper, drill = via_geometry(payload)
    return drill


def _layer_in_stack(layer_name: str, layers: list[str]) -> bool:
    return layer_name in layers or "*.Cu" in layers


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
        return geometries[0]
    return unary_union(geometries)


def _derived_layer_id(role: VisualRole) -> str:
    parts = [role.namespace, role.function]
    if role.side:
        parts.append(role.side)
    if role.side == "inner" and role.inner_index is not None:
        parts.append(str(role.inner_index))
    return ":".join(parts)


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
