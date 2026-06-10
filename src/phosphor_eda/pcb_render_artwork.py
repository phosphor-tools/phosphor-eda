"""Artwork selection and derived PCB render layer data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.pcb import PcbPad, PcbVia
from phosphor_eda.pcb_render_inventory import (
    InventoryItem,
    InventoryItemKind,
    InventoryPurpose,
    PcbRenderInventory,
)
from phosphor_eda.pcb_render_primitives import (
    inventory_item_to_svg_primitive,
    pad_solder_mask_opening_primitive,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.pcb_render_primitives import LayerClip, LayerMask, SvgPrimitive
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


def solder_mask_opening_primitives(
    inventory: PcbRenderInventory,
    *,
    side: str,
) -> tuple[SvgPrimitive, ...]:
    """Return source-derived solder-mask openings."""
    primitives: list[SvgPrimitive] = []
    explicit_sources: set[tuple[InventoryItemKind, str, str]] = set()
    for item in inventory.items:
        if item.purpose == InventoryPurpose.SOLDER_MASK:
            if item.layer is not None and item.layer.side not in {"", side}:
                continue
            primitive = inventory_item_to_svg_primitive(item)
            explicit_sources.add((item.item_kind, _mask_source_id(item), side))
        else:
            primitive = None
        if primitive is not None:
            primitives.append(primitive)
    for item in inventory.items:
        if item.item_kind != InventoryItemKind.PAD:
            continue
        if (item.item_kind, _mask_source_id(item), side) in explicit_sources:
            continue
        primitive = pad_solder_mask_opening_primitive(item, side=side)
        if primitive is not None:
            primitives.append(primitive)
    return tuple(primitives)


def _mask_source_id(item: InventoryItem) -> str:
    if isinstance(item.source, (PcbPad, PcbVia)):
        return item.source.id
    return item.id
