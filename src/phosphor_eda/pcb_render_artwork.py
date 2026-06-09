"""Artwork selection and derived PCB render layer data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shapely import GeometryCollection
from shapely.ops import unary_union

from phosphor_eda.pcb import LayerRole, PcbBoardProfile, PcbDrill, PcbLayer
from phosphor_eda.pcb_render_drills import drill_geometry
from phosphor_eda.pcb_render_inventory import (
    InventoryItem,
    InventoryItemKind,
    InventoryPurpose,
    PcbRenderInventory,
    inventory_item_matches_rule,
    select_inventory_items,
)
from phosphor_eda.pcb_render_primitives import (
    inventory_item_to_svg_primitive,
    pad_solder_mask_opening_primitive,
)
from phosphor_eda.sql.geometry import board_outline_polygon

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from shapely.geometry.base import BaseGeometry

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
    inventory: PcbRenderInventory,
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str = "",
) -> tuple[InventoryItem, ...]:
    """Select typed inventory items using source-layer rules."""
    return select_inventory_items(inventory, tuple(rules), active_side=active_side)


def selected_copper_layers(
    inventory: PcbRenderInventory,
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str,
) -> tuple[PcbLayer, ...]:
    """Return concrete copper layers selected by render settings."""
    selected: dict[str, PcbLayer] = {}
    for item in inventory.items:
        if item.layer is None or not item.layer.has_role(LayerRole.COPPER):
            continue
        if any(inventory_item_matches_rule(item, rule, active_side=active_side) for rule in rules):
            selected[item.layer.name] = item.layer
    return tuple(selected.values())


def board_profile_shape(inventory: PcbRenderInventory) -> BaseGeometry:
    """Return board profile geometry from inventory."""
    profile_items = [
        item
        for item in inventory.items
        if item.item_kind == InventoryItemKind.BOARD_PROFILE
        and item.purpose == InventoryPurpose.BOARD_MATERIAL
    ]
    for item in profile_items:
        if not isinstance(item.source, PcbBoardProfile):
            continue
        polygon = board_outline_polygon(item.source)
        if polygon is not None:
            return polygon
    return GeometryCollection()


def drill_shape_for_layer(
    inventory: PcbRenderInventory,
    *,
    layer_name: str | None = None,
) -> BaseGeometry:
    """Return drill geometry relevant to a source layer."""
    _ = layer_name
    geometries = [
        geometry
        for item in inventory.items
        if item.item_kind == InventoryItemKind.DRILL
        and isinstance(item.source, PcbDrill)
        and (geometry := drill_geometry(item.source)) is not None
    ]
    return GeometryCollection() if not geometries else unary_union(geometries)


def solder_mask_opening_primitives(
    inventory: PcbRenderInventory,
    *,
    side: str,
) -> tuple[SvgPrimitive, ...]:
    """Return source-derived solder-mask openings."""
    primitives: list[SvgPrimitive] = []
    for item in inventory.items:
        if item.purpose == InventoryPurpose.SOLDER_MASK:
            primitive = inventory_item_to_svg_primitive(item)
        elif item.item_kind == InventoryItemKind.PAD:
            primitive = pad_solder_mask_opening_primitive(item, side=side)
        else:
            primitive = None
        if primitive is not None:
            primitives.append(primitive)
    return tuple(primitives)
