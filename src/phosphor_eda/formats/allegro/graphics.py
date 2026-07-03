"""Graphics, copper, board-profile, text, and keepout extraction for Allegro boards."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbCircle,
    PcbConductorKind,
    PcbLine,
    PcbObjectMetadata,
    PcbPolygon,
    PcbText,
)
from phosphor_eda.formats.allegro.coords import BoardFrame, board_frame
from phosphor_eda.formats.allegro.diagnostics import (
    drc_marker_diagnostic,
    drop_diagnostic,
    missing_header_diagnostic,
    missing_layer_diagnostic,
    missing_payload_diagnostic,
)
from phosphor_eda.formats.allegro.graph import build_allegro_object_graph
from phosphor_eda.formats.allegro.primitives import (
    AllegroConductorPrimitive,
    AllegroCopper,
    AllegroGraphicPrimitive,
    AllegroGraphics,
    AllegroPourPrimitive,
    AllegroPrimitiveKind,
    AllegroPrimitiveRole,
)
from phosphor_eda.formats.allegro.records import (
    AllegroRecordDiagnostic,
    payload_coords,
    payload_float,
    payload_int,
)

_FALLBACK_TEXT_FONT_SIZE_MM = 1.0
_DEFAULT_RECTANGLE_STROKE_WIDTH_MM = 0.12

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import PcbLayer
    from phosphor_eda.formats.allegro.graph import AllegroObjectGraph
    from phosphor_eda.formats.allegro.layers import AllegroLayerMap
    from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

_CLASS_BOARD_GEOMETRY = 0x01
_CLASS_ETCH = 0x06
_OUTLINE_SUBCLASSES = {0xEA, 0xFD}
_OUTLINE_RECTANGLE_ROLES = {
    LayerRole.SILKSCREEN,
    LayerRole.FABRICATION,
    LayerRole.ASSEMBLY,
    LayerRole.COURTYARD,
    LayerRole.DESIGNATOR,
    LayerRole.VALUE,
    LayerRole.USER,
    LayerRole.MECHANICAL,
    LayerRole.DIMENSION,
    LayerRole.EDGE,
    LayerRole.BOARD,
    LayerRole.BOARD_SHAPE,
}


@dataclass(frozen=True)
class _NetAssignedItem:
    assignment: AllegroRecord
    item: AllegroRecord
    net_key: int


def extract_allegro_graphics(
    record_set: AllegroRecordSet,
    layer_map: AllegroLayerMap,
) -> AllegroGraphics:
    graph = build_allegro_object_graph(record_set)
    frame = board_frame(record_set.header)
    diagnostics: list[AllegroRecordDiagnostic] = list(graph.diagnostics)
    board_profile: list[AllegroGraphicPrimitive] = []
    artwork: list[AllegroGraphicPrimitive] = []
    keepouts: list[AllegroGraphicPrimitive] = []

    for record in record_set.records:
        if record.key is None:
            continue
        if record.tag == 0x0A:
            diagnostics.append(drc_marker_diagnostic(record))
            continue
        if record.tag in {0x0E, 0x24}:
            rectangle = _rectangle_primitive(
                record,
                frame=frame,
                layer_map=layer_map,
                diagnostics=diagnostics,
            )
            if rectangle is not None:
                if rectangle.has_role(AllegroPrimitiveRole.BOARD_PROFILE):
                    board_profile.append(rectangle)
                else:
                    artwork.append(rectangle)
            continue
        if record.tag == 0x34:
            keepout = _keepout_primitive(
                record,
                graph=graph,
                frame=frame,
                layer_map=layer_map,
                diagnostics=diagnostics,
            )
            if keepout is not None:
                keepouts.append(keepout)
            continue
        if record.tag == 0x30:
            text = _text_primitive(
                record,
                graph=graph,
                frame=frame,
                layer_map=layer_map,
                diagnostics=diagnostics,
            )
            if text is not None:
                artwork.append(text)
            continue
        if record.tag != 0x14:
            continue
        layer = _record_layer(record, layer_map)
        if layer is None:
            diagnostics.append(missing_layer_diagnostic(record))
            continue
        roles = _roles_for_record(record, layer)
        primitives = _graphic_segment_primitives(
            record,
            graph=graph,
            frame=frame,
            layer=layer,
            roles=roles,
            diagnostics=diagnostics,
        )
        if AllegroPrimitiveRole.BOARD_PROFILE in roles:
            board_profile.extend(primitives)
        else:
            artwork.extend(primitives)

    return AllegroGraphics(
        board_profile=tuple(board_profile),
        artwork=tuple(artwork),
        keepouts=tuple(keepouts),
        diagnostics=tuple(diagnostics),
    )


def extract_allegro_copper(
    record_set: AllegroRecordSet,
    layer_map: AllegroLayerMap,
    graph: AllegroObjectGraph | None = None,
) -> AllegroCopper:
    graph = graph or build_allegro_object_graph(record_set)
    frame = board_frame(record_set.header)
    diagnostics: list[AllegroRecordDiagnostic] = list(graph.diagnostics)
    net_items = _net_assigned_items(record_set, graph, diagnostics)
    shape_pours, shape_fills = _shape_pours_and_fills(
        record_set,
        graph,
        layer_map,
        diagnostics,
        frame=frame,
        net_items=net_items,
    )
    conductors = [
        *_track_conductors(
            record_set, graph, layer_map, diagnostics, frame=frame, net_items=net_items
        ),
        *shape_fills,
        *_rectangle_region_conductors(record_set, layer_map, diagnostics, frame=frame),
        *_graphic_segment_conductors(record_set, graph, layer_map, diagnostics, frame=frame),
    ]
    return AllegroCopper(
        pours=shape_pours,
        conductors=tuple(conductors),
        diagnostics=tuple(diagnostics),
    )


def _track_conductors(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
    *,
    frame: BoardFrame | None,
    net_items: tuple[_NetAssignedItem, ...],
) -> tuple[AllegroConductorPrimitive, ...]:
    conductors: list[AllegroConductorPrimitive] = []
    for item in net_items:
        track = item.item
        if track.tag != 0x05:
            continue
        layer = _copper_layer(track, layer_map, diagnostics, require_etch=True)
        if layer is None:
            continue
        segment_key = payload_int(track, "first_segment_key")
        if segment_key == 0:
            diagnostics.append(
                drop_diagnostic(
                    track,
                    code="missing-track-segment-chain",
                    message=f"track record {track.key} has no first segment key",
                )
            )
            continue
        for segment in _owned_segment_chain(
            track,
            graph=graph,
            head_key=segment_key,
            diagnostics=diagnostics,
        ):
            conductor = _track_conductor_primitive(
                segment,
                track=track,
                frame=frame,
                layer=layer,
                net_key=item.net_key,
            )
            if conductor is not None:
                conductors.append(conductor)
    return tuple(conductors)


def _shape_pours_and_fills(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
    *,
    frame: BoardFrame | None,
    net_items: tuple[_NetAssignedItem, ...],
) -> tuple[tuple[AllegroPourPrimitive, ...], tuple[AllegroConductorPrimitive, ...]]:
    pours: list[AllegroPourPrimitive] = []
    fills: list[AllegroConductorPrimitive] = []
    assigned_shapes = {
        item.item.key: item
        for item in net_items
        if item.item.tag == 0x28 and item.item.key is not None
    }
    for shape in (record for record in record_set.records if record.tag == 0x28):
        item = assigned_shapes.get(shape.key) if shape.key is not None else None
        if item is None and payload_int(shape, "first_keepout_key") == 0:
            # Unassigned, unvoided copper shapes commonly include package-symbol
            # pad geometry in local footprint coordinates. Keep them out of the
            # board-level copper model until native ownership is known.
            continue
        layer = _copper_layer(shape, layer_map, diagnostics, require_etch=True)
        if layer is None:
            continue
        polygon = _polygon_from_segment_chain(
            shape,
            graph=graph,
            frame=frame,
            layer=layer,
            head_key=payload_int(shape, "first_segment_key"),
            diagnostics=diagnostics,
            diagnostic_prefix="shape",
        )
        if polygon is None:
            continue
        holes = _shape_void_holes(
            shape,
            graph=graph,
            frame=frame,
            layer=layer,
            diagnostics=diagnostics,
        )
        fill_polygon = PcbPolygon(points=polygon.points, holes=holes)
        pour_id = f"allegro:{shape.key}:pour"
        metadata = _shape_fill_metadata(
            shape,
            layer=layer,
            assignment_key=None if item is None else item.assignment.key,
            net_key=None if item is None else item.net_key,
            void_hole_count=len(holes),
        )
        dynamic_shape_flags = payload_int(shape, "dynamic_shape_flags")
        if dynamic_shape_flags & 0x1000:
            diagnostics.append(
                drop_diagnostic(
                    shape,
                    code="unsupported-dynamic-shape-rules",
                    message=(
                        f"dynamic shape record {shape.key} has native flags "
                        f"0x{dynamic_shape_flags:X}; preserving fill geometry without "
                        "reconstructing Allegro dynamic rules"
                    ),
                )
            )
        pours.append(
            AllegroPourPrimitive(
                id=pour_id,
                boundary=polygon,
                layer=layer,
                net_key=None if item is None else item.net_key,
                metadata=replace(metadata, native_type="copper_shape_pour"),
            )
        )
        first_keepout_key = payload_int(shape, "first_keepout_key")
        if first_keepout_key and not holes:
            diagnostics.append(
                drop_diagnostic(
                    shape,
                    code="unsupported-shape-voids",
                    message=(
                        f"shape record {shape.key} references void/keepout chain "
                        f"{first_keepout_key}; skipping uncut positive copper fill"
                    ),
                    reference_key=first_keepout_key,
                )
            )
            continue
        fills.append(
            AllegroConductorPrimitive(
                id=f"allegro:{shape.key}:fill",
                kind=PcbConductorKind.POUR_FILL,
                data=fill_polygon,
                layer=layer,
                net_key=None if item is None else item.net_key,
                pour_id=pour_id,
                metadata=metadata,
            )
        )
    return tuple(pours), tuple(fills)


_VOID_RECORD_TAGS = frozenset({0x34})
_VOID_CHAIN_CODES = {
    "linked-list-cycle": "shape-void-chain-cycle",
    "unresolved-reference": "unresolved-shape-void",
    "unexpected-record-tag": "invalid-shape-void-record",
}


def _shape_void_holes(
    shape: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    frame: BoardFrame | None,
    layer: PcbLayer,
    diagnostics: list[AllegroRecordDiagnostic],
) -> list[list[tuple[float, float]]]:
    holes: list[list[tuple[float, float]]] = []
    first_keepout_key = payload_int(shape, "first_keepout_key")
    if first_keepout_key == 0:
        return holes
    walk = graph.walk_key_chain(
        head_key=first_keepout_key,
        owner_key=shape.key,
        expected_tags=_VOID_RECORD_TAGS,
    )
    for diagnostic in walk.diagnostics:
        diagnostics.append(
            drop_diagnostic(
                shape,
                code=_VOID_CHAIN_CODES[diagnostic.code],
                message=f"shape record {shape.key} void chain degraded: {diagnostic.message}",
                reference_key=diagnostic.reference_key,
            )
        )
    for void in walk.records:
        polygon = _polygon_from_segment_chain(
            void,
            graph=graph,
            frame=frame,
            layer=layer,
            head_key=payload_int(void, "first_segment_key"),
            diagnostics=diagnostics,
            diagnostic_prefix="shape-void",
        )
        if polygon is not None:
            holes.append(polygon.points)
    return holes


def _rectangle_region_conductors(
    record_set: AllegroRecordSet,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
    *,
    frame: BoardFrame | None,
) -> tuple[AllegroConductorPrimitive, ...]:
    conductors: list[AllegroConductorPrimitive] = []
    for record in record_set.records:
        if record.tag not in {0x0E, 0x24} or record.key is None:
            continue
        if _copper_layer(record, layer_map, diagnostics, require_etch=False) is None:
            continue
        rectangle = _rectangle_primitive(
            record,
            frame=frame,
            layer_map=layer_map,
            diagnostics=diagnostics,
        )
        if rectangle is not None:
            conductor = _rectangle_region_conductor_primitive(record, rectangle)
            if conductor is not None:
                conductors.append(conductor)
    return tuple(conductors)


def _graphic_segment_conductors(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
    *,
    frame: BoardFrame | None,
) -> tuple[AllegroConductorPrimitive, ...]:
    conductors: list[AllegroConductorPrimitive] = []
    for record in record_set.records:
        if record.tag != 0x14 or record.key is None:
            continue
        layer = _copper_layer(record, layer_map, diagnostics, require_etch=True)
        if layer is None:
            continue
        footprint_key = _parent_footprint_key(payload_int(record, "parent_key"), graph)
        primitives = _graphic_segment_primitives(
            record,
            graph=graph,
            frame=frame,
            layer=layer,
            roles=(AllegroPrimitiveRole.ARTWORK,),
            diagnostics=diagnostics,
        )
        for primitive in primitives:
            conductor = _graphic_conductor_primitive(primitive, footprint_key=footprint_key)
            if conductor is not None:
                conductors.append(conductor)
    return tuple(conductors)


def _copper_layer(
    record: AllegroRecord,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
    *,
    require_etch: bool,
) -> PcbLayer | None:
    layer = _record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(missing_layer_diagnostic(record))
        return None
    if require_etch and payload_int(record, "layer_class_id") != _CLASS_ETCH:
        return None
    return layer if layer.has_role(LayerRole.COPPER) else None


_NET_ASSIGNMENT_TAGS = frozenset({0x04})
_NET_ASSIGNMENT_CHAIN_CODES = {
    "linked-list-cycle": "net-assignment-cycle",
    "unresolved-reference": "unresolved-net-assignment",
    "unexpected-record-tag": "invalid-net-assignment-record",
}


def _net_assigned_items(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[_NetAssignedItem, ...]:
    result: list[_NetAssignedItem] = []
    for net in (record for record in record_set.records if record.tag == 0x1B):
        net_key = net.key
        if net_key is None:
            continue
        walk = graph.walk_key_chain(
            head_key=payload_int(net, "assignment_key"),
            owner_key=net_key,
            expected_tags=_NET_ASSIGNMENT_TAGS,
        )
        for diagnostic in walk.diagnostics:
            diagnostics.append(
                drop_diagnostic(
                    net,
                    code=_NET_ASSIGNMENT_CHAIN_CODES[diagnostic.code],
                    message=(
                        f"net record {net.key} assignment chain degraded: {diagnostic.message}"
                    ),
                    reference_key=diagnostic.reference_key,
                )
            )
        for assignment in walk.records:
            connected_key = payload_int(assignment, "connected_item_key")
            connected_item = graph.by_key.get(connected_key)
            if connected_key and connected_item is None:
                diagnostics.append(
                    drop_diagnostic(
                        assignment,
                        code="unresolved-connected-copper-item",
                        message=(
                            f"net assignment {assignment.key} references missing "
                            f"connected item {connected_key}"
                        ),
                        reference_key=connected_key,
                    )
                )
            if connected_item is not None:
                result.append(
                    _NetAssignedItem(
                        assignment=assignment,
                        item=connected_item,
                        net_key=payload_int(assignment, "net_key") or net_key,
                    )
                )
    return tuple(result)


def _track_conductor_primitive(
    record: AllegroRecord,
    *,
    track: AllegroRecord,
    frame: BoardFrame | None,
    layer: PcbLayer,
    net_key: int,
) -> AllegroConductorPrimitive | None:
    if frame is None:
        return None
    graphic = _line_or_arc_primitive(
        record,
        owner=track,
        frame=frame,
        layer=layer,
        roles=(AllegroPrimitiveRole.ARTWORK,),
    )
    if graphic is None:
        return None
    if not isinstance(graphic.data, PcbLine | PcbArc):
        return None
    kind = (
        PcbConductorKind.TRACE_ARC
        if graphic.kind is AllegroPrimitiveKind.ARC
        else PcbConductorKind.TRACE
    )
    properties = dict(graphic.metadata.properties)
    properties["native_track_key"] = str(track.key or "")
    properties["native_net_key"] = str(net_key)
    return AllegroConductorPrimitive(
        id=graphic.id,
        kind=kind,
        data=graphic.data,
        layer=layer,
        net_key=net_key,
        metadata=replace(
            graphic.metadata,
            native_type="track_segment" if kind is PcbConductorKind.TRACE else "track_arc",
            native_id=str(record.key or ""),
            properties=properties,
        ),
    )


def _graphic_conductor_primitive(
    primitive: AllegroGraphicPrimitive,
    *,
    footprint_key: int | None,
) -> AllegroConductorPrimitive | None:
    if primitive.layer is None or not isinstance(primitive.data, PcbLine | PcbArc | PcbCircle):
        return None
    kind = (
        PcbConductorKind.TRACE_ARC
        if primitive.kind in {AllegroPrimitiveKind.ARC, AllegroPrimitiveKind.CIRCLE}
        else PcbConductorKind.TRACE
    )
    properties = dict(primitive.metadata.properties)
    return AllegroConductorPrimitive(
        id=primitive.id,
        kind=kind,
        data=primitive.data,
        layer=primitive.layer,
        footprint_key=footprint_key,
        metadata=replace(
            primitive.metadata,
            native_type=(
                "copper_graphic_arc"
                if kind is PcbConductorKind.TRACE_ARC
                else "copper_graphic_segment"
            ),
            native_id=str(primitive.source_key),
            properties=properties,
        ),
    )


def _rectangle_region_conductor_primitive(
    record: AllegroRecord,
    primitive: AllegroGraphicPrimitive,
) -> AllegroConductorPrimitive | None:
    if primitive.layer is None or not isinstance(primitive.data, PcbPolygon):
        return None
    properties = dict(primitive.metadata.properties)
    footprint_key = payload_int(record, "footprint_key")
    if footprint_key:
        properties["native_footprint_key"] = str(footprint_key)
    return AllegroConductorPrimitive(
        id=primitive.id,
        kind=PcbConductorKind.COPPER_REGION,
        data=primitive.data,
        layer=primitive.layer,
        footprint_key=footprint_key or None,
        metadata=replace(
            primitive.metadata,
            native_type="copper_rectangle_region",
            native_id=str(record.key or ""),
            properties=properties,
        ),
    )


def _rectangle_primitive(
    record: AllegroRecord,
    *,
    frame: BoardFrame | None,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> AllegroGraphicPrimitive | None:
    if frame is None:
        diagnostics.append(missing_header_diagnostic(record))
        return None
    layer = _record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(missing_layer_diagnostic(record))
        return None
    coords = payload_coords(record, "coords")
    if coords is None:
        diagnostics.append(missing_payload_diagnostic(record, "coords"))
        return None
    x0, y0, x1, y1 = coords
    left = frame.x(min(x0, x1))
    right = frame.x(max(x0, x1))
    top = frame.y(max(y0, y1))
    bottom = frame.y(min(y0, y1))
    roles = _roles_for_record(record, layer)
    fill = not _is_outline_rectangle_layer(layer)
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.RECTANGLE,
        roles=roles,
        data=PcbPolygon(
            points=[(left, top), (right, top), (right, bottom), (left, bottom)],
            width=0.0 if fill else _DEFAULT_RECTANGLE_STROKE_WIDTH_MM,
            fill=fill,
        ),
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=_metadata(record, layer),
    )


def _is_outline_rectangle_layer(layer: PcbLayer) -> bool:
    return any(layer.has_role(role) for role in _OUTLINE_RECTANGLE_ROLES)


def _keepout_primitive(
    record: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    frame: BoardFrame | None,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> AllegroGraphicPrimitive | None:
    if frame is None:
        diagnostics.append(missing_header_diagnostic(record))
        return None
    layer = _record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(missing_layer_diagnostic(record))
        return None
    segment_key = payload_int(record, "first_segment_key")
    polygon = _polygon_from_segment_chain(
        record,
        graph=graph,
        frame=frame,
        layer=layer,
        head_key=segment_key,
        diagnostics=diagnostics,
        diagnostic_prefix="keepout",
    )
    if polygon is None:
        return None
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.POLYGON,
        roles=(AllegroPrimitiveRole.KEEPOUT,),
        data=polygon,
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=_metadata(record, layer),
    )


def _polygon_from_segment_chain(
    record: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    frame: BoardFrame | None,
    layer: PcbLayer,
    head_key: int,
    diagnostics: list[AllegroRecordDiagnostic],
    diagnostic_prefix: str,
) -> PcbPolygon | None:
    if frame is None:
        diagnostics.append(missing_header_diagnostic(record))
        return None
    if head_key == 0:
        diagnostics.append(
            drop_diagnostic(
                record,
                code=f"missing-{diagnostic_prefix}-segment-chain",
                message=f"{diagnostic_prefix} record {record.key} has no first segment key",
            )
        )
        return None
    points: list[tuple[float, float]] = []
    for segment in _owned_segment_chain(
        record,
        graph=graph,
        head_key=head_key,
        diagnostics=diagnostics,
    ):
        if segment.tag == 0x01:
            diagnostics.append(
                drop_diagnostic(
                    record,
                    code=f"approximated-{diagnostic_prefix}-arc",
                    message=(
                        f"{diagnostic_prefix} record {record.key} includes arc segment "
                        f"{segment.key}; polygon boundary preserves only segment vertices"
                    ),
                    reference_key=segment.key,
                )
            )
        points.append(
            frame.point(
                payload_int(segment, "start_x"),
                payload_int(segment, "start_y"),
            )
        )
    if len(points) < 3:
        diagnostics.append(
            drop_diagnostic(
                record,
                code=f"invalid-{diagnostic_prefix}-boundary",
                message=f"{diagnostic_prefix} record {record.key} resolved to {len(points)} points",
            )
        )
        return None
    return PcbPolygon(points=points)


def _text_primitive(
    record: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    frame: BoardFrame | None,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> AllegroGraphicPrimitive | None:
    if frame is None:
        diagnostics.append(missing_header_diagnostic(record))
        return None
    layer = _record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(missing_layer_diagnostic(record))
        return None
    text_key = payload_int(record, "string_graphic_key")
    text_record = graph.by_key.get(text_key)
    if text_record is None:
        diagnostics.append(
            drop_diagnostic(
                record,
                code="unresolved-text-record",
                message=f"text wrapper {record.key} references missing text record {text_key}",
                reference_key=text_key,
            )
        )
        return None
    text_value = text_record.payload.get("text", "")
    if not isinstance(text_value, str) or not text_value:
        diagnostics.append(
            drop_diagnostic(
                record,
                code="empty-text-record",
                message=f"text wrapper {record.key} references an empty text record {text_key}",
                reference_key=text_key,
            )
        )
        return None
    metadata = _metadata(record, layer)
    properties = dict(metadata.properties)
    properties["native_text_key"] = str(text_key)
    properties["native_font_key"] = str(payload_int(record, "font_key"))
    if "text_alignment_code" in record.payload:
        properties["native_text_alignment_code"] = str(payload_int(record, "text_alignment_code"))
    metadata = replace(metadata, properties=properties)
    diagnostics.append(
        drop_diagnostic(
            record,
            code="unresolved-text-size",
            message=(f"text wrapper {record.key} preserves text content with fallback native size"),
            reference_key=text_key,
        )
    )
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.TEXT,
        roles=(AllegroPrimitiveRole.ARTWORK, AllegroPrimitiveRole.TEXT),
        data=PcbText(
            text=text_value,
            x=frame.x(payload_int(record, "x")),
            y=frame.y(payload_int(record, "y")),
            rotation=payload_int(record, "rotation_mdeg") / 1000.0,
            font_size=_FALLBACK_TEXT_FONT_SIZE_MM,
        ),
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=metadata,
    )


def _graphic_segment_primitives(
    record: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    frame: BoardFrame | None,
    layer: PcbLayer,
    roles: tuple[AllegroPrimitiveRole, ...],
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[AllegroGraphicPrimitive, ...]:
    segment_key = payload_int(record, "segment_key")
    if frame is None:
        diagnostics.append(missing_header_diagnostic(record))
        return ()
    if segment_key == 0:
        diagnostics.append(
            drop_diagnostic(
                record,
                code="missing-graphic-segment-chain",
                message=f"graphic record {record.key} has no segment key",
            )
        )
        return ()
    primitives: list[AllegroGraphicPrimitive] = []
    parent_key = payload_int(record, "parent_key")
    for segment in _owned_segment_chain(
        record,
        graph=graph,
        head_key=segment_key,
        diagnostics=diagnostics,
    ):
        if segment.key is None:
            continue
        primitive = _line_or_arc_primitive(
            segment,
            owner=record,
            frame=frame,
            layer=layer,
            roles=roles,
        )
        if primitive is not None:
            primitive = replace(
                primitive,
                metadata=_with_parent_metadata(primitive.metadata, parent_key, graph),
            )
            primitives.append(primitive)
    return tuple(primitives)


def _line_or_arc_primitive(
    record: AllegroRecord,
    *,
    owner: AllegroRecord,
    frame: BoardFrame,
    layer: PcbLayer,
    roles: tuple[AllegroPrimitiveRole, ...],
) -> AllegroGraphicPrimitive | None:
    if record.tag in {0x15, 0x16, 0x17}:
        start_x, start_y = frame.point(
            payload_int(record, "start_x"), payload_int(record, "start_y")
        )
        end_x, end_y = frame.point(payload_int(record, "end_x"), payload_int(record, "end_y"))
        return AllegroGraphicPrimitive(
            id=f"allegro:{record.key}",
            kind=AllegroPrimitiveKind.LINE,
            roles=roles,
            data=PcbLine(
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                width=frame.length(payload_int(record, "width")),
            ),
            layer=layer,
            source_tag=record.tag,
            source_key=record.key or 0,
            metadata=_metadata(owner, layer),
        )
    if record.tag == 0x01:
        if _is_full_circle_arc(record):
            cx, cy = frame.point(
                payload_float(record, "center_x"), payload_float(record, "center_y")
            )
            return AllegroGraphicPrimitive(
                id=f"allegro:{record.key}",
                kind=AllegroPrimitiveKind.CIRCLE,
                roles=roles,
                data=PcbCircle(
                    cx=cx,
                    cy=cy,
                    radius=frame.length(payload_float(record, "radius")),
                    width=frame.length(payload_int(record, "width")),
                    fill=False,
                ),
                layer=layer,
                source_tag=record.tag,
                source_key=record.key or 0,
                metadata=_metadata(owner, layer),
            )
        mid_x, mid_y = _arc_midpoint(record, frame)
        start_x, start_y = frame.point(
            payload_int(record, "start_x"), payload_int(record, "start_y")
        )
        end_x, end_y = frame.point(payload_int(record, "end_x"), payload_int(record, "end_y"))
        return AllegroGraphicPrimitive(
            id=f"allegro:{record.key}",
            kind=AllegroPrimitiveKind.ARC,
            roles=roles,
            data=PcbArc(
                start_x=start_x,
                start_y=start_y,
                mid_x=mid_x,
                mid_y=mid_y,
                end_x=end_x,
                end_y=end_y,
                width=frame.length(payload_int(record, "width")),
            ),
            layer=layer,
            source_tag=record.tag,
            source_key=record.key or 0,
            metadata=_metadata(owner, layer),
        )
    return None


def _is_full_circle_arc(record: AllegroRecord) -> bool:
    return (
        payload_int(record, "start_x") == payload_int(record, "end_x")
        and payload_int(record, "start_y") == payload_int(record, "end_y")
        and payload_float(record, "radius") > 0.0
    )


def _arc_midpoint(record: AllegroRecord, frame: BoardFrame) -> tuple[float, float]:
    # Midpoint is derived in the native Y-up frame (frame.length, no sign) so the
    # angle math matches Allegro; only the returned Y is flipped into the domain
    # Y-down frame at the end.
    center_x = frame.length(payload_float(record, "center_x"))
    center_y = frame.length(payload_float(record, "center_y"))
    radius = frame.length(payload_float(record, "radius"))
    if radius <= 0.0:
        return center_x, -center_y

    start_x = frame.length(payload_int(record, "start_x"))
    start_y = frame.length(payload_int(record, "start_y"))
    end_x = frame.length(payload_int(record, "end_x"))
    end_y = frame.length(payload_int(record, "end_y"))
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(end_y - center_y, end_x - center_x)
    clockwise = (payload_int(record, "subtype") & 0x40) != 0
    if clockwise:
        if start_angle < end_angle:
            start_angle += math.tau
    elif end_angle < start_angle:
        end_angle += math.tau
    mid_angle = (start_angle + end_angle) / 2.0
    return (
        center_x + radius * math.cos(mid_angle),
        -(center_y + radius * math.sin(mid_angle)),
    )


_SEGMENT_RECORD_TAGS = frozenset({0x01, 0x15, 0x16, 0x17})
_SEGMENT_CHAIN_CODES = {
    "linked-list-cycle": "segment-chain-cycle",
    "unresolved-reference": "unresolved-segment-record",
    "unexpected-record-tag": "invalid-segment-record",
    "chain-guard-rejected": "segment-owner-mismatch",
}


def _owned_segment_chain(
    owner: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    head_key: int,
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[AllegroRecord, ...]:
    def owned_by_owner(segment: AllegroRecord) -> str | None:
        parent_key = payload_int(segment, "parent_key")
        return None if parent_key == owner.key else f"owned by {parent_key}"

    walk = graph.walk_key_chain(
        head_key=head_key,
        owner_key=owner.key,
        expected_tags=_SEGMENT_RECORD_TAGS,
        guard=owned_by_owner,
    )
    for diagnostic in walk.diagnostics:
        diagnostics.append(
            drop_diagnostic(
                owner,
                code=_SEGMENT_CHAIN_CODES[diagnostic.code],
                message=f"record {owner.key} segment chain degraded: {diagnostic.message}",
                reference_key=diagnostic.reference_key,
            )
        )
    return walk.records


def _roles_for_record(
    record: AllegroRecord,
    layer: PcbLayer,
) -> tuple[AllegroPrimitiveRole, ...]:
    class_id = payload_int(record, "layer_class_id")
    subclass_id = payload_int(record, "layer_subclass_id")
    if _is_outline_layer(class_id, subclass_id, layer):
        return (AllegroPrimitiveRole.BOARD_PROFILE,)
    return (AllegroPrimitiveRole.ARTWORK,)


def _is_outline_layer(class_id: int, subclass_id: int, layer: PcbLayer) -> bool:
    return (
        class_id == _CLASS_BOARD_GEOMETRY
        and subclass_id in _OUTLINE_SUBCLASSES
        and layer.has_role(LayerRole.BOARD_SHAPE)
    )


def _record_layer(record: AllegroRecord, layer_map: AllegroLayerMap) -> PcbLayer | None:
    return layer_map.layer_for_class_subclass(
        payload_int(record, "layer_class_id"),
        payload_int(record, "layer_subclass_id"),
    )


def _metadata(record: AllegroRecord, layer: PcbLayer) -> PcbObjectMetadata:
    class_id = payload_int(record, "layer_class_id")
    subclass_id = payload_int(record, "layer_subclass_id")
    return PcbObjectMetadata(
        source_collection="allegro_records",
        native_type=f"0x{record.tag:02X}",
        native_id=str(record.key or ""),
        native_layer_id=f"{class_id}:{subclass_id}",
        properties={
            "native_class_id": str(class_id),
            "native_subclass_id": str(subclass_id),
            "native_layer_name": layer.name,
        },
    )


def _shape_fill_metadata(
    record: AllegroRecord,
    *,
    layer: PcbLayer,
    assignment_key: int | None,
    net_key: int | None,
    void_hole_count: int,
) -> PcbObjectMetadata:
    metadata = _metadata(record, layer)
    properties = dict(metadata.properties)
    properties["native_assignment_key"] = str(assignment_key or "")
    properties["native_net_key"] = "" if net_key is None else str(net_key)
    first_keepout_key = payload_int(record, "first_keepout_key")
    if first_keepout_key:
        properties["native_first_keepout_key"] = str(first_keepout_key)
    if void_hole_count:
        properties["native_void_hole_count"] = str(void_hole_count)
    dynamic_shape_flags = payload_int(record, "dynamic_shape_flags")
    if dynamic_shape_flags:
        properties["native_dynamic_shape_flags"] = str(dynamic_shape_flags)
    if dynamic_shape_flags & 0x1000:
        properties["dynamic_shape_degraded"] = "true"
    return replace(
        metadata,
        native_type="copper_shape_fill",
        native_id=str(record.key or ""),
        properties=properties,
    )


def _with_parent_metadata(
    metadata: PcbObjectMetadata,
    parent_key: int,
    graph: AllegroObjectGraph,
) -> PcbObjectMetadata:
    properties = dict(metadata.properties)
    if parent_key:
        properties["native_parent_key"] = str(parent_key)
    parent = graph.by_key.get(parent_key)
    if parent is not None and parent.tag == 0x2D:
        properties["native_footprint_key"] = str(parent_key)
    return replace(metadata, properties=properties)


def _parent_footprint_key(parent_key: int, graph: AllegroObjectGraph) -> int | None:
    parent = graph.by_key.get(parent_key)
    if parent is not None and parent.tag == 0x2D:
        return parent_key
    return None
