"""Binary and text stream parsers for the Altium PCB parser.

Each Altium PcbDoc OLE stream (Tracks6, Arcs6, Pads6, Texts6, Fills6,
Regions6, ShapeBasedRegions6, Polygons6, ComponentBodies6, Nets6,
Components6) is decoded here into the intermediate ``ParsedPrimitive``
model. Copper classification, region assembly, board-outline fallback,
component-body models, and the drill-manager mask-aperture heuristic all
live in this module.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    LayerRole,
    PadStack,
    PadStackLayer,
    PadStackMode,
    PcbArc,
    PcbCircle,
    PcbClosedPath,
    PcbFootprint,
    PcbFootprintMetadata,
    PcbKeepout,
    PcbLayer,
    PcbLine,
    PcbModel3D,
    PcbNet,
    PcbPolygon,
    PcbPour,
    PcbPourFillMode,
    PcbPourSettings,
    PcbText,
)
from phosphor_eda.formats.altium._helpers import guarded_float, guarded_int, u32
from phosphor_eda.formats.altium.enums import (
    AltiumLayer,
    PadHoleShape,
    PadMode,
    PadShape,
    PadShapeAlt,
    PcbRecordType,
)
from phosphor_eda.formats.altium.geometry import is_full_circle_arc, linearize_arc_vertices
from phosphor_eda.formats.altium.pcb_keepouts import (
    altium_keepout_rules,
    keepout_from_arc,
    keepout_from_line,
)
from phosphor_eda.formats.altium.pcb_layers import (
    V7_NAME_TO_NUM,
    altium_layer_name,
    altium_layer_ref,
)
from phosphor_eda.formats.altium.pcb_primitives import (
    COPPER_LAYERS,
    DrillManagerRecord,
    PadMaskAperture,
    ParsedObjectKind,
    ParsedPadPayload,
    ParsedPrimitive,
    ParsedRole,
    ParsedShapeKind,
    ParsedViaPayload,
    geometry_metadata,
    int_to_mm,
    keepout_metadata,
    layer_geometry_roles,
    layered_geometry_roles,
    normalize_parsed_roles,
    parse_mil,
    parse_rotation,
    pour_metadata,
    read_binary_records,
    read_text_records,
    resolve_pour_id,
    resolve_stream_net,
    warn_unknown_stream_nets,
)
from phosphor_eda.formats.altium.pcb_records import (
    COMPONENT_NONE,
    NET_UNCONNECTED,
    ArcRecord,
    ExtendedVertex,
    FillRecord,
    PadRecord,
    TextRecord,
    TrackRecord,
    ViaRecord,
)
from phosphor_eda.formats.altium.record_parser import parse_record_payload
from phosphor_eda.formats.common.text import strip_overline

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext


# Pad shape byte → domain string (octagonal is treated as rect).
_PAD_SHAPES: dict[PadShape, str] = {
    PadShape.CIRCLE: "circle",
    PadShape.RECT: "rect",
    PadShape.OCTAGONAL: "rect",
}


def _pad_shape(value: int) -> PadShape:
    try:
        return PadShape(value)
    except ValueError:
        return PadShape.UNKNOWN


_PAD_TEMPLATE_MASK_RE = re.compile(
    r"^r(?P<pad_w>\d+)_(?P<pad_h>\d+)hn(?P<drill>\d+)r(?P<rounding>\d+)"
    r"m(?P<mask_w>\d+)_(?P<mask_h>\d+)$"
)


def _classify_copper_primitive(
    layer_num: int,
    layer_map: dict[int, PcbLayer],
    component_index: int | None,
    net: int,
    nets: dict[int, PcbNet],
    unknown_nets: list[int],
) -> tuple[ParsedObjectKind, tuple[ParsedRole, ...], str, int]:
    """Classify a line/arc primitive by its layer.

    Returns (object_type, roles, source_collection, net_number).  Copper
    layers become conductors carrying their net; the board edge becomes a
    board-outline graphic; everything else is silkscreen/paste artwork with
    no net.  Shared verbatim by ``parse_tracks`` and ``parse_arcs``.  Copper
    nets absent from *nets* degrade to unconnected and are recorded in
    *unknown_nets* for a single per-stream diagnostic.
    """
    if layer_num in COPPER_LAYERS:
        return (
            ParsedObjectKind.TRACK,
            layered_geometry_roles(layer_num, layer_map, ParsedRole.CONDUCTOR),
            "conductors",
            resolve_stream_net(net, nets, unknown_nets),
        )
    if layer_map[layer_num].has_role(LayerRole.EDGE):
        return (
            ParsedObjectKind.GRAPHIC,
            layered_geometry_roles(layer_num, layer_map, ParsedRole.BOARD_OUTLINE),
            "board_profile",
            0,
        )
    return (
        ParsedObjectKind.GRAPHIC,
        layered_geometry_roles(layer_num, layer_map),
        "footprint_artwork" if component_index is not None else "artwork",
        0,
    )


def arc_to_three_point(
    cx_mm: float,
    cy_mm: float,
    radius_mm: float,
    start_deg: float,
    end_deg: float,
) -> tuple[float, float, float, float, float, float]:
    """Convert a center/radius/angle arc to (sx, sy, mx, my, ex, ey).

    The arc goes **counter-clockwise** from ``start_deg`` to ``end_deg``.
    When ``end_deg < start_deg`` the arc wraps past 360°.

    Callers that negate Y should use original (non-negated) angles here,
    then negate the Y coordinates of the returned points.
    """
    sa = math.radians(start_deg)
    ea = math.radians(end_deg)
    # Mid-angle: halfway around the CCW arc from start to end.
    if end_deg >= start_deg:
        ma = (sa + ea) / 2
    else:
        # Arc wraps past 360°.
        ma = (sa + ea + 2 * math.pi) / 2
        if ma >= 2 * math.pi:
            ma -= 2 * math.pi

    sx = cx_mm + radius_mm * math.cos(sa)
    sy = cy_mm + radius_mm * math.sin(sa)
    mx = cx_mm + radius_mm * math.cos(ma)
    my = cy_mm + radius_mm * math.sin(ma)
    ex = cx_mm + radius_mm * math.cos(ea)
    ey = cy_mm + radius_mm * math.sin(ea)
    return (sx, sy, mx, my, ex, ey)


def _arc_shape_payload(
    cx: float,
    cy_orig: float,
    radius: float,
    width: float,
    start_deg: float,
    end_deg: float,
) -> tuple[ParsedShapeKind, PcbArc | PcbCircle]:
    if is_full_circle_arc(start_deg, end_deg):
        # Altium's arc radius is the stroke centerline, matching PcbCircle's
        # canonical centerline convention -- store it directly (the annulus
        # spans radius +/- width/2).
        return (
            ParsedShapeKind.CIRCLE,
            PcbCircle(cx, -cy_orig, radius, width, fill=False),
        )

    sx, sy, mx, my, ex, ey = arc_to_three_point(cx, cy_orig, radius, start_deg, end_deg)
    return ParsedShapeKind.ARC, PcbArc(sx, -sy, mx, -my, ex, -ey, width)


def parse_nets(data: bytes, ctx: ParseContext) -> dict[int, PcbNet]:
    """Parse Nets6/Data → {net_number: PcbNet}.

    Nets are numbered starting at 1 (index+1 in the stream order).
    Net 0 is reserved for "unconnected".
    """
    records = read_text_records(data, ctx, source="Nets6/Data")
    nets: dict[int, PcbNet] = {}
    for i, rec in enumerate(records):
        num = i + 1
        raw_name = rec.get("name", "")
        # Strip Altium overline markup (e.g. "C\S\" → "CS") so net names
        # are clean for CSS selectors and downstream tooling.
        clean_name = strip_overline(raw_name)[0]
        nets[num] = PcbNet(number=num, name=clean_name)
    return nets


def parse_components(
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[PcbFootprint]:
    """Parse Components6/Data → list of footprint shells.

    Component records are text-based and contain position, pattern,
    layer, rotation, and designator.  Pads and geometry are added later.
    """
    records = read_text_records(data, ctx, source="Components6/Data")
    footprints: list[PcbFootprint] = []
    for index, rec in enumerate(records):
        x_str = rec.get("x", "0mil")
        y_str = rec.get("y", "0mil")
        x_mm = parse_mil(x_str, ctx=ctx, field=f"component {index} x")
        y_mm = -parse_mil(y_str, ctx=ctx, field=f"component {index} y")  # Negate Y

        layer_str = rec.get("layer", "TOP")
        layer = altium_layer_ref(
            1 if layer_str.upper() == "TOP" else 32,
            layer_map,
            ctx,
            source=f"component {index}",
        )
        if layer is None:
            # Placement layer missing from Board6; fall back to any concrete
            # layer so footprint indexing (pads reference it by position) stays
            # stable rather than shifting on a dropped entry.
            layer = next(iter(layer_map.values()), None)
            if layer is None:
                continue

        rot = parse_rotation(rec.get("rotation", "0"), ctx=ctx, field=f"component {index} rotation")

        ref = rec.get("sourcedesignator", rec.get("designator", "?"))
        pattern = rec.get("pattern", "")

        footprints.append(
            PcbFootprint(
                reference=ref,
                footprint_lib=pattern,
                x=x_mm,
                y=y_mm,
                rotation=rot,
                layer=layer,
                metadata=PcbFootprintMetadata(
                    source_format="altium",
                    native_type="component",
                    properties={
                        "nameon": rec.get("nameon", "TRUE"),
                        "commenton": rec.get("commenton", "FALSE"),
                    },
                    source_designator=ref,
                    source_unique_id=rec.get("uniqueid", ""),
                    source_footprint_library=pattern,
                    source_component_library=rec.get("sourcelibref", ""),
                ),
            )
        )
    return footprints


def parse_tracks(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_id_map: dict[int, str] | None = None,
) -> tuple[list[ParsedPrimitive], list[PcbKeepout]]:
    """Parse Tracks6/Data into normalized line geometry."""
    records = read_binary_records(data, ctx, source="Tracks6/Data")
    geometry: list[ParsedPrimitive] = []
    keepouts: list[PcbKeepout] = []
    resolved_pour_id_map = pour_id_map or {}
    unknown_nets: list[int] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.TRACK:
            continue
        track = TrackRecord.from_bytes(body, ctx)
        if track is None:
            continue

        layer_ref = altium_layer_ref(track.layer, layer_map, ctx, source=f"track {index}")
        if layer_ref is None:
            continue
        layer = layer_ref.name

        x1 = int_to_mm(track.start[0])
        y1 = -int_to_mm(track.start[1])
        x2 = int_to_mm(track.end[0])
        y2 = -int_to_mm(track.end[1])
        width = int_to_mm(track.width)

        component_index = None if track.component == COMPONENT_NONE else track.component
        if track.is_keepout:
            keepouts.append(
                keepout_from_line(
                    layer=layer_ref,
                    track=track,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    width=width,
                    index=index,
                    component_index=component_index,
                )
            )
            continue

        pour_id = resolve_pour_id(resolved_pour_id_map, track.polygon)
        object_type, roles, source_collection, net_number = _classify_copper_primitive(
            track.layer, layer_map, component_index, track.net, nets, unknown_nets
        )

        geometry.append(
            ParsedPrimitive(
                id=f"track:{track.layer}:{index}",
                object_type=object_type,
                shape=ParsedShapeKind.LINE,
                roles=roles,
                data=PcbLine(x1, y1, x2, y2, width),
                layers=(layer,),
                net_number=net_number,
                pour_id=pour_id,
                metadata=geometry_metadata(
                    native_type="TRACK",
                    source_collection=source_collection,
                    native_index=index,
                    native_component_index=component_index,
                    native_polygon_index=track.polygon,
                    native_subpolygon_index=track.subpoly_index,
                ),
            )
        )

    warn_unknown_stream_nets(ctx, "Tracks6/Data", unknown_nets)
    return geometry, keepouts


# ---------------------------------------------------------------------------
# Padstacks — non-simple stack modes from Pads6/Vias6 (KiCad altium_pcb.cpp
# mapping: TMB tiers come from sub5 top/mid/bottom; FULL_STACK maps copper
# layer n (Top=1 … Bottom=32) to the per-layer arrays at index n-1, with Mid1
# taken from the sub5 mid geometry and Mid2..Mid30 from the sub6 inner arrays).
# ---------------------------------------------------------------------------


def _copper_stack_layers(layer_map: dict[int, PcbLayer]) -> list[tuple[int, str]]:
    """Copper layers as (Altium layer number, name), ordered top → bottom."""
    return sorted(
        (number, layer.name)
        for number, layer in layer_map.items()
        if AltiumLayer.TOP_LAYER <= number <= AltiumLayer.BOTTOM_LAYER
        and layer.has_role(LayerRole.COPPER)
    )


def _stack_is_uniform(entries: tuple[PadStackLayer, ...]) -> bool:
    """True when every entry shares the first entry's geometry (names aside)."""
    first = replace(entries[0], layer="")
    return all(replace(entry, layer="") == first for entry in entries[1:])


