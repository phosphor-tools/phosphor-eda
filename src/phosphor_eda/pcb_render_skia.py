"""Skia PathOps adapter for PCB render artwork."""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from phosphor_eda.pcb import PcbPad, PcbPolygon, PcbSegment, PcbTraceArc, PcbVia, PcbZone
from phosphor_eda.pcb_render_geometry import GeometryKind, GeometryTags
from phosphor_eda.sql.geometry import arc_to_polyline

if TYPE_CHECKING:
    from collections.abc import Sequence

    from phosphor_eda.pcb_render_geometry import RenderableGeometry


SKIA_CONIC_TO_QUAD_TOLERANCE = 0.02
_CIRCLE_KAPPA = 0.5522847498307936
_TRACE_ARC_SEGMENTS = 32


class _Path(Protocol):
    def moveTo(self, x: float, y: float) -> None: ...

    def lineTo(self, x: float, y: float) -> None: ...

    def cubicTo(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        x3: float,
        y3: float,
    ) -> None: ...

    def close(self) -> None: ...

    def convertConicsToQuads(self, tolerance: float = SKIA_CONIC_TO_QUAD_TOLERANCE) -> None: ...

    def draw(self, pen: _SvgPointPen) -> None: ...

    def stroke(self, width: float, cap: object, join: object, miter_limit: float) -> None: ...


class _PathFactory(Protocol):
    def __call__(self) -> _Path: ...


class _LineCapValues(Protocol):
    BUTT_CAP: object
    ROUND_CAP: object


class _LineJoinValues(Protocol):
    ROUND_JOIN: object


class _PathopsModule(Protocol):
    Path: _PathFactory
    LineCap: _LineCapValues
    LineJoin: _LineJoinValues


class _SvgPointPen(Protocol):
    def moveTo(self, point: tuple[float, float]) -> None: ...

    def lineTo(self, point: tuple[float, float]) -> None: ...

    def curveTo(self, *points: tuple[float, float]) -> None: ...

    def qCurveTo(self, *points: tuple[float, float] | None) -> None: ...

    def closePath(self) -> None: ...


_PATHOPS = cast("_PathopsModule", cast("object", import_module("pathops")))


@dataclass(frozen=True)
class SkiaArtwork:
    path: _Path
    source_ids: tuple[str, ...]
    source_layers: tuple[str, ...]
    tags: GeometryTags


class _SvgPathPen:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def moveTo(self, point: tuple[float, float]) -> None:
        self.parts.append(f"M {point[0]:.4f} {point[1]:.4f}")

    def lineTo(self, point: tuple[float, float]) -> None:
        self.parts.append(f"L {point[0]:.4f} {point[1]:.4f}")

    def curveTo(self, *points: tuple[float, float]) -> None:
        for start in range(0, len(points), 3):
            p1, p2, p3 = points[start : start + 3]
            self.parts.append("C " + " ".join(f"{coord:.4f}" for coord in (*p1, *p2, *p3)))

    def qCurveTo(self, *points: tuple[float, float] | None) -> None:
        clean_points = [point for point in points if point is not None]
        if len(clean_points) < 2:
            return
        off_curves = clean_points[:-1]
        on_curve = clean_points[-1]
        for index, p1 in enumerate(off_curves):
            if index + 1 < len(off_curves):
                next_off = off_curves[index + 1]
                p2 = ((p1[0] + next_off[0]) / 2, (p1[1] + next_off[1]) / 2)
            else:
                p2 = on_curve
            self.parts.append(f"Q {p1[0]:.4f} {p1[1]:.4f} {p2[0]:.4f} {p2[1]:.4f}")

    def closePath(self) -> None:
        self.parts.append("Z")

    def svg_d(self) -> str:
        return " ".join(self.parts)


def geometry_to_skia_artwork(
    item: RenderableGeometry,
    *,
    target_layer_name: str,
) -> SkiaArtwork | None:
    """Convert one raw renderable PCB primitive into Skia artwork."""
    payload = item.payload if item.payload is not None else item.source
    path: _Path | None = None
    if item.kind is GeometryKind.PAD and isinstance(payload, PcbPad):
        path = _pad_path(payload, target_layer_name)
    elif item.kind is GeometryKind.TRACE and isinstance(payload, PcbSegment):
        path = _trace_path(payload)
    elif item.kind is GeometryKind.TRACE_ARC and isinstance(payload, PcbTraceArc):
        path = _trace_arc_path(payload)
    elif item.kind is GeometryKind.ZONE and isinstance(payload, PcbZone):
        path = _zone_path(payload)
    elif item.kind is GeometryKind.VIA and isinstance(payload, PcbVia):
        path = _via_path(payload, target_layer_name)
    elif item.kind in _POLYGON_KINDS and isinstance(payload, PcbPolygon):
        path = _polygon_path(payload.points, holes=payload.holes)

    if path is None:
        return None
    _convert_conics(path)
    return SkiaArtwork(
        path=path,
        source_ids=(item.id,),
        source_layers=(item.layer.name,),
        tags=item.tags,
    )


