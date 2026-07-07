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

from phosphor_eda.domain.pcb import (
    PcbArc,
    PcbCircle,
    PcbClosedPath,
    PcbLine,
    PcbPathSegmentKind,
    PcbPolygon,
)
from phosphor_eda.geometry.shapely_ops import normalize_geometry, robust_polygonize

if TYPE_CHECKING:
    from shapely.coords import CoordinateSequence
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.domain.pcb import (
        PcbBoardProfile,
        PcbFootprint,
        PcbPad,
        PcbVia,
    )

PAD_CURVE_QUAD_SEGS = 12
PAD_ROUNDRECT_QUAD_SEGS = 8
VIA_DRILL_QUAD_SEGS = 8

# Minimum painted width for a zero/None-width stroke. A hairline outline still
# occupies physical area on the board, so both the SVG stroke and the SQL
# geometry floor to this width rather than vanishing (SVG) or filling the whole
# enclosed region (SQL). Lives here so geometry, the SQL loader, and the
# renderer all share one value without the renderer leaking into geometry.
MIN_STROKE_WIDTH_MM = 0.05


def _box(min_x: float, min_y: float, max_x: float, max_y: float) -> Polygon:
    """Create a rectangular Polygon from bounds."""
    return Polygon([(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)])


# ---------------------------------------------------------------------------
# Pad geometry
# ---------------------------------------------------------------------------


def _rotate_point(x: float, y: float, cx: float, cy: float, degrees: float) -> tuple[float, float]:
    """Rotate (x, y) about (cx, cy) by ``-degrees`` (pad/shapely convention)."""
    if degrees == 0.0:
        return x, y
    angle = math.radians(-degrees)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = x - cx
    dy = y - cy
    return cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a


def circle_path_d(cx: float, cy: float, radius: float, *, clockwise: bool = False) -> str:
    """Exact SVG path for a full circle (two semicircular arcs).

    ``clockwise`` flips the winding (arc sweep flag). Annular rings pair a
    default-wound outer circle with a clockwise inner circle so the hole
    survives ``fill-rule="nonzero"``.
    """
    if radius <= 0.0:
        return ""
    sweep = 1 if clockwise else 0
    return (
        f"M {cx + radius:.3f} {cy:.3f} "
        f"A {radius:.3f} {radius:.3f} 0 1 {sweep} {cx - radius:.3f} {cy:.3f} "
        f"A {radius:.3f} {radius:.3f} 0 1 {sweep} {cx + radius:.3f} {cy:.3f} Z"
    )


def rect_path_d(cx: float, cy: float, w: float, h: float, rotation: float = 0.0) -> str:
    """Exact SVG path for an (optionally rotated) rectangle centered at (cx, cy)."""
    if w <= 0.0 or h <= 0.0:
        return ""
    hw = w / 2.0
    hh = h / 2.0
    corners = (
        (cx - hw, cy - hh),
        (cx + hw, cy - hh),
        (cx + hw, cy + hh),
        (cx - hw, cy + hh),
    )
    rotated = [_rotate_point(x, y, cx, cy, rotation) for x, y in corners]
    commands = [f"M {rotated[0][0]:.3f} {rotated[0][1]:.3f}"]
    commands.extend(f"L {x:.3f} {y:.3f}" for x, y in rotated[1:])
    commands.append("Z")
    return " ".join(commands)


# Corner-chamfer fraction for a regular octagon inscribed in its bounding box:
# the axis vertex sits at (half-extent * OCTAGON_CHAMFER) from the box corner.
OCTAGON_CHAMFER = math.sqrt(2.0) - 1.0


def _octagon_corners(cx: float, cy: float, w: float, h: float) -> tuple[tuple[float, float], ...]:
    """Regular octagon vertices inscribed in the ``w``x``h`` box about (cx, cy)."""
    hw = w / 2.0
    hh = h / 2.0
    kx = hw * OCTAGON_CHAMFER
    ky = hh * OCTAGON_CHAMFER
    return (
        (cx + hw, cy - ky),
        (cx + kx, cy - hh),
        (cx - kx, cy - hh),
        (cx - hw, cy - ky),
        (cx - hw, cy + ky),
        (cx - kx, cy + hh),
        (cx + kx, cy + hh),
        (cx + hw, cy + ky),
    )