def _pad_stack_entry(
    layer_name: str,
    shape_byte: int,
    size: tuple[int, int],
    alt_shape: int | None,
    corner_radius_pct: int,
) -> PadStackLayer:
    shape = _PAD_SHAPES.get(_pad_shape(shape_byte), "rect")
    corner_radius_ratio = 0.0
    if alt_shape == PadShapeAlt.ROUNDRECT:
        shape = "roundrect"
        # Altium stores percent where 100 = fully round; the ratio is
        # radius / min(width, height), so 100% maps to 0.5.
        corner_radius_ratio = corner_radius_pct / 200.0
    return PadStackLayer(
        layer=layer_name,
        shape=shape,
        size_x=int_to_mm(size[0]),
        size_y=int_to_mm(size[1]),
        corner_radius_ratio=corner_radius_ratio,
    )


def _altium_pad_stack(
    pad: PadRecord, layer_map: dict[int, PcbLayer], ctx: ParseContext, index: int
) -> PadStack | None:
    """Non-simple stack for a Pads6 record; None keeps the SIMPLE wrap."""
    if pad.pad_mode == PadMode.SIMPLE:
        return None
    if pad.pad_mode == PadMode.TOP_MIDDLE_BOTTOM:
        return _pad_stack_top_mid_bottom(pad)
    if pad.pad_mode == PadMode.FULL_STACK:
        return _pad_stack_full(pad, layer_map, ctx, index)
    ctx.warn(
        "unsupported_padstack",
        f"pad {index} ({pad.name}): unknown pad stack mode {pad.pad_mode}; using top geometry",
        record_index=index,
    )
    return None


