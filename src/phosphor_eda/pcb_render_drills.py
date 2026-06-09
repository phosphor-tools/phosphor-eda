"""Shared drill geometry helpers for PCB rendering."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from shapely import LineString, Point
from shapely.affinity import rotate

from phosphor_eda.sql.geometry import VIA_DRILL_QUAD_SEGS

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb import PcbDrill


def drill_dimensions(drill: PcbDrill) -> tuple[float, float]:
    """Return the drill aperture dimensions in millimetres."""
    width = drill.width if drill.width > 0.0 else drill.diameter
    height = drill.height if drill.height > 0.0 else drill.diameter
    return width, height


def drill_geometry(drill: PcbDrill) -> BaseGeometry | None:
    """Return the subtractive drill aperture."""
    width, height = drill_dimensions(drill)
    if width <= 0.0 or height <= 0.0:
        return None
    if drill.shape != "slot" or math.isclose(width, height):
        return Point(drill.x, drill.y).buffer(width / 2.0, quad_segs=VIA_DRILL_QUAD_SEGS)

    radius = min(width, height) / 2.0
    if width > height:
        half_span = (width - height) / 2.0
        line = LineString(((drill.x - half_span, drill.y), (drill.x + half_span, drill.y)))
    else:
        half_span = (height - width) / 2.0
        line = LineString(((drill.x, drill.y - half_span), (drill.x, drill.y + half_span)))
    geometry = line.buffer(radius, quad_segs=VIA_DRILL_QUAD_SEGS)
    if not math.isclose(drill.rotation % 360.0, 0.0):
        geometry = rotate(geometry, drill.rotation, origin=(drill.x, drill.y))
    return geometry
