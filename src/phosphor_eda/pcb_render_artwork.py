"""Artwork selection and derived PCB render layer data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, Polygon
from shapely.geometry.base import BaseGeometry

from phosphor_eda.pcb import (
    Pcb,
    PcbArc,
    PcbLine,
    PcbPad,
    PcbVia,
)
from phosphor_eda.pcb_render_drills import pad_drill_geometry
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometryLayer,
)
from phosphor_eda.pcb_render_primitives import (
    geometry_to_svg_primitive,
    pad_solder_mask_opening_primitive,
)
from phosphor_eda.shapely_geometry import normalize_geometry, robust_union
from phosphor_eda.sql.geometry import (
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
    "drill": "drill",
    "keepout": "keepout",
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
    "keepout": frozenset({GeometryKind.KEEPOUT}),
    "keepouts": frozenset({GeometryKind.KEEPOUT}),
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


def selected_copper_layers(
    store: PcbGeometryStore,
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str,
) -> tuple[GeometryLayer, ...]:
    """Return copper target layers selected for via projection."""
    rule_tuple = tuple(rules)
    known_layers = {
        item.layer.name: item.layer for item in store.items if item.layer.role == "copper"
    }
    layers: dict[str, GeometryLayer] = {}
    for item in store.items:
        if item.layer.role != "copper":
            continue
        if not any(
            rule_selects_layer(
                rule,
                item.layer,
                object_kind=GeometryKind.VIA,
                active_side=active_side,
            )
            for rule in rule_tuple
        ):
            continue
        layers[item.layer.name] = item.layer

    for item in store.by_kind(GeometryKind.VIA):
        for layer_name in via_layers(item):
            if layer_name == "*.Cu":
                continue
            layer = known_layers.get(layer_name, _copper_layer_for_name(layer_name))
            if any(
                rule_selects_layer(
                    rule,
                    layer,
                    object_kind=GeometryKind.VIA,
                    active_side=active_side,
                )
                for rule in rule_tuple
            ):
                layers[layer.name] = layer
    return tuple(layers.values())


def rule_selects_layer(
    rule: LayerSelectionRule,
    layer: GeometryLayer,
    *,
    object_kind: GeometryKind,
    active_side: str,
) -> bool:
    """Return whether a source-layer rule selects an object on a geometry layer."""
    if not rule.visible:
        return False
    if rule.match.name and layer.name != rule.match.name:
        return False
    if rule.match.function and layer.role != source_function_layer_role(rule.match.function):
        return False
    match_side = active_side if rule.match.side == "active" else rule.match.side
    if match_side and layer.side != match_side:
        return False
    return _matches_object_filter(object_kind, rule.objects)


def source_function_layer_role(function: str) -> str:
    """Map render-settings source function names to geometry layer roles."""
    return _FUNCTION_ROLE_ALIASES.get(function, function)


def via_layers(item: RenderableGeometry) -> frozenset[str]:
    """Return the copper-layer span of a via renderable item."""
    payload = item.payload if item.payload is not None else item.source
    if not isinstance(payload, PcbVia):
        return frozenset()
    return frozenset(str(layer) for layer in payload.layers)


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


def solder_mask_opening_primitives(
    store: PcbGeometryStore,
    *,
    side: str,
) -> tuple[SvgPrimitive, ...]:
    """Return source-derived solder-mask openings for manufacturable artwork.

    This intentionally scans the full source geometry inventory instead of the
    currently visible source selection. Silkscreen must be clipped by real mask
    apertures even when solder-mask layers are not rendered as visible layers.
    """
    mask_layer_name = side_mask_layer_name(store, side)
    primitives: list[SvgPrimitive] = []
    for item in store.items:
        if item.layer.role == "mask" and item.layer.side == side:
            primitive = geometry_to_svg_primitive(item, target_layer_name=item.layer.name)
            if primitive is not None:
                primitives.append(primitive)
            continue
        primitive = pad_solder_mask_opening_primitive(
            item,
            side=side,
            target_layer_name=mask_layer_name,
        )
        if primitive is not None:
            primitives.append(primitive)
    return tuple(primitives)


def side_mask_layer_name(store: PcbGeometryStore, side: str) -> str:
    """Return the native solder-mask layer name for a side."""
    for item in store.items:
        if item.layer.role == "mask" and item.layer.side == side:
            return item.layer.name
    return "B.Mask" if side == "back" else "F.Mask"


def _matches_rule(
    item: RenderableGeometry,
    rule: LayerSelectionRule,
    *,
    active_side: str,
) -> bool:
    if item.kind is GeometryKind.BOARD_MATERIAL:
        return False
    if item.kind is GeometryKind.KEEPOUT and not _rule_selects_keepouts(rule):
        return False
    return rule_selects_layer(rule, item.layer, object_kind=item.kind, active_side=active_side)


def _rule_selects_keepouts(rule: LayerSelectionRule) -> bool:
    return rule.match.function == "keepout" or (
        bool(rule.objects) and _matches_object_filter(GeometryKind.KEEPOUT, rule.objects)
    )


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
    return pad_drill_geometry(payload)


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
