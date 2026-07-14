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
from phosphor_eda.formats.allegro.constants import AllegroVersion, version_at_least
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
    AllegroPadstackComponent,
    AllegroRecordDiagnostic,
    payload_coords,
    payload_float,
    payload_int,
    payload_int_items,
    rectangle_owner_key,
)

_FALLBACK_TEXT_FONT_SIZE_MM = 1.0
_DEFAULT_RECTANGLE_STROKE_WIDTH_MM = 0.12

# Index of the char-height field inside a text-parameter table item.
_TEXT_PARAM_HEIGHT_INDEX = 2

# Allegro text-justification codes surveyed across the four V16.x fixtures:
# 1 dominates left-aligned title-block prose and refdes, 3 dominates centered
# single-character labels (pin numbers, legend counts), 2 is rare and covers
# right-aligned cells. Codes map to renderer justify tokens; center is "".
_TEXT_ALIGNMENT_JUSTIFY = {
    1: "left",
    2: "right",
    3: "",
}

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import PcbLayer
    from phosphor_eda.formats.allegro.graph import AllegroObjectGraph
    from phosphor_eda.formats.allegro.layers import AllegroLayerMap
    from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

_CLASS_BOARD_GEOMETRY = 0x01
_CLASS_ETCH = 0x06
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
    flash_keys = flash_symbol_keys(record_set)
    diagnostics: list[AllegroRecordDiagnostic] = list(graph.diagnostics)
    board_profile: list[AllegroGraphicPrimitive] = []
    artwork: list[AllegroGraphicPrimitive] = []
    keepouts: list[AllegroGraphicPrimitive] = []
    shape_owned_void_keys = _shape_owned_void_keys(record_set, graph)
    master_text_keys = _footprint_master_text_keys(record_set, graph)
    text_params = _text_parameter_table(record_set)
    version = record_set.header.version if record_set.header is not None else None
    text_size_ceiling = _text_size_ceiling(record_set, frame, version)

    for record in record_set.records:
        if record.key is None:
            continue
        if record.tag == 0x0A:
            diagnostics.append(drc_marker_diagnostic(record))
            continue
        if record.tag in {0x0E, 0x24}:
            if owned_by_footprint_definition(graph, rectangle_owner_key(record)):
                continue
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
            if record.key in master_text_keys:
                continue
            text = _text_primitive(
                record,
                graph=graph,
                frame=frame,
                layer_map=layer_map,
                diagnostics=diagnostics,
                text_params=text_params,
                version=version,
                size_ceiling=text_size_ceiling,
            )
            if text is not None:
                artwork.append(text)
            continue
        if record.tag == 0x28:
            shape = _shape_polygon_primitive(
                record,
                graph=graph,
                frame=frame,
                layer_map=layer_map,
                diagnostics=diagnostics,
                flash_keys=flash_keys,
            )
            if shape is not None:
                if shape.has_role(AllegroPrimitiveRole.BOARD_PROFILE):
                    board_profile.append(shape)
                else:
                    artwork.append(shape)
            continue
        if record.tag != 0x14:
            continue
        if owned_by_footprint_definition(graph, payload_int(record, "parent_key")):
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
    points = [(left, top), (right, top), (right, bottom), (left, bottom)]
    rotation_mdeg = payload_int(record, "rotation_mdeg")
    if rotation_mdeg:
        # rotation_mdeg is CCW in Allegro's native y-up frame; the board frame
        # flips y, so rotate the corners about the rectangle center by the
        # negated angle. The center anchor was confirmed empirically: it keeps
        # every rotated fixture rectangle inside the board bbox, unlike a
        # corner anchor. (For right-angle rotations the corner order is
        # immaterial to the filled shape.)
        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0
        angle = -rotation_mdeg / 1000.0
        points = [_rotate_about(px, py, center_x, center_y, angle) for px, py in points]
    roles = _roles_for_record(record, layer)
    fill = _is_filled_rectangle_layer(layer)
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.RECTANGLE,
        roles=roles,
        data=PcbPolygon(
            points=points,
            width=0.0 if fill else _DEFAULT_RECTANGLE_STROKE_WIDTH_MM,
            fill=fill,
        ),
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=record_metadata(record, layer),
    )


def _rotate_about(x: float, y: float, cx: float, cy: float, degrees: float) -> tuple[float, float]:
    """Rotate (x, y) about (cx, cy) by ``degrees`` (CCW positive)."""
    angle = math.radians(degrees)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = x - cx
    dy = y - cy
    return cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a


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


_VOID_CHAIN_CODES = {
    "linked-list-cycle": "shape-void-chain-cycle",
    "unresolved-reference": "unresolved-shape-void",
    "unexpected-record-tag": "invalid-shape-void-record",
}


