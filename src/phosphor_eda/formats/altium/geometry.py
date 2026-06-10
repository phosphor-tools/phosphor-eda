"""Arc geometry helpers for the Altium PCB parser.

A single arc-sampling kernel feeds the parser's three arc consumers, which
differ only in their segment density and output form:

- ``linearize_arc_vertices`` (ShapeBasedRegions6 outlines): 64 segments per
  circle, integer Altium units, no Y flip — the caller converts to mm.
- ``_arc_ring_points`` (keepout ring synthesis): 96 segments per circle,
  float mm with Y negated.
- ``_arc_to_three_point`` (track/arc primitives): start / mid / end only.

The full-circle epsilon (an arc whose endpoints differ by ≥ ``359.999``°
is treated as a closed circle) is documented here, in one place.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.formats.altium.pcb_records import ExtendedVertex

# An arc whose start/end angles differ by at least this many degrees is a
# full circle.  Shared by every arc consumer so the threshold can't drift.
FULL_CIRCLE_EPSILON_DEG = 359.999

# Segment density per full circle for the two linearizers.
SHAPE_REGION_SEGMENTS_PER_CIRCLE = 64
KEEPOUT_RING_SEGMENTS_PER_CIRCLE = 96


def is_full_circle_arc(start_deg: float, end_deg: float) -> bool:
    """Return whether an arc's endpoints describe a closed circle."""
    return abs(end_deg - start_deg) >= FULL_CIRCLE_EPSILON_DEG


def arc_sweep_degrees(start_deg: float, end_deg: float) -> float:
    """CCW sweep in degrees, signed for full circles, normalized otherwise."""
    sweep = end_deg - start_deg
    if is_full_circle_arc(start_deg, end_deg):
        return 360.0 if sweep >= 0 else -360.0
    if sweep < 0:
        sweep += 360.0
    return sweep


def point_on_arc(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
    """Point on a circle at *angle_deg* (CCW from +X)."""
    rad = math.radians(angle_deg)
    return (cx + radius * math.cos(rad), cy + radius * math.sin(rad))


def sample_arc(
    cx: float,
    cy: float,
    radius: float,
    start_deg: float,
    sweep_deg: float,
    segments: int,
) -> list[tuple[float, float]]:
    """Sample *segments* points along an arc, start inclusive, end exclusive.

    Points run CCW from ``start_deg`` over ``sweep_deg`` degrees.  Returns
    floats with no Y flip; callers round and/or negate as needed.  Both
    linearizers reduce to this loop — only the segment count and the
    post-processing differ.
    """
    return [
        point_on_arc(cx, cy, radius, start_deg + sweep_deg * i / segments) for i in range(segments)
    ]


def linearize_arc_vertices(
    vertices: list[ExtendedVertex],
    segments_per_circle: int = SHAPE_REGION_SEGMENTS_PER_CIRCLE,
) -> list[tuple[int, int]]:
    """Convert extended vertices to a polyline, interpolating arc edges.

    When a vertex has ``is_round=True``, the edge from that vertex to the
    next is an arc defined by center/radius/angles. This function replaces
    each arc edge with a sequence of line segments approximating the curve.

    Coordinates remain in Altium internal units (0.1 µinch). The caller
    handles mm conversion.
    """
    if not vertices:
        return []

    points: list[tuple[int, int]] = []

    for v in vertices:
        if not v.is_round:
            points.append((v.x, v.y))
            continue

        # Arc edge: interpolate from start_angle to end_angle (always CCW).
        sweep = v.end_angle - v.start_angle
        if sweep <= 0:
            sweep += 360.0
        n_segs = max(2, round(segments_per_circle * sweep / 360.0))
        points.extend(
            (round(px), round(py))
            for px, py in sample_arc(v.center_x, v.center_y, v.radius, v.start_angle, sweep, n_segs)
        )

    return points
