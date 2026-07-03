"""Convert extracted Allegro primitives into PCB domain objects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfileElement,
    PcbCircle,
    PcbClosedPath,
    PcbConductor,
    PcbKeepout,
    PcbLine,
    PcbPolygon,
    PcbPour,
    PcbPourFillMode,
    PcbPourSettings,
    artwork_purpose_for_layer,
)
from phosphor_eda.formats.allegro.primitives import (
    AllegroConductorPrimitive,
    AllegroGraphicPrimitive,
    AllegroPourPrimitive,
    AllegroPrimitiveKind,
    AllegroPrimitiveRole,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.domain.pcb import PcbFootprint, PcbNet


def pour_from_primitive(
    primitive: AllegroPourPrimitive,
    *,
    nets_by_key: Mapping[int, PcbNet],
) -> PcbPour:
    return PcbPour(
        id=primitive.id,
        boundary=PcbClosedPath.from_points(primitive.boundary.points),
        layers=(primitive.layer,),
        net=nets_by_key.get(primitive.net_key) if primitive.net_key is not None else None,
        settings=PcbPourSettings(fill_mode=_pour_fill_mode(primitive)),
        footprint=None,
        metadata=primitive.metadata,
    )


def _pour_fill_mode(primitive: AllegroPourPrimitive) -> PcbPourFillMode:
    properties = primitive.metadata.properties
    if (
        "native_first_keepout_key" in properties and "native_void_hole_count" not in properties
    ) or properties.get("dynamic_shape_degraded") == "true":
        return PcbPourFillMode.UNKNOWN
    return PcbPourFillMode.SOLID


def conductor_from_primitive(
    primitive: AllegroConductorPrimitive,
    *,
    nets_by_key: Mapping[int, PcbNet],
    footprints_by_instance_key: Mapping[int, PcbFootprint],
    pours_by_id: Mapping[str, PcbPour],
) -> PcbConductor:
    return PcbConductor(
        id=primitive.id,
        kind=primitive.kind,
        layer=primitive.layer,
        data=primitive.data,
        net=nets_by_key.get(primitive.net_key) if primitive.net_key is not None else None,
        footprint=(
            footprints_by_instance_key.get(primitive.footprint_key)
            if primitive.footprint_key is not None
            else None
        ),
        pour=pours_by_id.get(primitive.pour_id) if primitive.pour_id is not None else None,
        metadata=primitive.metadata,
    )


def profile_element_from_primitive(
    primitive: AllegroGraphicPrimitive,
) -> PcbBoardProfileElement:
    if not isinstance(primitive.data, PcbLine | PcbArc | PcbCircle | PcbPolygon):
        msg = f"board profile primitive {primitive.id} has unsupported data"
        raise ValueError(msg)
    return PcbBoardProfileElement(
        id=primitive.id,
        kind=_artwork_kind(primitive.kind),
        layer=primitive.layer,
        data=primitive.data,
        is_cutout=primitive.is_cutout,
        metadata=primitive.metadata,
    )


def artwork_from_primitive(primitive: AllegroGraphicPrimitive) -> PcbArtwork:
    return PcbArtwork(
        id=primitive.id,
        kind=_artwork_kind(primitive.kind),
        purpose=_artwork_purpose(primitive),
        layer=primitive.layer,
        data=primitive.data,
        metadata=primitive.metadata,
    )


def keepout_from_primitive(primitive: AllegroGraphicPrimitive) -> PcbKeepout:
    if not isinstance(primitive.data, PcbPolygon):
        msg = f"keepout primitive {primitive.id} has unsupported data"
        raise ValueError(msg)
    if primitive.layer is None:
        msg = f"keepout primitive {primitive.id} has no resolved layer"
        raise ValueError(msg)
    return PcbKeepout(
        id=primitive.id,
        boundary=PcbClosedPath.from_points(primitive.data.points),
        layers=(primitive.layer,),
        metadata=primitive.metadata,
    )


def _artwork_kind(kind: AllegroPrimitiveKind) -> PcbArtworkKind:
    if kind is AllegroPrimitiveKind.LINE:
        return PcbArtworkKind.LINE
    if kind is AllegroPrimitiveKind.ARC:
        return PcbArtworkKind.ARC
    if kind is AllegroPrimitiveKind.CIRCLE:
        return PcbArtworkKind.CIRCLE
    if kind is AllegroPrimitiveKind.TEXT:
        return PcbArtworkKind.TEXT
    return PcbArtworkKind.POLYGON


def _artwork_purpose(primitive: AllegroGraphicPrimitive) -> PcbArtworkPurpose:
    purpose = artwork_purpose_for_layer(primitive.layer)
    if purpose is not None:
        return purpose
    if primitive.has_role(AllegroPrimitiveRole.TEXT):
        return PcbArtworkPurpose.USER_TEXT
    return PcbArtworkPurpose.MECHANICAL