def shape_void_holes(
    shape: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    frame: BoardFrame | None,
    diagnostics: list[AllegroRecordDiagnostic],
) -> tuple[list[PcbClosedPath], int]:
    """Return a 0x28 shape's parsed void holes and the total void count.

    ``void_total`` is the number of 0x34 void records reached on the shape's
    keepout chain; ``len(holes)`` is how many of those resolved to a boundary.
    A shortfall means a void was dropped, so the fill is not fully resolved.
    """
    holes: list[PcbClosedPath] = []
    first_keepout_key = payload_int(shape, "first_keepout_key")
    if first_keepout_key == 0:
        return holes, 0
    walk = graph.walk_key_chain(
        head_key=first_keepout_key,
        owner_key=shape.key,
        expected_tags=_SHAPE_VOID_RECORD_TAGS,
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
        path = closed_path_from_segment_chain(
            void,
            graph=graph,
            frame=frame,
            head_key=payload_int(void, "first_segment_key"),
            diagnostics=diagnostics,
            diagnostic_prefix="shape-void",
        )
        if path is not None:
            holes.append(path)
    return holes, len(walk.records)


def _shape_polygon_primitive(
    record: AllegroRecord,
    *,
    graph: AllegroObjectGraph,
    frame: BoardFrame | None,
    layer_map: AllegroLayerMap,
    diagnostics: list[AllegroRecordDiagnostic],
    flash_keys: frozenset[int] = frozenset(),
) -> AllegroGraphicPrimitive | None:
    """Build a filled-polygon primitive from a non-etch 0x28 shape record.

    Etch-class shapes are copper pours/fills and belong to
    ``extract_allegro_copper``; footprint-definition-local shapes are package
    symbol geometry, not placed board geometry. Everything else becomes board
    artwork (or a board profile when the layer is a board-outline subclass),
    carrying its void chain as closed-path holes.
    """
    # Key off the class id, not the COPPER role, so anti-etch (keepout) shapes
    # are not excluded here as well as by the etch-class check.
    if payload_int(record, "layer_class_id") == _CLASS_ETCH:
        return None
    owner = graph.by_key.get(payload_int(record, "owner_key"))
    if owner is not None and owner.tag == 0x2B:
        if record.key is not None and record.key not in flash_keys:
            diagnostics.append(
                drop_diagnostic(
                    record,
                    code="skipped-footprint-shape",
                    message=(
                        f"shape record {record.key} is footprint-definition-owned but "
                        "not referenced as a padstack flash symbol; geometry dropped"
                    ),
                )
            )
        return None
    if frame is None:
        diagnostics.append(missing_header_diagnostic(record))
        return None
    layer = record_layer(record, layer_map)
    if layer is None:
        diagnostics.append(missing_layer_diagnostic(record))
        return None
    boundary = closed_path_from_segment_chain(
        record,
        graph=graph,
        frame=frame,
        head_key=payload_int(record, "first_segment_key"),
        diagnostics=diagnostics,
        diagnostic_prefix="shape",
    )
    if boundary is None:
        return None
    holes, _void_total = shape_void_holes(
        record,
        graph=graph,
        frame=frame,
        diagnostics=diagnostics,
    )
    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.POLYGON,
        roles=_roles_for_record(record, layer),
        data=PcbClosedPath(segments=boundary.segments, holes=tuple(holes)),
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=record_metadata(record, layer),
    )


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


def flash_symbol_keys(record_set: AllegroRecordSet) -> frozenset[int]:
    """Shape-record keys referenced as padstack flash symbols."""
    keys: set[int] = set()
    for record in record_set.records:
        if record.tag != 0x1C:
            continue
        components = record.payload.get("components", ())
        if isinstance(components, tuple):
            for component in components:
                if isinstance(component, AllegroPadstackComponent) and component.string_key:
                    keys.add(component.string_key)
    return frozenset(keys)


def _footprint_master_text_keys(
    record_set: AllegroRecordSet, graph: AllegroObjectGraph
) -> frozenset[int]:
    """Keys of 0x30 text wrappers whose chain rings back to a 0x2B definition.

    Text wrappers carry no owner field; ownership is recovered from the ring
    terminator of the ``next_key`` chain they sit on. Chain membership is
    shared, so every wrapper on a chain gets its terminator's classification.
    """
    terminator_is_master: dict[int, bool] = {}
    masters: set[int] = set()
    for record in record_set.records:
        if record.tag != 0x30 or record.key is None or record.key in terminator_is_master:
            continue
        chain: list[int] = []
        seen: set[int] = set()
        current = record
        is_master = False
        while current.tag == 0x30:
            if current.key is None or current.key in seen:
                break
            if current.key in terminator_is_master:
                is_master = terminator_is_master[current.key]
                break
            seen.add(current.key)
            chain.append(current.key)
            next_key = current.next_key or 0
            terminator = graph.by_key.get(next_key)
            if terminator is None or next_key == 0:
                break
            if terminator.tag != 0x30:
                is_master = terminator.tag == 0x2B
                break
            current = terminator
        for key in chain:
            terminator_is_master[key] = is_master
            if is_master:
                masters.add(key)
    return frozenset(masters)


