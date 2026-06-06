"""Artwork selection and derived PCB render layer data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, Polygon
from shapely.geometry.base import BaseGeometry

from phosphor_eda.pcb import (
    LayerRole,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbPadGeometry,
    PcbViaGeometry,
)
from phosphor_eda.pcb import (
    PcbGeometry as DomainPcbGeometry,
)
from phosphor_eda.pcb_render_drills import pad_drill_geometry
from phosphor_eda.pcb_render_geometry import (
    SYNTHETIC_BOARD_MATERIAL_ROLE,
    SYNTHETIC_BOARD_OUTLINE_ROLE,
    SYNTHETIC_DRILL_ROLE,
    GeometryLayer,
    GeometryTags,
    RenderableGeometry,
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
                item=_via_selection_probe(),
                active_side=active_side,
            )
            for rule in rule_tuple
        ):
            continue
        layers[item.layer.name] = item.layer

    for item in store.by_object_type(PcbGeometryObject.VIA):
        for layer_name in via_layers(item):
            if layer_name == "*.Cu":
                continue
            layer = known_layers.get(layer_name, _copper_layer_for_name(layer_name))
            if any(
                rule_selects_layer(
                    rule,
                    layer,
                    item=item,
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
    item: RenderableGeometry,
    active_side: str,
) -> bool:
    """Return whether a source-layer rule selects an object on a geometry layer."""
    if not rule.visible:
        return False
    if rule.match.name and layer.name != rule.match.name:
        return False
    if rule.match.role and not _layer_has_role(layer, rule.match.role):
        return False
    match_side = active_side if rule.match.side == "active" else rule.match.side
    if match_side and layer.side != match_side:
        return False
    return _matches_object_filter(item, rule.objects)


def _layer_has_role(layer: GeometryLayer, role: str) -> bool:
    if layer.source is not None:
        return layer.source.has_role(role)
    try:
        return layer.role == LayerRole(role).value
    except ValueError:
        return False


def via_layers(item: RenderableGeometry) -> frozenset[str]:
    """Return the copper-layer span of a via renderable item."""
    if isinstance(item.source, DomainPcbGeometry):
        return frozenset(str(layer) for layer in item.source.layers)
    return frozenset()


def board_outline_geometry(store: PcbGeometryStore) -> BaseGeometry:
    """Return the board outline polygon assembled from edge-cut primitives."""
    outlines: list[BaseGeometry] = []
    for item in store.by_display_role(SYNTHETIC_BOARD_OUTLINE_ROLE):
        geometry = _board_outline_from_item(item)
        if geometry is not None and not geometry.is_empty:
            outlines.append(geometry)
    outline = _union_or_empty(outlines)
    if not outline.is_empty:
        return outline
    for item in store.by_display_role(SYNTHETIC_BOARD_MATERIAL_ROLE):
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
        if item.display_role == SYNTHETIC_DRILL_ROLE:
            drill = _pad_drill_geometry(_item_payload(item), layer_name=layer_name)
        elif item.object_type == PcbGeometryObject.VIA:
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
        if item.layer.role == "solder_mask" and item.layer.side == side:
            primitive = geometry_to_svg_primitive(item, target_layer_name=item.layer.name)
            if primitive is not None:
                primitives.append(primitive)
            continue
        if item.object_type == PcbGeometryObject.PAD and item.layer.side not in {"", side}:
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
        if item.layer.role == "solder_mask" and item.layer.side == side:
            return item.layer.name
    return "B.Mask" if side == "back" else "F.Mask"


def _matches_rule(
    item: RenderableGeometry,
    rule: LayerSelectionRule,
    *,
    active_side: str,
) -> bool:
    if item.display_role == SYNTHETIC_BOARD_MATERIAL_ROLE:
        return False
    if item.object_type == PcbGeometryObject.KEEP_OUT and not _rule_selects_keepouts(rule):
        return False
    return rule_selects_layer(rule, item.layer, item=item, active_side=active_side)


def _rule_selects_keepouts(rule: LayerSelectionRule) -> bool:
    return rule.match.role == "keepout" or (
        bool(rule.objects) and _matches_object_filter(_keepout_selection_probe(), rule.objects)
    )


def _matches_object_filter(item: RenderableGeometry, objects: tuple[str, ...]) -> bool:
    if not objects:
        return True
    return any(_matches_object_class(item, object_class) for object_class in objects)


def _matches_object_class(item: RenderableGeometry, object_class: str) -> bool:
    normalized = object_class.strip().lower().replace("-", "_")
    if normalized.startswith("shape:"):
        try:
            return item.shape == PcbGeometryShape(normalized.removeprefix("shape:"))
        except ValueError:
            return False
    if normalized == item.display_role:
        return True
    try:
        if item.object_type == PcbGeometryObject(normalized):
            return True
    except ValueError:
        pass
    try:
        return PcbGeometryRole(normalized) in item.roles
    except ValueError:
        return False


def _via_selection_probe() -> RenderableGeometry:
    return _selection_probe(
        object_type=PcbGeometryObject.VIA,
        shape=PcbGeometryShape.CIRCLE,
        roles=(PcbGeometryRole.COPPER, PcbGeometryRole.CONDUCTOR, PcbGeometryRole.DRILL),
        display_role=PcbGeometryObject.VIA.value,
    )


def _keepout_selection_probe() -> RenderableGeometry:
    return _selection_probe(
        object_type=PcbGeometryObject.KEEP_OUT,
        shape=PcbGeometryShape.UNKNOWN,
        roles=(PcbGeometryRole.KEEPOUT,),
        display_role=PcbGeometryRole.KEEPOUT.value,
    )


def _selection_probe(
    *,
    object_type: PcbGeometryObject,
    shape: PcbGeometryShape,
    roles: tuple[PcbGeometryRole, ...],
    display_role: str,
) -> RenderableGeometry:
    return RenderableGeometry(
        id="selection-probe",
        object_type=object_type,
        shape=shape,
        roles=roles,
        display_role=display_role,
        layer=GeometryLayer(name="", role="", side="", stack_index=0),
        tags=GeometryTags(),
        payload=None,
    )


def _item_payload(item: RenderableGeometry) -> object:
    return item.payload if item.payload is not None else item.source


def _pad_drill_geometry(payload: object, *, layer_name: str | None) -> BaseGeometry | None:
    if not isinstance(payload, PcbPadGeometry) or payload.drill <= 0:
        return None
    _ = layer_name
    return pad_drill_geometry(payload)


def _via_drill_geometry(payload: object, *, layer_name: str | None) -> BaseGeometry | None:
    if not isinstance(payload, PcbViaGeometry) or payload.drill <= 0:
        return None
    _ = layer_name
    _copper, drill = via_geometry(payload)
    return drill


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
    outline_payload = _outline_payload(payload)
    if outline_payload is not None:
        return board_outline_polygon(outline_payload)
    if isinstance(item.source, tuple):
        outline_payload = _outline_payload(cast("tuple[object, ...]", item.source))
        if outline_payload is not None:
            return board_outline_polygon(outline_payload)
    return None


def _outline_payload(payload: object) -> list[DomainPcbGeometry] | None:
    if not isinstance(payload, tuple):
        return None
    payload_tuple = cast("tuple[object, ...]", payload)
    if not all(isinstance(item, DomainPcbGeometry) for item in payload_tuple):
        return None
    return list(cast("tuple[DomainPcbGeometry, ...]", payload_tuple))


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
