"""Keepout geometry synthesis for the Altium PCB parser.

Altium stores keepouts as flagged track/arc/fill primitives. These helpers
turn each flagged primitive into a closed-path ``PcbKeepout`` with its
permission rules, synthesizing ring/rectangle outlines as needed.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    PcbClosedPath,
    PcbKeepout,
    PcbKeepoutPermission,
    PcbKeepoutRules,
    PcbLayer,
)
from phosphor_eda.formats.altium.geometry import (
    KEEPOUT_RING_SEGMENTS_PER_CIRCLE,
    arc_sweep_degrees,
    is_full_circle_arc,
    sample_arc,
)
from phosphor_eda.formats.altium.pcb_primitives import keepout_metadata

if TYPE_CHECKING:
    from phosphor_eda.formats.altium.pcb_records import ArcRecord, TrackRecord


def keepout_from_arc(
    *,
    layer: PcbLayer,
    layer_num: int,
    arc: ArcRecord,
    cx: float,
    cy_orig: float,
    radius: float,
    width: float,
    index: int,
    component_index: int | None,
) -> PcbKeepout:
    outer_radius = radius + width / 2.0
    inner_radius = max(radius - width / 2.0, 0.0)
    boundary = _arc_ring_points(
        cx=cx,
        cy_orig=cy_orig,
        radius=outer_radius,
        start_deg=arc.start_angle,
        end_deg=arc.end_angle,
    )
    holes: list[list[tuple[float, float]]] = []
    if is_full_circle_arc(arc.start_angle, arc.end_angle) and inner_radius > 0:
        holes.append(
            list(
                reversed(
                    _arc_ring_points(
                        cx=cx,
                        cy_orig=cy_orig,
                        radius=inner_radius,
                        start_deg=arc.start_angle,
                        end_deg=arc.end_angle,
                    )
                )
            )
        )
    elif inner_radius > 0:
        inner = list(
            reversed(
                _arc_ring_points(
                    cx=cx,
                    cy_orig=cy_orig,
                    radius=inner_radius,
                    start_deg=arc.start_angle,
                    end_deg=arc.end_angle,
                )
            )
        )
        boundary = [*boundary, *inner]
    return PcbKeepout(
        id=f"keepout_arc:{layer_num}:{index}",
        boundary=PcbClosedPath.from_points(
            boundary,
            holes=tuple(PcbClosedPath.from_points(hole) for hole in holes),
        ),
        layers=(layer,),
        rules=altium_keepout_rules(arc.keepout_restrictions),
        metadata=keepout_metadata(
            native_type="ARC",
            native_kind="keepout",
            native_index=index,
            native_component_index=component_index,
            properties={"keepout_restrictions": str(arc.keepout_restrictions)},
        ),
    )


def keepout_from_line(
    *,
    layer: PcbLayer,
    track: TrackRecord,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: float,
    index: int,
    component_index: int | None,
) -> PcbKeepout:
    return PcbKeepout(
        id=f"keepout_track:{track.layer}:{index}",
        boundary=PcbClosedPath.from_points(_line_rect_points(x1, y1, x2, y2, width)),
        layers=(layer,),
        rules=altium_keepout_rules(track.keepout_restrictions),
        metadata=keepout_metadata(
            native_type="TRACK",
            native_kind="keepout",
            native_index=index,
            native_component_index=component_index,
            properties={"keepout_restrictions": str(track.keepout_restrictions)},
        ),
    )


def _line_rect_points(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: float,
) -> list[tuple[float, float]]:
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    half_width = max(width, 0.01) / 2.0
    if length <= 0.0:
        return [
            (x1 - half_width, y1 - half_width),
            (x1 + half_width, y1 - half_width),
            (x1 + half_width, y1 + half_width),
            (x1 - half_width, y1 + half_width),
        ]
    nx = -dy / length * half_width
    ny = dx / length * half_width
    return [
        (x1 + nx, y1 + ny),
        (x2 + nx, y2 + ny),
        (x2 - nx, y2 - ny),
        (x1 - nx, y1 - ny),
    ]


def _arc_ring_points(
    *,
    cx: float,
    cy_orig: float,
    radius: float,
    start_deg: float,
    end_deg: float,
) -> list[tuple[float, float]]:
    sweep = arc_sweep_degrees(start_deg, end_deg)
    segments = max(16, int(abs(sweep) / 360.0 * KEEPOUT_RING_SEGMENTS_PER_CIRCLE))
    return [(px, -py) for px, py in sample_arc(cx, cy_orig, radius, start_deg, sweep, segments)]


def altium_keepout_rules(mask: int) -> PcbKeepoutRules:
    if mask == 0:
        return PcbKeepoutRules(
            tracks=PcbKeepoutPermission.NOT_ALLOWED,
            vias=PcbKeepoutPermission.NOT_ALLOWED,
            pads=PcbKeepoutPermission.NOT_ALLOWED,
            copper_pours=PcbKeepoutPermission.NOT_ALLOWED,
            footprints=PcbKeepoutPermission.NOT_ALLOWED,
        )

    def restriction(bit: int) -> PcbKeepoutPermission:
        return PcbKeepoutPermission.NOT_ALLOWED if mask & bit else PcbKeepoutPermission.ALLOWED

    return PcbKeepoutRules(
        tracks=restriction(0x01),
        vias=restriction(0x02),
        pads=restriction(0x04),
        copper_pours=restriction(0x08),
        footprints=restriction(0x10),
    )
