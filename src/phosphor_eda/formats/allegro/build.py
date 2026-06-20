"""Domain assembly helpers for Allegro board primitives."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    Board,
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbCircle,
    PcbClosedPath,
    PcbKeepout,
    PcbLine,
    PcbMetadata,
    PcbPolygon,
)
from phosphor_eda.domain.pcb_builder import PcbBuilder
from phosphor_eda.formats.allegro.graphics import extract_allegro_graphics
from phosphor_eda.formats.allegro.layers import build_allegro_layers
from phosphor_eda.formats.allegro.primitives import (
    AllegroGraphicPrimitive,
    AllegroPrimitiveKind,
    AllegroPrimitiveRole,
)

if TYPE_CHECKING:
    from phosphor_eda.formats.allegro.records import AllegroRecordSet


def build_allegro_graphics_board(record_set: AllegroRecordSet, *, name: str) -> Board:
    """Assemble the PR04 graphics subset into a strict PCB ``Board``."""
    layer_map = build_allegro_layers(record_set)
    graphics = extract_allegro_graphics(record_set, layer_map)
    metadata = PcbMetadata(source_format="allegro")
    if graphics.diagnostics:
        metadata.properties["parse_diagnostic_count"] = str(len(graphics.diagnostics))

    builder = PcbBuilder(name, metadata=metadata)
    for layer in layer_map.layers:
        builder.add_layer(layer, source="allegro layers")

    profile_elements = tuple(_profile_element(primitive) for primitive in graphics.board_profile)
    builder.set_board_profile(PcbBoardProfile(elements=profile_elements), source="allegro profile")

    for primitive in graphics.artwork:
        builder.add_artwork_object(_artwork(primitive), source=primitive.id)
    for primitive in graphics.keepouts:
        builder.add_keepout_object(_keepout(primitive), source=primitive.id)

    board = builder.build(require_board_profile=True)
    board.stackup = layer_map.stackup
    return board


def _profile_element(primitive: AllegroGraphicPrimitive) -> PcbBoardProfileElement:
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


def _artwork(primitive: AllegroGraphicPrimitive) -> PcbArtwork:
    return PcbArtwork(
        id=primitive.id,
        kind=_artwork_kind(primitive.kind),
        purpose=_artwork_purpose(primitive),
        layer=primitive.layer,
        data=primitive.data,
        metadata=primitive.metadata,
    )


def _keepout(primitive: AllegroGraphicPrimitive) -> PcbKeepout:
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
    if kind is AllegroPrimitiveKind.TEXT:
        return PcbArtworkKind.TEXT
    return PcbArtworkKind.POLYGON


def _artwork_purpose(primitive: AllegroGraphicPrimitive) -> PcbArtworkPurpose:
    if primitive.has_role(AllegroPrimitiveRole.TEXT):
        return PcbArtworkPurpose.USER_TEXT
    return PcbArtworkPurpose.MECHANICAL
