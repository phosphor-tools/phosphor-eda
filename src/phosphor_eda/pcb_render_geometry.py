"""Renderable geometry inventory for PCB SVG rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
    PcbArc,
    PcbCircle,
    PcbDimension,
    PcbFootprint,
    PcbGraphicText,
    PcbKeepout,
    PcbLayer,
    PcbLine,
    PcbPad,
    PcbPolygon,
    PcbSegment,
    PcbText,
    PcbTraceArc,
    PcbVia,
)
from phosphor_eda.pcb_render_drills import pad_drill_geometry


class GeometryKind(StrEnum):
    BOARD_MATERIAL = "board_material"
    BOARD_OUTLINE = "board_outline"
    DRILL = "drill"
    PAD = "pad"
    TRACE = "trace"
    TRACE_ARC = "trace_arc"
    ZONE = "zone"
    KEEPOUT = "keepout"
    VIA = "via"
    SILK_LINE = "silk_line"
    SILK_POLYGON = "silk_polygon"
    FAB_LINE = "fab_line"
    FAB_ARC = "fab_arc"
    FAB_CIRCLE = "fab_circle"
    FAB_POLYGON = "fab_polygon"
    BODY_LINE = "body_line"
    BODY_ARC = "body_arc"
    BODY_CIRCLE = "body_circle"
    BODY_POLYGON = "body_polygon"
    REF_TEXT = "ref_text"
    VALUE_TEXT = "value_text"
    USER_TEXT = "user_text"
    BOARD_GRAPHIC_TEXT = "board_graphic_text"
    DIMENSION = "dimension"
    MASK = "mask"
    PASTE = "paste"
    MECHANICAL = "mechanical"


@dataclass(frozen=True)
class RenderPoint:
    x: float
    y: float


@dataclass(frozen=True)
class GeometryLayer:
    name: str
    role: str
    side: str = ""
    stack_index: int = 0
    source: PcbLayer | None = None


@dataclass(frozen=True)
class GeometryTags:
    source_collection: str = ""
    source_index: int = 0
    component_ref: str = ""
    component_prefix: str = ""
    pad_number: str = ""
    net_number: int | None = None
    net_name: str = ""
    text_kind: str = ""
    footprint_lib: str = ""
    value: str = ""


@dataclass(frozen=True)
class RenderableGeometry:
    id: str
    kind: GeometryKind
    layer: GeometryLayer
    tags: GeometryTags
    payload: object
    source: object | None = None
    points: tuple[RenderPoint, ...] = ()
    bbox: tuple[float, float, float, float] | None = None
    clipped: bool = True


@dataclass(frozen=True)
class PcbGeometryStore:
    items: tuple[RenderableGeometry, ...]

    def by_id(self, geometry_id: str) -> RenderableGeometry | None:
        for item in self.items:
            if item.id == geometry_id:
                return item
        return None

    def by_kind(self, kind: GeometryKind) -> tuple[RenderableGeometry, ...]:
        return tuple(item for item in self.items if item.kind is kind)


@dataclass(frozen=True)
class GeometrySelector:
    kinds: frozenset[GeometryKind] = frozenset()
    role: str = ""
    side: str = ""
    layer_name: str = ""
    net_name: str = ""
    net_number: int | None = None
    component_ref: str = ""
    component_prefixes: tuple[str, ...] = ()
    pad_number: str = ""
    text_kinds: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _RenderedViewTransform:
    mirror_x: float | None = None

    def point(self, x: float, y: float) -> RenderPoint:
        return RenderPoint(self.x(x), y)

    def x(self, value: float) -> float:
        if self.mirror_x is None:
            return value
        return self.mirror_x - value


def build_geometry_store(board: Pcb, *, side: str) -> PcbGeometryStore:
    """Build a complete, style-neutral inventory of renderable PCB geometry."""
    board_bbox = board.bbox()
    transform = _rendered_view_transform(side=side, board_bbox=board_bbox)
    layer_lookup = {layer.name: layer for layer in board.layers}
    layers = _geometry_layers(board)
    items: list[RenderableGeometry] = []

    edge_layer = _board_edge_layer(board, layers, layer_lookup)
    outline_points = _outline_points(board.outline_lines, board.outline_arcs, transform)
    items.append(
        RenderableGeometry(
            id="board_material:0",
            kind=GeometryKind.BOARD_MATERIAL,
            layer=edge_layer,
            tags=GeometryTags(source_collection="board"),
            payload=board_bbox,
            source=board,
            points=outline_points,
            bbox=board_bbox,
        )
    )
    items.append(
        RenderableGeometry(
            id="board_outline:0",
            kind=GeometryKind.BOARD_OUTLINE,
            layer=edge_layer,
            tags=GeometryTags(source_collection="outline"),
            payload=(board.outline_lines, board.outline_arcs),
            source=board,
            points=outline_points,
            bbox=board_bbox,
            clipped=False,
        )
    )

    footprints_by_ref = {footprint.reference: footprint for footprint in board.footprints}

    for fp_index, footprint in enumerate(board.footprints):
        for pad_index, pad in enumerate(footprint.pads):
            layer_name = _pad_copper_layer(pad, footprint.layer, layer_lookup)
            transformed = _transform_pad(pad, transform)
            items.append(
                RenderableGeometry(
                    id=f"pad:{footprint.reference}:{pad.number}:{pad_index}",
                    kind=GeometryKind.PAD,
                    layer=layers.get(layer_name, _layer_for_name(layer_name, layer_lookup)),
                    tags=_component_tags(
                        footprint,
                        source_collection="pads",
                        source_index=pad_index,
                        pad_number=pad.number,
                        net_number=pad.net_number,
                        net_name=pad.net_name or _net_name(board, pad.net_number),
                    ),
                    payload=transformed,
                    source=transformed,
                    points=(transform.point(pad.x, pad.y),),
                    bbox=_pad_bbox(transformed),
                )
            )
            if pad.drill > 0:
                drill_bbox = _pad_drill_bbox(transformed)
                items.append(
                    RenderableGeometry(
                        id=f"drill:{footprint.reference}:{pad.number}:{pad_index}",
                        kind=GeometryKind.DRILL,
                        layer=_synthetic_layer("drills", "drill", "", 900),
                        tags=_component_tags(
                            footprint,
                            source_collection="pad_drills",
                            source_index=pad_index,
                            pad_number=pad.number,
                            net_number=pad.net_number,
                            net_name=pad.net_name or _net_name(board, pad.net_number),
                        ),
                        payload=transformed,
                        source=transformed,
                        points=(transform.point(pad.x, pad.y),),
                        bbox=drill_bbox,
                    )
                )

        _append_footprint_graphics(items, footprint, fp_index, layers, layer_lookup, transform)

    for index, segment in enumerate(board.segments):
        transformed = _transform_segment(segment, transform)
        items.append(
            RenderableGeometry(
                id=f"trace:{segment.layer}:{index}",
                kind=GeometryKind.TRACE,
                layer=layers.get(segment.layer, _layer_for_name(segment.layer, layer_lookup)),
                tags=GeometryTags(
                    source_collection="segments",
                    source_index=index,
                    net_number=segment.net_number,
                    net_name=_net_name(board, segment.net_number),
                ),
                payload=transformed,
                source=transformed,
                points=(
                    transform.point(segment.start_x, segment.start_y),
                    transform.point(segment.end_x, segment.end_y),
                ),
                bbox=_line_bbox(
                    transformed.start_x,
                    transformed.start_y,
                    transformed.end_x,
                    transformed.end_y,
                ),
            )
        )

    for index, trace_arc in enumerate(board.trace_arcs):
        transformed = _transform_trace_arc(trace_arc, transform)
        items.append(
            RenderableGeometry(
                id=f"trace_arc:{trace_arc.layer}:{index}",
                kind=GeometryKind.TRACE_ARC,
                layer=layers.get(trace_arc.layer, _layer_for_name(trace_arc.layer, layer_lookup)),
                tags=GeometryTags(
                    source_collection="trace_arcs",
                    source_index=index,
                    net_number=trace_arc.net_number,
                    net_name=_net_name(board, trace_arc.net_number),
                ),
                payload=transformed,
                source=transformed,
                points=(
                    transform.point(trace_arc.start_x, trace_arc.start_y),
                    transform.point(trace_arc.mid_x, trace_arc.mid_y),
                    transform.point(trace_arc.end_x, trace_arc.end_y),
                ),
                bbox=_points_bbox(
                    (
                        (transformed.start_x, transformed.start_y),
                        (transformed.mid_x, transformed.mid_y),
                        (transformed.end_x, transformed.end_y),
                    )
                ),
            )
        )

    for index, polygon in enumerate(board.polygons):
        transformed = _transform_polygon(polygon, transform)
        kind = _polygon_kind_for_layer(layers.get(polygon.layer), polygon)
        items.append(
            RenderableGeometry(
                id=f"{kind.value}:{polygon.layer}:{index}",
                kind=kind,
                layer=layers.get(polygon.layer, _layer_for_name(polygon.layer, layer_lookup)),
                tags=_tags_for_polygon(board, polygon, index),
                payload=transformed,
                source=transformed,
                points=tuple(RenderPoint(x, y) for x, y in transformed.points),
                bbox=_points_bbox(transformed.points),
            )
        )

    for index, keepout in enumerate(board.keepouts):
        transformed = _transform_keepout(keepout, transform)
        for layer_name in transformed.layers:
            layer = _keepout_layer_for_name(layer_name, layers, layer_lookup)
            layer_payload = replace(transformed, layers=[layer_name])
            items.append(
                RenderableGeometry(
                    id=f"keepout:{layer_name}:{index}",
                    kind=GeometryKind.KEEPOUT,
                    layer=layer,
                    tags=GeometryTags(
                        source_collection="keepouts",
                        source_index=index,
                        component_ref=transformed.footprint_ref,
                        component_prefix=_component_prefix(transformed.footprint_ref),
                    ),
                    payload=layer_payload,
                    source=transformed,
                    points=tuple(RenderPoint(x, y) for x, y in transformed.boundary),
                    bbox=_points_bbox(transformed.boundary),
                )
            )

    for index, via in enumerate(board.vias):
        transformed = _transform_via(via, transform)
        items.append(
            RenderableGeometry(
                id=f"via:{index}",
                kind=GeometryKind.VIA,
                layer=_synthetic_layer("vias", "via", "", 800),
                tags=GeometryTags(
                    source_collection="vias",
                    source_index=index,
                    net_number=via.net_number,
                    net_name=_net_name(board, via.net_number),
                ),
                payload=transformed,
                source=transformed,
                points=(transform.point(via.x, via.y),),
                bbox=_circle_bbox(transformed.x, transformed.y, transformed.size / 2),
            )
        )

    for index, graphic_text in enumerate(board.graphic_texts):
        transformed = _transform_graphic_text(graphic_text, transform)
        items.append(
            RenderableGeometry(
                id=f"board_text:{graphic_text.layer}:{index}",
                kind=GeometryKind.BOARD_GRAPHIC_TEXT,
                layer=layers.get(
                    graphic_text.layer,
                    _layer_for_name(graphic_text.layer, layer_lookup),
                ),
                tags=GeometryTags(
                    source_collection="graphic_texts",
                    source_index=index,
                    text_kind="board",
                ),
                payload=transformed,
                source=transformed,
                points=(transform.point(graphic_text.x, graphic_text.y),),
            )
        )

    for index, dimension in enumerate(board.dimensions):
        transformed = _transform_dimension(dimension, transform)
        items.append(
            RenderableGeometry(
                id=f"dimension:{dimension.layer}:{index}",
                kind=GeometryKind.DIMENSION,
                layer=layers.get(dimension.layer, _layer_for_name(dimension.layer, layer_lookup)),
                tags=GeometryTags(source_collection="dimensions", source_index=index),
                payload=transformed,
                source=transformed,
                points=(
                    transform.point(dimension.start_x, dimension.start_y),
                    transform.point(dimension.end_x, dimension.end_y),
                ),
                bbox=_line_bbox(
                    transformed.start_x,
                    transformed.start_y,
                    transformed.end_x,
                    transformed.end_y,
                ),
            )
        )

    _append_model_only_bodies(items, board.footprints, footprints_by_ref)
    return PcbGeometryStore(items=tuple(items))


def geometry_matches_selector(
    geometry: RenderableGeometry,
    selector: GeometrySelector,
    *,
    active_side: str,
) -> bool:
    if selector.kinds and geometry.kind not in selector.kinds:
        return False
    if selector.role and geometry.layer.role != selector.role:
        return False
    if selector.side and not _side_matches(geometry.layer.side, selector.side, active_side):
        return False
    if selector.layer_name and geometry.layer.name != selector.layer_name:
        return False
    if selector.net_name and geometry.tags.net_name != selector.net_name:
        return False
    if selector.net_number is not None and geometry.tags.net_number != selector.net_number:
        return False
    if selector.component_ref and geometry.tags.component_ref != selector.component_ref:
        return False
    if (
        selector.component_prefixes
        and geometry.tags.component_prefix not in selector.component_prefixes
    ):
        return False
    if selector.pad_number and geometry.tags.pad_number != selector.pad_number:
        return False
    return not (selector.text_kinds and geometry.tags.text_kind not in selector.text_kinds)


def layer_role(layer: PcbLayer) -> str:
    if layer.function == LayerFunction.COPPER:
        return "copper"
    if layer.function == LayerFunction.SILKSCREEN:
        return "silkscreen"
    if layer.function == LayerFunction.FAB:
        return "fabrication"
    if layer.function == LayerFunction.SOLDER_MASK:
        return "mask"
    if layer.function == LayerFunction.SOLDER_PASTE:
        return "paste"
    if layer.function == LayerFunction.MECHANICAL:
        return "mechanical"
    if layer.function == LayerFunction.COURTYARD:
        return "courtyard"
    if layer.function == LayerFunction.EDGE:
        return "edge"
    return "unknown"


def _append_footprint_graphics(
    items: list[RenderableGeometry],
    footprint: PcbFootprint,
    footprint_index: int,
    layers: dict[str, GeometryLayer],
    layer_lookup: dict[str, PcbLayer],
    transform: _RenderedViewTransform,
) -> None:
    for index, line in enumerate(footprint.silkscreen_lines):
        transformed = _transform_line(line, transform)
        items.append(
            _footprint_item(
                id_prefix="silk_line",
                kind=GeometryKind.SILK_LINE,
                footprint=footprint,
                footprint_index=footprint_index,
                index=index,
                layer=layers.get(line.layer, _layer_for_name(line.layer, layer_lookup)),
                source_collection="silkscreen_lines",
                payload=transformed,
                points=(
                    transform.point(line.start_x, line.start_y),
                    transform.point(line.end_x, line.end_y),
                ),
                bbox=_line_bbox(
                    transformed.start_x,
                    transformed.start_y,
                    transformed.end_x,
                    transformed.end_y,
                ),
            )
        )
    for index, polygon in enumerate(footprint.silkscreen_polygons):
        transformed = _transform_polygon(polygon, transform)
        items.append(
            _footprint_item(
                id_prefix="silk_polygon",
                kind=GeometryKind.SILK_POLYGON,
                footprint=footprint,
                footprint_index=footprint_index,
                index=index,
                layer=layers.get(polygon.layer, _layer_for_name(polygon.layer, layer_lookup)),
                source_collection="silkscreen_polygons",
                payload=transformed,
                points=tuple(RenderPoint(x, y) for x, y in transformed.points),
                bbox=_points_bbox(transformed.points),
            )
        )
    for index, line in enumerate(footprint.fab_lines):
        transformed = _transform_line(line, transform)
        items.append(
            _footprint_item(
                id_prefix="fab_line",
                kind=GeometryKind.FAB_LINE,
                footprint=footprint,
                footprint_index=footprint_index,
                index=index,
                layer=layers.get(line.layer, _layer_for_name(line.layer, layer_lookup)),
                source_collection="fab_lines",
                payload=transformed,
                points=(
                    transform.point(line.start_x, line.start_y),
                    transform.point(line.end_x, line.end_y),
                ),
                bbox=_line_bbox(
                    transformed.start_x,
                    transformed.start_y,
                    transformed.end_x,
                    transformed.end_y,
                ),
            )
        )
    for index, circle in enumerate(footprint.fab_circles):
        transformed = _transform_circle(circle, transform)
        items.append(
            _footprint_item(
                id_prefix="fab_circle",
                kind=GeometryKind.FAB_CIRCLE,
                footprint=footprint,
                footprint_index=footprint_index,
                index=index,
                layer=layers.get(circle.layer, _layer_for_name(circle.layer, layer_lookup)),
                source_collection="fab_circles",
                payload=transformed,
                points=(transform.point(circle.cx, circle.cy),),
                bbox=_circle_bbox(transformed.cx, transformed.cy, transformed.radius),
            )
        )
    for index, arc in enumerate(footprint.fab_arcs):
        transformed = _transform_arc(arc, transform)
        items.append(
            _footprint_item(
                id_prefix="fab_arc",
                kind=GeometryKind.FAB_ARC,
                footprint=footprint,
                footprint_index=footprint_index,
                index=index,
                layer=layers.get(arc.layer, _layer_for_name(arc.layer, layer_lookup)),
                source_collection="fab_arcs",
                payload=transformed,
                points=(
                    transform.point(arc.start_x, arc.start_y),
                    transform.point(arc.mid_x, arc.mid_y),
                    transform.point(arc.end_x, arc.end_y),
                ),
                bbox=_points_bbox(
                    (
                        (transformed.start_x, transformed.start_y),
                        (transformed.mid_x, transformed.mid_y),
                        (transformed.end_x, transformed.end_y),
                    )
                ),
            )
        )
    for index, polygon in enumerate(footprint.fab_polygons):
        transformed = _transform_polygon(polygon, transform)
        items.append(
            _footprint_item(
                id_prefix="fab_polygon",
                kind=GeometryKind.FAB_POLYGON,
                footprint=footprint,
                footprint_index=footprint_index,
                index=index,
                layer=layers.get(polygon.layer, _layer_for_name(polygon.layer, layer_lookup)),
                source_collection="fab_polygons",
                payload=transformed,
                points=tuple(RenderPoint(x, y) for x, y in transformed.points),
                bbox=_points_bbox(transformed.points),
            )
        )
    for index, text in enumerate(footprint.texts):
        if text.hidden:
            continue
        transformed = _transform_text(text, transform)
        items.append(
            _footprint_item(
                id_prefix=_text_kind(text).value,
                kind=_text_kind(text),
                footprint=footprint,
                footprint_index=footprint_index,
                index=index,
                layer=layers.get(text.layer, _layer_for_name(text.layer, layer_lookup)),
                source_collection="texts",
                payload=transformed,
                points=(transform.point(text.x, text.y),),
                text_kind=text.kind or "user",
            )
        )


def _append_model_only_bodies(
    items: list[RenderableGeometry],
    footprints: list[PcbFootprint],
    _footprints_by_ref: dict[str, PcbFootprint],
) -> None:
    body_refs = {
        item.tags.component_ref
        for item in items
        if item.kind
        in {
            GeometryKind.FAB_LINE,
            GeometryKind.FAB_ARC,
            GeometryKind.FAB_CIRCLE,
            GeometryKind.FAB_POLYGON,
        }
    }
    model_layer = _synthetic_layer("models", "fabrication", "", 1200)
    for index, footprint in enumerate(footprints):
        if footprint.reference in body_refs or not any(
            model.cache_key for model in footprint.models_3d
        ):
            continue
        items.append(
            RenderableGeometry(
                id=f"body_model:{footprint.reference}:{index}",
                kind=GeometryKind.BODY_POLYGON,
                layer=model_layer,
                tags=_component_tags(
                    footprint,
                    source_collection="models_3d",
                    source_index=index,
                ),
                payload=footprint,
                source=footprint,
                bbox=footprint.bbox,
            )
        )


def _footprint_item(
    *,
    id_prefix: str,
    kind: GeometryKind,
    footprint: PcbFootprint,
    footprint_index: int,
    index: int,
    layer: GeometryLayer,
    source_collection: str,
    payload: object,
    points: tuple[RenderPoint, ...] = (),
    bbox: tuple[float, float, float, float] | None = None,
    text_kind: str = "",
) -> RenderableGeometry:
    return RenderableGeometry(
        id=f"{id_prefix}:{footprint.reference}:{footprint_index}:{index}",
        kind=kind,
        layer=layer,
        tags=_component_tags(
            footprint,
            source_collection=source_collection,
            source_index=index,
            text_kind=text_kind,
        ),
        payload=payload,
        source=payload,
        points=points,
        bbox=bbox,
    )


def _component_tags(
    footprint: PcbFootprint,
    *,
    source_collection: str,
    source_index: int,
    pad_number: str = "",
    net_number: int | None = None,
    net_name: str = "",
    text_kind: str = "",
) -> GeometryTags:
    return GeometryTags(
        source_collection=source_collection,
        source_index=source_index,
        component_ref=footprint.reference,
        component_prefix=_component_prefix(footprint.reference),
        pad_number=pad_number,
        net_number=net_number,
        net_name=net_name,
        text_kind=text_kind,
        footprint_lib=footprint.footprint_lib,
        value=footprint.value,
    )


def _geometry_layers(board: Pcb) -> dict[str, GeometryLayer]:
    return {
        layer.name: GeometryLayer(
            name=layer.name,
            role=layer_role(layer),
            side=layer.side or ("inner" if layer.function == LayerFunction.COPPER else ""),
            stack_index=index,
            source=layer,
        )
        for index, layer in enumerate(board.layers)
    }


def _layer_for_name(name: str, layer_lookup: dict[str, PcbLayer]) -> GeometryLayer:
    layer = layer_lookup.get(name)
    if layer is None:
        return _synthetic_layer(name, "unknown", "", 10_000)
    return GeometryLayer(
        name=layer.name,
        role=layer_role(layer),
        side=layer.side or ("inner" if layer.function == LayerFunction.COPPER else ""),
        stack_index=layer.number if layer.number is not None else 10_000,
        source=layer,
    )


def _synthetic_layer(name: str, role: str, side: str, stack_index: int) -> GeometryLayer:
    return GeometryLayer(name=name, role=role, side=side, stack_index=stack_index)


def _keepout_layer_for_name(
    name: str,
    layers: dict[str, GeometryLayer],
    layer_lookup: dict[str, PcbLayer],
) -> GeometryLayer:
    layer = layers.get(name, _layer_for_name(name, layer_lookup))
    return replace(layer, role="keepout")


def _board_edge_layer(
    board: Pcb,
    layers: dict[str, GeometryLayer],
    layer_lookup: dict[str, PcbLayer],
) -> GeometryLayer:
    for layer_name in _board_outline_layer_names(board):
        if layer_name in layers:
            return replace(layers[layer_name], role="edge", side="")
        if layer_name in layer_lookup:
            return replace(_layer_for_name(layer_name, layer_lookup), role="edge", side="")
    for layer in layers.values():
        if layer.role == "edge":
            return layer
    return _synthetic_layer("Edge.Cuts", "edge", "", -300)


def _board_outline_layer_names(board: Pcb) -> tuple[str, ...]:
    names: list[str] = []
    for line in board.outline_lines:
        names.append(line.layer)
    for arc in board.outline_arcs:
        names.append(arc.layer)
    return tuple(dict.fromkeys(name for name in names if name))


def _rendered_view_transform(
    *,
    side: str,
    board_bbox: tuple[float, float, float, float],
) -> _RenderedViewTransform:
    if side != "back":
        return _RenderedViewTransform()
    bx0, _by0, bx1, _by1 = board_bbox
    return _RenderedViewTransform(mirror_x=bx0 + bx1)


def _transform_pad(pad: PcbPad, transform: _RenderedViewTransform) -> PcbPad:
    if transform.mirror_x is None:
        return pad
    return replace(
        pad,
        x=transform.x(pad.x),
        rotation=(-pad.rotation) % 360 if pad.rotation else 0.0,
    )


def _transform_segment(segment: PcbSegment, transform: _RenderedViewTransform) -> PcbSegment:
    if transform.mirror_x is None:
        return segment
    return replace(segment, start_x=transform.x(segment.start_x), end_x=transform.x(segment.end_x))


def _transform_trace_arc(
    trace_arc: PcbTraceArc,
    transform: _RenderedViewTransform,
) -> PcbTraceArc:
    if transform.mirror_x is None:
        return trace_arc
    return replace(
        trace_arc,
        start_x=transform.x(trace_arc.start_x),
        mid_x=transform.x(trace_arc.mid_x),
        end_x=transform.x(trace_arc.end_x),
    )


def _transform_via(via: PcbVia, transform: _RenderedViewTransform) -> PcbVia:
    if transform.mirror_x is None:
        return via
    return replace(via, x=transform.x(via.x))


def _transform_line(line: PcbLine, transform: _RenderedViewTransform) -> PcbLine:
    if transform.mirror_x is None:
        return line
    return replace(line, start_x=transform.x(line.start_x), end_x=transform.x(line.end_x))


def _transform_circle(circle: PcbCircle, transform: _RenderedViewTransform) -> PcbCircle:
    if transform.mirror_x is None:
        return circle
    return replace(circle, cx=transform.x(circle.cx))


def _transform_arc(arc: PcbArc, transform: _RenderedViewTransform) -> PcbArc:
    if transform.mirror_x is None:
        return arc
    return replace(
        arc,
        start_x=transform.x(arc.start_x),
        mid_x=transform.x(arc.mid_x),
        end_x=transform.x(arc.end_x),
    )


def _transform_polygon(polygon: PcbPolygon, transform: _RenderedViewTransform) -> PcbPolygon:
    if transform.mirror_x is None:
        return polygon
    return replace(
        polygon,
        points=[(transform.x(x), y) for x, y in polygon.points],
        holes=[[(transform.x(x), y) for x, y in hole] for hole in polygon.holes],
    )


def _transform_keepout(keepout: PcbKeepout, transform: _RenderedViewTransform) -> PcbKeepout:
    if transform.mirror_x is None:
        return keepout
    return replace(
        keepout,
        boundary=[(transform.x(x), y) for x, y in keepout.boundary],
        holes=[[(transform.x(x), y) for x, y in hole] for hole in keepout.holes],
    )


def _transform_text(text: PcbText, transform: _RenderedViewTransform) -> PcbText:
    if transform.mirror_x is None:
        return text
    rotation = 180.0 - text.rotation
    return replace(text, x=transform.x(text.x), rotation=rotation)


def _transform_graphic_text(
    text: PcbGraphicText,
    transform: _RenderedViewTransform,
) -> PcbGraphicText:
    if transform.mirror_x is None:
        return text
    rotation = 180.0 - text.rotation
    return replace(text, x=transform.x(text.x), rotation=rotation)


def _transform_dimension(
    dimension: PcbDimension,
    transform: _RenderedViewTransform,
) -> PcbDimension:
    if transform.mirror_x is None:
        return dimension
    return replace(
        dimension,
        start_x=transform.x(dimension.start_x),
        end_x=transform.x(dimension.end_x),
    )


def _outline_points(
    lines: list[PcbLine],
    arcs: list[PcbArc],
    transform: _RenderedViewTransform,
) -> tuple[RenderPoint, ...]:
    if not lines and not arcs:
        return ()
    points: list[RenderPoint] = []
    for line in lines:
        if not points:
            points.append(transform.point(line.start_x, line.start_y))
        points.append(transform.point(line.end_x, line.end_y))
    for arc in arcs:
        if not points:
            points.append(transform.point(arc.start_x, arc.start_y))
        points.append(transform.point(arc.mid_x, arc.mid_y))
        points.append(transform.point(arc.end_x, arc.end_y))
    return tuple(points)


def _pad_copper_layer(pad: PcbPad, footprint_layer: str, layer_lookup: dict[str, PcbLayer]) -> str:
    for raw_layer_name in pad.layers:
        layer_name = str(raw_layer_name)
        if layer_name == "*.Cu":
            return footprint_layer
        layer = layer_lookup.get(layer_name)
        if layer and layer.function == LayerFunction.COPPER:
            return layer_name
    return footprint_layer


def _polygon_kind_for_layer(layer: GeometryLayer | None, polygon: PcbPolygon) -> GeometryKind:
    if polygon.footprint_ref and layer is not None and layer.role == "fabrication":
        return GeometryKind.FAB_POLYGON
    if layer is not None and layer.role == "copper":
        return GeometryKind.ZONE
    if layer is not None and layer.role == "silkscreen":
        return GeometryKind.SILK_POLYGON
    if layer is not None and layer.role == "mask":
        return GeometryKind.MASK
    if layer is not None and layer.role == "paste":
        return GeometryKind.PASTE
    if layer is not None and layer.role == "mechanical":
        return GeometryKind.MECHANICAL
    return GeometryKind.MECHANICAL


def _tags_for_polygon(board: Pcb, polygon: PcbPolygon, index: int) -> GeometryTags:
    return GeometryTags(
        source_collection="polygons",
        source_index=index,
        component_ref=polygon.footprint_ref,
        component_prefix=_component_prefix(polygon.footprint_ref),
        net_number=polygon.net_number,
        net_name=polygon.net_name or _net_name(board, polygon.net_number),
    )


def _text_kind(text: PcbText) -> GeometryKind:
    if text.kind == "reference":
        return GeometryKind.REF_TEXT
    if text.kind == "value":
        return GeometryKind.VALUE_TEXT
    return GeometryKind.USER_TEXT


def _side_matches(layer_side: str, expected: str, active_side: str) -> bool:
    if expected in ("", "any"):
        return True
    if expected == "active":
        return layer_side == active_side
    if expected == "opposite":
        return layer_side in ("front", "back") and layer_side != active_side
    return layer_side == expected


def _component_prefix(ref: str) -> str:
    match = re.match(r"[A-Za-z]+", ref)
    return match.group(0).upper() if match else ""


def _net_name(board: Pcb, net_number: int) -> str:
    net = board.nets.get(net_number)
    return net.name if net else ""


def _pad_bbox(pad: PcbPad) -> tuple[float, float, float, float]:
    return (
        pad.x - pad.width / 2,
        pad.y - pad.height / 2,
        pad.x + pad.width / 2,
        pad.y + pad.height / 2,
    )


def _pad_drill_bbox(pad: PcbPad) -> tuple[float, float, float, float]:
    geometry = pad_drill_geometry(pad)
    if geometry is None or geometry.is_empty:
        return _circle_bbox(pad.x, pad.y, pad.drill / 2)
    min_x, min_y, max_x, max_y = geometry.bounds
    return (min_x, min_y, max_x, max_y)


def _circle_bbox(x: float, y: float, radius: float) -> tuple[float, float, float, float]:
    return (x - radius, y - radius, x + radius, y + radius)


def _line_bbox(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _points_bbox(
    points: tuple[tuple[float, float], ...] | list[tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))
