"""KiCad PCB graphic primitives: payloads, strokes, text effects, bounds."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import sexpdata

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbCircle,
    PcbLine,
    PcbModel3D,
    PcbPolygon,
    PcbText,
    extend_shape_bounds,
)
from phosphor_eda.formats.kicad import pcb_common, sexp

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import PcbArtwork, PcbLayer, PcbPad
    from phosphor_eda.formats.kicad.sexp import SExpNode


def stroke_width(item: SExpNode, *, default: float = 0.1) -> float:
    width_node = sexp.find(item, "width")
    if width_node:
        return sexp.num(width_node, 1)
    stroke = sexp.find_path(item, "stroke", "width")
    return sexp.num(stroke, 1) if stroke else default


def fill_flag(item: SExpNode) -> bool:
    fill_node = sexp.find(item, "fill")
    return fill_node is not None and sexp.val(fill_node) == "solid"


# Layer-role → artwork-purpose, in priority order (first matching role wins).
_ARTWORK_PURPOSE_BY_ROLE: tuple[tuple[LayerRole, PcbArtworkPurpose], ...] = (
    (LayerRole.SILKSCREEN, PcbArtworkPurpose.SILKSCREEN),
    (LayerRole.COURTYARD, PcbArtworkPurpose.COURTYARD),
    (LayerRole.FABRICATION, PcbArtworkPurpose.FABRICATION),
    (LayerRole.ASSEMBLY, PcbArtworkPurpose.ASSEMBLY),
    (LayerRole.SOLDER_MASK, PcbArtworkPurpose.SOLDER_MASK),
    (LayerRole.SOLDER_PASTE, PcbArtworkPurpose.SOLDER_PASTE),
    (LayerRole.DIMENSION, PcbArtworkPurpose.DIMENSION),
    (LayerRole.MECHANICAL, PcbArtworkPurpose.MECHANICAL),
    (LayerRole.USER, PcbArtworkPurpose.USER),
    (LayerRole.COMMENT, PcbArtworkPurpose.USER),
)

# Footprint-text kinds mapped to their artwork purpose (empty kind falls through).
_TEXT_KIND_PURPOSES: dict[str, PcbArtworkPurpose] = {
    "reference": PcbArtworkPurpose.DESIGNATOR,
    "value": PcbArtworkPurpose.VALUE,
}


def artwork_purpose(
    layer: PcbLayer | None,
    *,
    native_type: str,
    text_kind: str = "",
) -> PcbArtworkPurpose:
    if native_type == "model":
        return PcbArtworkPurpose.COMPONENT_BODY
    if text_kind:
        return _TEXT_KIND_PURPOSES.get(text_kind, PcbArtworkPurpose.USER_TEXT)
    if layer is None:
        return PcbArtworkPurpose.UNKNOWN
    for role, purpose in _ARTWORK_PURPOSE_BY_ROLE:
        if layer.has_role(role):
            return purpose
    return PcbArtworkPurpose.UNKNOWN


def graphic_payload(
    item: SExpNode,
    *,
    tag: str,
    transform: tuple[float, float, float] | None,
) -> PcbLine | PcbArc | PcbCircle | PcbPolygon | None:
    if tag.endswith("_line") or tag == "gr_line":
        start_node = sexp.find(item, "start")
        end_node = sexp.find(item, "end")
        if not start_node or not end_node:
            return None
        start = pcb_common.maybe_transform(pcb_common.xy(start_node), transform)
        end = pcb_common.maybe_transform(pcb_common.xy(end_node), transform)
        return PcbLine(start[0], start[1], end[0], end[1], stroke_width(item))
    if tag.endswith("_arc") or tag == "gr_arc":
        return arc_payload(item, transform)
    if tag.endswith("_circle") or tag == "gr_circle":
        center_node = sexp.find(item, "center")
        end_node = sexp.find(item, "end")
        if not center_node or not end_node:
            return None
        center_local = pcb_common.xy(center_node)
        end_local = pcb_common.xy(end_node)
        radius = math.hypot(end_local[0] - center_local[0], end_local[1] - center_local[1])
        center = pcb_common.maybe_transform(center_local, transform)
        return PcbCircle(center[0], center[1], radius, stroke_width(item), fill_flag(item))
    if tag.endswith("_rect") or tag == "gr_rect":
        start_node = sexp.find(item, "start")
        end_node = sexp.find(item, "end")
        if not start_node or not end_node:
            return None
        sx, sy = pcb_common.xy(start_node)
        ex, ey = pcb_common.xy(end_node)
        points = [
            pcb_common.maybe_transform(point, transform)
            for point in ((sx, sy), (ex, sy), (ex, ey), (sx, ey))
        ]
        return PcbPolygon(points, width=stroke_width(item), fill=fill_flag(item))
    if tag.endswith("_poly") or tag == "gr_poly":
        pts_node = sexp.find(item, "pts")
        if not pts_node:
            return None
        points = [
            pcb_common.maybe_transform(pcb_common.xy(xy_node), transform)
            for xy_node in sexp.find_all(pts_node, "xy")
        ]
        return (
            PcbPolygon(points, width=stroke_width(item), fill=fill_flag(item)) if points else None
        )
    return None


def arc_payload(
    item: SExpNode,
    transform: tuple[float, float, float] | None,
) -> PcbArc | None:
    start_node = sexp.find(item, "start")
    mid_node = sexp.find(item, "mid")
    end_node = sexp.find(item, "end")
    if not start_node or not end_node:
        return None
    if mid_node:
        start = pcb_common.maybe_transform(pcb_common.xy(start_node), transform)
        mid = pcb_common.maybe_transform(pcb_common.xy(mid_node), transform)
        end = pcb_common.maybe_transform(pcb_common.xy(end_node), transform)
        return PcbArc(start[0], start[1], mid[0], mid[1], end[0], end[1], stroke_width(item))
    angle_node = sexp.find(item, "angle")
    if not angle_node:
        return None
    cx, cy = pcb_common.xy(start_node)
    ex, ey = pcb_common.xy(end_node)
    angle_rad = math.radians(sexp.num(angle_node, 1))
    half_rad = angle_rad / 2.0
    dx = ex - cx
    dy = ey - cy
    mid = (
        cx + dx * math.cos(half_rad) - dy * math.sin(half_rad),
        cy + dx * math.sin(half_rad) + dy * math.cos(half_rad),
    )
    far = (
        cx + dx * math.cos(angle_rad) - dy * math.sin(angle_rad),
        cy + dx * math.sin(angle_rad) + dy * math.cos(angle_rad),
    )
    start = pcb_common.maybe_transform((ex, ey), transform)
    middle = pcb_common.maybe_transform(mid, transform)
    end = pcb_common.maybe_transform(far, transform)
    return PcbArc(start[0], start[1], middle[0], middle[1], end[0], end[1], stroke_width(item))


def artwork_kind_for_payload(payload: object) -> PcbArtworkKind:
    if isinstance(payload, PcbLine):
        return PcbArtworkKind.LINE
    if isinstance(payload, PcbArc):
        return PcbArtworkKind.ARC
    if isinstance(payload, PcbCircle):
        return PcbArtworkKind.CIRCLE
    if isinstance(payload, PcbPolygon):
        return PcbArtworkKind.POLYGON
    if isinstance(payload, PcbText):
        return PcbArtworkKind.TEXT
    if isinstance(payload, PcbModel3D):
        return PcbArtworkKind.MODEL_3D
    return PcbArtworkKind.IMAGE


def font_size(item: SExpNode) -> float:
    size_node = sexp.find_path(item, "effects", "font", "size")
    return sexp.num(size_node, 1) if size_node else 1.0


def justify(effects: SExpNode | None) -> str:
    justify_node = sexp.find(effects, "justify") if effects else None
    if not justify_node:
        return ""
    values: list[str] = []
    for value in justify_node[1:]:
        values.append(value.value() if isinstance(value, sexpdata.Symbol) else str(value))
    return " ".join(values)


# Padding around bare-pad footprint extents when no courtyard is present.
_PAD_BBOX_MARGIN_MM = 0.5


def compute_bbox(
    pads: list[PcbPad], courtyard_artwork: list[PcbArtwork]
) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    if courtyard_artwork:
        for artwork in courtyard_artwork:
            extend_shape_bounds(xs, ys, artwork.data)
    elif pads:
        for pad in pads:
            xs.extend(
                [
                    pad.x - pad.width / 2 - _PAD_BBOX_MARGIN_MM,
                    pad.x + pad.width / 2 + _PAD_BBOX_MARGIN_MM,
                ]
            )
            ys.extend(
                [
                    pad.y - pad.height / 2 - _PAD_BBOX_MARGIN_MM,
                    pad.y + pad.height / 2 + _PAD_BBOX_MARGIN_MM,
                ]
            )
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))
