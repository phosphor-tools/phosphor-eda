"""Shapely geometry construction from PCB domain model objects.

All functions produce geometries in board-space millimetres. These are
inserted into DuckDB as WKB for spatial queries.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from shapely import GeometryCollection, LineString, MultiPolygon, Point, Polygon
from shapely.affinity import rotate
from shapely.ops import unary_union

from phosphor_eda.pcb import PcbArc, PcbCircle, PcbLine, PcbPathSegmentKind, PcbPolygon
from phosphor_eda.shapely_geometry import normalize_geometry, robust_polygonize

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb import (
        PcbBoardProfile,
        PcbClosedPath,
        PcbFootprint,
        PcbPad,
        PcbVia,
    )

# Layer names indicating front copper (KiCad and Altium conventions)
_FRONT_LAYERS = {"F.Cu", "Top Layer", "Top"}
_BACK_LAYERS = {"B.Cu", "Bottom Layer", "Bottom"}
PAD_CURVE_QUAD_SEGS = 12
PAD_ROUNDRECT_QUAD_SEGS = 8
VIA_DRILL_QUAD_SEGS = 8


def _box(min_x: float, min_y: float, max_x: float, max_y: float) -> Polygon:
    """Create a rectangular Polygon from bounds."""
    return Polygon([(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)])


# ---------------------------------------------------------------------------
# Pad geometry
# ---------------------------------------------------------------------------


def pad_polygon(pad: PcbPad) -> BaseGeometry:
    """Construct the actual copper polygon for a pad in board coordinates."""
    cx, cy = pad.x, pad.y
    w, h = pad.width, pad.height

    if pad.shape == "custom" and pad.custom_shapes:
        geometries = [
            geometry
            for shape in pad.custom_shapes
            if not (geometry := _custom_pad_shape_geometry(shape)).is_empty
        ]
        return (
            GeometryCollection() if not geometries else normalize_geometry(unary_union(geometries))
        )

    if pad.shape == "circle":
        return Point(cx, cy).buffer(w / 2, quad_segs=PAD_CURVE_QUAD_SEGS)

    if pad.shape == "oval":
        # Capsule shape: buffered line along major axis
        if w >= h:
            half = (w - h) / 2
            line = LineString([(cx - half, cy), (cx + half, cy)])
            geom = line.buffer(h / 2, quad_segs=PAD_CURVE_QUAD_SEGS)
        else:
            half = (h - w) / 2
            line = LineString([(cx, cy - half), (cx, cy + half)])
            geom = line.buffer(w / 2, quad_segs=PAD_CURVE_QUAD_SEGS)
        if pad.rotation != 0.0:
            geom = rotate(geom, -pad.rotation, origin=(cx, cy))
        return geom

    if pad.shape == "roundrect":
        # Buffer a smaller rectangle by the corner radius
        corner_radius = min(w, h) * pad.roundrect_rratio / 2
        inset_w = w - 2 * corner_radius
        inset_h = h - 2 * corner_radius
        inner = _box(
            cx - inset_w / 2,
            cy - inset_h / 2,
            cx + inset_w / 2,
            cy + inset_h / 2,
        )
        geom = inner.buffer(corner_radius, quad_segs=PAD_ROUNDRECT_QUAD_SEGS)
        if pad.rotation != 0.0:
            geom = rotate(geom, -pad.rotation, origin=(cx, cy))
        return geom

    # Default: rectangle (also handles "rect" and "custom" as bounding box)
    rect = _box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    if pad.rotation != 0.0:
        rect = rotate(rect, -pad.rotation, origin=(cx, cy))
    return rect


def _custom_pad_shape_geometry(
    shape: PcbLine | PcbArc | PcbCircle | PcbPolygon,
) -> BaseGeometry:
    if isinstance(shape, PcbLine):
        if shape.width <= 0.0:
            return GeometryCollection()
        return LineString([(shape.start_x, shape.start_y), (shape.end_x, shape.end_y)]).buffer(
            shape.width / 2.0, cap_style="round"
        )
    if isinstance(shape, PcbArc):
        if shape.width <= 0.0:
            return GeometryCollection()
        return LineString(
            arc_to_polyline(
                shape.start_x,
                shape.start_y,
                shape.mid_x,
                shape.mid_y,
                shape.end_x,
                shape.end_y,
                num_points=32,
            )
        ).buffer(shape.width / 2.0, cap_style="round")
    if isinstance(shape, PcbCircle):
        outer = Point(shape.cx, shape.cy).buffer(shape.radius, quad_segs=PAD_CURVE_QUAD_SEGS)
        if shape.fill or shape.width <= 0.0:
            return outer
        inner_radius = max(shape.radius - shape.width, 0.0)
        if inner_radius <= 0.0:
            return outer
        return outer.difference(Point(shape.cx, shape.cy).buffer(inner_radius))
    polygon = polygon_geometry(shape)
    return GeometryCollection() if polygon is None else polygon


# ---------------------------------------------------------------------------
# Segment geometry
# ---------------------------------------------------------------------------


def segment_geometry(seg: PcbLine) -> tuple[LineString, Polygon]:
    """Return (centerline, copper corridor) for a straight trace segment."""
    centerline = LineString([(seg.start_x, seg.start_y), (seg.end_x, seg.end_y)])
    corridor = centerline.buffer(seg.width / 2, cap_style="flat")
    return centerline, corridor


# ---------------------------------------------------------------------------
# Arc geometry
# ---------------------------------------------------------------------------


def arc_center_from_three_points(
    sx: float, sy: float, mx: float, my: float, ex: float, ey: float
) -> tuple[float, float, float]:
    """Compute arc center and radius from three points on the arc.

    Uses the circumcircle determinant formula. Returns (cx, cy, radius).
    For degenerate (collinear) input, returns the midpoint with a large radius.
    """
    ax, ay = sx, sy
    bx, by = mx, my
    cx_p, cy_p = ex, ey

    d = 2.0 * (ax * (by - cy_p) + bx * (cy_p - ay) + cx_p * (ay - by))

    if abs(d) < 1e-10:
        # Degenerate — collinear points, treat as straight line
        mid_x = (sx + ex) / 2
        mid_y = (sy + ey) / 2
        dist = math.hypot(ex - sx, ey - sy)
        return mid_x, mid_y, dist / 2 if dist > 0 else 1.0

    a_sq = ax * ax + ay * ay
    b_sq = bx * bx + by * by
    c_sq = cx_p * cx_p + cy_p * cy_p
    ux = (a_sq * (by - cy_p) + b_sq * (cy_p - ay) + c_sq * (ay - by)) / d
    uy = (a_sq * (cx_p - bx) + b_sq * (ax - cx_p) + c_sq * (bx - ax)) / d

    radius = math.hypot(ax - ux, ay - uy)
    return ux, uy, radius


def arc_sweep_angle(
    sx: float,
    sy: float,
    mx: float,
    my: float,
    ex: float,
    ey: float,
    cx: float,
    cy: float,
) -> float:
    """Compute signed sweep angle in degrees from start to end through mid.

    Positive = counter-clockwise, negative = clockwise.
    """
    start_angle = math.atan2(sy - cy, sx - cx)
    mid_angle = math.atan2(my - cy, mx - cx)
    end_angle = math.atan2(ey - cy, ex - cx)

    # Determine direction by checking if mid is between start and end CCW
    def _normalize(a: float) -> float:
        while a < 0:
            a += 2 * math.pi
        while a >= 2 * math.pi:
            a -= 2 * math.pi
        return a

    s = _normalize(start_angle)
    m = _normalize(mid_angle)
    e = _normalize(end_angle)

    # Check if going CCW from start passes through mid before reaching end
    def _ccw_between(start: float, mid: float, end: float) -> bool:
        if start <= end:
            return start <= mid <= end
        # Wraps around 0
        return mid >= start or mid <= end

    if _ccw_between(s, m, e):
        # CCW direction
        sweep = e - s
        if sweep <= 0:
            sweep += 2 * math.pi
    else:
        # CW direction
        sweep = e - s
        if sweep >= 0:
            sweep -= 2 * math.pi

    return math.degrees(sweep)


def arc_to_polyline(
    sx: float,
    sy: float,
    mx: float,
    my: float,
    ex: float,
    ey: float,
    num_points: int = 64,
) -> list[tuple[float, float]]:
    """Linearize an arc (defined by 3 points) into a polyline.

    Returns a list of (x, y) coordinate pairs approximating the arc.
    """
    cx, cy, radius = arc_center_from_three_points(sx, sy, mx, my, ex, ey)
    sweep_deg = arc_sweep_angle(sx, sy, mx, my, ex, ey, cx, cy)
    sweep_rad = math.radians(sweep_deg)

    start_angle = math.atan2(sy - cy, sx - cx)

    points: list[tuple[float, float]] = []
    for i in range(num_points + 1):
        t = i / num_points
        angle = start_angle + t * sweep_rad
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        points.append((x, y))

    return points


def trace_arc_geometry(arc: PcbArc) -> tuple[LineString, Polygon]:
    """Return (centerline, copper corridor) for a curved trace arc."""
    points = arc_to_polyline(arc.start_x, arc.start_y, arc.mid_x, arc.mid_y, arc.end_x, arc.end_y)
    centerline = LineString(points)
    corridor = centerline.buffer(arc.width / 2, cap_style="flat")
    return centerline, corridor


# ---------------------------------------------------------------------------
# Via geometry
# ---------------------------------------------------------------------------


def via_geometry(via: PcbVia) -> tuple[Polygon, Polygon]:
    """Return (copper annular ring, drill hole) as circle polygons."""
    copper = Point(via.x, via.y).buffer(via.diameter / 2, quad_segs=VIA_DRILL_QUAD_SEGS)
    drill = Point(via.x, via.y).buffer(via.drill.diameter / 2, quad_segs=VIA_DRILL_QUAD_SEGS)
    return copper, drill


# ---------------------------------------------------------------------------
# Polygon geometry
# ---------------------------------------------------------------------------


def polygon_geometry(poly: PcbPolygon) -> Polygon | None:
    """Convert polygon geometry to a Shapely Polygon, or None if degenerate."""
    if len(poly.points) < 3:
        return None
    holes = [h for h in poly.holes if len(h) >= 3]
    geometry = Polygon(poly.points, holes=holes or None)
    normalized = normalize_geometry(geometry)
    if not normalized.is_empty and isinstance(normalized, Polygon):
        return normalized
    return geometry


def closed_path_geometry(path: PcbClosedPath) -> Polygon | None:
    """Convert a closed PCB path to a Shapely Polygon, or None if degenerate."""
    boundary = _closed_path_points(path)
    if len(boundary) < 3:
        return None
    holes = [
        hole_points for hole in path.holes if len(hole_points := _closed_path_points(hole)) >= 3
    ]
    geometry = Polygon(boundary, holes=holes or None)
    normalized = normalize_geometry(geometry)
    if not normalized.is_empty and isinstance(normalized, Polygon):
        return normalized
    return geometry


def _closed_path_points(path: PcbClosedPath) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for segment in path.segments:
        if not points:
            points.append((segment.start_x, segment.start_y))
        if segment.kind == PcbPathSegmentKind.ARC:
            arc_points = arc_to_polyline(
                segment.start_x,
                segment.start_y,
                segment.mid_x,
                segment.mid_y,
                segment.end_x,
                segment.end_y,
                num_points=24,
            )
            points.extend(arc_points[1:])
        else:
            points.append((segment.end_x, segment.end_y))
    if points and points[-1] == points[0]:
        points.pop()
    return points


# ---------------------------------------------------------------------------
# Board outline
# ---------------------------------------------------------------------------


def board_outline_polygon(profile: PcbBoardProfile) -> Polygon | MultiPolygon | None:
    """Assemble board outline from edge-cut lines and arcs into a polygon.

    Linearizes arcs, collects all segments, and uses shapely.ops.polygonize
    to form a closed polygon. Returns a MultiPolygon for panelized or
    multi-outline boards so no material is dropped, or None if the outline
    cannot be closed.
    """
    outline_segments: list[LineString] = []
    cutout_segments: list[LineString] = []
    solids: list[Polygon] = []
    cutouts: list[Polygon] = []

    for item in profile.elements:
        if isinstance(item.data, PcbLine):
            ln = item.data
            target = cutout_segments if item.is_cutout else outline_segments
            target.append(LineString([(ln.start_x, ln.start_y), (ln.end_x, ln.end_y)]))

        elif isinstance(item.data, PcbArc):
            arc = item.data
            points = arc_to_polyline(
                arc.start_x,
                arc.start_y,
                arc.mid_x,
                arc.mid_y,
                arc.end_x,
                arc.end_y,
                num_points=32,
            )
            if len(points) >= 2:
                # Snap endpoints to the exact arc start/end to avoid precision gaps
                points[0] = (arc.start_x, arc.start_y)
                points[-1] = (arc.end_x, arc.end_y)
                target = cutout_segments if item.is_cutout else outline_segments
                target.append(LineString(points))

        elif isinstance(item.data, PcbCircle):
            circle = item.data
            ring = Point(circle.cx, circle.cy).buffer(circle.radius, quad_segs=32)
            if item.is_cutout:
                cutouts.append(ring)
            else:
                solids.append(ring)

        else:
            polygon = polygon_geometry(item.data)
            if polygon is None:
                continue
            if item.is_cutout:
                cutouts.append(polygon)
            else:
                solids.append(polygon)

    if outline_segments:
        outline_polygon = robust_polygonize(outline_segments)
        if outline_polygon is not None:
            solids.append(outline_polygon)
    if cutout_segments:
        cutout_polygon = robust_polygonize(cutout_segments)
        if cutout_polygon is not None:
            cutouts.append(cutout_polygon)
    if not solids:
        return None

    material = unary_union(solids)
    if cutouts:
        material = material.difference(unary_union(cutouts))
    normalized = normalize_geometry(material)
    if isinstance(normalized, Polygon):
        return normalized
    if isinstance(normalized, MultiPolygon) and normalized.geoms:
        return normalized
    return None


# ---------------------------------------------------------------------------
# Footprint helpers
# ---------------------------------------------------------------------------


def footprint_bbox_polygon(fp: PcbFootprint) -> Polygon | None:
    """Build a bounding-box polygon for a footprint."""
    if fp.bbox:
        min_x, min_y, max_x, max_y = fp.bbox
        return _box(min_x, min_y, max_x, max_y)
    return None


def pad_side(layers: tuple[str, ...]) -> str:
    """Determine which board side a pad is accessible from."""
    layer_names = [str(layer) for layer in layers]
    has_wildcard = any("*" in ly for ly in layer_names)
    has_front = any(ly in _FRONT_LAYERS for ly in layer_names)
    has_back = any(ly in _BACK_LAYERS for ly in layer_names)

    if has_wildcard or (has_front and has_back):
        return "through"
    if has_back:
        return "back"
    return "front"


def footprint_side(fp: PcbFootprint) -> str:
    """Determine which board side a footprint is on."""
    if fp.layer.name in _BACK_LAYERS or fp.layer.side == "back":
        return "back"
    return "front"