def _pad_mode_name(value: int) -> str:
    try:
        return PadMode(value).name
    except ValueError:
        return str(value)


def _pad_stack_alt_shape(pad: PadRecord, layer_index: int) -> tuple[int | None, int]:
    """Per-layer (alt shape, corner radius pct) from sub6, defaulting when absent."""
    alt_shape = pad.alt_shapes[layer_index] if pad.alt_shapes else None
    corner_radius_pct = pad.corner_radii[layer_index] if pad.corner_radii else 0
    return alt_shape, corner_radius_pct


def _pad_stack_top_mid_bottom(pad: PadRecord) -> PadStack | None:
    tiers = tuple(
        _pad_stack_entry(tier, shape_byte, size, *_pad_stack_alt_shape(pad, layer_index))
        for tier, shape_byte, size, layer_index in (
            ("top", pad.shape, pad.top_size, 0),
            ("mid", pad.mid_shape, pad.mid_size, 1),
            ("bottom", pad.bot_shape, pad.bot_size, 31),
        )
    )
    if _stack_is_uniform(tiers):
        return None
    return PadStack(mode=PadStackMode.TOP_MID_BOTTOM, layers=tiers)


def _pad_stack_full(
    pad: PadRecord, layer_map: dict[int, PcbLayer], ctx: ParseContext, index: int
) -> PadStack | None:
    copper = _copper_stack_layers(layer_map)
    needs_inner_arrays = any(3 <= number <= 31 for number, _ in copper)
    if needs_inner_arrays and not pad.inner_sizes:
        ctx.warn(
            "unsupported_padstack",
            f"pad {index} ({pad.name}): full-stack pad has no per-layer size data; "
            "using top geometry",
            record_index=index,
        )
        return None
    entries: list[PadStackLayer] = []
    for number, name in copper:
        if number == AltiumLayer.TOP_LAYER:
            shape_byte, size = pad.shape, pad.top_size
        elif number == AltiumLayer.MID_LAYER_1:  # Mid1 geometry lives in sub5, not sub6
            shape_byte, size = pad.mid_shape, pad.mid_size
        elif number == AltiumLayer.BOTTOM_LAYER:
            shape_byte, size = pad.bot_shape, pad.bot_size
        else:  # Mid2..Mid30 → sub6 inner arrays
            shape_byte, size = pad.inner_shapes[number - 3], pad.inner_sizes[number - 3]
        entries.append(
            _pad_stack_entry(name, shape_byte, size, *_pad_stack_alt_shape(pad, number - 1))
        )
    if not entries or _stack_is_uniform(tuple(entries)):
        return None
    return PadStack(mode=PadStackMode.PER_LAYER, layers=tuple(entries))


def _via_stack_entry(layer_name: str, raw_diameter: int) -> PadStackLayer:
    diameter = int_to_mm(raw_diameter)
    return PadStackLayer(layer=layer_name, shape="circle", size_x=diameter, size_y=diameter)


def _altium_via_stack(
    via: ViaRecord, layer_map: dict[int, PcbLayer], ctx: ParseContext, index: int
) -> PadStack | None:
    """Non-simple stack for a Vias6 record; None keeps the SIMPLE wrap."""
    if via.via_mode == PadMode.SIMPLE:
        return None
    if via.via_mode not in (PadMode.TOP_MIDDLE_BOTTOM, PadMode.FULL_STACK):
        ctx.warn(
            "unsupported_padstack",
            f"via {index}: unknown via stack mode {via.via_mode}; using outer diameter",
            record_index=index,
        )
        return None
    if not via.diameter_by_layer:
        ctx.warn(
            "unsupported_padstack",
            f"via {index}: stack mode {via.via_mode} without per-layer diameters; "
            "using outer diameter",
            record_index=index,
        )
        return None
    if via.via_mode == PadMode.TOP_MIDDLE_BOTTOM:
        tiers = tuple(
            _via_stack_entry(tier, via.diameter_by_layer[layer_index])
            for tier, layer_index in (("top", 0), ("mid", 1), ("bottom", 31))
        )
        if _stack_is_uniform(tiers):
            return None
        return PadStack(mode=PadStackMode.TOP_MID_BOTTOM, layers=tiers)
    entries = tuple(
        _via_stack_entry(name, via.diameter_by_layer[number - 1])
        for number, name in _copper_stack_layers(layer_map)
    )
    if not entries or _stack_is_uniform(entries):
        return None
    return PadStack(mode=PadStackMode.PER_LAYER, layers=entries)


