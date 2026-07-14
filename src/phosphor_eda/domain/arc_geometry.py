"""Pure arc math on plain coordinates.

Circumcircle, sweep, linearization, and bounds for a three-point arc. Depends
only on the standard library so the base domain layer (bounding boxes) and the
higher geometry/render/SQL layers can all share one implementation without a
circular import.
"""

from __future__ import annotations

import math


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


def _angle_within_sweep(angle: float, start_angle: float, sweep_rad: float) -> bool:
    """Whether ``angle`` lies on the arc swept from ``start_angle`` by ``sweep_rad``."""
    two_pi = 2 * math.pi
    if sweep_rad >= 0:
        return (angle - start_angle) % two_pi <= sweep_rad
    return (start_angle - angle) % two_pi <= -sweep_rad


def arc_bounds(
    sx: float, sy: float, mx: float, my: float, ex: float, ey: float
) -> tuple[float, float, float, float]:
    """Axis-aligned bounds of an arc's centerline (through the 3 defining points).

    Beyond the endpoints, an arc reaches its extreme x/y where it crosses an
    axis direction from the center. Those crossings fall outside the hull of the
    three defining points once the sweep exceeds 180 degrees, so bounding by the
    defining points alone under-bounds the arc. Returns (min_x, min_y, max_x, max_y).
    """
    xs = [sx, mx, ex]
    ys = [sy, my, ey]
    # Collinear input has no meaningful curvature; the three points bound it.
    if abs((mx - sx) * (ey - sy) - (my - sy) * (ex - sx)) < 1e-12:
        return min(xs), min(ys), max(xs), max(ys)

    cx, cy, radius = arc_center_from_three_points(sx, sy, mx, my, ex, ey)
    start_angle = math.atan2(sy - cy, sx - cx)
    sweep_rad = math.radians(arc_sweep_angle(sx, sy, mx, my, ex, ey, cx, cy))
    for axis_angle in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
        if _angle_within_sweep(axis_angle, start_angle, sweep_rad):
            xs.append(cx + radius * math.cos(axis_angle))
            ys.append(cy + radius * math.sin(axis_angle))
    return min(xs), min(ys), max(xs), max(ys)
