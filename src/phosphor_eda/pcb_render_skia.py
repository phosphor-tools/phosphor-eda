"""Typed render path adapter.

The current renderer no longer exposes a generic geometry wrapper.  This
module keeps a narrow adapter for callers that want one path-shaped artifact
from an inventory item without reintroducing display-role dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.pcb_render_primitives import inventory_item_to_svg_primitive

if TYPE_CHECKING:
    from phosphor_eda.pcb_render_inventory import InventoryItem, InventoryTags

SKIA_CONIC_TO_QUAD_TOLERANCE = 0.02


@dataclass(frozen=True)
class SkiaArtwork:
    path: str
    source_ids: tuple[str, ...]
    source_layers: tuple[str, ...]
    tags: InventoryTags


def inventory_item_to_skia_artwork(item: InventoryItem) -> SkiaArtwork | None:
    """Convert one inventory item into path artwork."""
    primitive = inventory_item_to_svg_primitive(item)
    if primitive is None:
        return None
    return SkiaArtwork(
        path=primitive.d,
        source_ids=(primitive.source_id,),
        source_layers=(primitive.source_layer,),
        tags=primitive.tags,
    )


def skia_path_to_svg_d(path: str) -> str:
    """Serialize a path artifact to SVG path data."""
    return path
