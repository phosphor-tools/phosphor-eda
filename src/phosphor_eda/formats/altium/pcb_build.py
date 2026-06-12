"""Domain assembly for the Altium PCB parser.

Turns the intermediate ``ParsedPrimitive`` list into a domain ``Board`` via
``PcbBuilder``: routes each primitive to the right collection (pad, via,
conductor, artwork, board-profile element), resolves owner footprints and
parent pours, and derives artwork visibility from footprint metadata.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    Board,
    LayerRole,
    PadStack,
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbCircle,
    PcbConductor,
    PcbConductorKind,
    PcbDrill,
    PcbDrillPlating,
    PcbDrillShape,
    PcbFootprint,
    PcbKeepout,
    PcbLayer,
    PcbLine,
    PcbMaskAperture,
    PcbMetadata,
    PcbModel3D,
    PcbNet,
    PcbObjectMetadata,
    PcbPad,
    PcbPadType,
    PcbPolygon,
    PcbPour,
    PcbText,
    PcbVia,
    PcbViaType,
)
from phosphor_eda.domain.pcb_builder import PcbBuilder
from phosphor_eda.formats.altium.errors import AltiumPcbParseError
from phosphor_eda.formats.altium.pcb_primitives import (
    ParsedObjectKind,
    ParsedPadPayload,
    ParsedPrimitive,
    ParsedRole,
    ParsedShapeKind,
    ParsedViaPayload,
)

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext


def compute_bbox(
    pads: list[ParsedPrimitive],
) -> tuple[float, float, float, float] | None:
    """Compute footprint bounding box from pads with 0.5mm margin."""
    pad_payloads = [pad.data for pad in pads if isinstance(pad.data, ParsedPadPayload)]
    if not pad_payloads:
        return None
    xs = [p.x - p.width / 2 for p in pad_payloads] + [p.x + p.width / 2 for p in pad_payloads]
    ys = [p.y - p.height / 2 for p in pad_payloads] + [p.y + p.height / 2 for p in pad_payloads]
    margin = 0.5
    return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def build_pcb_from_parsed_primitives(
    *,
    name: str,
    layer_map: dict[int, PcbLayer],
    nets: dict[int, PcbNet],
    footprints: list[PcbFootprint],
    pours: list[PcbPour],
    keepouts: list[PcbKeepout],
    primitives: list[ParsedPrimitive],
    ctx: ParseContext,
) -> Board:
    metadata = PcbMetadata(source_format="altium")
    if ctx.issues:
        metadata.properties["parse_issue_count"] = str(len(ctx.issues))
    builder = PcbBuilder(name, metadata=metadata)
    for layer in layer_map.values():
        builder.add_layer(layer, source="Board6/Data")
    for net in nets.values():
        builder.add_net(net, source="Nets6/Data")
    for footprint in footprints:
        builder.add_footprint(footprint, source=f"component {footprint.reference}")
    for pour in pours:
        builder.add_pour_object(pour, source=f"pour {pour.id}")

    # Index footprints by their native component index and pours by id once, so
    # primitive assembly resolves its owner footprint/pour in O(1) instead of
    # scanning the lists per primitive.
    footprints_by_index = dict(enumerate(footprints))
    pours_by_id = {pour.id: pour for pour in pours}

    board_profile_elements: list[PcbBoardProfileElement] = []
    pour_fills: dict[str, list[PcbConductor]] = {}
    for primitive in primitives:
        _add_parsed_primitive(
            builder,
            primitive,
            footprints_by_index=footprints_by_index,
            pours_by_id=pours_by_id,
            board_profile_elements=board_profile_elements,
            pour_fills=pour_fills,
        )
    for keepout in keepouts:
        builder.add_keepout_object(keepout, source=keepout.id)
    for pour in pours:
        pour.fills = tuple(pour_fills.get(pour.id, ()))
    builder.set_board_profile(
        PcbBoardProfile(elements=tuple(board_profile_elements)),
        source="board profile",
    )
    return builder.build(require_board_profile=True)


def _add_parsed_primitive(
    builder: PcbBuilder,
    primitive: ParsedPrimitive,
    *,
    footprints_by_index: dict[int, PcbFootprint],
    pours_by_id: dict[str, PcbPour],
    board_profile_elements: list[PcbBoardProfileElement],
    pour_fills: dict[str, list[PcbConductor]],
) -> None:
    if primitive.has_role(ParsedRole.BOARD_OUTLINE):
        element = _board_profile_element(builder, primitive)
        if element is not None:
            board_profile_elements.append(element)
        return
    if primitive.object_type == ParsedObjectKind.PAD and isinstance(
        primitive.data, ParsedPadPayload
    ):
        _add_parsed_pad(builder, primitive, primitive.data, footprints_by_index)
        return
    if primitive.object_type == ParsedObjectKind.VIA and isinstance(
        primitive.data, ParsedViaPayload
    ):
        _add_parsed_via(builder, primitive, primitive.data)
        return
    if _is_conductor_primitive(primitive):
        conductor = _parsed_conductor(builder, primitive, footprints_by_index, pours_by_id)
        if conductor is not None:
            builder.add_conductor_object(conductor, source=primitive.id)
            if conductor.pour is not None:
                pour_fills.setdefault(conductor.pour.id, []).append(conductor)
        return
    artwork = _parsed_artwork(builder, primitive, footprints_by_index)
    if artwork is not None:
        builder.add_artwork_object(artwork, source=primitive.id)


def _add_parsed_pad(
    builder: PcbBuilder,
    primitive: ParsedPrimitive,
    pad: ParsedPadPayload,
    footprints_by_index: dict[int, PcbFootprint],
) -> None:
    layers = _parsed_layer_refs(builder, primitive.layers, source=primitive.id)
    drill = None
    if pad.drill > 0:
        if primitive.has_role(ParsedRole.PLATED_HOLE):
            plating = PcbDrillPlating.PLATED
        elif pad.hole_plated is False:
            plating = PcbDrillPlating.NON_PLATED
        else:
            plating = PcbDrillPlating.UNKNOWN
        drill = builder.add_drill_object(
            PcbDrill(
                id=f"drill:{primitive.id}",
                x=pad.x,
                y=pad.y,
                diameter=pad.drill,
                shape=PcbDrillShape.SLOT if pad.hole_is_slot else PcbDrillShape.ROUND,
                plating=plating,
                width=pad.slot_length if pad.hole_is_slot else 0.0,
                height=pad.drill if pad.hole_is_slot else 0.0,
                rotation=pad.slot_rotation if pad.hole_is_slot else pad.rotation,
                layers=layers,
                metadata=primitive.metadata,
            ),
            source=primitive.id,
        )
    mask_aperture = None
    if pad.mask_aperture_width is not None or pad.mask_aperture_height is not None:
        mask_aperture = PcbMaskAperture(
            aperture_width=pad.mask_aperture_width,
            aperture_height=pad.mask_aperture_height,
            source=pad.mask_aperture_source,
        )
    builder.add_pad_object(
        PcbPad(
            id=primitive.id,
            number=pad.number,
            x=pad.x,
            y=pad.y,
            stack=PadStack.simple(
                pad.shape,
                pad.width,
                pad.height,
                corner_radius_ratio=pad.roundrect_rratio,
            ),
            pad_type=PcbPadType.THROUGH_HOLE if drill is not None else PcbPadType.SMD,
            layers=layers,
            net=_net_from_parsed_number(builder, primitive.net_number, primitive.id),
            footprint=_footprint_for_primitive(primitive, footprints_by_index),
            drill=drill,
            rotation=pad.rotation,
            mask_aperture=mask_aperture,
            metadata=primitive.metadata,
        ),
        source=primitive.id,
    )


def _add_parsed_via(
    builder: PcbBuilder,
    primitive: ParsedPrimitive,
    via: ParsedViaPayload,
) -> None:
    layers = _parsed_layer_refs(builder, primitive.layers, source=primitive.id)
    drill = builder.add_drill_object(
        PcbDrill(
            id=f"drill:{primitive.id}",
            x=via.x,
            y=via.y,
            diameter=via.drill,
            shape=PcbDrillShape.ROUND,
            plating=PcbDrillPlating.PLATED,
            layers=layers,
            metadata=primitive.metadata,
        ),
        source=primitive.id,
    )
    builder.add_via_object(
        PcbVia(
            id=primitive.id,
            x=via.x,
            y=via.y,
            stack=PadStack.simple("circle", via.size, via.size),
            layers=layers,
            drill=drill,
            net=_net_from_parsed_number(builder, primitive.net_number, primitive.id),
            via_type=_parsed_via_type(primitive),
            metadata=primitive.metadata,
        ),
        source=primitive.id,
    )


def _is_conductor_primitive(primitive: ParsedPrimitive) -> bool:
    return (
        primitive.object_type in {ParsedObjectKind.TRACK, ParsedObjectKind.REGION}
        and primitive.has_role(ParsedRole.CONDUCTOR)
        and not primitive.has_role(ParsedRole.POLYGON_CUTOUT)
    )


def _parsed_conductor(
    builder: PcbBuilder,
    primitive: ParsedPrimitive,
    footprints_by_index: dict[int, PcbFootprint],
    pours_by_id: dict[str, PcbPour],
) -> PcbConductor | None:
    if not isinstance(primitive.data, PcbLine | PcbArc | PcbCircle | PcbPolygon):
        return None
    layer = _primary_layer_ref(builder, primitive, source=primitive.id)
    pour = _pour_for_primitive(primitive, pours_by_id)
    if pour is not None:
        kind = PcbConductorKind.POUR_FILL
    elif isinstance(primitive.data, PcbArc):
        kind = PcbConductorKind.TRACE_ARC
    elif isinstance(primitive.data, PcbLine):
        kind = PcbConductorKind.TRACE
    else:
        kind = PcbConductorKind.COPPER_REGION
    return PcbConductor(
        id=primitive.id,
        kind=kind,
        layer=layer,
        data=primitive.data,
        net=_net_from_parsed_number(builder, primitive.net_number, primitive.id),
        footprint=_footprint_for_primitive(primitive, footprints_by_index),
        pour=pour,
        metadata=primitive.metadata,
    )


def _parsed_artwork(
    builder: PcbBuilder,
    primitive: ParsedPrimitive,
    footprints_by_index: dict[int, PcbFootprint],
) -> PcbArtwork | None:
    if not isinstance(
        primitive.data,
        PcbLine | PcbArc | PcbCircle | PcbPolygon | PcbText | PcbModel3D,
    ):
        return None
    layer = (
        None
        if not primitive.layers
        else _primary_layer_ref(builder, primitive, source=primitive.id)
    )
    footprint = _footprint_for_primitive(primitive, footprints_by_index)
    metadata = _artwork_metadata_for_visibility(primitive, footprint)
    return PcbArtwork(
        id=primitive.id,
        kind=_artwork_kind(primitive),
        purpose=_artwork_purpose(primitive, layer),
        layer=layer,
        data=primitive.data,
        footprint=footprint,
        metadata=metadata,
    )


def _board_profile_element(
    builder: PcbBuilder,
    primitive: ParsedPrimitive,
) -> PcbBoardProfileElement | None:
    if not isinstance(primitive.data, PcbLine | PcbArc | PcbCircle | PcbPolygon):
        return None
    return PcbBoardProfileElement(
        id=primitive.id,
        kind=_artwork_kind(primitive),
        layer=_primary_layer_ref(builder, primitive, source=primitive.id),
        data=primitive.data,
        metadata=primitive.metadata,
    )


def _parsed_layer_refs(
    builder: PcbBuilder,
    layer_names: tuple[str, ...],
    *,
    source: str,
) -> tuple[PcbLayer, ...]:
    layers: list[PcbLayer] = []
    for layer_name in layer_names:
        if layer_name == "*.Cu":
            selected = tuple(layer for layer in builder.layers if layer.has_role(LayerRole.COPPER))
        else:
            selected = (builder.resolve_layer(layer_name, source=source),)
        for layer in selected:
            if layer not in layers:
                layers.append(layer)
    return tuple(layers)


def _primary_layer_ref(builder: PcbBuilder, primitive: ParsedPrimitive, *, source: str) -> PcbLayer:
    layers = _parsed_layer_refs(builder, primitive.layers, source=source)
    if not layers:
        msg = f"{source}: primitive has no layer"
        raise AltiumPcbParseError(msg)
    return layers[0]


def _net_from_parsed_number(
    builder: PcbBuilder,
    net_number: int,
    source: str,
) -> PcbNet | None:
    return None if net_number == 0 else builder.resolve_net_number(net_number, source=source)


def _footprint_for_primitive(
    primitive: ParsedPrimitive,
    footprints_by_index: dict[int, PcbFootprint],
) -> PcbFootprint | None:
    component_index = primitive.metadata.native_component_index
    if component_index is None:
        return None
    return footprints_by_index.get(component_index)


def _artwork_metadata_for_visibility(
    primitive: ParsedPrimitive,
    footprint: PcbFootprint | None,
) -> PcbObjectMetadata:
    if primitive.metadata.hidden or footprint is None:
        return primitive.metadata
    if primitive.has_role(ParsedRole.DESIGNATOR) and not _altium_component_text_visible(
        footprint, "nameon", default=True
    ):
        return replace(primitive.metadata, hidden=True)
    if primitive.has_role(ParsedRole.VALUE) and not _altium_component_text_visible(
        footprint, "commenton", default=False
    ):
        return replace(primitive.metadata, hidden=True)
    return primitive.metadata


def _altium_component_text_visible(
    footprint: PcbFootprint,
    key: str,
    *,
    default: bool,
) -> bool:
    raw = footprint.metadata.properties.get(key, "")
    if not raw:
        return default
    return raw.upper() in {"T", "TRUE", "1", "YES"}


def _pour_for_primitive(
    primitive: ParsedPrimitive, pours_by_id: dict[str, PcbPour]
) -> PcbPour | None:
    if not primitive.pour_id:
        return None
    return pours_by_id.get(primitive.pour_id)


def _parsed_via_type(primitive: ParsedPrimitive) -> PcbViaType:
    if primitive.has_role(ParsedRole.FREE_VIA):
        return PcbViaType.FREE
    if primitive.has_role(ParsedRole.BLIND_VIA):
        return PcbViaType.BLIND
    return PcbViaType.THROUGH


def _artwork_kind(primitive: ParsedPrimitive) -> PcbArtworkKind:
    if primitive.shape == ParsedShapeKind.LINE:
        return PcbArtworkKind.LINE
    if primitive.shape == ParsedShapeKind.ARC:
        return PcbArtworkKind.ARC
    if primitive.shape == ParsedShapeKind.CIRCLE:
        return PcbArtworkKind.CIRCLE
    if primitive.shape == ParsedShapeKind.TEXT:
        return PcbArtworkKind.TEXT
    if primitive.shape == ParsedShapeKind.MODEL:
        return PcbArtworkKind.MODEL_3D
    return PcbArtworkKind.POLYGON


def _artwork_purpose(
    primitive: ParsedPrimitive,
    layer: PcbLayer | None,
) -> PcbArtworkPurpose:
    if primitive.has_role(ParsedRole.DESIGNATOR):
        return PcbArtworkPurpose.DESIGNATOR
    if primitive.has_role(ParsedRole.VALUE):
        return PcbArtworkPurpose.VALUE
    if primitive.has_role(ParsedRole.USER_TEXT) or primitive.has_role(ParsedRole.TEXT):
        return PcbArtworkPurpose.USER_TEXT
    if primitive.has_role(ParsedRole.COMPONENT_BODY):
        return PcbArtworkPurpose.COMPONENT_BODY
    if primitive.has_role(ParsedRole.SILKSCREEN):
        return PcbArtworkPurpose.SILKSCREEN
    if primitive.has_role(ParsedRole.FABRICATION):
        return PcbArtworkPurpose.FABRICATION
    if primitive.has_role(ParsedRole.ASSEMBLY):
        return PcbArtworkPurpose.ASSEMBLY
    if primitive.has_role(ParsedRole.COURTYARD):
        return PcbArtworkPurpose.COURTYARD
    if primitive.has_role(ParsedRole.SOLDER_MASK):
        return PcbArtworkPurpose.SOLDER_MASK
    if primitive.has_role(ParsedRole.SOLDER_PASTE):
        return PcbArtworkPurpose.SOLDER_PASTE
    if primitive.has_role(ParsedRole.MECHANICAL):
        return PcbArtworkPurpose.MECHANICAL
    if layer is not None and layer.has_role(LayerRole.USER):
        return PcbArtworkPurpose.USER
    return PcbArtworkPurpose.UNKNOWN