def parse_vias(
    data: bytes, nets: dict[int, PcbNet], layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[ParsedPrimitive]:
    """Parse Vias6/Data into normalized via geometry."""
    records = read_binary_records(data, ctx, source="Vias6/Data")
    vias: list[ParsedPrimitive] = []
    unknown_nets: list[int] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.VIA:
            continue
        via = ViaRecord.from_bytes(body, ctx)
        if via is None:
            continue

        start_ref = altium_layer_ref(via.start_layer, layer_map, ctx, source=f"via {index} start")
        end_ref = altium_layer_ref(via.end_layer, layer_map, ctx, source=f"via {index} end")
        if start_ref is None or end_ref is None:
            continue
        layer_refs = [start_ref, end_ref]
        layers: list[str] = []
        for layer_ref in layer_refs:
            if layer_ref.name not in layers:
                layers.append(layer_ref.name)

        roles = [ParsedRole.CONDUCTOR]
        through_hole = (
            via.start_layer == AltiumLayer.TOP_LAYER and via.end_layer == AltiumLayer.BOTTOM_LAYER
        )
        if not through_hole:
            if via.start_layer == via.end_layer:
                roles.append(ParsedRole.FREE_VIA)
            else:
                roles.append(ParsedRole.BLIND_VIA)

        component_index = None if via.component == COMPONENT_NONE else via.component

        vias.append(
            ParsedPrimitive(
                id=f"via:{index}",
                object_type=ParsedObjectKind.VIA,
                shape=ParsedShapeKind.CIRCLE,
                roles=tuple(roles),
                data=ParsedViaPayload(
                    x=int_to_mm(via.position[0]),
                    y=-int_to_mm(via.position[1]),
                    size=int_to_mm(via.diameter),
                    drill=int_to_mm(via.hole_size),
                    stack=_altium_via_stack(via, layer_map, ctx, index),
                ),
                layers=tuple(layers),
                net_number=resolve_stream_net(via.net, nets, unknown_nets),
                metadata=geometry_metadata(
                    native_type="VIA",
                    source_collection="vias",
                    native_index=index,
                    native_component_index=component_index,
                    properties={
                        "start_layer": str(via.start_layer),
                        "end_layer": str(via.end_layer),
                    },
                ),
            )
        )

    warn_unknown_stream_nets(ctx, "Vias6/Data", unknown_nets)
    return vias


def parse_arcs(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_id_map: dict[int, str] | None = None,
) -> tuple[list[ParsedPrimitive], list[PcbKeepout]]:
    """Parse Arcs6/Data into normalized arc and keepout geometry."""
    records = read_binary_records(data, ctx, source="Arcs6/Data")
    geometry: list[ParsedPrimitive] = []
    keepouts: list[PcbKeepout] = []
    resolved_pour_id_map = pour_id_map or {}
    unknown_nets: list[int] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.ARC:
            continue
        arc = ArcRecord.from_bytes(body, ctx)
        if arc is None:
            continue

        layer_ref = altium_layer_ref(arc.layer, layer_map, ctx, source=f"arc {index}")
        if layer_ref is None:
            continue
        layer = layer_ref.name

        cx = int_to_mm(arc.center[0])
        cy_orig = int_to_mm(arc.center[1])
        radius = int_to_mm(arc.radius)
        width = int_to_mm(arc.width)

        shape, payload = _arc_shape_payload(
            cx, cy_orig, radius, width, arc.start_angle, arc.end_angle
        )

        component_index = None if arc.component == COMPONENT_NONE else arc.component
        if arc.is_keepout:
            keepouts.append(
                keepout_from_arc(
                    layer=layer_ref,
                    layer_num=arc.layer,
                    arc=arc,
                    cx=cx,
                    cy_orig=cy_orig,
                    radius=radius,
                    width=width,
                    index=index,
                    component_index=component_index,
                )
            )
            continue

        pour_id = resolve_pour_id(resolved_pour_id_map, arc.polygon)
        object_type, roles, source_collection, net_number = _classify_copper_primitive(
            arc.layer, layer_map, component_index, arc.net, nets, unknown_nets
        )

        geometry.append(
            ParsedPrimitive(
                id=f"arc:{arc.layer}:{index}",
                object_type=object_type,
                shape=shape,
                roles=roles,
                data=payload,
                layers=(layer,),
                net_number=net_number,
                pour_id=pour_id,
                metadata=geometry_metadata(
                    native_type="ARC",
                    source_collection=source_collection,
                    native_index=index,
                    native_component_index=component_index,
                    native_polygon_index=arc.polygon,
                    native_subpolygon_index=arc.subpoly_index,
                ),
            )
        )

    warn_unknown_stream_nets(ctx, "Arcs6/Data", unknown_nets)
    return geometry, keepouts


def parse_pads(
    data: bytes, nets: dict[int, PcbNet], layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[tuple[int, ParsedPrimitive]]:
    """Parse Pads6/Data into component-indexed pad geometry.

    Each pad record has 6 subrecords: name, skip, skip, skip, geometry,
    per-layer-overrides. PadRecord.parse walks the chain once, returning the
    record and the cursor past it.
    """
    pads: list[tuple[int, ParsedPrimitive]] = []
    unknown_nets: list[int] = []
    pos = 0
    index = 0

    while pos < len(data):
        if data[pos] != 2:
            ctx.warn(
                "truncated_stream",
                f"Pads6/Data: unexpected record type byte {data[pos]} at byte {pos}; "
                f"{len(data) - pos} trailing bytes dropped",
            )
            break
        pad, pos = PadRecord.parse(data, pos, ctx)
        if pad is None:
            continue

        # Determine shape string
        shape = _PAD_SHAPES.get(_pad_shape(pad.shape), "rect")
        roundrect_rratio = 0.0
        if pad.shape_alt == PadShapeAlt.ROUNDRECT:
            shape = "roundrect"
            # Altium stores percent where 100 = fully round; the ratio is
            # radius / min(width, height), so 100% maps to 0.5.
            roundrect_rratio = (pad.corner_radius_pct or 0) / 200.0

        # Hole shape: slots carry their length/rotation in sub6; square
        # holes have no domain model and degrade to round with a diagnostic.
        hole_is_slot = False
        slot_length = 0.0
        slot_rotation = 0.0
        if pad.hole_size > 0 and pad.hole_shape is not None:
            if pad.hole_shape == PadHoleShape.SLOT and pad.slot_size > 0:
                hole_is_slot = True
                slot_length = int_to_mm(pad.slot_size)
                slot_rotation = pad.slot_rotation
            elif pad.hole_shape == PadHoleShape.SQUARE:
                ctx.warn(
                    "unsupported_geometry",
                    f"pad {index} ({pad.name}): square hole has no domain model; treated as round",
                    record_index=index,
                )

        # Determine layers (multi-layer pad = layer 74 = through-hole)
        if pad.layer == AltiumLayer.MULTI_LAYER:
            layers = [
                layer.name for layer in layer_map.values() if layer.has_role(LayerRole.COPPER)
            ]
        else:
            pad_layer_ref = altium_layer_ref(pad.layer, layer_map, ctx, source=f"pad {index}")
            if pad_layer_ref is None:
                continue
            layers = [pad_layer_ref.name]

        net_num = resolve_stream_net(pad.net, nets, unknown_nets)
        net_obj = None if net_num == 0 else nets.get(net_num)
        net_name = net_obj.name if net_obj is not None else ""

        roles = [ParsedRole.CONDUCTOR]
        if pad.layer not in COPPER_LAYERS and pad.layer != AltiumLayer.MULTI_LAYER:
            roles.extend(layer_geometry_roles(pad.layer, layer_map))
        if pad.hole_size > 0 and pad.plated:
            roles.append(ParsedRole.PLATED_HOLE)

        geometry_shape = ParsedShapeKind.CIRCLE if shape == "circle" else ParsedShapeKind.RECTANGLE
        if shape in {"oval", "roundrect"}:
            geometry_shape = ParsedShapeKind.POLYGON

        pads.append(
            (
                pad.component,
                ParsedPrimitive(
                    id=f"pad:{index}:{pad.name}",
                    object_type=ParsedObjectKind.PAD,
                    shape=geometry_shape,
                    roles=tuple(roles),
                    data=ParsedPadPayload(
                        number=pad.name,
                        x=int_to_mm(pad.position[0]),
                        y=-int_to_mm(pad.position[1]),
                        width=int_to_mm(pad.top_size[0]),
                        height=int_to_mm(pad.top_size[1]),
                        shape=shape,
                        rotation=pad.rotation,
                        drill=int_to_mm(pad.hole_size),
                        roundrect_rratio=roundrect_rratio,
                        hole_plated=pad.plated if pad.hole_size > 0 else None,
                        hole_is_slot=hole_is_slot,
                        slot_length=slot_length,
                        slot_rotation=slot_rotation,
                        stack=_altium_pad_stack(pad, layer_map, ctx, index),
                    ),
                    layers=tuple(layers),
                    net_number=net_num,
                    net_name=net_name,
                    metadata=geometry_metadata(
                        native_type="PAD",
                        source_collection="pads",
                        native_index=index,
                        native_component_index=None
                        if pad.component == COMPONENT_NONE
                        else pad.component,
                        properties={
                            "pad_mode": _pad_mode_name(pad.pad_mode),
                            "shape_alt": "" if pad.shape_alt is None else str(pad.shape_alt),
                        },
                    ),
                ),
            )
        )
        index += 1

    warn_unknown_stream_nets(ctx, "Pads6/Data", unknown_nets)
    return pads


def apply_drill_manager_mask_apertures(
    raw_pads: list[tuple[int, ParsedPrimitive]],
    drill_manager_data: bytes,
    ctx: ParseContext,
) -> list[tuple[int, ParsedPrimitive]]:
    """Attach validated Altium pad-template solder-mask apertures to pads.

    Altium pad/via templates can carry mask opening data. This parser only
    uses a narrow, validated template-name encoding when richer template data
    is not present in the file streams.

    Returns a new pad list; matched pads get fresh frozen payloads carrying
    the mask aperture, the rest are passed through unchanged.
    """
    if not drill_manager_data:
        return raw_pads
    updated = list(raw_pads)
    for record in _parse_drill_manager_records(drill_manager_data, ctx):
        aperture = _pad_mask_aperture_from_drill_manager_record(record)
        if aperture is None:
            continue
        for primitive_index in record.primitive_indices:
            if primitive_index < 0 or primitive_index >= len(updated):
                continue
            component, pad_geometry = updated[primitive_index]
            if not isinstance(pad_geometry.data, ParsedPadPayload):
                continue
            pad = pad_geometry.data
            if not _pad_matches_template_aperture_source(pad, record.properties):
                continue
            new_pad = replace(
                pad,
                mask_aperture_width=aperture.width,
                mask_aperture_height=aperture.height,
                mask_aperture_source=aperture.source,
            )
            updated[primitive_index] = (component, replace(pad_geometry, data=new_pad))
    return updated


def _parse_drill_manager_records(data: bytes, ctx: ParseContext) -> tuple[DrillManagerRecord, ...]:
    records: list[DrillManagerRecord] = []
    pos = 0
    while pos < len(data):
        header_size = _drill_manager_header_size(data, pos)
        if header_size == 0:
            if pos != 0:
                ctx.warn(
                    "drill_manager_truncated",
                    f"DrillManager record header unrecognized at offset {pos}; stopping mid-stream",
                )
            break
        prop_len = u32(data, pos + header_size - 4)
        prop_start = pos + header_size
        prop_end = prop_start + prop_len
        if prop_end > len(data):
            break
        properties = parse_record_payload(data[prop_start:prop_end].rstrip(b"\0"))
        pos = prop_end
        if pos + 4 > len(data):
            break
        primitive_count = u32(data, pos)
        pos += 4
        refs_end = pos + primitive_count * 4
        if refs_end > len(data):
            break
        primitive_indices = tuple(u32(data, pos + index * 4) for index in range(primitive_count))
        pos = refs_end
        if properties:
            records.append(
                DrillManagerRecord(
                    properties=properties,
                    primitive_indices=primitive_indices,
                )
            )
    return tuple(records)


def _drill_manager_header_size(data: bytes, pos: int) -> int:
    """Disambiguate the two DrillManager record-header layouts.

    Altium writes DrillManager/Data with one of two fixed header sizes before
    the pipe-delimited property payload: an 8-byte header (older
    pre-flag-word format) and a 12-byte header (newer format with an extra
    4-byte object/flag word). Both end in a u32 property length. The header is
    valid only when the property payload begins with the ``b"|"`` field
    separator, so probe each size and accept the first whose payload starts
    with ``|``.
    """
    for header_size in (8, 12):
        if pos + header_size > len(data):
            continue
        prop_len = u32(data, pos + header_size - 4)
        prop_start = pos + header_size
        prop_end = prop_start + prop_len
        if prop_len <= 0 or prop_end > len(data):
            continue
        if data[prop_start : prop_start + 1] == b"|":
            return header_size
    return 0


def _pad_mask_aperture_from_drill_manager_record(
    record: DrillManagerRecord,
) -> PadMaskAperture | None:
    properties = record.properties
    if properties.get("objectid", "").lower() != "pad":
        return None
    template_name = properties.get("templatename", "")
    match = _PAD_TEMPLATE_MASK_RE.fullmatch(template_name)
    if match is None:
        return None
    mask_width = _template_hundredths_mm(match.group("mask_w"))
    mask_height = _template_hundredths_mm(match.group("mask_h"))
    if mask_width <= 0.0 or mask_height <= 0.0:
        return None
    return PadMaskAperture(
        width=mask_width,
        height=mask_height,
        source=f"altium:drill-manager-template:{template_name}",
    )


def _pad_matches_template_aperture_source(
    pad: ParsedPadPayload,
    properties: dict[str, str],
) -> bool:
    template_name = properties.get("templatename", "")
    match = _PAD_TEMPLATE_MASK_RE.fullmatch(template_name)
    if match is None:
        return False
    expected_width = _template_hundredths_mm(match.group("pad_w"))
    expected_height = _template_hundredths_mm(match.group("pad_h"))
    expected_drill = _template_hundredths_mm(match.group("drill"))
    expected_mask_width = _template_hundredths_mm(match.group("mask_w"))
    expected_mask_height = _template_hundredths_mm(match.group("mask_h"))
    return (
        _close_mm(pad.width, expected_width)
        and _close_mm(pad.height, expected_height)
        and _close_mm(pad.drill, expected_drill)
        and expected_mask_width >= max(pad.width, pad.drill)
        and expected_mask_height >= max(pad.height, pad.drill)
    )


def _template_hundredths_mm(raw: str) -> float:
    return int(raw) / 100.0


def _close_mm(value: float, expected: float) -> bool:
    return math.isclose(value, expected, abs_tol=0.03)


def parse_texts(
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[tuple[int, ParsedPrimitive]]:
    """Parse Texts6/Data into component-indexed text geometry.

    Each text record has 2 subrecords: binary properties + Pascal string.
    TextRecord.parse walks both subrecords once, returning the record and the
    cursor past it.
    """
    texts: list[tuple[int, ParsedPrimitive]] = []
    pos = 0
    index = 0

    while pos < len(data):
        if data[pos] != 5:
            ctx.warn(
                "truncated_stream",
                f"Texts6/Data: unexpected record type byte {data[pos]} at byte {pos}; "
                f"{len(data) - pos} trailing bytes dropped",
            )
            break
        text_rec, pos = TextRecord.parse(data, pos, ctx)
        if text_rec is None:
            continue

        text_layer_ref = altium_layer_ref(text_rec.layer, layer_map, ctx, source=f"text {index}")
        if text_layer_ref is None:
            continue
        layer = text_layer_ref.name

        roles = list(layer_geometry_roles(text_rec.layer, layer_map))
        roles.append(ParsedRole.TEXT)
        if text_rec.is_designator:
            roles.append(ParsedRole.DESIGNATOR)
        elif text_rec.is_comment:
            roles.append(ParsedRole.VALUE)
        else:
            roles.append(ParsedRole.USER_TEXT)

        component_index = None if text_rec.component == COMPONENT_NONE else text_rec.component

        texts.append(
            (
                text_rec.component,
                ParsedPrimitive(
                    id=f"text:{index}",
                    object_type=ParsedObjectKind.TEXT,
                    shape=ParsedShapeKind.TEXT,
                    roles=tuple(roles),
                    data=PcbText(
                        text=text_rec.text,
                        x=int_to_mm(text_rec.position[0]),
                        y=-int_to_mm(text_rec.position[1]),
                        rotation=text_rec.rotation,
                        font_size=int_to_mm(text_rec.height),
                        mirrored=text_rec.is_mirrored,
                    ),
                    layers=(layer,),
                    metadata=geometry_metadata(
                        native_type="TEXT",
                        source_collection="artwork"
                        if component_index is None
                        else "footprint_artwork",
                        native_index=index,
                        native_component_index=component_index,
                    ),
                ),
            )
        )
        index += 1

    return texts


def parse_fills(
    data: bytes, nets: dict[int, PcbNet], layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> tuple[list[ParsedPrimitive], list[PcbKeepout]]:
    """Parse Fills6/Data into rectangular source-layer geometry."""
    records = read_binary_records(data, ctx, source="Fills6/Data")
    fills: list[ParsedPrimitive] = []
    keepouts: list[PcbKeepout] = []
    unknown_nets: list[int] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.FILL:
            continue
        fill = FillRecord.from_bytes(body, ctx)
        if fill is None:
            continue
        layer_ref = altium_layer_ref(fill.layer, layer_map, ctx, source=f"fill {index}")
        if layer_ref is None:
            continue
        layer = layer_ref.name

        x1 = int_to_mm(fill.pos1[0])
        y1 = -int_to_mm(fill.pos1[1])
        x2 = int_to_mm(fill.pos2[0])
        y2 = -int_to_mm(fill.pos2[1])

        # Build 4-corner rectangle, apply rotation around center
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        hw, hh = (x2 - x1) / 2, (y2 - y1) / 2
        corners: list[tuple[float, float]] = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]

        if fill.rotation != 0:
            rad = math.radians(fill.rotation)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            corners = [(dx * cos_r - dy * sin_r, dx * sin_r + dy * cos_r) for dx, dy in corners]

        points = [(cx + dx, cy + dy) for dx, dy in corners]
        if fill.is_keepout:
            keepouts.append(
                PcbKeepout(
                    id=f"keepout_fill:{fill.layer}:{index}",
                    boundary=PcbClosedPath.from_points(points),
                    layers=(layer_ref,),
                    rules=altium_keepout_rules(fill.keepout_restrictions),
                    metadata=keepout_metadata(
                        native_type="FILL",
                        native_kind="keepout",
                        native_index=index,
                        properties={"keepout_restrictions": str(fill.keepout_restrictions)},
                    ),
                )
            )
            continue

        roles = list(layer_geometry_roles(fill.layer, layer_map))
        if fill.layer in COPPER_LAYERS:
            roles.append(ParsedRole.CONDUCTOR)
            object_type = ParsedObjectKind.REGION
            source_collection = "conductors"
        else:
            object_type = ParsedObjectKind.GRAPHIC
            source_collection = "artwork"

        component_index = None if fill.component == COMPONENT_NONE else fill.component

        fills.append(
            ParsedPrimitive(
                id=f"fill:{fill.layer}:{index}",
                object_type=object_type,
                shape=ParsedShapeKind.POLYGON,
                roles=normalize_parsed_roles(*roles),
                data=PcbPolygon(points=points),
                layers=(layer,),
                net_number=(
                    resolve_stream_net(fill.net, nets, unknown_nets)
                    if fill.layer in COPPER_LAYERS
                    else 0
                ),
                metadata=geometry_metadata(
                    native_type="FILL",
                    source_collection=source_collection,
                    native_index=index,
                    native_component_index=component_index,
                ),
            )
        )

    warn_unknown_stream_nets(ctx, "Fills6/Data", unknown_nets)
    return fills, keepouts


def parse_polygon_pours(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
) -> tuple[list[PcbPour], dict[int, str], dict[int, int]]:
    """Parse Polygons6/Data → copper-pour intent and lookup maps.

    Returns (pours, pour_id_map, pour_net_map). The maps let concrete fill
    geometry inherit net and parent-pour identity without rendering the source
    boundary as copper.
    """
    records = read_text_records(data, ctx, source="Polygons6/Data")
    pours: list[PcbPour] = []
    pour_id_map: dict[int, str] = {}
    pour_net_map: dict[int, int] = {}
    unknown_nets: list[int] = []

    for index, rec in enumerate(records):
        pourindex = guarded_int(
            rec.get("pourindex", "-1") or "-1",
            ctx=ctx,
            field=f"polygon pour {index} pourindex",
            default=-1,
        )

        # Resolve net: text records store 0-based Nets6 index,
        # resolve_stream_net converts to the 1-based pcb.nets key and degrades
        # an unknown index to unconnected.
        net_raw = guarded_int(
            rec.get("net", str(NET_UNCONNECTED)) or str(NET_UNCONNECTED),
            ctx=ctx,
            field=f"polygon pour {index} net",
            default=NET_UNCONNECTED,
        )
        net_num = resolve_stream_net(net_raw, nets, unknown_nets)
        net = None if net_num == 0 else nets.get(net_num)

        # Resolve layer from V7 layer name
        layer_id = rec.get("layer", "").upper()
        layer_num = V7_NAME_TO_NUM.get(layer_id)
        if layer_num is None:
            continue
        layer = altium_layer_ref(layer_num, layer_map, ctx, source=f"polygon pour {index}")
        if layer is None:
            continue

        # Extract boundary vertices (vx0..vxN, vy0..vyN in mils)
        boundary: list[tuple[float, float]] = []
        i = 0
        while True:
            vx_key = f"vx{i}"
            vy_key = f"vy{i}"
            if vx_key not in rec or vy_key not in rec:
                break
            x_mm = parse_mil(rec[vx_key], ctx=ctx, field=f"polygon pour {index} {vx_key}")
            y_mm = -parse_mil(rec[vy_key], ctx=ctx, field=f"polygon pour {index} {vy_key}")
            boundary.append((x_mm, y_mm))
            i += 1

        if len(boundary) < 3:
            continue

        # Fill type from hatchstyle
        hatchstyle = rec.get("hatchstyle", "")
        fill_mode = _altium_pour_fill_mode(hatchstyle)

        # Track width (min thickness within pour)
        trackwidth_str = rec.get("trackwidth", "")
        track_width = (
            parse_mil(trackwidth_str, ctx=ctx, field=f"polygon pour {index} trackwidth")
            if trackwidth_str
            else 0.0
        )
        grid_str = rec.get("gridsize", "")
        grid = (
            parse_mil(grid_str, ctx=ctx, field=f"polygon pour {index} gridsize")
            if grid_str
            else 0.0
        )

        pour_id = f"polygon_pour:{pourindex}:{index}"
        if pourindex >= 0:
            pour_id_map[pourindex] = pour_id
            pour_net_map[pourindex] = net_num

        pours.append(
            PcbPour(
                id=pour_id,
                boundary=PcbClosedPath.from_points(boundary),
                layers=(layer,),
                net=net,
                priority=pourindex,
                settings=PcbPourSettings(
                    fill_mode=fill_mode,
                    hatch_style=hatchstyle,
                    grid_mm=grid,
                    track_width_mm=track_width,
                    min_thickness_mm=track_width,
                ),
                metadata=pour_metadata(
                    native_type="POLYGON",
                    native_index=index,
                    native_pour_index=pourindex,
                    properties=rec,
                ),
            )
        )

    warn_unknown_stream_nets(ctx, "Polygons6/Data", unknown_nets)
    return pours, pour_id_map, pour_net_map


def _altium_pour_fill_mode(hatchstyle: str) -> PcbPourFillMode:
    normalized = hatchstyle.strip().lower()
    if not normalized:
        return PcbPourFillMode.UNKNOWN
    if normalized == "solid":
        return PcbPourFillMode.SOLID
    if normalized in {"none", "no", "unfilled"}:
        return PcbPourFillMode.NONE
    return PcbPourFillMode.HATCH


def parse_board6_outline(
    board_props: dict[str, str],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
) -> list[ParsedPrimitive]:
    """Synthesize the board profile from Board6 ``KIND{i}/VX{i}/VY{i}`` vertices.

    Older Altium files keep the board shape only as Board6 vertex keys —
    they have no outline-layer primitives at all. Arc edges (``kind != 0``)
    are defined by ``CX/CY/SA/EA/R`` and linearized like region outlines.
    """
    vertices: list[ExtendedVertex] = []
    i = 0
    while f"vx{i}" in board_props and f"vy{i}" in board_props:
        is_round = board_props.get(f"kind{i}", "0").strip() != "0"
        vertices.append(
            ExtendedVertex(
                x=_mil_str_to_internal(board_props[f"vx{i}"], ctx, f"Board6 vx{i}"),
                y=_mil_str_to_internal(board_props[f"vy{i}"], ctx, f"Board6 vy{i}"),
                is_round=is_round,
                center_x=_mil_str_to_internal(
                    board_props.get(f"cx{i}", "0mil"), ctx, f"Board6 cx{i}"
                ),
                center_y=_mil_str_to_internal(
                    board_props.get(f"cy{i}", "0mil"), ctx, f"Board6 cy{i}"
                ),
                radius=_mil_str_to_internal(board_props.get(f"r{i}", "0mil"), ctx, f"Board6 r{i}"),
                start_angle=guarded_float(
                    board_props.get(f"sa{i}", "0"), ctx=ctx, field=f"Board6 sa{i}"
                ),
                end_angle=guarded_float(
                    board_props.get(f"ea{i}", "0"), ctx=ctx, field=f"Board6 ea{i}"
                ),
            )
        )
        i += 1
    if len(vertices) < 3:
        if vertices:
            ctx.warn(
                "board_outline",
                f"Board6 outline has only {len(vertices)} vertices; ignored",
            )
        return []

    points = linearize_arc_vertices(vertices)
    polygon = PcbPolygon(points=[(int_to_mm(x), -int_to_mm(y)) for x, y in points])
    layer_name = altium_layer_name(int(AltiumLayer.MECHANICAL_1), layer_map)
    return [
        ParsedPrimitive(
            id="outline_board6:0",
            object_type=ParsedObjectKind.GRAPHIC,
            shape=ParsedShapeKind.POLYGON,
            roles=normalize_parsed_roles(ParsedRole.BOARD_OUTLINE),
            data=polygon,
            layers=(layer_name,) if layer_name else (),
            metadata=geometry_metadata(
                native_type="BOARD6_OUTLINE",
                native_kind="board_outline",
                source_collection="board_profile",
                native_index=0,
            ),
        )
    ]


def _mil_str_to_internal(
    value: str, ctx: ParseContext | None = None, field: str = "Board6 mil value"
) -> int:
    """Convert a Board6 mil-string to Altium internal units (0.1 µinch)."""
    return round(parse_mil(value, ctx=ctx, field=field) / int_to_mm(1))


def parse_board_outline(
    tracks_data: bytes,
    arcs_data: bytes,
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
) -> list[ParsedPrimitive]:
    """Extract board outline geometry from fallback mechanical/keepout layers.

    Falls back to Keep-Out layer (74) if no Mechanical 1 primitives found.
    Also checks for any mechanical layer whose MECHKIND is EDGE.
    """
    outline: list[ParsedPrimitive] = []

    # Prefer a layer with EDGE function (from MECHKIND=BoardShape), then
    # fall back to Mechanical 1 (57), then Keep-Out (74).
    edge_layers = [
        num
        for num, lyr in layer_map.items()
        if lyr.has_role(LayerRole.EDGE) and num >= AltiumLayer.MECHANICAL_1
    ]
    candidates = edge_layers or [int(AltiumLayer.MECHANICAL_1)]
    candidates.append(int(AltiumLayer.MULTI_LAYER))
    # Deduplicate while preserving order
    seen: set[int] = set()
    target_layers: list[int] = []
    for n in candidates:
        if n not in seen:
            seen.add(n)
            target_layers.append(n)

    for target_layer in target_layers:
        if outline:
            break

        edge_name = altium_layer_name(target_layer, layer_map)
        if not edge_name:
            continue

        for index, (rec_type, body) in enumerate(
            read_binary_records(tracks_data, ctx, source="Tracks6/Data (board outline)")
        ):
            if rec_type != PcbRecordType.TRACK:
                continue
            track = TrackRecord.from_bytes(body, ctx)
            if track is None or track.layer != target_layer:
                continue
            if track.component != COMPONENT_NONE:
                continue

            outline.append(
                ParsedPrimitive(
                    id=f"outline_track:{target_layer}:{index}",
                    object_type=ParsedObjectKind.GRAPHIC,
                    shape=ParsedShapeKind.LINE,
                    roles=layered_geometry_roles(
                        target_layer,
                        layer_map,
                        ParsedRole.BOARD_OUTLINE,
                    ),
                    data=PcbLine(
                        start_x=int_to_mm(track.start[0]),
                        start_y=-int_to_mm(track.start[1]),
                        end_x=int_to_mm(track.end[0]),
                        end_y=-int_to_mm(track.end[1]),
                        width=int_to_mm(track.width),
                    ),
                    layers=(edge_name,),
                    metadata=geometry_metadata(
                        native_type="TRACK",
                        native_kind="board_outline",
                        source_collection="board_profile",
                        native_index=index,
                    ),
                )
            )

        for index, (rec_type, body) in enumerate(
            read_binary_records(arcs_data, ctx, source="Arcs6/Data (board outline)")
        ):
            if rec_type != PcbRecordType.ARC:
                continue
            arc = ArcRecord.from_bytes(body, ctx)
            if arc is None or arc.layer != target_layer:
                continue
            if arc.component != COMPONENT_NONE:
                continue

            cx = int_to_mm(arc.center[0])
            cy_orig = int_to_mm(arc.center[1])
            radius = int_to_mm(arc.radius)
            width = int_to_mm(arc.width)

            shape, payload = _arc_shape_payload(
                cx, cy_orig, radius, width, arc.start_angle, arc.end_angle
            )
            outline.append(
                ParsedPrimitive(
                    id=f"outline_arc:{target_layer}:{index}",
                    object_type=ParsedObjectKind.GRAPHIC,
                    shape=shape,
                    roles=layered_geometry_roles(
                        target_layer,
                        layer_map,
                        ParsedRole.BOARD_OUTLINE,
                    ),
                    data=payload,
                    layers=(edge_name,),
                    metadata=geometry_metadata(
                        native_type="ARC",
                        native_kind="board_outline",
                        source_collection="board_profile",
                        native_index=index,
                    ),
                )
            )

    return outline


def parse_component_bodies(data: bytes, ctx: ParseContext) -> dict[int, list[ParsedPrimitive]]:
    """Parse ComponentBodies6/Data into component-indexed model geometry.

    Text records with pipe-delimited properties. Key properties:
    - ``MODELID``: OLE stream ID for the embedded STEP data
    - ``COMPONENT``: component index (int, 65535 = board-level body)
    - ``MODEL.2D.X``, ``MODEL.2D.Y``: 2D position in mil
    - ``MODEL.3D.ROTX/Y/Z``: rotation in degrees
    - ``MODEL.3D.DZ``: Z offset in mil
    """
    records = read_text_records(data, ctx, source="ComponentBodies6/Data")
    result: dict[int, list[ParsedPrimitive]] = {}

    for index, rec in enumerate(records):
        model_id = rec.get("modelid", "")
        if not model_id:
            continue

        comp_str = rec.get("component", "")
        if not comp_str:
            continue
        comp_idx = guarded_int(
            comp_str, ctx=ctx, field=f"component body {index} component", default=COMPONENT_NONE
        )
        if comp_idx == COMPONENT_NONE:
            continue

        # 2D position (mil → mm)
        x_str = rec.get("model.2d.x", "0mil")
        y_str = rec.get("model.2d.y", "0mil")
        offset_x = parse_mil(x_str, ctx=ctx, field=f"component body {index} x")
        offset_y = -parse_mil(y_str, ctx=ctx, field=f"component body {index} y")

        # Z offset (mil → mm)
        dz_str = rec.get("model.3d.dz", "0mil")
        offset_z = parse_mil(dz_str, ctx=ctx, field=f"component body {index} dz")

        # Rotation (degrees, may be scientific notation)
        rot_x = guarded_float(rec.get("model.3d.rotx", "0"), ctx=ctx, field=f"body {index} rotx")
        rot_y = guarded_float(rec.get("model.3d.roty", "0"), ctx=ctx, field=f"body {index} roty")
        rot_z = guarded_float(rec.get("model.3d.rotz", "0"), ctx=ctx, field=f"body {index} rotz")

        model = ParsedPrimitive(
            id=f"component_body:{comp_idx}:{index}",
            object_type=ParsedObjectKind.MODEL_3D,
            shape=ParsedShapeKind.MODEL,
            roles=(ParsedRole.COMPONENT_BODY,),
            data=PcbModel3D(
                source=model_id,
                offset=(offset_x, offset_y, offset_z),
                rotation=(rot_x, rot_y, rot_z),
            ),
            metadata=geometry_metadata(
                native_type="COMPONENT_BODY",
                source_collection="footprint_artwork",
                native_index=index,
                native_component_index=comp_idx,
                properties=rec,
            ),
        )
        result.setdefault(comp_idx, []).append(model)

    return result
