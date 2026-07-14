"""KiCad PCB footprint parsing: pads, drills, text, 3D models, graphics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sexpdata

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfileElement,
    PcbCircle,
    PcbDrill,
    PcbDrillPlating,
    PcbDrillShape,
    PcbFootprint,
    PcbFootprintMetadata,
    PcbLine,
    PcbModel3D,
    PcbPad,
    PcbPadType,
    PcbPolygon,
    PcbText,
)
from phosphor_eda.formats.common.diagnostics import warn_optional
from phosphor_eda.formats.kicad import graphics, pcb_common, sexp
from phosphor_eda.formats.kicad.errors import MALFORMED_PCB_ITEM
from phosphor_eda.formats.kicad.layers import resolve_layers
from phosphor_eda.formats.kicad.padstack import parse_pad_stack
from phosphor_eda.formats.kicad.zones import parse_zone_keepout

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import PcbLayer
    from phosphor_eda.domain.pcb_builder import PcbBuilder
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.kicad.sexp import SExpNode


@dataclass
class FootprintParseResult:
    footprint: PcbFootprint
    profile_elements: list[PcbBoardProfileElement]


def extract_reference(fp_sexpr: SExpNode) -> str:
    ref = sexp.find_property(fp_sexpr, "Reference")
    if ref:
        return ref
    for item in fp_sexpr:
        if isinstance(item, list) and sexp.tag(item) == "fp_text" and len(item) > 2:
            value = item[1]
            if isinstance(value, sexpdata.Symbol) and value.value() == "reference":
                return str(item[2])
    return "?"


def extract_value(fp_sexpr: SExpNode) -> str:
    value = sexp.find_property(fp_sexpr, "Value")
    if value:
        return value
    for item in fp_sexpr:
        if isinstance(item, list) and sexp.tag(item) == "fp_text" and len(item) > 2:
            kind = item[1]
            if isinstance(kind, sexpdata.Symbol) and kind.value() == "value":
                return str(item[2])
    return ""


def parse_fp_properties(fp_sexpr: SExpNode) -> dict[str, str]:
    builtin = {"Reference", "Value", "Footprint", "Datasheet", "Description"}
    properties: dict[str, str] = {}
    for item in sexp.find_all(fp_sexpr, "property"):
        if len(item) < 3:
            continue
        key = str(item[1])
        if key not in builtin:
            properties[key] = str(item[2])
    return properties


def parse_pad(
    builder: PcbBuilder,
    pad_sexpr: SExpNode,
    footprint: PcbFootprint,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    index: int,
    ctx: ParseContext | None = None,
) -> None:
    # A well-formed pad is (pad NUMBER TYPE SHAPE ...); a shorter node is
    # malformed. Skip it with a diagnostic rather than an IndexError.
    if len(pad_sexpr) < 4:
        warn_optional(
            ctx,
            MALFORMED_PCB_ITEM,
            f"Skipped pad {index} on footprint {footprint.reference}: node too short",
        )
        return
    global_pad_index = len(builder.pads)
    number = str(pad_sexpr[1])
    raw_pad_type = pad_sexpr[2]
    native_pad_type = (
        raw_pad_type.value() if isinstance(raw_pad_type, sexpdata.Symbol) else str(raw_pad_type)
    )
    raw_shape = pad_sexpr[3]
    shape = raw_shape.value() if isinstance(raw_shape, sexpdata.Symbol) else str(raw_shape)
    at_node = sexp.find(pad_sexpr, "at")
    local_x, local_y, pad_rotation = pcb_common.at(at_node) if at_node else (0.0, 0.0, 0.0)
    abs_x, abs_y = pcb_common.transform_point(local_x, local_y, fp_x, fp_y, fp_rot)
    # KiCad placed-board pad positions are footprint-local, but pad angles are
    # already board-space orientations. Adding footprint rotation rotates pads twice.
    pad_board_rotation = pad_rotation
    size_node = sexp.find(pad_sexpr, "size")
    width = sexp.num(size_node, 1) if size_node else 0.0
    height = sexp.num(size_node, 2) if size_node and len(size_node) > 2 else width
    layer_names = pcb_common.layer_names(sexp.find(pad_sexpr, "layers"))
    source = f"pad {footprint.reference}.{number}"
    layers = resolve_layers(builder, layer_names, source=source)
    drill = parse_pad_drill(
        builder,
        pad_sexpr,
        drill_id=f"drill:pad:{global_pad_index}:{number}",
        x=abs_x,
        y=abs_y,
        layers=layers,
        native_pad_type=native_pad_type,
        rotation=pad_board_rotation,
        source=source,
    )
    builder.add_pad_object(
        PcbPad(
            id=f"pad:{global_pad_index}:{footprint.reference}:{number}",
            number=number,
            x=abs_x,
            y=abs_y,
            stack=parse_pad_stack(
                pad_sexpr,
                shape=shape,
                size_x=width,
                size_y=height,
                corner_radius_ratio=sexp.find_num(pad_sexpr, "roundrect_rratio"),
            ),
            pad_type=(
                PcbPadType.SMD
                if native_pad_type == "smd" or drill is None
                else PcbPadType.THROUGH_HOLE
            ),
            layers=layers,
            net=pcb_common.resolve_net_node(builder, pad_sexpr, source=source),
            footprint=footprint,
            drill=drill,
            rotation=pad_board_rotation,
            pin_function=sexp.find_str(pad_sexpr, "pinfunction"),
            pin_type=sexp.find_str(pad_sexpr, "pintype"),
            custom_shapes=parse_pad_custom_shapes(
                pad_sexpr,
                transform=(abs_x, abs_y, pad_board_rotation),
            )
            if shape == "custom"
            else (),
            metadata=pcb_common.object_metadata(
                native_type="pad",
                source_collection="pads",
                native_kind=native_pad_type,
                native_id=pcb_common.item_uuid(pad_sexpr),
                native_index=index,
                locked=pcb_common.item_locked(pad_sexpr),
                properties={"shape": shape},
            ),
        ),
        source=source,
    )


def parse_pad_custom_shapes(
    pad_sexpr: SExpNode,
    *,
    transform: tuple[float, float, float],
) -> tuple[PcbLine | PcbArc | PcbCircle | PcbPolygon, ...]:
    primitives_node = sexp.find(pad_sexpr, "primitives")
    if not primitives_node:
        return ()
    shapes: list[PcbLine | PcbArc | PcbCircle | PcbPolygon] = []
    for item in primitives_node[1:]:
        tag = sexp.tag(item)
        if tag is None or not isinstance(item, list):
            continue
        payload = graphics.graphic_payload(item, tag=tag, transform=transform)
        if isinstance(payload, (PcbLine, PcbArc, PcbCircle, PcbPolygon)):
            shapes.append(payload)
    return tuple(shapes)


def parse_pad_drill(
    builder: PcbBuilder,
    pad_sexpr: SExpNode,
    *,
    drill_id: str,
    x: float,
    y: float,
    layers: tuple[PcbLayer, ...],
    native_pad_type: str,
    rotation: float,
    source: str,
) -> PcbDrill | None:
    drill_node = sexp.find(pad_sexpr, "drill")
    if not drill_node or len(drill_node) < 2:
        return None
    numeric_values = [float(value) for value in drill_node[1:] if isinstance(value, (int, float))]
    if not numeric_values:
        return None
    shape = PcbDrillShape.ROUND
    width = numeric_values[0]
    height = numeric_values[0]
    diameter = numeric_values[0]
    if (
        len(drill_node) > 1
        and isinstance(drill_node[1], sexpdata.Symbol)
        and drill_node[1].value() == "oval"
    ):
        shape = PcbDrillShape.SLOT
        height = numeric_values[1] if len(numeric_values) > 1 else width
        diameter = min(width, height)
    plating = (
        PcbDrillPlating.NON_PLATED if native_pad_type == "np_thru_hole" else PcbDrillPlating.PLATED
    )
    return builder.add_drill_object(
        PcbDrill(
            id=drill_id,
            x=x,
            y=y,
            diameter=diameter,
            shape=shape,
            plating=plating,
            width=width,
            height=height,
            rotation=rotation,
            layers=layers,
            metadata=pcb_common.object_metadata(
                native_type="drill",
                source_collection="drills",
                native_kind=native_pad_type,
                native_id=pcb_common.item_uuid(pad_sexpr),
            ),
        ),
        source=source,
    )


def parse_graphic_item(
    builder: PcbBuilder,
    item: SExpNode,
    *,
    tag: str,
    index: int,
    footprint: PcbFootprint | None = None,
    transform: tuple[float, float, float] | None = None,
) -> PcbArtwork | PcbBoardProfileElement:
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        msg = f"{tag} graphic missing required layer"
        raise ValueError(msg)
    layer = builder.resolve_layer(sexp.val(layer_node), source=tag)
    payload = graphics.graphic_payload(item, tag=tag, transform=transform)
    if payload is None:
        msg = f"{tag} graphic has missing or malformed geometry"
        raise ValueError(msg)
    kind = graphics.artwork_kind_for_payload(payload)
    metadata = pcb_common.object_metadata(
        native_type=tag,
        source_collection="footprint_artwork" if footprint is not None else "artwork",
        native_id=pcb_common.item_uuid(item),
        native_index=index,
        locked=pcb_common.item_locked(item),
    )
    if layer.has_role(LayerRole.EDGE):
        return PcbBoardProfileElement(
            id=f"{tag}:profile:{index}",
            kind=kind,
            layer=layer,
            data=payload,
            metadata=metadata,
        )
    return PcbArtwork(
        id=f"{tag}:{footprint.reference if footprint else 'board'}:{index}:{layer.name}",
        kind=kind,
        purpose=graphics.artwork_purpose(layer, native_type=tag),
        layer=layer,
        data=payload,
        footprint=footprint,
        metadata=metadata,
    )


def parse_fp_text(
    builder: PcbBuilder,
    item: SExpNode,
    footprint: PcbFootprint,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    index: int,
) -> PcbArtwork:
    if len(item) < 3:
        msg = "fp_text missing required kind/text fields"
        raise ValueError(msg)
    kind_node = item[1]
    text_kind = kind_node.value() if isinstance(kind_node, sexpdata.Symbol) else str(kind_node)
    text = str(item[2]).replace("${REFERENCE}", footprint.reference)
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        msg = "fp_text missing required layer"
        raise ValueError(msg)
    layer = builder.resolve_layer(sexp.val(layer_node), source=f"fp_text {footprint.reference}")
    at_node = sexp.find(item, "at")
    local_x, local_y, local_rotation = pcb_common.at(at_node) if at_node else (0.0, 0.0, 0.0)
    x, y = pcb_common.transform_point(local_x, local_y, fp_x, fp_y, fp_rot)
    effects = sexp.find(item, "effects")
    font_size = graphics.font_size(item)
    return PcbArtwork(
        id=f"fp_text:{footprint.reference}:{index}:{text_kind}",
        kind=PcbArtworkKind.TEXT,
        purpose=graphics.artwork_purpose(layer, native_type="fp_text", text_kind=text_kind),
        layer=layer,
        data=PcbText(
            text=text,
            x=x,
            y=y,
            rotation=pcb_common.transform_rotation(local_rotation, fp_rot),
            font_size=font_size,
            justify=graphics.justify(effects),
        ),
        footprint=footprint,
        metadata=pcb_common.object_metadata(
            native_type="fp_text",
            source_collection="artwork",
            native_kind=text_kind,
            native_id=pcb_common.item_uuid(item),
            native_index=index,
            hidden=pcb_common.item_hidden(item),
            locked=pcb_common.item_locked(item),
        ),
    )


def parse_model(item: SExpNode, footprint: PcbFootprint, index: int) -> PcbArtwork:
    raw_path = item[1] if len(item) > 1 else ""
    source = raw_path.value() if isinstance(raw_path, sexpdata.Symbol) else str(raw_path)
    offset_node = sexp.find(item, "offset") or sexp.find(item, "at")
    scale_node = sexp.find(item, "scale")
    rotate_node = sexp.find(item, "rotate")
    scale = _xyz(scale_node)
    if scale == (0.0, 0.0, 0.0) and not scale_node:
        scale = (1.0, 1.0, 1.0)
    return PcbArtwork(
        id=f"model_3d:{footprint.reference}:{index}",
        kind=PcbArtworkKind.MODEL_3D,
        purpose=PcbArtworkPurpose.COMPONENT_BODY,
        layer=None,
        data=PcbModel3D(
            source=source,
            offset=_xyz(offset_node),
            rotation=_xyz(rotate_node),
            scale=scale,
        ),
        footprint=footprint,
        metadata=pcb_common.object_metadata(
            native_type="model",
            source_collection="artwork",
            native_id=pcb_common.item_uuid(item),
            native_index=index,
            hidden=pcb_common.item_hidden(item),
        ),
    )


def _xyz(parent: SExpNode | None) -> tuple[float, float, float]:
    if not parent:
        return (0.0, 0.0, 0.0)
    xyz = sexp.find(parent, "xyz")
    if not xyz or len(xyz) < 4:
        return (0.0, 0.0, 0.0)
    return (sexp.num(xyz, 1), sexp.num(xyz, 2), sexp.num(xyz, 3))


def parse_footprint(
    builder: PcbBuilder, fp_sexpr: SExpNode, ctx: ParseContext | None = None
) -> FootprintParseResult:
    lib_name = str(fp_sexpr[1])
    layer_node = sexp.find(fp_sexpr, "layer")
    layer = builder.resolve_layer(
        sexp.val(layer_node) if layer_node else "F.Cu", source="footprint"
    )
    at_node = sexp.find(fp_sexpr, "at")
    fp_x, fp_y, fp_rot = pcb_common.at(at_node) if at_node else (0.0, 0.0, 0.0)
    reference = extract_reference(fp_sexpr)
    footprint = builder.add_footprint(
        PcbFootprint(
            reference=reference,
            footprint_lib=lib_name,
            x=fp_x,
            y=fp_y,
            rotation=fp_rot,
            layer=layer,
            value=extract_value(fp_sexpr),
            properties=parse_fp_properties(fp_sexpr),
            metadata=PcbFootprintMetadata(source_format="kicad", native_type="footprint"),
        ),
        source=f"footprint {reference}",
    )

    pad_start_index = len(builder.pads)
    for index, pad_sexpr in enumerate(sexp.find_all(fp_sexpr, "pad")):
        parse_pad(builder, pad_sexpr, footprint, fp_x, fp_y, fp_rot, index, ctx)

    profile_elements: list[PcbBoardProfileElement] = []
    courtyard_artwork: list[PcbArtwork] = []
    for tag in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
        for index, item in enumerate(sexp.find_all(fp_sexpr, tag)):
            parsed = parse_graphic_item(
                builder,
                item,
                tag=tag,
                index=index,
                footprint=footprint,
                transform=(fp_x, fp_y, fp_rot),
            )
            if isinstance(parsed, PcbBoardProfileElement):
                profile_elements.append(parsed)
            else:
                builder.add_artwork_object(parsed, source=f"{tag} {reference}")
                if parsed.layer is not None and parsed.layer.has_role(LayerRole.COURTYARD):
                    courtyard_artwork.append(parsed)

    for index, item in enumerate(sexp.find_all(fp_sexpr, "fp_text")):
        artwork = parse_fp_text(builder, item, footprint, fp_x, fp_y, fp_rot, index)
        builder.add_artwork_object(artwork, source=f"fp_text {reference}")

    for index, item in enumerate(sexp.find_all(fp_sexpr, "model")):
        builder.add_artwork_object(parse_model(item, footprint, index), source=f"model {reference}")

    for index, zone_sexpr in enumerate(sexp.find_all(fp_sexpr, "zone")):
        keepout = parse_zone_keepout(
            builder,
            zone_sexpr,
            index=index,
            footprint=footprint,
            transform=(fp_x, fp_y, fp_rot),
        )
        if keepout is not None:
            builder.add_keepout_object(keepout, source=f"footprint keepout {reference}")

    footprint.bbox = graphics.compute_bbox(builder.pads[pad_start_index:], courtyard_artwork)
    return FootprintParseResult(footprint=footprint, profile_elements=profile_elements)
