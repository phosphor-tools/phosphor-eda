"""Graphics, board-profile, text, and keepout extraction for Allegro boards."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbCircle,
    PcbClosedPath,
    PcbLine,
    PcbObjectMetadata,
    PcbPathSegment,
    PcbPathSegmentKind,
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
    AllegroGraphicPrimitive,
    AllegroGraphics,
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
_OUTLINE_SUBCLASSES = {0xEA, 0xFD}
_FILLED_RECTANGLE_ROLES = (
    LayerRole.COPPER,
    LayerRole.SOLDER_MASK,
    LayerRole.SOLDER_PASTE,
)


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
    shape_owned_void_keys = _shape_owned_void_keys(record_set, graph)

    for record in record_set.records:
        if record.key is None:
            continue
        if record.tag == 0x0A:
            diagnostics.append(drc_marker_diagnostic(record))
            continue
        if record.tag in {0x0E, 0x24}:
            rectangle = rectangle_primitive(
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
            if record.key in shape_owned_void_keys:
                # This 0x34 is a void cut into a 0x28 shape's fill (a hole on the
                # fill conductor). Emitting it here too would double-render the
                # clearance as a standalone keepout.
                continue
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
        layer = record_layer(record, layer_map)
        if layer is None:
            diagnostics.append(missing_layer_diagnostic(record))
            continue
        roles = _roles_for_record(record, layer)
        primitives = graphic_segment_primitives(
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


def rectangle_primitive(
    record: AllegroRecord,
    *,
    frame: BoardFrame | None,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> AllegroGraphicPrimitive | None:
    if frame is None:
        diagnostics.append(missing_header_diagnostic(record))
        return None
    layer = record_layer(record, layer_map)
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
    fill = _is_filled_rectangle_layer(layer)
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
        metadata=record_metadata(record, layer),
    )


def _is_filled_rectangle_layer(layer: PcbLayer) -> bool:
    # Fill only physical apertures (copper, mask, paste); documentation
    # graphics are stroked outlines. Failing safe to "outline" keeps new or
    # unmapped roles from silently rendering as filled slabs.
    return any(layer.has_role(role) for role in _FILLED_RECTANGLE_ROLES)


_SHAPE_VOID_RECORD_TAGS = frozenset({0x34})


def _shape_owned_void_keys(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
) -> frozenset[int]:
    """Collect 0x34 void keys reachable from any 0x28 shape's keepout chain.

    These voids are cut into the shape's copper fill as holes elsewhere, so the
    keepout branch must not also emit them as standalone keepout primitives.
    """
    owned: set[int] = set()
    for shape in record_set.records:
        if shape.tag != 0x28 or shape.key is None:
            continue
        walk = graph.walk_key_chain(
            head_key=payload_int(shape, "first_keepout_key"),
            owner_key=shape.key,
            expected_tags=_SHAPE_VOID_RECORD_TAGS,
        )
        owned.update(void.key for void in walk.records if void.key is not None)
    return frozenset(owned)


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
    layer = record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(missing_layer_diagnostic(record))
        return None
    segment_key = payload_int(record, "first_segment_key")
    path = closed_path_from_segment_chain(
        record,
        graph=graph,
        frame=frame,
        head_key=segment_key,
        diagnostics=diagnostics,
        diagnostic_prefix="keepout",
    )
    if path is None:
        return None
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.POLYGON,
        roles=(AllegroPrimitiveRole.KEEPOUT,),
        data=path,
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=record_metadata(record, layer),
    )


def closed_path_from_segment_chain(
    record: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    frame: BoardFrame | None,
    head_key: int,
    diagnostics: list[AllegroRecordDiagnostic],
    diagnostic_prefix: str,
) -> PcbClosedPath | None:
    """Build a closed boundary path from a record's segment chain.

    Line segments become LINE path segments and arc segments keep their native
    curvature as ARC path segments; a full-circle arc (start == end) becomes
    two complementary half-circle arcs so circular boundaries survive intact.
    """
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
    segments: list[PcbPathSegment] = []
    for segment in owned_segment_chain(
        record,
        graph=graph,
        head_key=head_key,
        diagnostics=diagnostics,
    ):
        start = frame.point(payload_int(segment, "start_x"), payload_int(segment, "start_y"))
        end = frame.point(payload_int(segment, "end_x"), payload_int(segment, "end_y"))
        if segment.tag != 0x01:
            segments.append(
                PcbPathSegment(PcbPathSegmentKind.LINE, start[0], start[1], end[0], end[1])
            )
        elif _is_full_circle_arc(segment):
            segments.extend(_full_circle_path_segments(segment, frame))
        else:
            mid_x, mid_y = _arc_midpoint(segment, frame)
            segments.append(
                PcbPathSegment(
                    PcbPathSegmentKind.ARC,
                    start[0],
                    start[1],
                    end[0],
                    end[1],
                    mid_x=mid_x,
                    mid_y=mid_y,
                )
            )
    has_arc = any(segment.kind is PcbPathSegmentKind.ARC for segment in segments)
    if len(segments) < 3 and not has_arc:
        diagnostics.append(
            drop_diagnostic(
                record,
                code=f"invalid-{diagnostic_prefix}-boundary",
                message=(
                    f"{diagnostic_prefix} record {record.key} resolved to "
                    f"{len(segments)} line segments"
                ),
            )
        )
        return None
    return PcbClosedPath(segments=tuple(segments))


def _full_circle_path_segments(
    segment: AllegroRecord, frame: BoardFrame
) -> tuple[PcbPathSegment, PcbPathSegment]:
    """Split a full-circle arc record into two complementary half-circle arcs."""
    start_x, start_y = frame.point(payload_int(segment, "start_x"), payload_int(segment, "start_y"))
    center_x, center_y = frame.point(
        payload_float(segment, "center_x"), payload_float(segment, "center_y")
    )
    vector_x = start_x - center_x
    vector_y = start_y - center_y
    antipode_x = center_x - vector_x
    antipode_y = center_y - vector_y
    mid_1 = (center_x - vector_y, center_y + vector_x)
    mid_2 = (center_x + vector_y, center_y - vector_x)
    return (
        PcbPathSegment(
            PcbPathSegmentKind.ARC,
            start_x,
            start_y,
            antipode_x,
            antipode_y,
            mid_x=mid_1[0],
            mid_y=mid_1[1],
        ),
        PcbPathSegment(
            PcbPathSegmentKind.ARC,
            antipode_x,
            antipode_y,
            start_x,
            start_y,
            mid_x=mid_2[0],
            mid_y=mid_2[1],
        ),
    )


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
    layer = record_layer(record, layer_map)
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
    metadata = record_metadata(record, layer)
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


def graphic_segment_primitives(
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
    for segment in owned_segment_chain(
        record,
        graph=graph,
        head_key=segment_key,
        diagnostics=diagnostics,
    ):
        if segment.key is None:
            continue
        primitive = line_or_arc_primitive(
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


def line_or_arc_primitive(
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
            metadata=record_metadata(owner, layer),
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
                metadata=record_metadata(owner, layer),
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
            metadata=record_metadata(owner, layer),
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


def owned_segment_chain(
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


def record_layer(record: AllegroRecord, layer_map: AllegroLayerMap) -> PcbLayer | None:
    return layer_map.layer_for_class_subclass(
        payload_int(record, "layer_class_id"),
        payload_int(record, "layer_subclass_id"),
    )


def record_metadata(record: AllegroRecord, layer: PcbLayer) -> PcbObjectMetadata:
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
