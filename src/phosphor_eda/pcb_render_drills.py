"""Shared drill geometry helpers for PCB rendering."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from shapely import LineString, Point
from shapely.affinity import rotate

from phosphor_eda.sql.geometry import VIA_DRILL_QUAD_SEGS

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb import PcbPad


def pad_drill_dimensions(pad: PcbPad) -> tuple[float, float]:
    """Return the drill aperture dimensions for a pad in millimetres."""
    width = pad.drill_width if pad.drill_width > 0.0 else pad.drill
    height = pad.drill_height if pad.drill_height > 0.0 else pad.drill
    return width, height


def pad_drill_geometry(pad: PcbPad) -> BaseGeometry | None:
    """Return the subtractive drill aperture for a pad."""
    width, height = pad_drill_dimensions(pad)
    if width <= 0.0 or height <= 0.0:
        return None
    if pad.drill_shape != "oval" or math.isclose(width, height):
        return Point(pad.x, pad.y).buffer(width / 2.0, quad_segs=VIA_DRILL_QUAD_SEGS)

    radius = min(width, height) / 2.0
    if width > height:
        half_span = (width - height) / 2.0
        line = LineString(((pad.x - half_span, pad.y), (pad.x + half_span, pad.y)))
    else:
        half_span = (height - width) / 2.0
        line = LineString(((pad.x, pad.y - half_span), (pad.x, pad.y + half_span)))
    geometry = line.buffer(radius, quad_segs=VIA_DRILL_QUAD_SEGS)
    if not math.isclose(pad.rotation % 360.0, 0.0):
        geometry = rotate(geometry, pad.rotation, origin=(pad.x, pad.y))
    return geometry
