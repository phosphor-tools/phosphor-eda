"""Parse a KiCad .kicad_pcb file into the strict PCB domain model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sexpdata

from phosphor_eda.kicad import sexp
from phosphor_eda.pcb import (
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
    PcbDrill,
    PcbDrillPlating,
    PcbDrillShape,
    PcbFootprint,
    PcbFootprintMetadata,
    PcbKeepout,
    PcbKeepoutPermission,
    PcbKeepoutRules,
    PcbLayer,
    PcbLayerMetadata,
    PcbLine,
    PcbMetadata,
    PcbModel3D,
    PcbNet,
    PcbObjectMetadata,
    PcbPad,
    PcbPadType,
    PcbPolygon,
    PcbPour,
    PcbPourFillMode,
    PcbPourSettings,
    PcbText,
    PcbVia,
    PcbViaType,
)
from phosphor_eda.pcb_builder import PcbBuilder
from phosphor_eda.project import Stackup, StackupLayer

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.kicad.sexp import SExpNode


@dataclass
class _FootprintParseResult:
    footprint: PcbFootprint
    profile_elements: list[PcbBoardProfileElement]


def _xy(item: SExpNode) -> tuple[float, float]:
    return (sexp.num(item, 1), sexp.num(item, 2))


def _float_val(item: SExpNode) -> float:
    return sexp.num(item, 1)


def _at(item: SExpNode) -> tuple[float, float, float]:
    x = sexp.num(item, 1)
    y = sexp.num(item, 2)
    rotation = 0.0
    if len(item) > 3 and isinstance(item[3], (int, float)):
        rotation = float(item[3])
    return (x, y, rotation)


def _transform_point(
    local_x: float,
    local_y: float,
    fp_x: float,
    fp_y: float,
    fp_rot_deg: float,
) -> tuple[float, float]:
    rad = math.radians(-fp_rot_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)
    return (
        fp_x + local_x * cos_r - local_y * sin_r,
        fp_y + local_x * sin_r + local_y * cos_r,
    )


def _transform_rotation(local_rotation: float, fp_rotation: float) -> float:
    return fp_rotation + local_rotation


def _layers(item: SExpNode | None) -> list[str]:
    if item is None:
        return []
    result: list[str] = []
    for value in item[1:]:
        if isinstance(value, sexpdata.Symbol):
            result.append(value.value())
        elif isinstance(value, str):
            result.append(value)
    return result


def _kicad_type_roles(native_type: str) -> tuple[LayerRole, ...]:
    normalized = native_type.strip().lower()
    if normalized == "signal":
        return (LayerRole.COPPER, LayerRole.SIGNAL)
    if normalized == "power":
        return (LayerRole.COPPER, LayerRole.POWER)
    if normalized == "mixed":
        return (LayerRole.COPPER, LayerRole.MIXED)
    if normalized == "jumper":
        return (LayerRole.COPPER, LayerRole.JUMPER)
    if normalized == "front":
        return (LayerRole.FRONT,)
    if normalized == "back":
        return (LayerRole.BACK,)
    return ()


def _kicad_name_roles(name: str) -> tuple[LayerRole, ...]:
    if name == "F.Cu":
        return (LayerRole.COPPER, LayerRole.FRONT, LayerRole.OUTER)
    if name == "B.Cu":
        return (LayerRole.COPPER, LayerRole.BACK, LayerRole.OUTER)
    if name.startswith("In") and name.endswith(".Cu"):
        return (LayerRole.COPPER, LayerRole.INNER)
    if name == "F.Mask":
        return (LayerRole.SOLDER_MASK, LayerRole.FRONT)
    if name == "B.Mask":
        return (LayerRole.SOLDER_MASK, LayerRole.BACK)
    if name == "F.Paste":
        return (LayerRole.SOLDER_PASTE, LayerRole.FRONT)
    if name == "B.Paste":
        return (LayerRole.SOLDER_PASTE, LayerRole.BACK)
    if name == "F.SilkS":
        return (LayerRole.SILKSCREEN, LayerRole.FRONT)
    if name == "B.SilkS":
        return (LayerRole.SILKSCREEN, LayerRole.BACK)
    if name == "F.Adhes":
        return (LayerRole.ADHESIVE, LayerRole.FRONT)
    if name == "B.Adhes":
        return (LayerRole.ADHESIVE, LayerRole.BACK)
    if name == "F.Fab":
        return (LayerRole.FABRICATION, LayerRole.FRONT)
    if name == "B.Fab":
        return (LayerRole.FABRICATION, LayerRole.BACK)
    if name == "F.CrtYd":
        return (LayerRole.FABRICATION, LayerRole.COURTYARD, LayerRole.FRONT)
    if name == "B.CrtYd":
        return (LayerRole.FABRICATION, LayerRole.COURTYARD, LayerRole.BACK)
    if name == "Edge.Cuts":
        return (LayerRole.EDGE,)
    if name == "Margin":
        return (LayerRole.MARGIN,)
    if name == "Dwgs.User":
        return (LayerRole.DRAWING,)
    if name == "Cmts.User":
        return (LayerRole.COMMENT,)
    if name in {"Eco1.User", "Eco2.User"} or name.startswith("User."):
        return (LayerRole.USER,)
    return ()


def _parse_layer_defs(sexpr: SExpNode) -> list[PcbLayer]:
    layers_section = sexp.find(sexpr, "layers")
    if not layers_section:
        return []
    layers: list[PcbLayer] = []
    for item in layers_section[1:]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        raw_num = item[0]
        number = int(raw_num) if isinstance(raw_num, (int, float)) else 0
        raw_name = item[1]
        name = raw_name.value() if isinstance(raw_name, sexpdata.Symbol) else str(raw_name)
        raw_type = item[2]
        native_type = raw_type.value() if isinstance(raw_type, sexpdata.Symbol) else str(raw_type)
        native_user_name = str(item[3]) if len(item) > 3 else ""
        layers.append(
            PcbLayer(
                name=name,
                roles=(*_kicad_type_roles(native_type), *_kicad_name_roles(name)),
                number=number,
                metadata=PcbLayerMetadata(
                    source_format="kicad",
                    native_type=native_type,
                    native_user_name=native_user_name,
                ),
            )
        )
    return layers


def _parse_nets(sexpr: SExpNode) -> dict[int, PcbNet]:
    nets: dict[int, PcbNet] = {}
    for item in sexp.find_all(sexpr, "net"):
        if len(item) < 3:
            continue
        number = int(sexp.num(item, 1))
        if number == 0:
            continue
        nets[number] = PcbNet(number=number, name=str(item[2]))
    return nets


def _object_metadata(
    *,
    native_type: str,
    source_collection: str,
    native_kind: str = "",
    native_id: str = "",
    native_index: int | None = None,
    locked: bool = False,
    hidden: bool = False,
    properties: dict[str, str] | None = None,
) -> PcbObjectMetadata:
    return PcbObjectMetadata(
        source_format="kicad",
        native_type=native_type,
        native_kind=native_kind,
        native_id=native_id,
        native_index=native_index,
        source_collection=source_collection,
        locked=locked,
        hidden=hidden,
        properties=properties or {},
    )


def _item_uuid(item: SExpNode) -> str:
    uuid_node = sexp.find(item, "uuid") or sexp.find(item, "tstamp")
    return sexp.val(uuid_node) if uuid_node else ""


def _item_locked(item: SExpNode) -> bool:
    return any(isinstance(node, sexpdata.Symbol) and node.value() == "locked" for node in item)


def _item_hidden(item: SExpNode) -> bool:
    for node in item:
        if isinstance(node, sexpdata.Symbol) and node.value() == "hide":
            return True
        if isinstance(node, list) and sexp.tag(node) == "hide":
            if len(node) < 2:
                return True
            return _sexp_bool(node[1], default=True)
    return False


def _sexp_bool(value: object, *, default: bool) -> bool:
    raw = value.value() if isinstance(value, sexpdata.Symbol) else str(value)
    normalized = raw.lower()
    if normalized in {"yes", "true"}:
        return True
    if normalized in {"no", "false"}:
        return False
    return default


def _resolve_layer_selector(builder: PcbBuilder, name: str, *, source: str) -> tuple[PcbLayer, ...]:
    if name == "*.Cu":
        return tuple(layer for layer in builder.layers if layer.has_role(LayerRole.COPPER))
    if name == "*.Mask":
        return tuple(layer for layer in builder.layers if layer.has_role(LayerRole.SOLDER_MASK))
    if name == "*.Paste":
        return tuple(layer for layer in builder.layers if layer.has_role(LayerRole.SOLDER_PASTE))
    if name == "*.SilkS":
        return tuple(layer for layer in builder.layers if layer.has_role(LayerRole.SILKSCREEN))
    if name == "F&B.Cu":
        return tuple(
            layer
            for layer in builder.layers
            if layer.has_role(LayerRole.COPPER)
            and (layer.has_role(LayerRole.FRONT) or layer.has_role(LayerRole.BACK))
        )
    if name == "F&B.Mask":
        return tuple(
            layer
            for layer in builder.layers
            if layer.has_role(LayerRole.SOLDER_MASK)
            and (layer.has_role(LayerRole.FRONT) or layer.has_role(LayerRole.BACK))
        )
    if name == "F&B.Paste":
        return tuple(
            layer
            for layer in builder.layers
            if layer.has_role(LayerRole.SOLDER_PASTE)
            and (layer.has_role(LayerRole.FRONT) or layer.has_role(LayerRole.BACK))
        )
    return (builder.resolve_layer(name, source=source),)


def _resolve_layers(builder: PcbBuilder, names: list[str], *, source: str) -> tuple[PcbLayer, ...]:
    resolved: list[PcbLayer] = []
    for name in names:
        for layer in _resolve_layer_selector(builder, name, source=source):
            if layer not in resolved:
                resolved.append(layer)
    return tuple(resolved)


def _resolve_net_node(builder: PcbBuilder, item: SExpNode, *, source: str) -> PcbNet | None:
    net_node = sexp.find(item, "net")
    if not net_node or len(net_node) < 2:
        return None
    number = int(sexp.num(net_node, 1))
    if number == 0:
        return None
    return builder.resolve_net_number(number, source=source)


def _extract_reference(fp_sexpr: SExpNode) -> str:
    ref = sexp.find_property(fp_sexpr, "Reference")
    if ref:
        return ref
    for item in fp_sexpr:
        if isinstance(item, list) and sexp.tag(item) == "fp_text" and len(item) > 2:
            value = item[1]
            if isinstance(value, sexpdata.Symbol) and value.value() == "reference":
                return str(item[2])
    return "?"


def _extract_value(fp_sexpr: SExpNode) -> str:
    value = sexp.find_property(fp_sexpr, "Value")
    if value:
        return value
    for item in fp_sexpr:
        if isinstance(item, list) and sexp.tag(item) == "fp_text" and len(item) > 2:
            kind = item[1]
            if isinstance(kind, sexpdata.Symbol) and kind.value() == "value":
                return str(item[2])
    return ""


def _parse_fp_properties(fp_sexpr: SExpNode) -> dict[str, str]:
    builtin = {"Reference", "Value", "Footprint", "Datasheet", "Description"}
    properties: dict[str, str] = {}
    for item in sexp.find_all(fp_sexpr, "property"):
        if len(item) < 3:
            continue
        key = str(item[1])
        if key not in builtin:
            properties[key] = str(item[2])
    return properties


def _stroke_width(item: SExpNode, *, default: float = 0.1) -> float:
    width_node = sexp.find(item, "width")
    if width_node:
        return _float_val(width_node)
    stroke_node = sexp.find(item, "stroke")
    stroke_width = sexp.find(stroke_node, "width") if stroke_node else None
    return _float_val(stroke_width) if stroke_width else default


def _fill_flag(item: SExpNode) -> bool:
    fill_node = sexp.find(item, "fill")
    return fill_node is not None and sexp.val(fill_node) == "solid"


def _artwork_purpose(
    layer: PcbLayer | None,
    *,
    native_type: str,
    text_kind: str = "",
) -> PcbArtworkPurpose:
    if native_type == "model":
        return PcbArtworkPurpose.COMPONENT_BODY
    if text_kind == "reference":
        return PcbArtworkPurpose.DESIGNATOR
    if text_kind == "value":
        return PcbArtworkPurpose.VALUE
    if text_kind:
        return PcbArtworkPurpose.USER_TEXT
    if layer is None:
        return PcbArtworkPurpose.UNKNOWN
    if layer.has_role(LayerRole.SILKSCREEN):
        return PcbArtworkPurpose.SILKSCREEN
    if layer.has_role(LayerRole.COURTYARD):
        return PcbArtworkPurpose.COURTYARD
    if layer.has_role(LayerRole.FABRICATION):
        return PcbArtworkPurpose.FABRICATION
    if layer.has_role(LayerRole.ASSEMBLY):
        return PcbArtworkPurpose.ASSEMBLY
    if layer.has_role(LayerRole.SOLDER_MASK):
        return PcbArtworkPurpose.SOLDER_MASK
    if layer.has_role(LayerRole.SOLDER_PASTE):
        return PcbArtworkPurpose.SOLDER_PASTE
    if layer.has_role(LayerRole.DIMENSION):
        return PcbArtworkPurpose.DIMENSION
    if layer.has_role(LayerRole.MECHANICAL):
        return PcbArtworkPurpose.MECHANICAL
    if layer.has_role(LayerRole.USER) or layer.has_role(LayerRole.COMMENT):
        return PcbArtworkPurpose.USER
    return PcbArtworkPurpose.UNKNOWN


def _parse_pad(
    builder: PcbBuilder,
    pad_sexpr: SExpNode,
    footprint: PcbFootprint,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    index: int,
) -> None:
    global_pad_index = len(builder.pads)
    number = str(pad_sexpr[1])
    raw_pad_type = pad_sexpr[2]
    native_pad_type = (
        raw_pad_type.value() if isinstance(raw_pad_type, sexpdata.Symbol) else str(raw_pad_type)
    )
    raw_shape = pad_sexpr[3]
    shape = raw_shape.value() if isinstance(raw_shape, sexpdata.Symbol) else str(raw_shape)
    at_node = sexp.find(pad_sexpr, "at")
    local_x, local_y, pad_rotation = _at(at_node) if at_node else (0.0, 0.0, 0.0)
    abs_x, abs_y = _transform_point(local_x, local_y, fp_x, fp_y, fp_rot)
    # KiCad placed-board pad positions are footprint-local, but pad angles are
    # already board-space orientations. Adding footprint rotation rotates pads twice.
    pad_board_rotation = pad_rotation
    size_node = sexp.find(pad_sexpr, "size")
    width = sexp.num(size_node, 1) if size_node else 0.0
    height = sexp.num(size_node, 2) if size_node and len(size_node) > 2 else width
    layer_names = _layers(sexp.find(pad_sexpr, "layers"))
    source = f"pad {footprint.reference}.{number}"
    layers = _resolve_layers(builder, layer_names, source=source)
    drill = _parse_pad_drill(
        builder,
        pad_sexpr,
        id=f"drill:pad:{global_pad_index}:{number}",
        x=abs_x,
        y=abs_y,
        layers=layers,
        native_pad_type=native_pad_type,
        source=source,
    )
    rratio_node = sexp.find(pad_sexpr, "roundrect_rratio")
    pin_function_node = sexp.find(pad_sexpr, "pinfunction")
    pin_type_node = sexp.find(pad_sexpr, "pintype")
    builder.add_pad_object(
        PcbPad(
            id=f"pad:{global_pad_index}:{footprint.reference}:{number}",
            number=number,
            x=abs_x,
            y=abs_y,
            width=width,
            height=height,
            shape=shape,
            pad_type=(
                PcbPadType.SMD
                if native_pad_type == "smd" or drill is None
                else PcbPadType.THROUGH_HOLE
            ),
            layers=layers,
            net=_resolve_net_node(builder, pad_sexpr, source=source),
            footprint=footprint,
            drill=drill,
            rotation=pad_board_rotation,
            roundrect_rratio=_float_val(rratio_node) if rratio_node else 0.0,
            pin_function=sexp.val(pin_function_node) if pin_function_node else "",
            pin_type=sexp.val(pin_type_node) if pin_type_node else "",
            custom_shapes=_parse_pad_custom_shapes(
                pad_sexpr,
                transform=(abs_x, abs_y, pad_board_rotation),
            )
            if shape == "custom"
            else (),
            metadata=_object_metadata(
                native_type="pad",
                source_collection="pads",
                native_kind=native_pad_type,
                native_id=_item_uuid(pad_sexpr),
                native_index=index,
                locked=_item_locked(pad_sexpr),
                properties={"shape": shape},
            ),
        ),
        source=source,
    )


def _parse_pad_custom_shapes(
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
        payload = _graphic_payload(item, tag=tag, transform=transform)
        if isinstance(payload, (PcbLine, PcbArc, PcbCircle, PcbPolygon)):
            shapes.append(payload)
    return tuple(shapes)


def _parse_pad_drill(
    builder: PcbBuilder,
    pad_sexpr: SExpNode,
    *,
    id: str,
    x: float,
    y: float,
    layers: tuple[PcbLayer, ...],
    native_pad_type: str,
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
            id=id,
            x=x,
            y=y,
            diameter=diameter,
            shape=shape,
            plating=plating,
            width=width,
            height=height,
            layers=layers,
            metadata=_object_metadata(
                native_type="drill",
                source_collection="drills",
                native_kind=native_pad_type,
                native_id=_item_uuid(pad_sexpr),
            ),
        ),
        source=source,
    )


def _compute_bbox(
    pads: list[PcbPad], courtyard_artwork: list[PcbArtwork]
) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    if courtyard_artwork:
        for artwork in courtyard_artwork:
            _extend_payload_bounds(xs, ys, artwork.data)
    elif pads:
        margin = 0.5
        for pad in pads:
            xs.extend([pad.x - pad.width / 2 - margin, pad.x + pad.width / 2 + margin])
            ys.extend([pad.y - pad.height / 2 - margin, pad.y + pad.height / 2 + margin])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _extend_payload_bounds(xs: list[float], ys: list[float], payload: object) -> None:
    if isinstance(payload, PcbLine):
        xs.extend([payload.start_x, payload.end_x])
        ys.extend([payload.start_y, payload.end_y])
    elif isinstance(payload, PcbArc):
        xs.extend([payload.start_x, payload.mid_x, payload.end_x])
        ys.extend([payload.start_y, payload.mid_y, payload.end_y])
    elif isinstance(payload, PcbCircle):
        xs.extend([payload.cx - payload.radius, payload.cx + payload.radius])
        ys.extend([payload.cy - payload.radius, payload.cy + payload.radius])
    elif isinstance(payload, PcbPolygon):
        xs.extend(x for x, _y in payload.points)
        ys.extend(y for _x, y in payload.points)


def _parse_footprint(builder: PcbBuilder, fp_sexpr: SExpNode) -> _FootprintParseResult:
    lib_name = str(fp_sexpr[1])
    layer_node = sexp.find(fp_sexpr, "layer")
    layer = builder.resolve_layer(
        sexp.val(layer_node) if layer_node else "F.Cu", source="footprint"
    )
    at_node = sexp.find(fp_sexpr, "at")
    fp_x, fp_y, fp_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)
    reference = _extract_reference(fp_sexpr)
    footprint = builder.add_footprint(
        PcbFootprint(
            reference=reference,
            footprint_lib=lib_name,
            x=fp_x,
            y=fp_y,
            rotation=fp_rot,
            layer=layer,
            value=_extract_value(fp_sexpr),
            properties=_parse_fp_properties(fp_sexpr),
            metadata=PcbFootprintMetadata(source_format="kicad", native_type="footprint"),
        ),
        source=f"footprint {reference}",
    )

    pad_start_index = len(builder.pads)
    for index, pad_sexpr in enumerate(sexp.find_all(fp_sexpr, "pad")):
        _parse_pad(builder, pad_sexpr, footprint, fp_x, fp_y, fp_rot, index)

    profile_elements: list[PcbBoardProfileElement] = []
    courtyard_artwork: list[PcbArtwork] = []
    for tag in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
        for index, item in enumerate(sexp.find_all(fp_sexpr, tag)):
            parsed = _parse_graphic_item(
                builder,
                item,
                tag=tag,
                index=index,
                footprint=footprint,
                transform=(fp_x, fp_y, fp_rot),
            )
            if isinstance(parsed, PcbBoardProfileElement):
                profile_elements.append(parsed)
            elif isinstance(parsed, PcbArtwork):
                builder.add_artwork_object(parsed, source=f"{tag} {reference}")
                if parsed.layer is not None and parsed.layer.has_role(LayerRole.COURTYARD):
                    courtyard_artwork.append(parsed)

    for index, item in enumerate(sexp.find_all(fp_sexpr, "fp_text")):
        artwork = _parse_fp_text(builder, item, footprint, fp_x, fp_y, fp_rot, index)
        if artwork is not None:
            builder.add_artwork_object(artwork, source=f"fp_text {reference}")

    for index, item in enumerate(sexp.find_all(fp_sexpr, "model")):
        builder.add_artwork_object(
            _parse_model(item, footprint, index), source=f"model {reference}"
        )

    for index, zone_sexpr in enumerate(sexp.find_all(fp_sexpr, "zone")):
        keepout = _parse_zone_keepout(
            builder,
            zone_sexpr,
            index=index,
            footprint=footprint,
            transform=(fp_x, fp_y, fp_rot),
        )
        if keepout is not None:
            builder.add_keepout_object(keepout, source=f"footprint keepout {reference}")

    footprint.bbox = _compute_bbox(builder.pads[pad_start_index:], courtyard_artwork)
    return _FootprintParseResult(footprint=footprint, profile_elements=profile_elements)


def _parse_graphic_item(
    builder: PcbBuilder,
    item: SExpNode,
    *,
    tag: str,
    index: int,
    footprint: PcbFootprint | None = None,
    transform: tuple[float, float, float] | None = None,
) -> PcbArtwork | PcbBoardProfileElement | None:
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        return None
    layer = builder.resolve_layer(sexp.val(layer_node), source=tag)
    payload = _graphic_payload(item, tag=tag, transform=transform)
    if payload is None:
        return None
    kind = _artwork_kind_for_payload(payload)
    metadata = _object_metadata(
        native_type=tag,
        source_collection="footprint_artwork" if footprint is not None else "artwork",
        native_id=_item_uuid(item),
        native_index=index,
        locked=_item_locked(item),
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
        purpose=_artwork_purpose(layer, native_type=tag),
        layer=layer,
        data=payload,
        footprint=footprint,
        metadata=metadata,
    )


def _graphic_payload(
    item: SExpNode,
    *,
    tag: str,
    transform: tuple[float, float, float] | None,
) -> PcbLine | PcbArc | PcbCircle | PcbPolygon | None:
    if tag.endswith("_line") or tag == "gr_line":
        start_node = sexp.find(item, "start")
        end_node = sexp.find(item, "end")
        if not start_node or not end_node:
            return None
        start = _maybe_transform(_xy(start_node), transform)
        end = _maybe_transform(_xy(end_node), transform)
        return PcbLine(start[0], start[1], end[0], end[1], _stroke_width(item))
    if tag.endswith("_arc") or tag == "gr_arc":
        return _arc_payload(item, transform)
    if tag.endswith("_circle") or tag == "gr_circle":
        center_node = sexp.find(item, "center")
        end_node = sexp.find(item, "end")
        if not center_node or not end_node:
            return None
        center_local = _xy(center_node)
        end_local = _xy(end_node)
        radius = math.hypot(end_local[0] - center_local[0], end_local[1] - center_local[1])
        center = _maybe_transform(center_local, transform)
        return PcbCircle(center[0], center[1], radius, _stroke_width(item), _fill_flag(item))
    if tag.endswith("_rect") or tag == "gr_rect":
        start_node = sexp.find(item, "start")
        end_node = sexp.find(item, "end")
        if not start_node or not end_node:
            return None
        sx, sy = _xy(start_node)
        ex, ey = _xy(end_node)
        points = [
            _maybe_transform(point, transform) for point in ((sx, sy), (ex, sy), (ex, ey), (sx, ey))
        ]
        return PcbPolygon(points)
    if tag.endswith("_poly") or tag == "gr_poly":
        pts_node = sexp.find(item, "pts")
        if not pts_node:
            return None
        points = [
            _maybe_transform(_xy(xy_node), transform) for xy_node in sexp.find_all(pts_node, "xy")
        ]
        return PcbPolygon(points) if points else None
    return None


def _arc_payload(
    item: SExpNode,
    transform: tuple[float, float, float] | None,
) -> PcbArc | None:
    start_node = sexp.find(item, "start")
    mid_node = sexp.find(item, "mid")
    end_node = sexp.find(item, "end")
    if not start_node or not end_node:
        return None
    if mid_node:
        start = _maybe_transform(_xy(start_node), transform)
        mid = _maybe_transform(_xy(mid_node), transform)
        end = _maybe_transform(_xy(end_node), transform)
        return PcbArc(start[0], start[1], mid[0], mid[1], end[0], end[1], _stroke_width(item))
    angle_node = sexp.find(item, "angle")
    if not angle_node:
        return None
    cx, cy = _xy(start_node)
    ex, ey = _xy(end_node)
    angle_rad = math.radians(_float_val(angle_node))
    half_rad = angle_rad / 2.0
    dx = ex - cx
    dy = ey - cy
    mid = (
        cx + dx * math.cos(half_rad) - dy * math.sin(half_rad),
        cy + dx * math.sin(half_rad) + dy * math.cos(half_rad),
    )
    far = (
        cx + dx * math.cos(angle_rad) - dy * math.sin(angle_rad),
        cy + dx * math.sin(angle_rad) + dy * math.cos(angle_rad),
    )
    start = _maybe_transform((ex, ey), transform)
    middle = _maybe_transform(mid, transform)
    end = _maybe_transform(far, transform)
    return PcbArc(start[0], start[1], middle[0], middle[1], end[0], end[1], _stroke_width(item))


def _maybe_transform(
    point: tuple[float, float],
    transform: tuple[float, float, float] | None,
) -> tuple[float, float]:
    if transform is None:
        return point
    return _transform_point(point[0], point[1], transform[0], transform[1], transform[2])


def _artwork_kind_for_payload(payload: object) -> PcbArtworkKind:
    if isinstance(payload, PcbLine):
        return PcbArtworkKind.LINE
    if isinstance(payload, PcbArc):
        return PcbArtworkKind.ARC
    if isinstance(payload, PcbCircle):
        return PcbArtworkKind.CIRCLE
    if isinstance(payload, PcbPolygon):
        return PcbArtworkKind.POLYGON
    if isinstance(payload, PcbText):
        return PcbArtworkKind.TEXT
    if isinstance(payload, PcbModel3D):
        return PcbArtworkKind.MODEL_3D
    return PcbArtworkKind.IMAGE


def _parse_fp_text(
    builder: PcbBuilder,
    item: SExpNode,
    footprint: PcbFootprint,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    index: int,
) -> PcbArtwork | None:
    if len(item) < 3:
        return None
    kind_node = item[1]
    text_kind = kind_node.value() if isinstance(kind_node, sexpdata.Symbol) else str(kind_node)
    text = str(item[2]).replace("${REFERENCE}", footprint.reference)
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        return None
    layer = builder.resolve_layer(sexp.val(layer_node), source=f"fp_text {footprint.reference}")
    at_node = sexp.find(item, "at")
    local_x, local_y, local_rotation = _at(at_node) if at_node else (0.0, 0.0, 0.0)
    x, y = _transform_point(local_x, local_y, fp_x, fp_y, fp_rot)
    effects = sexp.find(item, "effects")
    font = sexp.find(effects, "font") if effects else None
    size_node = sexp.find(font, "size") if font else None
    font_size = sexp.num(size_node, 1) if size_node else 1.0
    return PcbArtwork(
        id=f"fp_text:{footprint.reference}:{index}:{text_kind}",
        kind=PcbArtworkKind.TEXT,
        purpose=_artwork_purpose(layer, native_type="fp_text", text_kind=text_kind),
        layer=layer,
        data=PcbText(
            text=text,
            x=x,
            y=y,
            rotation=_transform_rotation(local_rotation, fp_rot),
            font_size=font_size,
            justify=_justify(effects),
        ),
        footprint=footprint,
        metadata=_object_metadata(
            native_type="fp_text",
            source_collection="artwork",
            native_kind=text_kind,
            native_id=_item_uuid(item),
            native_index=index,
            hidden=_item_hidden(item),
            locked=_item_locked(item),
        ),
    )


def _parse_gr_text(builder: PcbBuilder, item: SExpNode, index: int) -> PcbArtwork | None:
    if len(item) < 2:
        return None
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        return None
    layer = builder.resolve_layer(sexp.val(layer_node), source="gr_text")
    at_node = sexp.find(item, "at")
    x, y, rotation = _at(at_node) if at_node else (0.0, 0.0, 0.0)
    effects = sexp.find(item, "effects")
    font = sexp.find(effects, "font") if effects else None
    size_node = sexp.find(font, "size") if font else None
    font_size = sexp.num(size_node, 1) if size_node else 1.0
    return PcbArtwork(
        id=f"gr_text:board:{index}:{layer.name}",
        kind=PcbArtworkKind.TEXT,
        purpose=_artwork_purpose(layer, native_type="gr_text", text_kind="user"),
        layer=layer,
        data=PcbText(
            text=str(item[1]),
            x=x,
            y=y,
            rotation=rotation,
            font_size=font_size,
            justify=_justify(effects),
        ),
        metadata=_object_metadata(
            native_type="gr_text",
            source_collection="artwork",
            native_id=_item_uuid(item),
            native_index=index,
            locked=_item_locked(item),
        ),
    )


def _justify(effects: SExpNode | None) -> str:
    justify_node = sexp.find(effects, "justify") if effects else None
    if not justify_node:
        return ""
    values: list[str] = []
    for value in justify_node[1:]:
        values.append(value.value() if isinstance(value, sexpdata.Symbol) else str(value))
    return " ".join(values)


def _parse_model(item: SExpNode, footprint: PcbFootprint, index: int) -> PcbArtwork:
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
        metadata=_object_metadata(
            native_type="model",
            source_collection="artwork",
            native_id=_item_uuid(item),
            native_index=index,
            hidden=_item_hidden(item),
        ),
    )


def _xyz(parent: SExpNode | None) -> tuple[float, float, float]:
    if not parent:
        return (0.0, 0.0, 0.0)
    xyz = sexp.find(parent, "xyz")
    if not xyz or len(xyz) < 4:
        return (0.0, 0.0, 0.0)
    return (sexp.num(xyz, 1), sexp.num(xyz, 2), sexp.num(xyz, 3))


def _parse_segment(builder: PcbBuilder, item: SExpNode, index: int) -> None:
    start_node = sexp.find(item, "start")
    end_node = sexp.find(item, "end")
    width_node = sexp.find(item, "width")
    layer_node = sexp.find(item, "layer")
    if not start_node or not end_node or not width_node or not layer_node:
        msg = "Segment missing required start/end/width/layer"
        raise ValueError(msg)
    layer = builder.resolve_layer(sexp.val(layer_node), source="segment")
    start = _xy(start_node)
    end = _xy(end_node)
    builder.add_conductor_object(
        PcbConductor(
            id=f"segment:{layer.name}:{index}",
            kind=PcbConductorKind.TRACE,
            layer=layer,
            data=PcbLine(start[0], start[1], end[0], end[1], _float_val(width_node)),
            net=_resolve_net_node(builder, item, source="segment"),
            metadata=_object_metadata(
                native_type="segment",
                source_collection="conductors",
                native_id=_item_uuid(item),
                native_index=index,
                locked=_item_locked(item),
            ),
        ),
        source="segment",
    )


def _parse_trace_arc(builder: PcbBuilder, item: SExpNode, index: int) -> None:
    payload = _arc_payload(item, transform=None)
    layer_node = sexp.find(item, "layer")
    if payload is None or not layer_node:
        return
    layer = builder.resolve_layer(sexp.val(layer_node), source="arc")
    builder.add_conductor_object(
        PcbConductor(
            id=f"trace_arc:{layer.name}:{index}",
            kind=PcbConductorKind.TRACE_ARC,
            layer=layer,
            data=payload,
            net=_resolve_net_node(builder, item, source="arc"),
            metadata=_object_metadata(
                native_type="arc",
                source_collection="conductors",
                native_id=_item_uuid(item),
                native_index=index,
                locked=_item_locked(item),
            ),
        ),
        source="arc",
    )


def _parse_via(builder: PcbBuilder, item: SExpNode, index: int) -> None:
    at_node = sexp.find(item, "at")
    size_node = sexp.find(item, "size")
    drill_node = sexp.find(item, "drill")
    if not at_node or not size_node or not drill_node:
        msg = "Via missing required at/size/drill"
        raise ValueError(msg)
    x = sexp.num(at_node, 1)
    y = sexp.num(at_node, 2)
    layers = _resolve_layers(builder, _layers(sexp.find(item, "layers")), source="via")
    via_kind = ""
    if len(item) > 1 and isinstance(item[1], sexpdata.Symbol):
        via_kind = item[1].value()
    drill = builder.add_drill_object(
        PcbDrill(
            id=f"drill:via:{index}",
            x=x,
            y=y,
            diameter=_float_val(drill_node),
            shape=PcbDrillShape.ROUND,
            plating=PcbDrillPlating.PLATED,
            layers=layers,
            metadata=_object_metadata(
                native_type="drill",
                source_collection="drills",
                native_kind="via",
                native_id=_item_uuid(item),
                native_index=index,
            ),
        ),
        source="via",
    )
    builder.add_via_object(
        PcbVia(
            id=f"via:{index}",
            x=x,
            y=y,
            diameter=_float_val(size_node),
            layers=layers,
            drill=drill,
            net=_resolve_net_node(builder, item, source="via"),
            via_type=_via_type(via_kind),
            metadata=_object_metadata(
                native_type="via",
                source_collection="vias",
                native_kind=via_kind,
                native_id=_item_uuid(item),
                native_index=index,
                locked=_item_locked(item),
            ),
        ),
        source="via",
    )


def _via_type(native_kind: str) -> PcbViaType:
    if native_kind == "blind":
        return PcbViaType.BLIND
    if native_kind == "micro":
        return PcbViaType.MICROVIA
    if native_kind == "free":
        return PcbViaType.FREE
    return PcbViaType.THROUGH


def _parse_zone_keepout(
    builder: PcbBuilder,
    zone_sexpr: SExpNode,
    *,
    index: int,
    footprint: PcbFootprint | None = None,
    transform: tuple[float, float, float] | None = None,
) -> PcbKeepout | None:
    keepout_node = sexp.find(zone_sexpr, "keepout")
    if not keepout_node:
        return None
    boundary_points = _parse_zone_polygon_points(zone_sexpr)
    if not boundary_points:
        return None
    points = [_maybe_transform(point, transform) for point in boundary_points]
    layers = _resolve_layers(builder, _zone_layer_names(zone_sexpr), source="keepout")
    prefix = f"fp_keepout:{footprint.reference}" if footprint is not None else "keepout"
    return PcbKeepout(
        id=f"{prefix}:{index}",
        boundary=PcbClosedPath.from_points(points),
        layers=layers,
        rules=_parse_keepout_rules(keepout_node),
        footprint=footprint,
        metadata=_object_metadata(
            native_type="zone",
            source_collection="keepouts",
            native_kind="footprint_keepout" if footprint is not None else "keepout",
            native_id=_item_uuid(zone_sexpr),
            native_index=index,
            locked=_item_locked(zone_sexpr),
        ),
    )


def _parse_keepout_rules(keepout_node: SExpNode) -> PcbKeepoutRules:
    return PcbKeepoutRules(
        tracks=_keepout_rule_value(keepout_node, "tracks"),
        vias=_keepout_rule_value(keepout_node, "vias"),
        pads=_keepout_rule_value(keepout_node, "pads"),
        copper_pours=_keepout_rule_value(keepout_node, "copperpour"),
        footprints=_keepout_rule_value(keepout_node, "footprints"),
    )


def _keepout_rule_value(keepout_node: SExpNode, name: str) -> PcbKeepoutPermission:
    rule_node = sexp.find(keepout_node, name)
    if not rule_node:
        return PcbKeepoutPermission.UNKNOWN
    raw = sexp.val(rule_node)
    if raw == "allowed":
        return PcbKeepoutPermission.ALLOWED
    if raw == "not_allowed":
        return PcbKeepoutPermission.NOT_ALLOWED
    return PcbKeepoutPermission.UNKNOWN


def _zone_layer_names(zone_sexpr: SExpNode) -> list[str]:
    layer_node = sexp.find(zone_sexpr, "layer")
    if layer_node:
        return [sexp.val(layer_node)]
    return _layers(sexp.find(zone_sexpr, "layers"))


def _parse_zone_polygon_points(zone_sexpr: SExpNode) -> list[tuple[float, float]]:
    polygon_node = sexp.find(zone_sexpr, "polygon")
    pts_node = sexp.find(polygon_node, "pts") if polygon_node else None
    if not pts_node:
        return []
    return [_xy(xy_node) for xy_node in sexp.find_all(pts_node, "xy")]


def _parse_zone(builder: PcbBuilder, zone_sexpr: SExpNode, index: int) -> None:
    keepout = _parse_zone_keepout(builder, zone_sexpr, index=index)
    if keepout is not None:
        builder.add_keepout_object(keepout, source="zone keepout")
        return
    boundary_points = _parse_zone_polygon_points(zone_sexpr)
    if not boundary_points:
        return
    layers = _resolve_layers(builder, _zone_layer_names(zone_sexpr), source="zone")
    fill_node = sexp.find(zone_sexpr, "fill")
    thermal_gap_node = sexp.find(fill_node, "thermal_gap") if fill_node else None
    bridge_node = sexp.find(fill_node, "thermal_bridge_width") if fill_node else None
    min_thickness_node = sexp.find(zone_sexpr, "min_thickness")
    connect_node = sexp.find(zone_sexpr, "connect_pads")
    clearance_node = sexp.find(connect_node, "clearance") if connect_node else None
    priority_node = sexp.find(zone_sexpr, "priority")
    layer_name = layers[0].name if layers else "unknown"
    pour = builder.add_pour_object(
        PcbPour(
            id=f"zone:{index}:{layer_name}",
            boundary=PcbClosedPath.from_points(boundary_points),
            layers=layers,
            net=_resolve_net_node(builder, zone_sexpr, source="zone"),
            priority=int(sexp.num(priority_node, 1))
            if priority_node and len(priority_node) > 1
            else 0,
            settings=PcbPourSettings(
                fill_mode=_kicad_fill_mode(fill_node) if fill_node else PcbPourFillMode.UNKNOWN,
                min_thickness_mm=_float_val(min_thickness_node) if min_thickness_node else 0.0,
                thermal_gap_mm=_float_val(thermal_gap_node) if thermal_gap_node else 0.0,
                thermal_bridge_width_mm=_float_val(bridge_node) if bridge_node else 0.0,
                connect_pads_clearance_mm=_float_val(clearance_node) if clearance_node else 0.0,
            ),
            metadata=_object_metadata(
                native_type="zone",
                source_collection="pours",
                native_id=_item_uuid(zone_sexpr),
                native_index=index,
                locked=_item_locked(zone_sexpr),
            ),
        ),
        source="zone",
    )
    fills = _parse_zone_fills(builder, zone_sexpr, zone_index=index, pour=pour)
    pour.fills = tuple(fills)


def _parse_zone_fills(
    builder: PcbBuilder,
    zone_sexpr: SExpNode,
    *,
    zone_index: int,
    pour: PcbPour,
) -> list[PcbConductor]:
    zone_layers = _zone_layer_names(zone_sexpr)
    zone_layer = zone_layers[0] if zone_layers else ""
    conductors: list[PcbConductor] = []
    for index, fill_node in enumerate(sexp.find_all(zone_sexpr, "filled_polygon")):
        layer_node = sexp.find(fill_node, "layer")
        layer = builder.resolve_layer(
            sexp.val(layer_node) if layer_node else zone_layer,
            source="filled_polygon",
        )
        pts_node = sexp.find(fill_node, "pts")
        if not pts_node:
            continue
        points = [_xy(xy_node) for xy_node in sexp.find_all(pts_node, "xy")]
        if not points:
            continue
        conductor = builder.add_conductor_object(
            PcbConductor(
                id=f"pour_fill:{zone_index}:{index}:{layer.name}",
                kind=PcbConductorKind.POUR_FILL,
                layer=layer,
                data=PcbPolygon(points=points),
                net=pour.net,
                pour=pour,
                metadata=_object_metadata(
                    native_type="filled_polygon",
                    source_collection="conductors",
                    native_id=_item_uuid(fill_node),
                    native_index=index,
                ),
            ),
            source="filled_polygon",
        )
        conductors.append(conductor)
    return conductors


def _kicad_fill_mode(fill_node: SExpNode) -> PcbPourFillMode:
    if len(fill_node) > 1:
        raw = fill_node[1]
        if isinstance(raw, sexpdata.Symbol):
            value = raw.value()
            if value == "yes":
                return PcbPourFillMode.SOLID
            if value == "no":
                return PcbPourFillMode.NONE
    return PcbPourFillMode.UNKNOWN


def parse_kicad_stackup(sexpr: SExpNode) -> Stackup | None:
    setup_node = sexp.find(sexpr, "setup")
    if not setup_node:
        return None
    stackup_node = sexp.find(setup_node, "stackup")
    if not stackup_node:
        return None
    layers: list[StackupLayer] = []
    copper_finish = ""
    for item in stackup_node[1:]:
        if not isinstance(item, list) or not item:
            continue
        tag = item[0].value() if isinstance(item[0], sexpdata.Symbol) else str(item[0])
        if tag == "copper_finish":
            copper_finish = str(item[1]) if len(item) > 1 else ""
            continue
        if tag != "layer":
            continue
        name = str(item[1]) if len(item) > 1 else ""
        type_node = sexp.find(item, "type")
        thickness_node = sexp.find(item, "thickness")
        material_node = sexp.find(item, "material")
        epsilon_node = sexp.find(item, "epsilon_r")
        loss_node = sexp.find(item, "loss_tangent")
        side = ""
        if name.startswith("F.") or name == "Top":
            side = "front"
        elif name.startswith("B.") or name == "Bottom":
            side = "back"
        layer_type = sexp.val(type_node) if type_node else ""
        layers.append(
            StackupLayer(
                name=name,
                layer_type=layer_type,
                thickness_mm=_float_val(thickness_node) if thickness_node else 0.0,
                material=sexp.val(material_node) if material_node else "",
                epsilon_r=_float_val(epsilon_node) if epsilon_node else 0.0,
                loss_tangent=_float_val(loss_node) if loss_node else 0.0,
                side=side,
            )
        )
        for sub_item in item[2:]:
            if not isinstance(sub_item, list) or not sub_item:
                continue
            sub_tag = (
                sub_item[0].value()
                if isinstance(sub_item[0], sexpdata.Symbol)
                else str(sub_item[0])
            )
            if sub_tag != "addsublayer":
                continue
            sub_thickness_node = sexp.find(sub_item, "thickness")
            sub_material_node = sexp.find(sub_item, "material")
            sub_epsilon_node = sexp.find(sub_item, "epsilon_r")
            sub_loss_node = sexp.find(sub_item, "loss_tangent")
            layers.append(
                StackupLayer(
                    name=f"{name} (sublayer)",
                    layer_type=layer_type,
                    thickness_mm=_float_val(sub_thickness_node) if sub_thickness_node else 0.0,
                    material=sexp.val(sub_material_node) if sub_material_node else "",
                    epsilon_r=_float_val(sub_epsilon_node) if sub_epsilon_node else 0.0,
                    loss_tangent=_float_val(sub_loss_node) if sub_loss_node else 0.0,
                    side=side,
                )
            )
    if not layers:
        return None
    return Stackup(
        layers=layers,
        total_thickness_mm=sum(layer.thickness_mm for layer in layers),
        copper_finish=copper_finish,
    )


def load_kicad_stackup(path: Path) -> Stackup | None:
    text = path.read_text(encoding="utf-8")
    data: SExpNode = sexpdata.loads(text)
    sexpr: SExpNode = list(data[1:]) if data else []
    return parse_kicad_stackup(sexpr)


def parse_kicad_pcb(path: Path) -> Pcb:
    sexpr = read_kicad_pcb_sexpr(path)
    return parse_kicad_pcb_from_sexpr(sexpr, default_name=path.stem)


def read_kicad_pcb_sexpr(path: Path) -> SExpNode:
    text = path.read_text(encoding="utf-8")
    data: SExpNode = sexpdata.loads(text)
    return list(data[1:]) if data else []


def parse_kicad_pcb_from_sexpr(sexpr: SExpNode, *, default_name: str = "") -> Pcb:
    title_block = sexp.find(sexpr, "title_block")
    title_node = sexp.find(title_block, "title") if title_block else None
    builder = PcbBuilder(
        sexp.val(title_node) if title_node else default_name,
        metadata=PcbMetadata(source_format="kicad"),
    )
    for layer in _parse_layer_defs(sexpr):
        builder.add_layer(layer, source="layers")
    for net in _parse_nets(sexpr).values():
        builder.add_net(net, source="nets")

    profile_elements: list[PcbBoardProfileElement] = []
    for tag in ("footprint", "module"):
        for fp_sexpr in sexp.find_all(sexpr, tag):
            result = _parse_footprint(builder, fp_sexpr)
            profile_elements.extend(result.profile_elements)
    for index, item in enumerate(sexp.find_all(sexpr, "segment")):
        _parse_segment(builder, item, index)
    for index, item in enumerate(sexp.find_all(sexpr, "via")):
        _parse_via(builder, item, index)
    for index, item in enumerate(sexp.find_all(sexpr, "zone")):
        _parse_zone(builder, item, index)
    for index, item in enumerate(sexp.find_all(sexpr, "arc")):
        _parse_trace_arc(builder, item, index)
    for tag in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly"):
        for index, item in enumerate(sexp.find_all(sexpr, tag)):
            parsed = _parse_graphic_item(builder, item, tag=tag, index=index)
            if isinstance(parsed, PcbBoardProfileElement):
                profile_elements.append(parsed)
            elif isinstance(parsed, PcbArtwork):
                builder.add_artwork_object(parsed, source=tag)
    for index, item in enumerate(sexp.find_all(sexpr, "gr_text")):
        artwork = _parse_gr_text(builder, item, index)
        if artwork is not None:
            builder.add_artwork_object(artwork, source="gr_text")
    builder.set_board_profile(
        PcbBoardProfile(elements=tuple(profile_elements)), source="board profile"
    )
    return builder.build(require_board_profile=True)
