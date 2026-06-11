"""KiCad .kicad_pcb board parsing: nets, traces, vias, board text, top level."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sexpdata

from phosphor_eda.domain.pcb import (
    Pcb,
    PcbArtwork,
    PcbArtworkKind,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbConductor,
    PcbConductorKind,
    PcbDrill,
    PcbDrillPlating,
    PcbDrillShape,
    PcbLine,
    PcbMetadata,
    PcbNet,
    PcbText,
    PcbVia,
    PcbViaType,
)
from phosphor_eda.domain.pcb_builder import PcbBuilder
from phosphor_eda.formats.kicad import graphics, pcb_common, sexp
from phosphor_eda.formats.kicad.footprint import parse_footprint, parse_graphic_item
from phosphor_eda.formats.kicad.layers import parse_layer_defs, resolve_layers
from phosphor_eda.formats.kicad.zones import parse_zone

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.formats.kicad.sexp import SExpNode


def parse_nets(sexpr: SExpNode) -> dict[int, PcbNet]:
    nets: dict[int, PcbNet] = {}
    for item in sexp.find_all(sexpr, "net"):
        if len(item) < 3:
            continue
        number = int(sexp.num(item, 1))
        if number == 0:
            continue
        nets[number] = PcbNet(number=number, name=str(item[2]))
    return nets


def _parse_segment(builder: PcbBuilder, item: SExpNode, index: int) -> None:
    start_node = sexp.find(item, "start")
    end_node = sexp.find(item, "end")
    width_node = sexp.find(item, "width")
    layer_node = sexp.find(item, "layer")
    if not start_node or not end_node or not width_node or not layer_node:
        msg = "Segment missing required start/end/width/layer"
        raise ValueError(msg)
    layer = builder.resolve_layer(sexp.val(layer_node), source="segment")
    start = pcb_common.xy(start_node)
    end = pcb_common.xy(end_node)
    builder.add_conductor_object(
        PcbConductor(
            id=f"segment:{layer.name}:{index}",
            kind=PcbConductorKind.TRACE,
            layer=layer,
            data=PcbLine(start[0], start[1], end[0], end[1], sexp.num(width_node, 1)),
            net=pcb_common.resolve_net_node(builder, item, source="segment"),
            metadata=pcb_common.object_metadata(
                native_type="segment",
                source_collection="conductors",
                native_id=pcb_common.item_uuid(item),
                native_index=index,
                locked=pcb_common.item_locked(item),
            ),
        ),
        source="segment",
    )


def _parse_trace_arc(builder: PcbBuilder, item: SExpNode, index: int) -> None:
    start_node = sexp.find(item, "start")
    mid_node = sexp.find(item, "mid")
    end_node = sexp.find(item, "end")
    width_node = sexp.find(item, "width")
    layer_node = sexp.find(item, "layer")
    if not start_node or not mid_node or not end_node or not width_node or not layer_node:
        msg = "Trace arc missing required start/mid/end/width/layer"
        raise ValueError(msg)
    payload = graphics.arc_payload(item, transform=None)
    if payload is None:
        msg = "Trace arc has malformed geometry"
        raise ValueError(msg)
    layer = builder.resolve_layer(sexp.val(layer_node), source="arc")
    builder.add_conductor_object(
        PcbConductor(
            id=f"trace_arc:{layer.name}:{index}",
            kind=PcbConductorKind.TRACE_ARC,
            layer=layer,
            data=payload,
            net=pcb_common.resolve_net_node(builder, item, source="arc"),
            metadata=pcb_common.object_metadata(
                native_type="arc",
                source_collection="conductors",
                native_id=pcb_common.item_uuid(item),
                native_index=index,
                locked=pcb_common.item_locked(item),
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
    layer_names = pcb_common.layer_names(sexp.find(item, "layers"))
    layers = resolve_layers(builder, layer_names, source="via")
    via_kind = ""
    if len(item) > 1 and isinstance(item[1], sexpdata.Symbol):
        via_kind = item[1].value()
    tented_front, tented_back = _via_tenting(item)
    drill = builder.add_drill_object(
        PcbDrill(
            id=f"drill:via:{index}",
            x=x,
            y=y,
            diameter=sexp.num(drill_node, 1),
            shape=PcbDrillShape.ROUND,
            plating=PcbDrillPlating.PLATED,
            layers=layers,
            metadata=pcb_common.object_metadata(
                native_type="drill",
                source_collection="drills",
                native_kind="via",
                native_id=pcb_common.item_uuid(item),
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
            diameter=sexp.num(size_node, 1),
            layers=layers,
            drill=drill,
            net=pcb_common.resolve_net_node(builder, item, source="via"),
            via_type=_via_type(via_kind),
            tented_front=tented_front,
            tented_back=tented_back,
            metadata=pcb_common.object_metadata(
                native_type="via",
                source_collection="vias",
                native_kind=via_kind,
                native_id=pcb_common.item_uuid(item),
                native_index=index,
                locked=pcb_common.item_locked(item),
            ),
        ),
        source="via",
    )


def _via_tenting(item: SExpNode) -> tuple[bool, bool]:
    """Read a via's ``(tenting ...)`` sides.

    KiCad lists the tented sides as ``front``/``back`` symbols (``none`` or an
    absent node means neither side is tented).
    """
    tenting_node = sexp.find(item, "tenting")
    if not tenting_node:
        return False, False
    sides = {sub.value() for sub in tenting_node[1:] if isinstance(sub, sexpdata.Symbol)}
    return "front" in sides, "back" in sides


_VIA_TYPES: dict[str, PcbViaType] = {
    "blind": PcbViaType.BLIND,
    "micro": PcbViaType.MICROVIA,
    "free": PcbViaType.FREE,
}


def _via_type(native_kind: str) -> PcbViaType:
    return _VIA_TYPES.get(native_kind, PcbViaType.THROUGH)


def _parse_gr_text(builder: PcbBuilder, item: SExpNode, index: int) -> PcbArtwork:
    if len(item) < 2:
        msg = "gr_text missing required text field"
        raise ValueError(msg)
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        msg = "gr_text missing required layer"
        raise ValueError(msg)
    layer = builder.resolve_layer(sexp.val(layer_node), source="gr_text")
    at_node = sexp.find(item, "at")
    x, y, rotation = pcb_common.at(at_node) if at_node else (0.0, 0.0, 0.0)
    effects = sexp.find(item, "effects")
    font_size = graphics.font_size(item)
    return PcbArtwork(
        id=f"gr_text:board:{index}:{layer.name}",
        kind=PcbArtworkKind.TEXT,
        purpose=graphics.artwork_purpose(layer, native_type="gr_text", text_kind="user"),
        layer=layer,
        data=PcbText(
            text=str(item[1]),
            x=x,
            y=y,
            rotation=rotation,
            font_size=font_size,
            justify=graphics.justify(effects),
        ),
        metadata=pcb_common.object_metadata(
            native_type="gr_text",
            source_collection="artwork",
            native_id=pcb_common.item_uuid(item),
            native_index=index,
            locked=pcb_common.item_locked(item),
        ),
    )


def parse_kicad_pcb(path: Path) -> Pcb:
    sexpr = read_kicad_pcb_sexpr(path)
    return parse_kicad_pcb_from_sexpr(sexpr, default_name=path.stem)


def read_kicad_pcb_sexpr(path: Path) -> SExpNode:
    text = path.read_text(encoding="utf-8")
    data: SExpNode = sexpdata.loads(text)
    return list(data[1:]) if data else []


def parse_kicad_pcb_from_sexpr(sexpr: SExpNode, *, default_name: str = "") -> Pcb:
    title_node = sexp.find_path(sexpr, "title_block", "title")
    builder = PcbBuilder(
        sexp.val(title_node) if title_node else default_name,
        metadata=PcbMetadata(source_format="kicad"),
    )
    for layer in parse_layer_defs(sexpr):
        builder.add_layer(layer, source="layers")
    for net in parse_nets(sexpr).values():
        builder.add_net(net, source="nets")

    profile_elements: list[PcbBoardProfileElement] = []
    for tag in ("footprint", "module"):
        for fp_sexpr in sexp.find_all(sexpr, tag):
            result = parse_footprint(builder, fp_sexpr)
            profile_elements.extend(result.profile_elements)
    for index, item in enumerate(sexp.find_all(sexpr, "segment")):
        _parse_segment(builder, item, index)
    for index, item in enumerate(sexp.find_all(sexpr, "via")):
        _parse_via(builder, item, index)
    for index, item in enumerate(sexp.find_all(sexpr, "zone")):
        parse_zone(builder, item, index)
    for index, item in enumerate(sexp.find_all(sexpr, "arc")):
        _parse_trace_arc(builder, item, index)
    for tag in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly"):
        for index, item in enumerate(sexp.find_all(sexpr, tag)):
            parsed = parse_graphic_item(builder, item, tag=tag, index=index)
            if isinstance(parsed, PcbBoardProfileElement):
                profile_elements.append(parsed)
            else:
                builder.add_artwork_object(parsed, source=tag)
    for index, item in enumerate(sexp.find_all(sexpr, "gr_text")):
        artwork = _parse_gr_text(builder, item, index)
        builder.add_artwork_object(artwork, source="gr_text")
    builder.set_board_profile(
        PcbBoardProfile(elements=tuple(profile_elements)), source="board profile"
    )
    return builder.build(require_board_profile=True)
