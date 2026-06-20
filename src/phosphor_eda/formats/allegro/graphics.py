"""Graphics, copper, board-profile, text, and keepout extraction for Allegro boards."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbConductorKind,
    PcbLine,
    PcbObjectMetadata,
    PcbPolygon,
    PcbText,
)
from phosphor_eda.formats.allegro.constants import AllegroBoardUnits
from phosphor_eda.formats.allegro.graph import build_allegro_object_graph
from phosphor_eda.formats.allegro.primitives import (
    AllegroConductorPrimitive,
    AllegroCopper,
    AllegroGraphicPrimitive,
    AllegroGraphics,
    AllegroPrimitiveKind,
    AllegroPrimitiveRole,
)
from phosphor_eda.formats.allegro.records import AllegroRecordDiagnostic

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import PcbLayer
    from phosphor_eda.formats.allegro.graph import AllegroObjectGraph
    from phosphor_eda.formats.allegro.layers import AllegroLayerMap
    from phosphor_eda.formats.allegro.records import AllegroHeader, AllegroRecord, AllegroRecordSet

_CLASS_BOARD_GEOMETRY = 0x01
_CLASS_ETCH = 0x06
_OUTLINE_SUBCLASSES = {0xEA, 0xFD}


def extract_allegro_graphics(
    record_set: AllegroRecordSet,
    layer_map: AllegroLayerMap,
) -> AllegroGraphics:
    graph = build_allegro_object_graph(record_set)
    diagnostics: list[AllegroRecordDiagnostic] = list(graph.diagnostics)
    board_profile: list[AllegroGraphicPrimitive] = []
    artwork: list[AllegroGraphicPrimitive] = []
    keepouts: list[AllegroGraphicPrimitive] = []

    for record in record_set.records:
        if record.key is None:
            continue
        if record.tag == 0x0A:
            diagnostics.append(_drc_marker_diagnostic(record))
            continue
        if record.tag in {0x0E, 0x24}:
            rectangle = _rectangle_primitive(
                record,
                header=record_set.header,
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
                header=record_set.header,
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
                header=record_set.header,
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
            diagnostics.append(_missing_layer_diagnostic(record))
            continue
        roles = _roles_for_record(record, layer)
        primitives = _graphic_segment_primitives(
            record,
            graph=graph,
            header=record_set.header,
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
    diagnostics: list[AllegroRecordDiagnostic] = list(graph.diagnostics)
    conductors = [
        *_track_conductors(record_set, graph, layer_map, diagnostics),
        *_rectangle_region_conductors(record_set, layer_map, diagnostics),
        *_graphic_segment_conductors(record_set, graph, layer_map, diagnostics),
    ]
    return AllegroCopper(conductors=tuple(conductors), diagnostics=tuple(diagnostics))


def _track_conductors(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[AllegroConductorPrimitive, ...]:
    conductors: list[AllegroConductorPrimitive] = []
    for track, net_key in _net_owned_tracks(record_set, graph, diagnostics):
        layer = _copper_layer(track, layer_map, diagnostics, require_etch=True)
        if layer is None:
            continue
        segment_key = _payload_int(track, "first_segment_key")
        if segment_key == 0:
            diagnostics.append(
                _drop_diagnostic(
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
                header=record_set.header,
                layer=layer,
                net_key=net_key,
            )
            if conductor is not None:
                conductors.append(conductor)
    return tuple(conductors)


def _rectangle_region_conductors(
    record_set: AllegroRecordSet,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[AllegroConductorPrimitive, ...]:
    conductors: list[AllegroConductorPrimitive] = []
    for record in record_set.records:
        if record.tag not in {0x0E, 0x24} or record.key is None:
            continue
        if _copper_layer(record, layer_map, diagnostics, require_etch=False) is None:
            continue
        rectangle = _rectangle_primitive(
            record,
            header=record_set.header,
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
) -> tuple[AllegroConductorPrimitive, ...]:
    conductors: list[AllegroConductorPrimitive] = []
    for record in record_set.records:
        if record.tag != 0x14 or record.key is None:
            continue
        layer = _copper_layer(record, layer_map, diagnostics, require_etch=True)
        if layer is None:
            continue
        footprint_key = _parent_footprint_key(_payload_int(record, "parent_key"), graph)
        primitives = _graphic_segment_primitives(
            record,
            graph=graph,
            header=record_set.header,
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
        diagnostics.append(_missing_layer_diagnostic(record))
        return None
    if require_etch and _payload_int(record, "layer_class_id") != _CLASS_ETCH:
        return None
    return layer if layer.has_role(LayerRole.COPPER) else None


def _net_owned_tracks(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[tuple[AllegroRecord, int], ...]:
    result: list[tuple[AllegroRecord, int]] = []
    for net in (record for record in record_set.records if record.tag == 0x1B):
        net_key = net.key
        if net_key is None:
            continue
        current_key = _payload_int(net, "assignment_key")
        seen: set[int] = set()
        while current_key != 0:
            if current_key in seen:
                diagnostics.append(
                    _drop_diagnostic(
                        net,
                        code="net-assignment-cycle",
                        message=f"net record {net.key} assignment chain cycles at {current_key}",
                        reference_key=current_key,
                    )
                )
                break
            seen.add(current_key)
            assignment = graph.by_key.get(current_key)
            if assignment is None:
                diagnostics.append(
                    _drop_diagnostic(
                        net,
                        code="unresolved-net-assignment",
                        message=f"net record {net.key} references missing assignment {current_key}",
                        reference_key=current_key,
                    )
                )
                break
            if assignment.tag != 0x04:
                diagnostics.append(
                    _drop_diagnostic(
                        net,
                        code="invalid-net-assignment-record",
                        message=(
                            f"net record {net.key} assignment chain reached "
                            f"0x{assignment.tag:02X} record {assignment.key}"
                        ),
                        reference_key=assignment.key,
                    )
                )
                break
            connected_key = _payload_int(assignment, "connected_item_key")
            connected_item = graph.by_key.get(connected_key)
            if connected_key and connected_item is None:
                diagnostics.append(
                    _drop_diagnostic(
                        assignment,
                        code="unresolved-connected-copper-item",
                        message=(
                            f"net assignment {assignment.key} references missing "
                            f"connected item {connected_key}"
                        ),
                        reference_key=connected_key,
                    )
                )
            if connected_item is not None and connected_item.tag == 0x05:
                result.append((connected_item, _payload_int(assignment, "net_key") or net_key))
            current_key = assignment.next_key or 0
    return tuple(result)


def _track_conductor_primitive(
    record: AllegroRecord,
    *,
    track: AllegroRecord,
    header: AllegroHeader | None,
    layer: PcbLayer,
    net_key: int,
) -> AllegroConductorPrimitive | None:
    if header is None:
        return None
    graphic = _line_or_arc_primitive(
        record,
        owner=track,
        header=header,
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
    if primitive.layer is None or not isinstance(primitive.data, PcbLine | PcbArc):
        return None
    kind = (
        PcbConductorKind.TRACE_ARC
        if primitive.kind is AllegroPrimitiveKind.ARC
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
    footprint_key = _payload_int(record, "footprint_key")
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
    header: AllegroHeader | None,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> AllegroGraphicPrimitive | None:
    if header is None:
        diagnostics.append(_missing_header_diagnostic(record))
        return None
    layer = _record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(_missing_layer_diagnostic(record))
        return None
    coords = _payload_coords(record, "coords")
    if coords is None:
        diagnostics.append(_missing_payload_diagnostic(record, "coords"))
        return None
    x0, y0, x1, y1 = coords
    left = _coord_to_mm(min(x0, x1), header)
    right = _coord_to_mm(max(x0, x1), header)
    top = -_coord_to_mm(max(y0, y1), header)
    bottom = -_coord_to_mm(min(y0, y1), header)
    roles = _roles_for_record(record, layer)
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.RECTANGLE,
        roles=roles,
        data=PcbPolygon(points=[(left, top), (right, top), (right, bottom), (left, bottom)]),
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=_metadata(record, layer),
    )


def _keepout_primitive(
    record: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    header: AllegroHeader | None,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> AllegroGraphicPrimitive | None:
    if header is None:
        diagnostics.append(_missing_header_diagnostic(record))
        return None
    layer = _record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(_missing_layer_diagnostic(record))
        return None
    segment_key = _payload_int(record, "first_segment_key")
    if segment_key == 0:
        diagnostics.append(
            _drop_diagnostic(
                record,
                code="missing-keepout-segment-chain",
                message=f"keepout record {record.key} has no first segment key",
            )
        )
        return None
    points: list[tuple[float, float]] = []
    for segment in _owned_segment_chain(
        record,
        graph=graph,
        head_key=segment_key,
        diagnostics=diagnostics,
    ):
        if segment.tag == 0x01:
            diagnostics.append(
                _drop_diagnostic(
                    record,
                    code="approximated-keepout-arc",
                    message=(
                        f"keepout record {record.key} includes arc segment {segment.key}; "
                        "point-based keepout boundary preserves only segment vertices"
                    ),
                    reference_key=segment.key,
                )
            )
        points.append(
            (
                _coord_to_mm(_payload_int(segment, "start_x"), header),
                -_coord_to_mm(_payload_int(segment, "start_y"), header),
            )
        )
    if len(points) < 3:
        diagnostics.append(
            _drop_diagnostic(
                record,
                code="invalid-keepout-boundary",
                message=(f"keepout record {record.key} resolved to {len(points)} boundary points"),
            )
        )
        return None
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.POLYGON,
        roles=(AllegroPrimitiveRole.KEEPOUT,),
        data=PcbPolygon(points=points),
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=_metadata(record, layer),
    )


def _text_primitive(
    record: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    header: AllegroHeader | None,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
) -> AllegroGraphicPrimitive | None:
    if header is None:
        diagnostics.append(_missing_header_diagnostic(record))
        return None
    layer = _record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(_missing_layer_diagnostic(record))
        return None
    text_key = _payload_int(record, "string_graphic_key")
    text_record = graph.by_key.get(text_key)
    if text_record is None:
        diagnostics.append(
            _drop_diagnostic(
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
            _drop_diagnostic(
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
    properties["native_font_key"] = str(_payload_int(record, "font_key"))
    if "text_alignment_code" in record.payload:
        properties["native_text_alignment_code"] = str(_payload_int(record, "text_alignment_code"))
    metadata = replace(metadata, properties=properties)
    diagnostics.append(
        _drop_diagnostic(
            record,
            code="unresolved-text-size",
            message=(
                f"text wrapper {record.key} preserves text content but has unresolved native size"
            ),
            reference_key=text_key,
        )
    )
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.TEXT,
        roles=(AllegroPrimitiveRole.ARTWORK, AllegroPrimitiveRole.TEXT),
        data=PcbText(
            text=text_value,
            x=_coord_to_mm(_payload_int(record, "x"), header),
            y=-_coord_to_mm(_payload_int(record, "y"), header),
            rotation=_payload_int(record, "rotation_mdeg") / 1000.0,
            font_size=0.0,
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
    header: AllegroHeader | None,
    layer: PcbLayer,
    roles: tuple[AllegroPrimitiveRole, ...],
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[AllegroGraphicPrimitive, ...]:
    segment_key = _payload_int(record, "segment_key")
    if header is None:
        diagnostics.append(_missing_header_diagnostic(record))
        return ()
    if segment_key == 0:
        diagnostics.append(
            _drop_diagnostic(
                record,
                code="missing-graphic-segment-chain",
                message=f"graphic record {record.key} has no segment key",
            )
        )
        return ()
    primitives: list[AllegroGraphicPrimitive] = []
    parent_key = _payload_int(record, "parent_key")
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
            header=header,
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
    header: AllegroHeader,
    layer: PcbLayer,
    roles: tuple[AllegroPrimitiveRole, ...],
) -> AllegroGraphicPrimitive | None:
    if record.tag in {0x15, 0x16, 0x17}:
        return AllegroGraphicPrimitive(
            id=f"allegro:{record.key}",
            kind=AllegroPrimitiveKind.LINE,
            roles=roles,
            data=PcbLine(
                start_x=_coord_to_mm(_payload_int(record, "start_x"), header),
                start_y=-_coord_to_mm(_payload_int(record, "start_y"), header),
                end_x=_coord_to_mm(_payload_int(record, "end_x"), header),
                end_y=-_coord_to_mm(_payload_int(record, "end_y"), header),
                width=_coord_to_mm(_payload_int(record, "width"), header),
            ),
            layer=layer,
            source_tag=record.tag,
            source_key=record.key or 0,
            metadata=_metadata(owner, layer),
        )
    if record.tag == 0x01:
        mid_x, mid_y = _arc_midpoint(record, header)
        return AllegroGraphicPrimitive(
            id=f"allegro:{record.key}",
            kind=AllegroPrimitiveKind.ARC,
            roles=roles,
            data=PcbArc(
                start_x=_coord_to_mm(_payload_int(record, "start_x"), header),
                start_y=-_coord_to_mm(_payload_int(record, "start_y"), header),
                mid_x=mid_x,
                mid_y=mid_y,
                end_x=_coord_to_mm(_payload_int(record, "end_x"), header),
                end_y=-_coord_to_mm(_payload_int(record, "end_y"), header),
                width=_coord_to_mm(_payload_int(record, "width"), header),
            ),
            layer=layer,
            source_tag=record.tag,
            source_key=record.key or 0,
            metadata=_metadata(owner, layer),
        )
    return None


def _arc_midpoint(record: AllegroRecord, header: AllegroHeader) -> tuple[float, float]:
    center_x = _coord_to_mm(_payload_float(record, "center_x"), header)
    center_y = _coord_to_mm(_payload_float(record, "center_y"), header)
    radius = _coord_to_mm(_payload_float(record, "radius"), header)
    if radius <= 0.0:
        return center_x, -center_y

    start_x = _coord_to_mm(_payload_int(record, "start_x"), header)
    start_y = _coord_to_mm(_payload_int(record, "start_y"), header)
    end_x = _coord_to_mm(_payload_int(record, "end_x"), header)
    end_y = _coord_to_mm(_payload_int(record, "end_y"), header)
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(end_y - center_y, end_x - center_x)
    clockwise = (_payload_int(record, "subtype") & 0x40) != 0
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


def _owned_segment_chain(
    owner: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    head_key: int,
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[AllegroRecord, ...]:
    records: list[AllegroRecord] = []
    seen: set[int] = set()
    current_key = head_key
    while current_key != 0:
        if current_key in seen:
            diagnostics.append(
                _drop_diagnostic(
                    owner,
                    code="segment-chain-cycle",
                    message=(f"record {owner.key} segment chain cycles at segment {current_key}"),
                    reference_key=current_key,
                )
            )
            break
        seen.add(current_key)
        segment = graph.by_key.get(current_key)
        if segment is None:
            diagnostics.append(
                _drop_diagnostic(
                    owner,
                    code="unresolved-segment-record",
                    message=f"record {owner.key} references missing segment {current_key}",
                    reference_key=current_key,
                )
            )
            break
        parent_key = _payload_int(segment, "parent_key")
        if parent_key != owner.key:
            diagnostics.append(
                _drop_diagnostic(
                    owner,
                    code="segment-owner-mismatch",
                    message=(
                        f"record {owner.key} segment chain reached segment {current_key} "
                        f"owned by {parent_key}"
                    ),
                    reference_key=current_key,
                )
            )
            break
        records.append(segment)
        current_key = segment.next_key or 0
    return tuple(records)


def _roles_for_record(
    record: AllegroRecord,
    layer: PcbLayer,
) -> tuple[AllegroPrimitiveRole, ...]:
    class_id = _payload_int(record, "layer_class_id")
    subclass_id = _payload_int(record, "layer_subclass_id")
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
        _payload_int(record, "layer_class_id"),
        _payload_int(record, "layer_subclass_id"),
    )


def _coord_to_mm(value: float | int, header: AllegroHeader) -> float:
    source_units = float(value) / header.unit_divisor
    if header.board_units is AllegroBoardUnits.MILS:
        return source_units * 0.0254
    if header.board_units is AllegroBoardUnits.INCHES:
        return source_units * 25.4
    if header.board_units is AllegroBoardUnits.MILLIMETERS:
        return source_units
    if header.board_units is AllegroBoardUnits.CENTIMETERS:
        return source_units * 10.0
    if header.board_units is AllegroBoardUnits.MICROMETERS:
        return source_units / 1000.0
    return source_units


def _metadata(record: AllegroRecord, layer: PcbLayer) -> PcbObjectMetadata:
    class_id = _payload_int(record, "layer_class_id")
    subclass_id = _payload_int(record, "layer_subclass_id")
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


def _parent_footprint_key(parent_key: int, graph: AllegroObjectGraph) -> int | None:
    parent = graph.by_key.get(parent_key)
    if parent is not None and parent.tag == 0x2D:
        return parent_key
    return None


def _missing_layer_diagnostic(record: AllegroRecord) -> AllegroRecordDiagnostic:
    class_id = _payload_int(record, "layer_class_id")
    subclass_id = _payload_int(record, "layer_subclass_id")
    return AllegroRecordDiagnostic(
        code="unresolved-graphic-layer",
        message=(
            f"graphic record {record.key} references missing Allegro layer {class_id}:{subclass_id}"
        ),
        offset=record.offset,
        tag=record.tag,
        key=record.key,
    )


def _missing_header_diagnostic(record: AllegroRecord) -> AllegroRecordDiagnostic:
    return _drop_diagnostic(
        record,
        code="missing-allegro-header",
        message=f"graphic record {record.key} cannot be converted without an Allegro header",
    )


def _missing_payload_diagnostic(record: AllegroRecord, payload_key: str) -> AllegroRecordDiagnostic:
    return _drop_diagnostic(
        record,
        code="missing-graphic-payload",
        message=f"graphic record {record.key} is missing payload field {payload_key}",
    )


def _drop_diagnostic(
    record: AllegroRecord,
    *,
    code: str,
    message: str,
    reference_key: int | None = None,
) -> AllegroRecordDiagnostic:
    return AllegroRecordDiagnostic(
        code=code,
        message=message,
        offset=record.offset,
        tag=record.tag,
        key=record.key,
        reference_key=reference_key,
    )


def _drc_marker_diagnostic(record: AllegroRecord) -> AllegroRecordDiagnostic:
    class_id = _payload_int(record, "layer_class_id")
    subclass_id = _payload_int(record, "layer_subclass_id")
    coords = _payload_coords(record, "coords")
    coord_text = ",".join(str(coord) for coord in coords) if coords is not None else ""
    return AllegroRecordDiagnostic(
        code="drc-marker",
        message=(
            f"DRC marker {record.key} on Allegro layer {class_id}:{subclass_id}"
            + (f" has native coords {coord_text}" if coord_text else "")
        ),
        offset=record.offset,
        tag=record.tag,
        key=record.key,
    )


def _payload_int(record: AllegroRecord, key: str) -> int:
    value = record.payload.get(key, 0)
    return value if isinstance(value, int) else 0


def _payload_float(record: AllegroRecord, key: str) -> float:
    value = record.payload.get(key, 0.0)
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    return 0.0


def _payload_coords(record: AllegroRecord, key: str) -> tuple[int, int, int, int] | None:
    value = record.payload.get(key)
    if isinstance(value, tuple) and len(value) == 4:
        coords = tuple(coord for coord in value if isinstance(coord, int))
        if len(coords) == 4:
            return (coords[0], coords[1], coords[2], coords[3])
    return None