def skia_path_to_svg_d(path: _Path) -> str:
    """Serialize one Skia path as SVG path data without unioning it."""
    _convert_conics(path)
    pen = _SvgPathPen()
    path.draw(pen)
    return pen.svg_d()


_POLYGON_KINDS = frozenset(
    {
        GeometryKind.ZONE,
        GeometryKind.SILK_POLYGON,
        GeometryKind.FAB_POLYGON,
        GeometryKind.BODY_POLYGON,
        GeometryKind.MASK,
        GeometryKind.PASTE,
        GeometryKind.MECHANICAL,
    }
)


def _new_path() -> _Path:
    return _PATHOPS.Path()


def _pad_path(pad: PcbPad, target_layer_name: str) -> _Path | None:
    if not _layer_in_stack(target_layer_name, pad.layers):
        return None

    width, height, shape = _pad_layer_dimension(pad, target_layer_name)
    if width <= 0.0 or height <= 0.0:
        return None

    if shape == "circle":
        return _circle_path(pad.x, pad.y, width / 2.0)
    if shape == "oval":
        return _oval_path(pad.x, pad.y, width, height, pad.rotation)
    if shape == "roundrect":
        radius = min(width, height) * max(pad.roundrect_rratio, 0.0) / 2.0
        return _roundrect_path(pad.x, pad.y, width, height, radius, pad.rotation)
    if shape in {"rect", "custom"}:
        return _polygon_path(_rotated_rect_points(pad.x, pad.y, width, height, pad.rotation))
    return None


def _pad_layer_dimension(pad: PcbPad, target_layer_name: str) -> tuple[float, float, str]:
    if (
        _is_back_layer(target_layer_name)
        and pad.bot_width is not None
        and pad.bot_height is not None
    ):
        return pad.bot_width, pad.bot_height, pad.bot_shape or pad.shape
    if (
        not _is_front_layer(target_layer_name)
        and pad.mid_width is not None
        and pad.mid_height is not None
    ):
        return pad.mid_width, pad.mid_height, pad.mid_shape or pad.shape
    return pad.width, pad.height, pad.shape


def _via_path(via: PcbVia, target_layer_name: str) -> _Path | None:
    if via.size <= 0.0 or not _layer_in_stack(target_layer_name, via.layers):
        return None
    return _circle_path(via.x, via.y, via.size / 2.0)


def _trace_path(segment: PcbSegment) -> _Path | None:
    if segment.width <= 0.0:
        return None
    return _buffered_polyline_path(
        ((segment.start_x, segment.start_y), (segment.end_x, segment.end_y)),
        segment.width,
    )


def _trace_arc_path(trace_arc: PcbTraceArc) -> _Path | None:
    if trace_arc.width <= 0.0:
        return None
    points = arc_to_polyline(
        trace_arc.start_x,
        trace_arc.start_y,
        trace_arc.mid_x,
        trace_arc.mid_y,
        trace_arc.end_x,
        trace_arc.end_y,
        num_points=_TRACE_ARC_SEGMENTS,
    )
    return _buffered_polyline_path(points, trace_arc.width)


def _zone_path(zone: PcbZone) -> _Path | None:
    if len(zone.boundary) < 3:
        return None
    return _polygon_path(zone.boundary)


def _circle_path(cx: float, cy: float, radius: float) -> _Path | None:
    if radius <= 0.0:
        return None
    path = _new_path()
    control = radius * _CIRCLE_KAPPA
    path.moveTo(cx + radius, cy)
    path.cubicTo(cx + radius, cy + control, cx + control, cy + radius, cx, cy + radius)
    path.cubicTo(cx - control, cy + radius, cx - radius, cy + control, cx - radius, cy)
    path.cubicTo(cx - radius, cy - control, cx - control, cy - radius, cx, cy - radius)
    path.cubicTo(cx + control, cy - radius, cx + radius, cy - control, cx + radius, cy)
    path.close()
    return path


def _oval_path(cx: float, cy: float, width: float, height: float, rotation: float) -> _Path | None:
    radius = min(width, height) / 2.0
    if radius <= 0.0:
        return None
    if math.isclose(width, height):
        return _circle_path(cx, cy, radius)
    if width > height:
        half = (width - height) / 2.0
        points = ((cx - half, cy), (cx + half, cy))
    else:
        half = (height - width) / 2.0
        points = ((cx, cy - half), (cx, cy + half))
    if not math.isclose(rotation, 0.0):
        points = tuple(_rotate_point(x, y, cx, cy, rotation) for x, y in points)
    return _buffered_polyline_path(points, radius * 2.0, round_caps=True)


