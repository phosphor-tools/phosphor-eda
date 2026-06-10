"""SVG primitive conversion for typed PCB renderer inventory."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbArtworkKind,
    PcbBoardProfile,
    PcbCircle,
    PcbClosedPath,
    PcbConductorKind,
    PcbDimension,
    PcbDrill,
    PcbLine,
    PcbModel3D,
    PcbPad,
    PcbPolygon,
    PcbText,
    PcbVia,
)
from phosphor_eda.render.drills import drill_geometry
from phosphor_eda.render.inventory import (
    InventoryItem,
    InventoryItemKind,
    InventoryPurpose,
    PcbRenderInventory,
)
from phosphor_eda.geometry.shapely_ops import normalize_geometry
from phosphor_eda.geometry.pcb_geometry import (
    arc_center_from_three_points,
    arc_sweep_angle,
    arc_to_polyline,
    board_outline_polygon,
    closed_path_geometry,
    pad_polygon,
)
from phosphor_eda.geometry.text_outlines import text_outline_geometry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shapely.coords import CoordinateSequence

    from phosphor_eda.render.inventory import InventoryTags


def _empty_data() -> dict[str, str]:
    return {}


Bounds = tuple[float, float, float, float]


@dataclass(frozen=True)
class SvgPrimitive:
    d: str
    source_id: str
    source_layer: str
    kind: str
    tags: InventoryTags
    data: Mapping[str, str] = field(default_factory=_empty_data)
    style: Mapping[str, str] = field(default_factory=_empty_data)
    bbox: Bounds | None = None


def _union_bounds(primitives: tuple[SvgPrimitive, ...]) -> Bounds | None:
    boxes = [primitive.bbox for primitive in primitives if primitive.bbox is not None]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


@dataclass(frozen=True)
class LayerMask:
    board: tuple[SvgPrimitive, ...] = ()
    drills: tuple[SvgPrimitive, ...] = ()
    openings: tuple[SvgPrimitive, ...] = ()

    def bounds(self) -> Bounds | None:
        """Viewport bounds from the white (board/opening) region's real geometry.

        The mask paints ``board`` white and subtracts ``drills``/``openings``,
        so the viewport only needs to cover the white region.
        """
        return _union_bounds(self.board)


@dataclass(frozen=True)
class LayerClip:
    board: tuple[SvgPrimitive, ...] = ()


def inventory_item_to_svg_primitive(
    item: InventoryItem,
    *,
    target_layer_name: str = "",
) -> SvgPrimitive | None:
    """Convert one typed inventory item into SVG path data."""
    d = _path_d_for_item(item)
    if not d:
        return None
    source_layer = target_layer_name or ("" if item.layer is None else item.layer.name)
    data = {
        "purpose": item.purpose.value,
        "item-kind": item.item_kind.value,
    }
    if item.content_kind is not None:
        data["content-kind"] = item.content_kind.value
    return SvgPrimitive(
        d=d,
        source_id=item.id,
        source_layer=source_layer,
        kind=item.item_kind.value,
        tags=item.tags,
        data=data,
        bbox=_bounds_for_item(item),
    )


def drill_to_svg_primitive(item: InventoryItem) -> SvgPrimitive | None:
    """Convert a drill inventory item into a subtractive mask primitive."""
    if item.item_kind != InventoryItemKind.DRILL or not isinstance(item.source, PcbDrill):
        return None
    geometry = drill_geometry(item.source)
    if geometry is None:
        return None
    d = geometry_to_svg_path_d(geometry)
    if not d:
        return None
    return SvgPrimitive(
        d=d,
        source_id=item.id,
        source_layer="drills",
        kind=InventoryItemKind.DRILL.value,
        tags=item.tags,
        data={"purpose": InventoryPurpose.DRILL.value, "item-kind": InventoryItemKind.DRILL.value},
        bbox=_geometry_bounds(geometry),
    )


def pad_solder_mask_opening_primitive(item: InventoryItem, *, side: str) -> SvgPrimitive | None:
    """Create an implicit solder-mask opening from a pad."""
    if item.item_kind != InventoryItemKind.PAD or not isinstance(item.source, PcbPad):
        return None
    pad = item.source
    if item.layer is not None and item.layer.side not in {"", side}:
        return None
    d = _pad_solder_mask_opening_path_d(pad)
    if not d:
        return None
    source_layer = f"{side}.mask" if side else "mask"
    return SvgPrimitive(
        d=d,
        source_id=pad.id,
        source_layer=source_layer,
        kind=InventoryPurpose.SOLDER_MASK.value,
        tags=item.tags,
        data={
            "purpose": InventoryPurpose.SOLDER_MASK.value,
            "item-kind": InventoryItemKind.PAD.value,
        },
        bbox=_geometry_bounds(_pad_solder_mask_opening_geometry(pad)),
    )


def _geometry_bounds(geometry: BaseGeometry | None) -> Bounds | None:
    if geometry is None or geometry.is_empty:
        return None
    min_x, min_y, max_x, max_y = geometry.bounds
    return (float(min_x), float(min_y), float(max_x), float(max_y))


def _bounds_for_item(item: InventoryItem) -> Bounds | None:
    """Real-geometry bounds for an inventory item.

    Mirrors the geometry sources used by :func:`_path_d_for_item` so mask/clip
    viewports can be sized from true extents rather than re-parsing path data.
    """
    if item.purpose == InventoryPurpose.BOARD_MATERIAL:
        if isinstance(item.source, PcbBoardProfile):
            bounds = _geometry_bounds(board_outline_polygon(item.source))
            if bounds is not None:
                return bounds
        return item.bbox
    if item.item_kind == InventoryItemKind.PAD and isinstance(item.source, PcbPad):
        if item.purpose == InventoryPurpose.SOLDER_MASK:
            return _geometry_bounds(_pad_solder_mask_opening_geometry(item.source))
        return _geometry_bounds(pad_polygon(item.source))
    if item.item_kind == InventoryItemKind.VIA and isinstance(item.source, PcbVia):
        via = item.source
        radius = via.diameter / 2.0
        return (via.x - radius, via.y - radius, via.x + radius, via.y + radius)
    if item.item_kind == InventoryItemKind.DRILL and isinstance(item.source, PcbDrill):
        return _geometry_bounds(drill_geometry(item.source))
    if item.item_kind == InventoryItemKind.KEEPOUT and isinstance(item.payload, PcbClosedPath):
        return _geometry_bounds(closed_path_geometry(item.payload))
    return _payload_bounds(item.payload)


def _payload_bounds(payload: object) -> Bounds | None:
    if isinstance(payload, PcbLine):
        return _point_bounds(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
    if isinstance(payload, PcbArc):
        return _point_bounds(
            (
                (payload.start_x, payload.start_y),
                (payload.mid_x, payload.mid_y),
                (payload.end_x, payload.end_y),
            )
        )
    if isinstance(payload, PcbCircle):
        return (
            payload.cx - payload.radius,
            payload.cy - payload.radius,
            payload.cx + payload.radius,
            payload.cy + payload.radius,
        )
    if isinstance(payload, PcbDimension):
        return _point_bounds(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
    if isinstance(payload, PcbPolygon):
        return _geometry_bounds(polygon_geometry(payload))
    if isinstance(payload, PcbText):
        return _geometry_bounds(text_outline_geometry(payload))
    if isinstance(payload, PcbClosedPath):
        return _geometry_bounds(closed_path_geometry(payload))
    if isinstance(payload, BaseGeometry):
        return _geometry_bounds(payload)
    return None


def _point_bounds(points: tuple[tuple[float, float], ...]) -> Bounds | None:
    if not points:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _path_d_for_item(item: InventoryItem) -> str:
    if item.purpose == InventoryPurpose.BOARD_MATERIAL:
        return _board_material_path_d(item)
    if item.item_kind == InventoryItemKind.BOARD_PROFILE:
        return _board_profile_item_path_d(item)
    if item.item_kind == InventoryItemKind.PAD and isinstance(item.source, PcbPad):
        if item.purpose == InventoryPurpose.SOLDER_MASK:
            return _pad_solder_mask_opening_path_d(item.source)
        return geometry_to_svg_path_d(pad_polygon(item.source))
    if item.item_kind == InventoryItemKind.VIA and isinstance(item.source, PcbVia):
        return _circle_path_d(item.source.x, item.source.y, item.source.diameter / 2.0)
    if item.item_kind == InventoryItemKind.DRILL and isinstance(item.source, PcbDrill):
        geometry = drill_geometry(item.source)
        return "" if geometry is None else geometry_to_svg_path_d(geometry)
    if item.item_kind == InventoryItemKind.KEEPOUT and isinstance(item.payload, PcbClosedPath):
        geometry = closed_path_geometry(item.payload)
        return "" if geometry is None else geometry_to_svg_path_d(geometry)
    return shape_to_svg_path_d(item.payload, filled=item.purpose != InventoryPurpose.BOARD_PROFILE)


def _pad_solder_mask_opening_geometry(pad: PcbPad) -> BaseGeometry | None:
    width = pad.mask_aperture_width
    height = pad.mask_aperture_height
    if width is None or height is None:
        expansion = pad.mask_expansion or 0.0
        width = pad.width + 2.0 * expansion
        height = pad.height + 2.0 * expansion
    if width <= 0.0 or height <= 0.0:
        return None
    return pad_polygon(replace(pad, width=width, height=height))


def _pad_solder_mask_opening_path_d(pad: PcbPad) -> str:
    geometry = _pad_solder_mask_opening_geometry(pad)
    return "" if geometry is None else geometry_to_svg_path_d(geometry)


def shape_to_svg_path_d(payload: object, *, filled: bool = True) -> str:
    """Convert a PCB primitive payload to SVG path data."""
    if isinstance(payload, PcbLine):
        if filled:
            return _stroke_geometry_path_d(payload)
        return _line_path_d(payload)
    if isinstance(payload, PcbArc):
        if filled:
            return _stroke_geometry_path_d(payload)
        return _arc_path_d(payload)
    if isinstance(payload, PcbCircle):
        if payload.fill or filled:
            return _circle_path_d(payload.cx, payload.cy, payload.radius)
        outer = _circle_path_d(payload.cx, payload.cy, payload.radius)
        inner_radius = max(payload.radius - payload.width, 0.0)
        inner = _circle_path_d(payload.cx, payload.cy, inner_radius)
        return f"{outer} {inner}"
    if isinstance(payload, PcbPolygon):
        return geometry_to_svg_path_d(polygon_geometry(payload))
    if isinstance(payload, PcbText):
        return geometry_to_svg_path_d(text_outline_geometry(payload))
    if isinstance(payload, PcbDimension):
        return _line_path_d(
            PcbLine(payload.start_x, payload.start_y, payload.end_x, payload.end_y, 0.0)
        )
    if isinstance(payload, PcbModel3D):
        return ""
    if isinstance(payload, PcbClosedPath):
        geometry = closed_path_geometry(payload)
        return "" if geometry is None else geometry_to_svg_path_d(geometry)
    if isinstance(payload, BaseGeometry):
        return geometry_to_svg_path_d(payload)
    return ""


def polygon_geometry(poly: PcbPolygon) -> BaseGeometry:
    if len(poly.points) < 3:
        return GeometryCollection()
    holes = [hole for hole in poly.holes if len(hole) >= 3]
    return normalize_geometry(Polygon(poly.points, holes=holes or None))


def _stroke_geometry_path_d(payload: PcbLine | PcbArc) -> str:
    width = max(payload.width, 0.0)
    if width <= 0.0:
        return _line_path_d(payload) if isinstance(payload, PcbLine) else _arc_path_d(payload)
    if isinstance(payload, PcbLine):
        line = LineString(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
    else:
        line = LineString(
            arc_to_polyline(
                payload.start_x,
                payload.start_y,
                payload.mid_x,
                payload.mid_y,
                payload.end_x,
                payload.end_y,
                num_points=32,
            )
        )
    return geometry_to_svg_path_d(line.buffer(width / 2.0, cap_style="round"))


def _board_material_path_d(item: InventoryItem) -> str:
    if isinstance(item.source, PcbBoardProfile):
        polygon = board_outline_polygon(item.source)
        if polygon is not None:
            return geometry_to_svg_path_d(polygon)
    if item.bbox is not None:
        min_x, min_y, max_x, max_y = item.bbox
        return _closed_point_pairs_to_svg_path_d(
            ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y))
        )
    return ""


def _board_profile_item_path_d(item: InventoryItem) -> str:
    if item.content_kind == PcbArtworkKind.LINE and isinstance(item.payload, PcbLine):
        return _line_path_d(item.payload)
    if item.content_kind == PcbArtworkKind.ARC and isinstance(item.payload, PcbArc):
        return _arc_path_d(item.payload)
    return shape_to_svg_path_d(item.payload, filled=False)


def _line_path_d(line: PcbLine) -> str:
    return f"M {line.start_x:.4f} {line.start_y:.4f} L {line.end_x:.4f} {line.end_y:.4f}"


def _arc_path_d(arc: PcbArc) -> str:
    cx, cy, radius = arc_center_from_three_points(
        arc.start_x,
        arc.start_y,
        arc.mid_x,
        arc.mid_y,
        arc.end_x,
        arc.end_y,
    )
    if not all(math.isfinite(value) for value in (cx, cy, radius)) or radius <= 0:
        return _line_path_d(PcbLine(arc.start_x, arc.start_y, arc.end_x, arc.end_y, arc.width))
    sweep = arc_sweep_angle(
        arc.start_x,
        arc.start_y,
        arc.mid_x,
        arc.mid_y,
        arc.end_x,
        arc.end_y,
        cx,
        cy,
    )
    large_arc = 1 if abs(sweep) > 180.0 else 0
    sweep_flag = 1 if sweep > 0.0 else 0
    return (
        f"M {arc.start_x:.4f} {arc.start_y:.4f} "
        f"A {radius:.4f} {radius:.4f} 0 {large_arc} {sweep_flag} "
        f"{arc.end_x:.4f} {arc.end_y:.4f}"
    )


def _circle_path_d(cx: float, cy: float, radius: float) -> str:
    if radius <= 0.0:
        return ""
    return (
        f"M {cx + radius:.4f} {cy:.4f} "
        f"A {radius:.4f} {radius:.4f} 0 1 0 {cx - radius:.4f} {cy:.4f} "
        f"A {radius:.4f} {radius:.4f} 0 1 0 {cx + radius:.4f} {cy:.4f} Z"
    )


def _closed_point_pairs_to_svg_path_d(points: tuple[tuple[float, float], ...]) -> str:
    if len(points) < 3:
        return ""
    first = points[0]
    commands = [f"M {first[0]:.4f} {first[1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in points[1:])
    commands.append("Z")
    return " ".join(commands)


def geometry_to_svg_path_d(geometry: BaseGeometry) -> str:
    """Serialize supported Shapely geometry to SVG path data."""
    if geometry.is_empty:
        return ""
    if isinstance(geometry, Polygon):
        return _polygon_to_svg_path_d(geometry)
    if isinstance(geometry, MultiPolygon):
        return " ".join(_polygon_to_svg_path_d(polygon) for polygon in geometry.geoms)
    if isinstance(geometry, LineString):
        return _line_string_to_svg_path_d(geometry)
    if isinstance(geometry, MultiLineString):
        return " ".join(_line_string_to_svg_path_d(line) for line in geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return ""
    if isinstance(geometry, Point):
        return _circle_path_d(float(geometry.x), float(geometry.y), 0.05)
    return ""


def _polygon_to_svg_path_d(polygon: Polygon) -> str:
    parts = [_ring_to_svg_path_d(polygon.exterior.coords)]
    parts.extend(_ring_to_svg_path_d(interior.coords) for interior in polygon.interiors)
    return " ".join(part for part in parts if part)


def _ring_to_svg_path_d(coords: CoordinateSequence) -> str:
    points = [(float(x), float(y)) for x, y, *_ in coords]
    if len(points) < 3:
        return ""
    if points[0] == points[-1]:
        points = points[:-1]
    return _closed_point_pairs_to_svg_path_d(tuple(points))


def _line_string_to_svg_path_d(line: LineString) -> str:
    coords = [(float(x), float(y)) for x, y, *_ in line.coords]
    if len(coords) < 2:
        return ""
    commands = [f"M {coords[0][0]:.4f} {coords[0][1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in coords[1:])
    return " ".join(commands)


def layer_function_for_item(item: InventoryItem) -> str:
    if item.purpose == InventoryPurpose.BOARD_PROFILE:
        return "edge"
    if item.purpose == InventoryPurpose.BOARD_MATERIAL:
        return "substrate"
    if item.purpose == InventoryPurpose.DRILL:
        return "drill"
    if item.purpose == InventoryPurpose.KEEPOUT:
        return "keepout"
    if item.purpose in {
        InventoryPurpose.DESIGNATOR,
        InventoryPurpose.VALUE,
        InventoryPurpose.USER_TEXT,
    }:
        if item.layer is not None and item.layer.has_role(LayerRole.SILKSCREEN):
            return "silkscreen"
        return item.purpose.value
    if item.content_kind == PcbConductorKind.POUR_FILL:
        return "copper"
    return item.purpose.value


def source_layer_name(item: InventoryItem) -> str:
    return "" if item.layer is None else item.layer.name


def solder_mask_opening_primitives(
    inventory: PcbRenderInventory,
    *,
    side: str,
) -> tuple[SvgPrimitive, ...]:
    """Return source-derived solder-mask openings."""
    primitives: list[SvgPrimitive] = []
    explicit_sources: set[tuple[InventoryItemKind, str, str]] = set()
    for item in inventory.items:
        if item.purpose == InventoryPurpose.SOLDER_MASK:
            if item.layer is not None and item.layer.side not in {"", side}:
                continue
            primitive = inventory_item_to_svg_primitive(item)
            explicit_sources.add((item.item_kind, _mask_source_id(item), side))
        else:
            primitive = None
        if primitive is not None:
            primitives.append(primitive)
    for item in inventory.items:
        if item.item_kind != InventoryItemKind.PAD:
            continue
        if (item.item_kind, _mask_source_id(item), side) in explicit_sources:
            continue
        primitive = pad_solder_mask_opening_primitive(item, side=side)
        if primitive is not None:
            primitives.append(primitive)
    return tuple(primitives)


def _mask_source_id(item: InventoryItem) -> str:
    if isinstance(item.source, (PcbPad, PcbVia)):
        return item.source.id
    return item.id
