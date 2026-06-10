"""Typed renderer inventory built from the strict PCB domain model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    LayerRole,
    Pcb,
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbCircle,
    PcbClosedPath,
    PcbConductor,
    PcbConductorKind,
    PcbDimension,
    PcbDrill,
    PcbKeepout,
    PcbLayer,
    PcbLine,
    PcbModel3D,
    PcbPad,
    PcbPolygon,
    PcbText,
    PcbVia,
)

if TYPE_CHECKING:
    from phosphor_eda.render.settings import LayerSelectionRule

type RenderPoint = tuple[float, float]
type InventorySource = (
    Pcb
    | PcbBoardProfile
    | PcbBoardProfileElement
    | PcbPad
    | PcbVia
    | PcbDrill
    | PcbConductor
    | PcbArtwork
    | PcbKeepout
)


class InventoryItemKind(StrEnum):
    BOARD_PROFILE = "board_profile"
    PAD = "pad"
    VIA = "via"
    DRILL = "drill"
    CONDUCTOR = "conductor"
    ARTWORK = "artwork"
    KEEPOUT = "keepout"


class InventoryPurpose(StrEnum):
    BOARD_MATERIAL = "board_material"
    BOARD_PROFILE = "board_profile"
    COPPER = "copper"
    SOLDER_MASK = "solder_mask"
    SOLDER_PASTE = "solder_paste"
    DRILL = "drill"
    KEEPOUT = "keepout"
    SILKSCREEN = "silkscreen"
    FABRICATION = "fabrication"
    ASSEMBLY = "assembly"
    COURTYARD = "courtyard"
    DESIGNATOR = "designator"
    VALUE = "value"
    USER_TEXT = "user_text"
    MECHANICAL = "mechanical"
    COMPONENT_BODY = "component_body"
    DIMENSION = "dimension"
    USER = "user"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InventoryTags:
    source_collection: str = ""
    source_index: int | None = None
    component_ref: str = ""
    component_prefix: str = ""
    pad_number: str = ""
    net_number: int | None = None
    net_name: str = ""
    text_kind: str = ""
    footprint_lib: str = ""
    value: str = ""


@dataclass(frozen=True)
class InventoryItem:
    id: str
    item_kind: InventoryItemKind
    purpose: InventoryPurpose
    content_kind: PcbArtworkKind | PcbConductorKind | None
    layer: PcbLayer | None
    source: InventorySource
    payload: object
    tags: InventoryTags
    points: tuple[RenderPoint, ...] = ()
    bbox: tuple[float, float, float, float] | None = None
    clipped: bool = True


@dataclass(frozen=True)
class PcbRenderInventory:
    items: tuple[InventoryItem, ...]
    side: str = ""


def build_inventory(board: Pcb, *, side: str = "") -> PcbRenderInventory:
    """Build typed render inventory from domain collections."""
    items: list[InventoryItem] = []

    if board.board_profile is not None:
        items.append(
            InventoryItem(
                id="board:material",
                item_kind=InventoryItemKind.BOARD_PROFILE,
                purpose=InventoryPurpose.BOARD_MATERIAL,
                content_kind=None,
                layer=None,
                source=board.board_profile,
                payload=board.board_profile,
                tags=InventoryTags(source_collection="board_profile"),
            )
        )
        for index, element in enumerate(board.board_profile.elements):
            items.append(
                InventoryItem(
                    id=element.id,
                    item_kind=InventoryItemKind.BOARD_PROFILE,
                    purpose=InventoryPurpose.BOARD_PROFILE,
                    content_kind=element.kind,
                    layer=element.layer,
                    source=element,
                    payload=element.data,
                    tags=InventoryTags(
                        source_collection="board_profile",
                        source_index=index,
                    ),
                    points=_points_for_payload(element.data),
                )
            )

    for index, pad in enumerate(board.pads):
        for purpose, layer in _pad_inventory_layers(board, pad):
            items.append(
                InventoryItem(
                    id=_layered_id(pad.id, layer, purpose),
                    item_kind=InventoryItemKind.PAD,
                    purpose=purpose,
                    content_kind=None,
                    layer=layer,
                    source=pad,
                    payload=pad,
                    tags=_tags_for_pad(pad, index),
                    points=((pad.x, pad.y),),
                )
            )

    for index, via in enumerate(board.vias):
        for layer in via.layers:
            if not layer.has_role(LayerRole.COPPER):
                continue
            items.append(
                InventoryItem(
                    id=_layered_id(via.id, layer, InventoryPurpose.COPPER),
                    item_kind=InventoryItemKind.VIA,
                    purpose=InventoryPurpose.COPPER,
                    content_kind=None,
                    layer=layer,
                    source=via,
                    payload=via,
                    tags=_tags_for_via(via, index),
                    points=((via.x, via.y),),
                )
            )
        for layer in _via_solder_mask_layers(board, via):
            items.append(
                InventoryItem(
                    id=_layered_id(via.id, layer, InventoryPurpose.SOLDER_MASK),
                    item_kind=InventoryItemKind.VIA,
                    purpose=InventoryPurpose.SOLDER_MASK,
                    content_kind=None,
                    layer=layer,
                    source=via,
                    payload=via,
                    tags=_tags_for_via(via, index),
                    points=((via.x, via.y),),
                )
            )

    for index, drill in enumerate(board.drills):
        items.append(
            InventoryItem(
                id=drill.id,
                item_kind=InventoryItemKind.DRILL,
                purpose=InventoryPurpose.DRILL,
                content_kind=None,
                layer=None,
                source=drill,
                payload=drill,
                tags=_tags_for_drill(drill, index),
                points=((drill.x, drill.y),),
                clipped=False,
            )
        )

    for index, conductor in enumerate(board.conductors):
        items.append(
            InventoryItem(
                id=conductor.id,
                item_kind=InventoryItemKind.CONDUCTOR,
                purpose=InventoryPurpose.COPPER,
                content_kind=conductor.kind,
                layer=conductor.layer,
                source=conductor,
                payload=conductor.data,
                tags=_tags_for_conductor(conductor, index),
                points=_points_for_payload(conductor.data),
            )
        )

    for index, artwork in enumerate(board.artwork):
        items.append(
            InventoryItem(
                id=artwork.id,
                item_kind=InventoryItemKind.ARTWORK,
                purpose=_purpose_for_artwork(artwork.purpose),
                content_kind=artwork.kind,
                layer=artwork.layer,
                source=artwork,
                payload=artwork.data,
                tags=_tags_for_artwork(artwork, index),
                points=_points_for_payload(artwork.data),
            )
        )

    for index, keepout in enumerate(board.keepouts):
        layers = keepout.layers or (None,)
        for layer in layers:
            items.append(
                InventoryItem(
                    id=_layered_id(keepout.id, layer, InventoryPurpose.KEEPOUT),
                    item_kind=InventoryItemKind.KEEPOUT,
                    purpose=InventoryPurpose.KEEPOUT,
                    content_kind=None,
                    layer=layer,
                    source=keepout,
                    payload=keepout.boundary,
                    tags=_tags_for_keepout(keepout, index),
                    points=keepout.boundary.points,
                )
            )

    return PcbRenderInventory(
        items=tuple(item for item in items if not _source_is_hidden(item.source)),
        side=side,
    )


def _source_is_hidden(source: InventorySource) -> bool:
    if isinstance(source, PcbDrill):
        owner = source.owner
        return source.metadata.hidden or (owner is not None and owner.metadata.hidden)
    if isinstance(
        source,
        PcbBoardProfileElement | PcbPad | PcbVia | PcbConductor | PcbArtwork | PcbKeepout,
    ):
        return source.metadata.hidden
    return False


def select_inventory_items(
    inventory: PcbRenderInventory,
    rules: tuple[LayerSelectionRule, ...] | list[LayerSelectionRule],
    *,
    active_side: str,
) -> tuple[InventoryItem, ...]:
    """Select inventory items using typed source-layer rules."""
    active_rules = tuple(rule for rule in rules if rule.visible)
    if not active_rules:
        return ()
    return tuple(
        item
        for item in inventory.items
        if any(
            inventory_item_matches_rule(item, rule, active_side=active_side)
            for rule in active_rules
        )
    )


def inventory_item_matches_rule(
    item: InventoryItem,
    rule: LayerSelectionRule,
    *,
    active_side: str,
) -> bool:
    if not rule.visible:
        return False
    if rule.match.name and (item.layer is None or item.layer.name != rule.match.name):
        return False
    if rule.match.role and not _item_matches_role(item, rule.match.role):
        return False
    match_side = active_side if rule.match.side == "active" else rule.match.side
    if match_side and (item.layer is None or item.layer.side != match_side):
        return False
    if rule.item_kinds and item.item_kind.value not in rule.item_kinds:
        return False
    if rule.purposes and item.purpose.value not in rule.purposes:
        return False
    if rule.content_kinds:
        if item.content_kind is None:
            return False
        if item.content_kind.value not in rule.content_kinds:
            return False
    return True


def _item_matches_role(item: InventoryItem, role: str) -> bool:
    if item.layer is not None:
        try:
            if item.layer.has_role(LayerRole(role)):
                return True
        except ValueError:
            pass
    return item.purpose.value == role


def _purpose_for_pad_layer(layer: PcbLayer) -> InventoryPurpose | None:
    if layer.has_role(LayerRole.COPPER):
        return InventoryPurpose.COPPER
    if layer.has_role(LayerRole.SOLDER_MASK):
        return InventoryPurpose.SOLDER_MASK
    if layer.has_role(LayerRole.SOLDER_PASTE):
        return InventoryPurpose.SOLDER_PASTE
    return None


def _pad_inventory_layers(board: Pcb, pad: PcbPad) -> tuple[tuple[InventoryPurpose, PcbLayer], ...]:
    pairs: list[tuple[InventoryPurpose, PcbLayer]] = []
    seen: set[tuple[InventoryPurpose, int]] = set()
    for layer in pad.layers:
        purpose = _purpose_for_pad_layer(layer)
        if purpose is None:
            continue
        key = (purpose, id(layer))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((purpose, layer))
    if _pad_has_solder_mask_intent(pad):
        for layer in _solder_mask_layers_for_sides(board, _pad_solder_mask_sides(pad)):
            key = (InventoryPurpose.SOLDER_MASK, id(layer))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((InventoryPurpose.SOLDER_MASK, layer))
    return tuple(pairs)


def _pad_has_solder_mask_intent(pad: PcbPad) -> bool:
    aperture = pad.mask_aperture
    return (
        aperture is not None
        and (
            aperture.aperture_width is not None
            or aperture.aperture_height is not None
            or aperture.mask_expansion is not None
        )
    ) or pad.drill is not None


def _pad_solder_mask_sides(pad: PcbPad) -> set[str]:
    copper_sides = {
        layer.side for layer in pad.layers if layer.has_role(LayerRole.COPPER) and layer.side
    }
    if pad.drill is not None or pad.pad_type == "through_hole" or len(copper_sides) != 1:
        return {"front", "back"}
    return copper_sides


def _via_solder_mask_layers(board: Pcb, via: PcbVia) -> tuple[PcbLayer, ...]:
    sides: set[str] = set()
    copper_sides = {
        layer.side for layer in via.layers if layer.has_role(LayerRole.COPPER) and layer.side
    }
    if "front" in copper_sides and not via.tented_front:
        sides.add("front")
    if "back" in copper_sides and not via.tented_back:
        sides.add("back")
    return _solder_mask_layers_for_sides(board, sides)


def _solder_mask_layers_for_sides(board: Pcb, sides: set[str]) -> tuple[PcbLayer, ...]:
    if not sides:
        return ()
    return tuple(
        layer
        for layer in board.layers
        if layer.has_role(LayerRole.SOLDER_MASK) and layer.side in sides
    )


def _purpose_for_artwork(purpose: PcbArtworkPurpose) -> InventoryPurpose:
    try:
        return InventoryPurpose(purpose.value)
    except ValueError:
        return InventoryPurpose.UNKNOWN


def _tags_for_pad(pad: PcbPad, index: int) -> InventoryTags:
    footprint = pad.footprint
    net = pad.net
    return InventoryTags(
        source_collection="pads",
        source_index=index,
        component_ref="" if footprint is None else footprint.reference,
        component_prefix="" if footprint is None else _component_prefix(footprint.reference),
        pad_number=pad.number,
        net_number=None if net is None else net.number,
        net_name="" if net is None else net.name,
        footprint_lib="" if footprint is None else footprint.footprint_lib,
        value="" if footprint is None else footprint.value,
    )


def _tags_for_via(via: PcbVia, index: int) -> InventoryTags:
    net = via.net
    return InventoryTags(
        source_collection="vias",
        source_index=index,
        net_number=None if net is None else net.number,
        net_name="" if net is None else net.name,
    )


def _tags_for_drill(drill: PcbDrill, index: int) -> InventoryTags:
    owner = drill.owner
    if isinstance(owner, PcbPad):
        return _tags_for_pad(owner, index)
    if isinstance(owner, PcbVia):
        return _tags_for_via(owner, index)
    return InventoryTags(source_collection="drills", source_index=index)


def _tags_for_conductor(conductor: PcbConductor, index: int) -> InventoryTags:
    footprint = conductor.footprint
    net = conductor.net
    return InventoryTags(
        source_collection="conductors",
        source_index=index,
        component_ref="" if footprint is None else footprint.reference,
        component_prefix="" if footprint is None else _component_prefix(footprint.reference),
        net_number=None if net is None else net.number,
        net_name="" if net is None else net.name,
        footprint_lib="" if footprint is None else footprint.footprint_lib,
        value="" if footprint is None else footprint.value,
    )


def _tags_for_artwork(artwork: PcbArtwork, index: int) -> InventoryTags:
    footprint = artwork.footprint
    text_kind = artwork.purpose.value if artwork.kind == PcbArtworkKind.TEXT else ""
    return InventoryTags(
        source_collection="artwork",
        source_index=index,
        component_ref="" if footprint is None else footprint.reference,
        component_prefix="" if footprint is None else _component_prefix(footprint.reference),
        text_kind=text_kind,
        footprint_lib="" if footprint is None else footprint.footprint_lib,
        value="" if footprint is None else footprint.value,
    )


def _tags_for_keepout(keepout: PcbKeepout, index: int) -> InventoryTags:
    footprint = keepout.footprint
    return InventoryTags(
        source_collection="keepouts",
        source_index=index,
        component_ref="" if footprint is None else footprint.reference,
        component_prefix="" if footprint is None else _component_prefix(footprint.reference),
    )


def _component_prefix(reference: str) -> str:
    return reference.rstrip("0123456789") or reference


def _layered_id(
    object_id: str,
    layer: PcbLayer | None,
    purpose: InventoryPurpose,
) -> str:
    layer_part = "none" if layer is None else layer.name.replace(" ", "_")
    return f"{object_id}:{purpose.value}:{layer_part}"


def _points_for_payload(payload: object) -> tuple[RenderPoint, ...]:
    if isinstance(payload, PcbClosedPath):
        return payload.points
    if isinstance(payload, PcbPolygon):
        return tuple(payload.points)
    if isinstance(payload, PcbLine):
        return (
            (payload.start_x, payload.start_y),
            (payload.end_x, payload.end_y),
        )
    if isinstance(payload, PcbArc):
        return (
            (payload.start_x, payload.start_y),
            (payload.mid_x, payload.mid_y),
            (payload.end_x, payload.end_y),
        )
    if isinstance(payload, PcbCircle):
        return ((payload.cx, payload.cy),)
    if isinstance(payload, PcbText):
        return ((payload.x, payload.y),)
    if isinstance(payload, PcbDimension):
        return (
            (payload.start_x, payload.start_y),
            (payload.end_x, payload.end_y),
        )
    if isinstance(payload, PcbModel3D):
        return ((payload.offset[0], payload.offset[1]),)
    return ()