def _diamond_corners(cx: float, cy: float, w: float, h: float) -> tuple[tuple[float, float], ...]:
    """Rhombus vertices at the ``w``x``h`` box edge midpoints about (cx, cy)."""
    hw = w / 2.0
    hh = h / 2.0
    return ((cx + hw, cy), (cx, cy - hh), (cx - hw, cy), (cx, cy + hh))


def _rotated_polygon_path_d(
    cx: float, cy: float, corners: tuple[tuple[float, float], ...], rotation: float
) -> str:
    rotated = [_rotate_point(x, y, cx, cy, rotation) for x, y in corners]
    commands = [f"M {rotated[0][0]:.4f} {rotated[0][1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in rotated[1:])
    commands.append("Z")
    return " ".join(commands)


def octagon_path_d(cx: float, cy: float, w: float, h: float, rotation: float = 0.0) -> str:
    """Exact SVG path for a regular octagon inscribed in the ``w``x``h`` box."""
    if w <= 0.0 or h <= 0.0:
        return ""
    return _rotated_polygon_path_d(cx, cy, _octagon_corners(cx, cy, w, h), rotation)


def diamond_path_d(cx: float, cy: float, w: float, h: float, rotation: float = 0.0) -> str:
    """Exact SVG path for a diamond (rhombus) inscribed in the ``w``x``h`` box."""
    if w <= 0.0 or h <= 0.0:
        return ""
    return _rotated_polygon_path_d(cx, cy, _diamond_corners(cx, cy, w, h), rotation)


def oval_path_d(cx: float, cy: float, w: float, h: float, rotation: float = 0.0) -> str:
    """Exact SVG path for a capsule/stadium (two semicircle arcs + two lines)."""
    if w <= 0.0 or h <= 0.0:
        return ""
    if math.isclose(w, h):
        return circle_path_d(cx, cy, w / 2.0)
    if w > h:
        radius = h / 2.0
        half = (w - h) / 2.0
        # Top edge then right cap then bottom edge then left cap.
        p_tl = (cx - half, cy - radius)
        p_tr = (cx + half, cy - radius)
        p_br = (cx + half, cy + radius)
        p_bl = (cx - half, cy + radius)
        pts = [_rotate_point(x, y, cx, cy, rotation) for x, y in (p_tl, p_tr, p_br, p_bl)]
        return (
            f"M {pts[0][0]:.3f} {pts[0][1]:.3f} "
            f"L {pts[1][0]:.3f} {pts[1][1]:.3f} "
            f"A {radius:.3f} {radius:.3f} 0 0 0 {pts[2][0]:.3f} {pts[2][1]:.3f} "
            f"L {pts[3][0]:.3f} {pts[3][1]:.3f} "
            f"A {radius:.3f} {radius:.3f} 0 0 0 {pts[0][0]:.3f} {pts[0][1]:.3f} Z"
        )
    radius = w / 2.0
    half = (h - w) / 2.0
    # Left edge then bottom cap then right edge then top cap.
    p_tl = (cx - radius, cy - half)
    p_bl = (cx - radius, cy + half)
    p_br = (cx + radius, cy + half)
    p_tr = (cx + radius, cy - half)
    pts = [_rotate_point(x, y, cx, cy, rotation) for x, y in (p_tl, p_bl, p_br, p_tr)]
    return (
        f"M {pts[0][0]:.3f} {pts[0][1]:.3f} "
        f"L {pts[1][0]:.3f} {pts[1][1]:.3f} "
        f"A {radius:.3f} {radius:.3f} 0 0 0 {pts[2][0]:.3f} {pts[2][1]:.3f} "
        f"L {pts[3][0]:.3f} {pts[3][1]:.3f} "
        f"A {radius:.3f} {radius:.3f} 0 0 0 {pts[0][0]:.3f} {pts[0][1]:.3f} Z"
    )


def roundrect_path_d(
    cx: float, cy: float, w: float, h: float, corner_radius: float, rotation: float = 0.0
) -> str:
    """Exact SVG path for a rounded rectangle (four lines + four quarter arcs)."""
    if w <= 0.0 or h <= 0.0:
        return ""
    radius = max(min(corner_radius, w / 2.0, h / 2.0), 0.0)
    if radius <= 0.0:
        return rect_path_d(cx, cy, w, h, rotation)
    hw = w / 2.0
    hh = h / 2.0
    # Walk clockwise (in SVG's y-down space) from the top edge, inserting a
    # quarter arc at each corner. Sweep flag 1 keeps arcs convex outward.
    raw = (
        ("M", cx - hw + radius, cy - hh),
        ("L", cx + hw - radius, cy - hh),
        ("A", cx + hw, cy - hh + radius),
        ("L", cx + hw, cy + hh - radius),
        ("A", cx + hw - radius, cy + hh),
        ("L", cx - hw + radius, cy + hh),
        ("A", cx - hw, cy + hh - radius),
        ("L", cx - hw, cy - hh + radius),
        ("A", cx - hw + radius, cy - hh),
    )
    commands: list[str] = []
    for op, x, y in raw:
        rx, ry = _rotate_point(x, y, cx, cy, rotation)
        if op == "A":
            commands.append(f"A {radius:.3f} {radius:.3f} 0 0 1 {rx:.3f} {ry:.3f}")
        else:
            commands.append(f"{op} {rx:.3f} {ry:.3f}")
    commands.append("Z")
    return " ".join(commands)


def pad_path_d(pad: PcbPad, *, width: float | None = None, height: float | None = None) -> str:
    """Native SVG path for a pad's copper shape (no shapely).

    ``width``/``height`` override the pad dimensions (used for solder-mask
    openings that expand the aperture). ``custom`` pads concatenate their
    sub-shape subpaths; the caller paints them with ``fill-rule="nonzero"``.
    Overridden custom pads fall back to a shapely outline dilated by the
    override margin (see ``_custom_pad_margin``).
    """
    w = pad.width if width is None else width
    h = pad.height if height is None else height
    cx, cy = pad.x, pad.y
    if pad.shape == "custom" and pad.custom_shapes:
        if _custom_pad_margin(pad, w, h) == 0.0:
            return custom_pad_path_d(pad)
        return _polygon_to_path_d(pad_polygon(pad, width=w, height=h))
    if pad.shape == "circle":
        return circle_path_d(cx, cy, w / 2.0)
    if pad.shape == "oval":
        return oval_path_d(cx, cy, w, h, pad.rotation)
    if pad.shape == "roundrect":
        corner_radius = min(w, h) * pad.roundrect_rratio / 2.0
        return roundrect_path_d(cx, cy, w, h, corner_radius, pad.rotation)
    if pad.shape == "octagon":
        return octagon_path_d(cx, cy, w, h, pad.rotation)
    if pad.shape == "diamond":
        return diamond_path_d(cx, cy, w, h, pad.rotation)
    return rect_path_d(cx, cy, w, h, pad.rotation)


def custom_pad_path_d(pad: PcbPad) -> str:
    """Concatenate a custom pad's sub-shape subpaths into one ``d`` string."""
    subpaths = [d for shape in pad.custom_shapes if (d := _custom_pad_shape_path_d(shape))]
    return " ".join(subpaths)


def _custom_pad_shape_path_d(shape: PcbLine | PcbArc | PcbCircle | PcbPolygon) -> str:
    if isinstance(shape, PcbLine):
        if shape.width <= 0.0:
            return ""
        return _stroke_centerline_to_filled_path_d(
            ((shape.start_x, shape.start_y), (shape.end_x, shape.end_y)), shape.width
        )
    if isinstance(shape, PcbArc):
        if shape.width <= 0.0:
            return ""
        points = arc_to_polyline(
            shape.start_x,
            shape.start_y,
            shape.mid_x,
            shape.mid_y,
            shape.end_x,
            shape.end_y,
            num_points=32,
        )
        return _stroke_centerline_to_filled_path_d(tuple(points), shape.width)
    if isinstance(shape, PcbCircle):
        if shape.fill or shape.width <= 0.0:
            return circle_path_d(shape.cx, shape.cy, shape.radius)
        # radius is the stroke centerline: the annulus spans radius +/- width/2.
        outer_radius = shape.radius + shape.width / 2.0
        inner_radius = max(shape.radius - shape.width / 2.0, 0.0)
        outer = circle_path_d(shape.cx, shape.cy, outer_radius)
        if inner_radius <= 0.0:
            return outer
        return f"{outer} {circle_path_d(shape.cx, shape.cy, inner_radius, clockwise=True)}"
    return _polygon_to_path_d(polygon_geometry(shape))


def _stroke_centerline_to_filled_path_d(
    points: tuple[tuple[float, float], ...], width: float
) -> str:
    """Buffer a centerline into a filled-outline subpath (custom-pad sub-shapes).

    Custom-pad line/arc sub-shapes carry width and must contribute filled area
    to the union; a stroked centerline can't because the surrounding ``<path>``
    is a single fill element. Keep shapely here -- this is genuine polygonal
    union input, not the per-primitive curve bloat plan 11 removes.
    """
    line = LineString(points)
    return _polygon_to_path_d(line.buffer(width / 2.0, cap_style="round"))


def _polygon_to_path_d(geometry: BaseGeometry) -> str:
    if geometry.is_empty:
        return ""
    polygons: list[Polygon] = []
    if isinstance(geometry, Polygon):
        polygons = [geometry]
    elif isinstance(geometry, MultiPolygon):
        polygons = list(geometry.geoms)
    parts: list[str] = []
    for polygon in polygons:
        parts.append(_ring_to_path_d(polygon.exterior.coords))
        parts.extend(_ring_to_path_d(interior.coords) for interior in polygon.interiors)
    return " ".join(part for part in parts if part)


def _ring_to_path_d(coords: CoordinateSequence) -> str:
    points = [(float(x), float(y)) for x, y, *_ in coords]
    if len(points) < 3:
        return ""
    if points[0] == points[-1]:
        points = points[:-1]
    commands = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
    commands.extend(f"L {x:.3f} {y:.3f}" for x, y in points[1:])
    commands.append("Z")
    return " ".join(commands)


def _custom_pad_margin(pad: PcbPad, width: float, height: float) -> float:
    """Uniform outline offset equivalent to a width/height override.

    Custom pads are unions of absolute-coordinate sub-shapes with no
    width/height parameterization, so aperture overrides (e.g. solder-mask
    expansion) are applied as an offset of the copper outline instead. The
    per-axis half-deltas are averaged; mask-expansion callers grow both axes
    by the same amount, so the average is exact for them.
    """
    return ((width - pad.width) + (height - pad.height)) / 4.0


def pad_polygon(
    pad: PcbPad, *, width: float | None = None, height: float | None = None
) -> BaseGeometry:
    """Construct the actual copper polygon for a pad in board coordinates.

    ``width``/``height`` override the pad dimensions (used for solder-mask
    openings that expand the aperture). Custom pads apply the override as a
    uniform dilation of the sub-shape union (see ``_custom_pad_margin``).
    """
    cx, cy = pad.x, pad.y
    w = pad.width if width is None else width
    h = pad.height if height is None else height

    if pad.shape == "custom" and pad.custom_shapes:
        geometries = [
            geometry
            for shape in pad.custom_shapes
            if not (geometry := _custom_pad_shape_geometry(shape)).is_empty
        ]
        if not geometries:
            return GeometryCollection()
        union = normalize_geometry(unary_union(geometries))
        margin = _custom_pad_margin(pad, w, h)
        if margin == 0.0 or union.is_empty:
            return union
        return normalize_geometry(union.buffer(margin, quad_segs=PAD_CURVE_QUAD_SEGS))

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

    if pad.shape in ("octagon", "diamond"):
        corners = (
            _octagon_corners(cx, cy, w, h)
            if pad.shape == "octagon"
            else _diamond_corners(cx, cy, w, h)
        )
        geom: BaseGeometry = Polygon(corners)
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
        if shape.fill or shape.width <= 0.0:
            return Point(shape.cx, shape.cy).buffer(shape.radius, quad_segs=PAD_CURVE_QUAD_SEGS)
        # radius is the stroke centerline: the annulus spans radius +/- width/2.
        outer = Point(shape.cx, shape.cy).buffer(
            shape.radius + shape.width / 2.0, quad_segs=PAD_CURVE_QUAD_SEGS
        )
        inner_radius = max(shape.radius - shape.width / 2.0, 0.0)
        if inner_radius <= 0.0:
            return outer
        return outer.difference(Point(shape.cx, shape.cy).buffer(inner_radius))
    return polygon_shape_geometry(shape)


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


def polygon_geometry(poly: PcbPolygon) -> BaseGeometry:
    """Convert polygon geometry to normalized Shapely geometry.

    Returns an empty ``GeometryCollection`` for degenerate or non-repairable
    input — never raw, possibly-invalid geometry (invalid WKB corrupts spatial
    queries and SVG serialization alike).
    """
    if len(poly.points) < 3:
        return GeometryCollection()
    holes = [h for h in poly.holes if len(h) >= 3]
    return normalize_geometry(Polygon(poly.points, holes=holes or None))


def polygon_shape_geometry(poly: PcbPolygon) -> BaseGeometry:
    """Return the physical filled area for a polygon payload.

    ``polygon_geometry`` intentionally returns the enclosed region for consumers
    such as board-profile assembly.  This helper applies PCB paint semantics:
    unfilled polygons describe a stroked closed outline.
    """
    geometry = polygon_geometry(poly)
    if poly.fill or geometry.is_empty:
        return geometry
    # Stroke the drawn rings rather than the normalized region's boundary: a
    # self-intersecting outline normalizes to a GeometryCollection (whose
    # boundary is undefined), but the painted stroke still follows the rings
    # as authored. A zero-width outline still paints a hairline ring (matching
    # the SVG hairline), not the full enclosed region.
    stroke_width = max(poly.width, MIN_STROKE_WIDTH_MM)
    outlines = [
        LineString([*ring, ring[0]]).buffer(
            stroke_width / 2.0, cap_style="round", join_style="round"
        )
        for ring in (poly.points, *poly.holes)
        if len(ring) >= 2
    ]
    return normalize_geometry(unary_union(outlines))


def closed_path_geometry(path: PcbClosedPath) -> Polygon | MultiPolygon | None:
    """Convert a closed PCB path to normalized Shapely geometry.

    Returns ``None`` for degenerate paths. A self-intersecting boundary
    normalizes to its valid repair (possibly a ``MultiPolygon``) — never raw
    invalid geometry, which corrupts WKB and SVG serialization downstream.
    """
    boundary = _closed_path_points(path)
    if len(boundary) < 3:
        return None
    holes = [
        hole_points for hole in path.holes if len(hole_points := _closed_path_points(hole)) >= 3
    ]
    normalized = normalize_geometry(Polygon(boundary, holes=holes or None))
    if normalized.is_empty:
        return None
    if isinstance(normalized, Polygon | MultiPolygon):
        return normalized
    # A repair can yield a collection with linework alongside the area; keep
    # the polygonal parts only.
    parts = [
        geom
        for geom in getattr(normalized, "geoms", ())
        if isinstance(geom, Polygon | MultiPolygon) and not geom.is_empty
    ]
    if not parts:
        return None
    merged = normalize_geometry(unary_union(parts))
    if merged.is_empty or not isinstance(merged, Polygon | MultiPolygon):
        return None
    return merged


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
    solids: list[Polygon | MultiPolygon] = []
    cutouts: list[Polygon | MultiPolygon] = []

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

        elif isinstance(item.data, PcbClosedPath):
            polygon = closed_path_geometry(item.data)
            if polygon is None or polygon.is_empty:
                continue
            if item.is_cutout:
                cutouts.append(polygon)
            else:
                solids.append(polygon)

        else:
            # Repairing a self-intersecting outline can yield a MultiPolygon;
            # keep all pieces so no board material or cutout is dropped.
            polygon = polygon_geometry(item.data)
            if polygon.is_empty or not isinstance(polygon, (Polygon, MultiPolygon)):
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


def footprint_side(fp: PcbFootprint) -> str:
    """Determine which board side a footprint is on."""
    return "back" if fp.layer.side == "back" else "front"