def _roundrect_path(
    cx: float,
    cy: float,
    width: float,
    height: float,
    radius: float,
    rotation: float,
) -> _Path | None:
    clamped_radius = min(max(radius, 0.0), width / 2.0, height / 2.0)
    if math.isclose(clamped_radius, 0.0):
        return _polygon_path(_rotated_rect_points(cx, cy, width, height, rotation))
    if math.isclose(clamped_radius, width / 2.0) or math.isclose(clamped_radius, height / 2.0):
        return _oval_path(cx, cy, width, height, rotation)

    left = cx - width / 2.0
    right = cx + width / 2.0
    top = cy - height / 2.0
    bottom = cy + height / 2.0
    control = clamped_radius * _CIRCLE_KAPPA

    def point(x: float, y: float) -> tuple[float, float]:
        if math.isclose(rotation, 0.0):
            return (x, y)
        return _rotate_point(x, y, cx, cy, rotation)

    path = _new_path()
    path.moveTo(*point(right - clamped_radius, top))
    path.lineTo(*point(left + clamped_radius, top))
    path.cubicTo(
        *point(left + clamped_radius - control, top),
        *point(left, top + clamped_radius - control),
        *point(left, top + clamped_radius),
    )
    path.lineTo(*point(left, bottom - clamped_radius))
    path.cubicTo(
        *point(left, bottom - clamped_radius + control),
        *point(left + clamped_radius - control, bottom),
        *point(left + clamped_radius, bottom),
    )
    path.lineTo(*point(right - clamped_radius, bottom))
    path.cubicTo(
        *point(right - clamped_radius + control, bottom),
        *point(right, bottom - clamped_radius + control),
        *point(right, bottom - clamped_radius),
    )
    path.lineTo(*point(right, top + clamped_radius))
    path.cubicTo(
        *point(right, top + clamped_radius - control),
        *point(right - clamped_radius + control, top),
        *point(right - clamped_radius, top),
    )
    path.close()
    return path


def _polygon_path(
    points: Sequence[tuple[float, float]],
    *,
    holes: Sequence[Sequence[tuple[float, float]]] = (),
) -> _Path | None:
    if len(points) < 3:
        return None
    path = _new_path()
    _append_ring(path, points)
    for hole in holes:
        if len(hole) >= 3:
            _append_ring(path, tuple(reversed(hole)))
    return path


def _append_ring(path: _Path, points: Sequence[tuple[float, float]]) -> None:
    first_x, first_y = points[0]
    path.moveTo(first_x, first_y)
    for x, y in points[1:]:
        path.lineTo(x, y)
    path.close()


def _buffered_polyline_path(
    points: Sequence[tuple[float, float]],
    width: float,
    *,
    round_caps: bool = False,
) -> _Path | None:
    if len(points) < 2 or width <= 0.0:
        return None
    raw_path = _new_path()
    start_x, start_y = points[0]
    raw_path.moveTo(start_x, start_y)
    for x, y in points[1:]:
        raw_path.lineTo(x, y)
    raw_path.stroke(
        width,
        _PATHOPS.LineCap.ROUND_CAP if round_caps else _PATHOPS.LineCap.BUTT_CAP,
        _PATHOPS.LineJoin.ROUND_JOIN,
        4.0,
    )
    return raw_path


def _rotated_rect_points(
    cx: float,
    cy: float,
    width: float,
    height: float,
    rotation: float,
) -> tuple[tuple[float, float], ...]:
    half_width = width / 2.0
    half_height = height / 2.0
    points = (
        (cx - half_width, cy - half_height),
        (cx + half_width, cy - half_height),
        (cx + half_width, cy + half_height),
        (cx - half_width, cy + half_height),
    )
    if math.isclose(rotation, 0.0):
        return points
    return tuple(_rotate_point(x, y, cx, cy, rotation) for x, y in points)


def _rotate_point(
    x: float,
    y: float,
    cx: float,
    cy: float,
    degrees: float,
) -> tuple[float, float]:
    angle = math.radians(degrees)
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)
    dx = x - cx
    dy = y - cy
    return (
        cx + dx * cos_angle - dy * sin_angle,
        cy + dx * sin_angle + dy * cos_angle,
    )


def _convert_conics(path: _Path) -> None:
    path.convertConicsToQuads(SKIA_CONIC_TO_QUAD_TOLERANCE)


def _layer_in_stack(layer_name: str, layers: list[str]) -> bool:
    return layer_name in {str(layer) for layer in layers} or (
        "*.Cu" in layers and _is_copper_layer(layer_name)
    )


def _is_copper_layer(layer_name: str) -> bool:
    return (
        layer_name.endswith(".Cu")
        or _is_front_layer(layer_name)
        or _is_back_layer(layer_name)
        or layer_name.startswith("MidLayer")
    )


def _is_front_layer(layer_name: str) -> bool:
    return layer_name in {"F.Cu", "Top Layer", "Top"}


def _is_back_layer(layer_name: str) -> bool:
    return layer_name in {"B.Cu", "Bottom Layer", "Bottom"}
