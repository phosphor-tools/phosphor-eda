"""Region stream parsers for the Altium PCB parser.

Decodes the Regions6 and ShapeBasedRegions6 OLE streams into polygon
``ParsedPrimitive`` geometry, including pour net inheritance and the
board-polygon dedupe between the two representations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import PcbLayer, PcbNet, PcbPolygon
from phosphor_eda.formats.altium._helpers import guarded_int
from phosphor_eda.formats.altium.enums import PcbRecordType, RegionKind
from phosphor_eda.formats.altium.geometry import linearize_arc_vertices
from phosphor_eda.formats.altium.pcb_layers import V7_NAME_TO_NUM, altium_layer_ref
from phosphor_eda.formats.altium.pcb_primitives import (
    COPPER_LAYERS,
    ParsedObjectKind,
    ParsedPrimitive,
    ParsedRole,
    ParsedShapeKind,
    geometry_metadata,
    int_to_mm,
    layer_geometry_roles,
    read_binary_records,
    resolve_pour_id,
    resolve_pour_net,
    resolve_stream_net,
    warn_unknown_stream_nets,
)
from phosphor_eda.formats.altium.pcb_records import (
    COMPONENT_NONE,
    NET_UNCONNECTED,
    RegionRecord,
    ShapeBasedRegionRecord,
)

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext


def _region_primitive(
    *,
    id_prefix: str,
    native_type: str,
    region: RegionRecord | ShapeBasedRegionRecord,
    points: list[tuple[float, float]],
    holes: list[list[tuple[float, float]]],
    resolved_num: int,
    layer: str,
    region_kind: int | None,
    index: int,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    pour_id_map: dict[int, str] | None,
    pour_net_map: dict[int, int] | None,
    ctx: ParseContext,
    unknown_nets: list[int],
) -> ParsedPrimitive:
    """Build a polygon primitive shared by Regions6 and ShapeBasedRegions6.

    Both record streams resolve net, pour id, roles and metadata identically
    once their vertices are decoded; only the id prefix, ``native_type`` and
    the vertex-decoding step (raw f64 pairs vs arc-linearized extended
    vertices) differ, and those are handled by the callers.
    """
    polygon_property_index = guarded_int(
        region.properties.get("polygonindex", "-1") or "-1",
        ctx=ctx,
        field=f"region {index} polygonindex",
        default=-1,
    )
    polygon_index = polygon_property_index if polygon_property_index >= 0 else region.polygon
    subpolygon_index = guarded_int(
        region.properties.get("subpolyindex", "-1") or "-1",
        ctx=ctx,
        field=f"region {index} subpolyindex",
        default=-1,
    )
    pour_id = resolve_pour_id(pour_id_map or {}, polygon_index)

    # Net resolution: use direct net if assigned, otherwise inherit from pour.
    if resolved_num in COPPER_LAYERS:
        if region.net == NET_UNCONNECTED and pour_net_map:
            net_num = resolve_pour_net(pour_net_map, polygon_index)
        else:
            net_num = resolve_stream_net(region.net, nets, unknown_nets)
    else:
        net_num = 0
    net_obj = nets.get(net_num)
    net_name = net_obj.name if net_obj else ""

    roles = list(layer_geometry_roles(resolved_num, layer_map))
    if region_kind == RegionKind.POLYGON_CUTOUT:
        roles.append(ParsedRole.POLYGON_CUTOUT)
    elif resolved_num in COPPER_LAYERS:
        roles.append(ParsedRole.CONDUCTOR)

    component_index = None if region.component == COMPONENT_NONE else region.component

    return ParsedPrimitive(
        id=f"{id_prefix}:{resolved_num}:{index}",
        object_type=ParsedObjectKind.REGION,
        shape=ParsedShapeKind.POLYGON,
        roles=tuple(roles),
        data=PcbPolygon(points=points, holes=holes),
        layers=(layer,),
        net_number=net_num,
        net_name=net_name,
        pour_id=pour_id,
        metadata=geometry_metadata(
            native_type=native_type,
            native_kind="" if region_kind is None else str(region_kind),
            source_collection="conductors" if ParsedRole.CONDUCTOR in roles else "artwork",
            native_index=index,
            native_component_index=component_index,
            native_polygon_index=polygon_index,
            native_subpolygon_index=subpolygon_index,
            properties=region.properties,
        ),
    )


def parse_regions(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_id_map: dict[int, str] | None = None,
    pour_net_map: dict[int, int] | None = None,
) -> list[ParsedPrimitive]:
    """Parse Regions6/Data into polygon geometry.

    Region records contain a property string followed by vertex data
    (pairs of float64 in Altium internal units).  All layers are included —
    copper regions carry net info, non-copper regions (silkscreen fills,
    paste openings, etc.) have net_number 0.

    When pour_net_map is provided, regions with net=0xFFFF (inherit) and
    a valid polygon reference will inherit the net from their parent polygon
    pour.
    """
    records = read_binary_records(data, ctx, source="Regions6/Data")
    polygons: list[ParsedPrimitive] = []
    unknown_nets: list[int] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.REGION:
            continue
        region = RegionRecord.from_bytes(body, ctx)
        if region is None:
            continue

        # Determine layer from V7 property or fallback to byte
        v7_layer = region.properties.get("v7_layer", "").upper()
        resolved_num = (
            V7_NAME_TO_NUM[v7_layer] if v7_layer and v7_layer in V7_NAME_TO_NUM else region.layer
        )

        layer_ref = altium_layer_ref(resolved_num, layer_map, ctx, source=f"region {index}")
        if layer_ref is None:
            continue
        layer = layer_ref.name
        region_kind = parse_region_kind(region.properties, ctx)

        points = [(int_to_mm(int(vx)), -int_to_mm(int(vy))) for vx, vy in region.vertices]
        if len(points) < 3:
            continue

        # Convert hole vertices
        holes: list[list[tuple[float, float]]] = []
        for hole_verts in region.holes:
            h_pts = [(int_to_mm(int(vx)), -int_to_mm(int(vy))) for vx, vy in hole_verts]
            if len(h_pts) >= 3:
                holes.append(h_pts)

        polygons.append(
            _region_primitive(
                id_prefix="region",
                native_type="REGION",
                region=region,
                points=points,
                holes=holes,
                resolved_num=resolved_num,
                layer=layer,
                region_kind=region_kind,
                index=index,
                nets=nets,
                layer_map=layer_map,
                pour_id_map=pour_id_map,
                pour_net_map=pour_net_map,
                ctx=ctx,
                unknown_nets=unknown_nets,
            )
        )

    warn_unknown_stream_nets(ctx, "Regions6/Data", unknown_nets)
    return polygons


def parse_shape_based_regions(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_id_map: dict[int, str] | None = None,
    pour_net_map: dict[int, int] | None = None,
) -> list[ParsedPrimitive]:
    """Parse ShapeBasedRegions6/Data into polygon geometry.

    Uses the extended vertex format (37 bytes per vertex with arc support).

    Net inheritance matches ``parse_regions``: a copper region carrying the
    unconnected sentinel (net == 0xFFFF) inherits the net of its parent polygon
    pour via the text or binary polygon index.
    """
    records = read_binary_records(data, ctx, source="ShapeBasedRegions6/Data")
    polygons: list[ParsedPrimitive] = []
    unknown_nets: list[int] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.REGION:
            continue
        region = ShapeBasedRegionRecord.from_bytes(body, ctx)
        if region is None:
            continue

        # Determine layer from V7 property or fallback to byte
        v7_layer = region.properties.get("v7_layer", "").upper()
        resolved_num = (
            V7_NAME_TO_NUM[v7_layer] if v7_layer and v7_layer in V7_NAME_TO_NUM else region.layer
        )

        layer_ref = altium_layer_ref(resolved_num, layer_map, ctx, source=f"shape region {index}")
        if layer_ref is None:
            continue
        layer = layer_ref.name
        region_kind = parse_region_kind(region.properties, ctx)

        # Linearize arc edges, then convert to mm with Y negated
        raw_pts = linearize_arc_vertices(region.vertices)
        points: list[tuple[float, float]] = [(int_to_mm(x), -int_to_mm(y)) for x, y in raw_pts]
        if len(points) < 3:
            continue

        # Convert hole vertices (stored as f64 in internal units)
        holes: list[list[tuple[float, float]]] = []
        for hole_verts in region.holes:
            h_pts = [(int_to_mm(int(vx)), -int_to_mm(int(vy))) for vx, vy in hole_verts]
            if len(h_pts) >= 3:
                holes.append(h_pts)

        polygons.append(
            _region_primitive(
                id_prefix="shape_region",
                native_type="SHAPE_BASED_REGION",
                region=region,
                points=points,
                holes=holes,
                resolved_num=resolved_num,
                layer=layer,
                region_kind=region_kind,
                index=index,
                nets=nets,
                layer_map=layer_map,
                pour_id_map=pour_id_map,
                pour_net_map=pour_net_map,
                ctx=ctx,
                unknown_nets=unknown_nets,
            )
        )

    warn_unknown_stream_nets(ctx, "ShapeBasedRegions6/Data", unknown_nets)
    return polygons


def dedupe_shape_based_board_polygons(
    regions: list[ParsedPrimitive],
    shape_based_regions: list[ParsedPrimitive],
) -> list[ParsedPrimitive]:
    """Drop ShapeBasedRegions6 board polygons already represented by Regions6."""
    if not regions:
        return shape_based_regions
    region_keys = {
        key for polygon in regions for key in (_polygon_duplicate_key(polygon),) if key is not None
    }
    return [
        polygon
        for polygon in shape_based_regions
        if _polygon_duplicate_key(polygon) not in region_keys
    ]


type _PolygonDuplicateKey = tuple[str, int, tuple[tuple[float, float], ...]]


def _polygon_duplicate_key(poly: ParsedPrimitive) -> _PolygonDuplicateKey | None:
    # Key on layer + vertex count + the rounded vertices themselves. A bbox-only
    # key dropped distinct polygons that merely share a bounding box (e.g. a
    # board frame and an inscribed shape). The Regions6 and ShapeBasedRegions6
    # representations of the same primitive differ only by an explicit closing
    # vertex, so normalize that away before keying.
    if not isinstance(poly.data, PcbPolygon) or len(poly.data.points) < 3:
        return None
    vertices = [(round(x, 3), round(y, 3)) for x, y in poly.data.points]
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices.pop()
    return (poly.primary_layer, len(vertices), tuple(vertices))


def parse_region_kind(properties: dict[str, str], ctx: ParseContext | None = None) -> int | None:
    raw_kind = properties.get("kind")
    if raw_kind is None:
        return None
    try:
        return int(raw_kind)
    except ValueError:
        if ctx is not None:
            ctx.warn(
                "malformed_region_kind",
                f"non-integer region kind {raw_kind!r}; treated as default region",
            )
        return None
