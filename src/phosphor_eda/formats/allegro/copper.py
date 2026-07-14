"""Copper extraction (tracks, pours, fills, voids, regions) for Allegro boards."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbCircle,
    PcbClosedPath,
    PcbConductorKind,
    PcbLine,
    PcbObjectMetadata,
    PcbPolygon,
)
from phosphor_eda.formats.allegro.coords import board_frame
from phosphor_eda.formats.allegro.diagnostics import drop_diagnostic, missing_layer_diagnostic
from phosphor_eda.formats.allegro.graph import build_allegro_object_graph
from phosphor_eda.formats.allegro.graphics import (
    closed_path_from_segment_chain,
    flash_symbol_keys,
    graphic_segment_primitives,
    line_or_arc_primitive,
    owned_by_footprint_definition,
    owned_segment_chain,
    record_layer,
    record_metadata,
    rectangle_primitive,
    shape_void_holes,
)
from phosphor_eda.formats.allegro.primitives import (
    AllegroConductorPrimitive,
    AllegroCopper,
    AllegroGraphicPrimitive,
    AllegroPourPrimitive,
    AllegroPrimitiveKind,
    AllegroPrimitiveRole,
)
from phosphor_eda.formats.allegro.records import (
    AllegroRecordDiagnostic,
    payload_int,
    rectangle_owner_key,
)

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import PcbLayer
    from phosphor_eda.formats.allegro.coords import BoardFrame
    from phosphor_eda.formats.allegro.graph import AllegroObjectGraph
    from phosphor_eda.formats.allegro.layers import AllegroLayerMap
    from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

_CLASS_ETCH = 0x06


@dataclass(frozen=True)
class _NetAssignedItem:
    assignment: AllegroRecord
    item: AllegroRecord
    net_key: int


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
        *_rectangle_region_conductors(record_set, graph, layer_map, diagnostics, frame=frame),
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
    net_key_by_track = {
        item.item.key: item.net_key
        for item in net_items
        if item.item.tag == 0x05 and item.item.key is not None
    }
    for track in (record for record in record_set.records if record.tag == 0x05):
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
        # Tracks absent from the net-assignment map are un-netted board copper
        # (manually drawn etch, unrouted stubs); render them with net_key=None
        # rather than dropping the geometry.
        net_key = net_key_by_track.get(track.key) if track.key is not None else None
        for segment in owned_segment_chain(
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
                net_key=net_key,
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
    flash_keys = flash_symbol_keys(record_set)
    for shape in (record for record in record_set.records if record.tag == 0x28):
        if shape.key is None:
            # Without a stable key the pour/fill IDs would collide on
            # "allegro:None:pour" and overwrite earlier pours downstream.
            continue
        owner = graph.by_key.get(payload_int(shape, "owner_key"))
        if owner is not None and owner.tag == 0x2B:
            # Owned by a footprint definition: package-symbol pad geometry in
            # local footprint coordinates, not board-level copper. Flash-symbol
            # shapes surface through padstacks; anything else is dropped and
            # must say so.
            if payload_int(shape, "layer_class_id") == _CLASS_ETCH and shape.key not in flash_keys:
                diagnostics.append(
                    drop_diagnostic(
                        shape,
                        code="skipped-footprint-shape",
                        message=(
                            f"shape record {shape.key} is footprint-definition-owned but "
                            "not referenced as a padstack flash symbol; geometry dropped"
                        ),
                    )
                )
            continue
        item = assigned_shapes.get(shape.key)
        net_key = None if item is None else item.net_key
        assignment_key = None if item is None else item.assignment.key
        if net_key is None and owner is not None and owner.tag == 0x04:
            # Board-level shapes reference their net assignment directly even
            # when no net's assignment chain reaches them.
            owner_net_key = payload_int(owner, "net_key")
            owner_net = graph.by_key.get(owner_net_key)
            if owner_net is not None and owner_net.tag == 0x1B:
                net_key = owner_net_key
                assignment_key = owner.key
        first_keepout_key = payload_int(shape, "first_keepout_key")
        layer = _copper_layer(shape, layer_map, diagnostics, require_etch=True)
        if layer is None:
            continue
        boundary = closed_path_from_segment_chain(
            shape,
            graph=graph,
            frame=frame,
            head_key=payload_int(shape, "first_segment_key"),
            diagnostics=diagnostics,
            diagnostic_prefix="shape",
        )
        if boundary is None:
            continue
        holes, void_total = shape_void_holes(
            shape,
            graph=graph,
            frame=frame,
            diagnostics=diagnostics,
        )
        fill_path = PcbClosedPath(segments=boundary.segments, holes=tuple(holes))
        pour_id = f"allegro:{shape.key}:pour"
        metadata = _shape_fill_metadata(
            shape,
            layer=layer,
            assignment_key=assignment_key,
            net_key=net_key,
            void_hole_count=len(holes),
            void_total_count=void_total,
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
                boundary=boundary,
                layer=layer,
                net_key=net_key,
                metadata=replace(metadata, native_type="copper_shape_pour"),
            )
        )
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
                data=fill_path,
                layer=layer,
                net_key=net_key,
                pour_id=pour_id,
                metadata=metadata,
            )
        )
    return tuple(pours), tuple(fills)


def _rectangle_region_conductors(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
    *,
    frame: BoardFrame | None,
) -> tuple[AllegroConductorPrimitive, ...]:
    conductors: list[AllegroConductorPrimitive] = []
    for record in record_set.records:
        if record.tag not in {0x0E, 0x24} or record.key is None:
            continue
        if owned_by_footprint_definition(graph, rectangle_owner_key(record)):
            continue
        if _copper_layer(record, layer_map, diagnostics, require_etch=False) is None:
            continue
        rectangle = rectangle_primitive(
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
        parent = graph.by_key.get(payload_int(record, "parent_key"))
        if parent is not None and parent.tag == 0x2B:
            # Owned by a footprint definition: geometry in local footprint
            # coordinates, not placed board copper.
            continue
        layer = _copper_layer(record, layer_map, diagnostics, require_etch=True)
        if layer is None:
            continue
        footprint_key = _parent_footprint_key(payload_int(record, "parent_key"), graph)
        primitives = graphic_segment_primitives(
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
    layer = record_layer(record, layer_map)
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
    net_key: int | None,
) -> AllegroConductorPrimitive | None:
    if frame is None:
        return None
    graphic = line_or_arc_primitive(
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
    properties["native_net_key"] = "" if net_key is None else str(net_key)
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
    footprint_key = rectangle_owner_key(record)
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


def _shape_fill_metadata(
    record: AllegroRecord,
    *,
    layer: PcbLayer,
    assignment_key: int | None,
    net_key: int | None,
    void_hole_count: int,
    void_total_count: int,
) -> PcbObjectMetadata:
    metadata = record_metadata(record, layer)
    properties = dict(metadata.properties)
    properties["native_assignment_key"] = str(assignment_key or "")
    properties["native_net_key"] = "" if net_key is None else str(net_key)
    first_keepout_key = payload_int(record, "first_keepout_key")
    if first_keepout_key:
        properties["native_first_keepout_key"] = str(first_keepout_key)
    if void_hole_count:
        properties["native_void_hole_count"] = str(void_hole_count)
    if void_total_count:
        properties["native_void_total_count"] = str(void_total_count)
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


def _parent_footprint_key(parent_key: int, graph: AllegroObjectGraph) -> int | None:
    parent = graph.by_key.get(parent_key)
    if parent is not None and parent.tag == 0x2D:
        return parent_key
    return None