def owned_by_footprint_definition(graph: AllegroObjectGraph, owner_key: int) -> bool:
    """True when *owner_key* resolves to a 0x2B footprint definition.

    Definition-owned records are unplaced symbol masters in local
    coordinates; their placed copies carry 0x2D instance parents.
    """
    owner = graph.by_key.get(owner_key)
    return owner is not None and owner.tag == 0x2B


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
    text_params: tuple[tuple[int, ...], ...] | None,
    version: AllegroVersion | None,
    size_ceiling: float | None,
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
    font_key = payload_int(record, "font_key")
    alignment_code = payload_int(record, "text_alignment_code")
    reversal_code = payload_int(record, "text_reversal_code")

    metadata = record_metadata(record, layer)
    properties = dict(metadata.properties)
    properties["native_text_key"] = str(text_key)
    properties["native_font_key"] = str(font_key)
    if "text_alignment_code" in record.payload:
        properties["native_text_alignment_code"] = str(alignment_code)
    if "text_reversal_code" in record.payload:
        properties["native_text_reversal_code"] = str(reversal_code)
    metadata = replace(metadata, properties=properties)

    font_size = _resolve_text_font_size(
        text_params, font_key=font_key, frame=frame, version=version, size_ceiling=size_ceiling
    )
    if font_size is None:
        diagnostics.append(
            drop_diagnostic(
                record,
                code="unresolved-text-size",
                message=(
                    f"text wrapper {record.key} font key {font_key} "
                    "has no resolvable table entry; using fallback native size"
                ),
                reference_key=text_key,
            )
        )
        font_size = _FALLBACK_TEXT_FONT_SIZE_MM

    justify = _TEXT_ALIGNMENT_JUSTIFY.get(alignment_code)
    if justify is None:
        diagnostics.append(
            drop_diagnostic(
                record,
                code="unmapped-text-alignment",
                message=(f"text wrapper {record.key} has unmapped alignment code {alignment_code}"),
            )
        )
        justify = ""

    return AllegroGraphicPrimitive(
        id=f"allegro:{record.key}",
        kind=AllegroPrimitiveKind.TEXT,
        roles=(AllegroPrimitiveRole.ARTWORK, AllegroPrimitiveRole.TEXT),
        data=PcbText(
            text=text_value,
            x=frame.x(payload_int(record, "x")),
            y=frame.y(payload_int(record, "y")),
            rotation=payload_int(record, "rotation_mdeg") / 1000.0,
            font_size=font_size,
            justify=justify,
            mirrored=reversal_code != 0,
        ),
        layer=layer,
        source_tag=record.tag,
        source_key=record.key or 0,
        metadata=metadata,
    )


def _text_parameter_table(record_set: AllegroRecordSet) -> tuple[tuple[int, ...], ...] | None:
    """Return the single 0x36 code-0x08 text-parameter table's items, if present."""
    for record in record_set.records:
        if record.tag == 0x36 and payload_int(record, "code") == 0x08:
            items = payload_int_items(record, "text_parameter_items")
            if items:
                return items
    return None


def _resolve_text_font_size(
    text_params: tuple[tuple[int, ...], ...] | None,
    *,
    font_key: int,
    frame: BoardFrame,
    version: AllegroVersion | None,
    size_ceiling: float | None,
) -> float | None:
    """Resolve millimeter font size from a 1-based ``font_key`` into the table.

    Returns ``None`` for genuinely unresolvable keys (no table, key 0, key out
    of range, non-positive height, or an implausible V17.2+ value) so the caller
    emits ``unresolved-text-size`` and falls back.
    """
    if text_params is None or font_key < 1 or font_key > len(text_params):
        return None
    item = text_params[font_key - 1]
    if len(item) <= _TEXT_PARAM_HEIGHT_INDEX:
        return None
    size = frame.length(item[_TEXT_PARAM_HEIGHT_INDEX])
    if size <= 0:
        return None
    # V17.2+ item layout is unverified (hypothesis); reject values that exceed
    # the board so a mis-decoded field falls back instead of rendering garbage.
    if (
        version is not None
        and version_at_least(version, AllegroVersion.V_172)
        and size_ceiling is not None
        and size > size_ceiling
    ):
        return None
    return size


def _text_size_ceiling(
    record_set: AllegroRecordSet, frame: BoardFrame | None, version: AllegroVersion | None
) -> float | None:
    """Board coordinate extent (mm) used to reject implausible V17.2+ font sizes.

    Only computed for V17.2+ boards, where the text-parameter item layout is a
    hypothesis; V16.x boards return ``None`` (no ceiling, the layout is
    confirmed).
    """
    if frame is None or version is None or not version_at_least(version, AllegroVersion.V_172):
        return None
    min_x = min_y = max_x = max_y = None
    for record in record_set.records:
        coords = payload_coords(record, "coords")
        points = (
            [(coords[0], coords[1]), (coords[2], coords[3])]
            if coords is not None
            else [(payload_int(record, "coord_x"), payload_int(record, "coord_y"))]
        )
        for native_x, native_y in points:
            min_x = native_x if min_x is None else min(min_x, native_x)
            max_x = native_x if max_x is None else max(max_x, native_x)
            min_y = native_y if min_y is None else min(min_y, native_y)
            max_y = native_y if max_y is None else max(max_y, native_y)
    if min_x is None or max_x is None or min_y is None or max_y is None:
        return None
    return max(frame.length(max_x - min_x), frame.length(max_y - min_y))


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
