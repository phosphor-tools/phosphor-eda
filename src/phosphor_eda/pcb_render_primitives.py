"""SVG primitive models and conversion helpers for PCB rendering."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from phosphor_eda.pcb import Pcb, PcbArc, PcbCircle, PcbGraphicText, PcbLine, PcbText
from phosphor_eda.pcb_render_geometry import GeometryKind, RenderPoint
from phosphor_eda.pcb_render_skia import geometry_to_skia_artwork, skia_path_to_svg_d
from phosphor_eda.sql.geometry import arc_to_polyline
from phosphor_eda.text_outlines import text_outline_geometry

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from shapely.coords import CoordinateSequence

    from phosphor_eda.pcb_render_geometry import GeometryTags, RenderableGeometry


def _empty_data() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class SvgPrimitive:
    d: str
    source_id: str
    source_layer: str
    kind: GeometryKind
    tags: GeometryTags
    data: Mapping[str, str] = field(default_factory=_empty_data)


@dataclass(frozen=True)
class LayerMask:
    board: tuple[SvgPrimitive, ...] = ()
    drills: tuple[SvgPrimitive, ...] = ()
    openings: tuple[SvgPrimitive, ...] = ()


def geometry_to_svg_primitive(
    item: RenderableGeometry,
    *,
    target_layer_name: str,
) -> SvgPrimitive | None:
    """Convert one renderable PCB geometry item into one SVG path primitive."""
    artwork = geometry_to_skia_artwork(item, target_layer_name=target_layer_name)
    d = skia_path_to_svg_d(artwork.path) if artwork is not None else _non_skia_svg_path_d(item)
    if not d:
        return None
    return SvgPrimitive(
        d=d,
        source_id=item.id,
        source_layer=target_layer_name,
        kind=item.kind,
        tags=item.tags,
    )


def _non_skia_svg_path_d(item: RenderableGeometry) -> str:
    if item.kind is GeometryKind.BOARD_OUTLINE:
        return _board_outline_svg_path_d(item)
    payload = item.payload if item.payload is not None else item.source
    if item.kind in _LINE_KINDS and isinstance(payload, PcbLine):
        return _line_svg_path_d(payload)
    if item.kind in _ARC_KINDS and isinstance(payload, PcbArc):
        return _arc_svg_path_d(payload)
    if item.kind in _CIRCLE_KINDS and isinstance(payload, PcbCircle):
        return _circle_svg_path_d(payload)
    if isinstance(payload, BaseGeometry):
        return " ".join(_geometry_to_svg_path_parts(payload))
    if isinstance(payload, PcbText | PcbGraphicText):
        return " ".join(_geometry_to_svg_path_parts(text_outline_geometry(payload)))
    return ""


def _board_outline_svg_path_d(item: RenderableGeometry) -> str:
    if item.points:
        return _points_to_closed_svg_path_d(item.points)
    payload = item.payload if item.payload is not None else item.source
    outline = _outline_payload(payload)
    if outline is None and isinstance(item.source, Pcb):
        outline = (item.source.outline_lines, item.source.outline_arcs)
    if outline is None:
        return ""

    lines, arcs = outline
    commands: list[str] = []
    for line in lines:
        commands.append(
            " ".join(
                (
                    f"M {line.start_x:.4f} {line.start_y:.4f}",
                    f"L {line.end_x:.4f} {line.end_y:.4f}",
                )
            )
        )
    for arc in arcs:
        points = arc_to_polyline(
            arc.start_x,
            arc.start_y,
            arc.mid_x,
            arc.mid_y,
            arc.end_x,
            arc.end_y,
            num_points=32,
        )
        if points:
            commands.append(_points_to_open_svg_path_d(points))
    return " ".join(command for command in commands if command)


def _points_to_closed_svg_path_d(points: tuple[RenderPoint, ...]) -> str:
    point_pairs = tuple((point.x, point.y) for point in points)
    d = _points_to_open_svg_path_d(point_pairs)
    return f"{d} Z" if d else ""


def _points_to_open_svg_path_d(points: Iterable[tuple[float, float]]) -> str:
    point_tuple = tuple(points)
    if len(point_tuple) < 2:
        return ""
    commands = [f"M {point_tuple[0][0]:.4f} {point_tuple[0][1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in point_tuple[1:])
    return " ".join(commands)


def _line_svg_path_d(line: PcbLine) -> str:
    return _stroked_polyline_path_d(
        ((line.start_x, line.start_y), (line.end_x, line.end_y)),
        line.width,
    )


def _arc_svg_path_d(arc: PcbArc) -> str:
    return _stroked_polyline_path_d(
        arc_to_polyline(
            arc.start_x,
            arc.start_y,
            arc.mid_x,
            arc.mid_y,
            arc.end_x,
            arc.end_y,
            num_points=32,
        ),
        arc.width,
    )


def _circle_svg_path_d(circle: PcbCircle) -> str:
    if circle.radius <= 0:
        return ""
    outer = _circle_path_d(circle.cx, circle.cy, circle.radius)
    if circle.fill:
        return outer
    inner_radius = circle.radius - circle.width
    if inner_radius <= 0:
        return outer
    return f"{outer} {_circle_path_d(circle.cx, circle.cy, inner_radius)}"


def _circle_path_d(cx: float, cy: float, radius: float) -> str:
    kappa = 0.5522847498307936
    control = radius * kappa
    curves = (
        (
            cx + radius,
            cy + control,
            cx + control,
            cy + radius,
            cx,
            cy + radius,
        ),
        (
            cx - control,
            cy + radius,
            cx - radius,
            cy + control,
            cx - radius,
            cy,
        ),
        (
            cx - radius,
            cy - control,
            cx - control,
            cy - radius,
            cx,
            cy - radius,
        ),
        (
            cx + control,
            cy - radius,
            cx + radius,
            cy - control,
            cx + radius,
            cy,
        ),
    )
    curve_commands = tuple(
        f"C {x1:.4f} {y1:.4f} {x2:.4f} {y2:.4f} {x3:.4f} {y3:.4f}"
        for x1, y1, x2, y2, x3, y3 in curves
    )
    return " ".join((f"M {cx + radius:.4f} {cy:.4f}", *curve_commands, "Z"))


def _stroked_polyline_path_d(points: Iterable[tuple[float, float]], width: float) -> str:
    point_tuple = tuple(points)
    if width <= 0 or len(point_tuple) < 2:
        return ""
    half_width = width / 2
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for start, end in zip(point_tuple, point_tuple[1:], strict=False):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        nx = -dy / length * half_width
        ny = dx / length * half_width
        if not left:
            left.append((start[0] + nx, start[1] + ny))
            right.append((start[0] - nx, start[1] - ny))
        left.append((end[0] + nx, end[1] + ny))
        right.append((end[0] - nx, end[1] - ny))
    if len(left) < 2 or len(right) < 2:
        return ""
    return _closed_point_pairs_to_svg_path_d((*left, *reversed(right)))


def _closed_point_pairs_to_svg_path_d(points: tuple[tuple[float, float], ...]) -> str:
    if len(points) < 3:
        return ""
    commands = [f"M {points[0][0]:.4f} {points[0][1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in points[1:])
    commands.append("Z")
    return " ".join(commands)


def _outline_payload(payload: object) -> tuple[list[PcbLine], list[PcbArc]] | None:
    if not isinstance(payload, tuple):
        return None
    payload_tuple = cast("tuple[object, ...]", payload)
    if len(payload_tuple) != 2:
        return None
    lines_object, arcs_object = payload_tuple
    if not isinstance(lines_object, list) or not isinstance(arcs_object, list):
        return None
    raw_lines = cast("list[object]", lines_object)
    raw_arcs = cast("list[object]", arcs_object)
    lines: list[PcbLine] = []
    for line in raw_lines:
        if not isinstance(line, PcbLine):
            return None
        lines.append(line)
    arcs: list[PcbArc] = []
    for arc in raw_arcs:
        if not isinstance(arc, PcbArc):
            return None
        arcs.append(arc)
    return lines, arcs


_LINE_KINDS = frozenset(
    {
        GeometryKind.SILK_LINE,
        GeometryKind.FAB_LINE,
        GeometryKind.BODY_LINE,
    }
)

_ARC_KINDS = frozenset(
    {
        GeometryKind.FAB_ARC,
        GeometryKind.BODY_ARC,
    }
)

_CIRCLE_KINDS = frozenset(
    {
        GeometryKind.FAB_CIRCLE,
        GeometryKind.BODY_CIRCLE,
    }
)


def svg_primitives_from_geometry(
    geometry: BaseGeometry,
    *,
    source_ids: Iterable[str],
    source_layers: Iterable[str],
    kind: GeometryKind,
    tags: GeometryTags,
    data: Mapping[str, str] | None = None,
) -> tuple[SvgPrimitive, ...]:
    """Convert geometry into SVG primitives for render-mode transition code."""
    source_id = ",".join(source_ids)
    source_layer = ",".join(source_layers)
    primitive_data: Mapping[str, str] = {} if data is None else data
    return tuple(
        SvgPrimitive(
            d=d,
            source_id=source_id,
            source_layer=source_layer,
            kind=kind,
            tags=tags,
            data=primitive_data,
        )
        for d in _geometry_to_svg_path_parts(geometry)
        if d
    )


def _geometry_to_svg_path_parts(geometry: BaseGeometry) -> tuple[str, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, Polygon):
        return (_polygon_to_svg_path_d(geometry),)
    if isinstance(geometry, MultiPolygon):
        return tuple(
            path_d
            for polygon in geometry.geoms
            for path_d in (_polygon_to_svg_path_d(polygon),)
            if path_d
        )
    if isinstance(geometry, LineString):
        return (_line_string_to_svg_path_d(geometry),)
    if isinstance(geometry, MultiLineString):
        return tuple(
            path_d
            for line in geometry.geoms
            for path_d in (_line_string_to_svg_path_d(line),)
            if path_d
        )
    if isinstance(geometry, GeometryCollection):
        collection = cast("GeometryCollection[BaseGeometry]", geometry)
        return tuple(
            path_d
            for part in collection.geoms
            for path_d in _geometry_to_svg_path_parts(part)
            if path_d
        )
    return ()


def _polygon_to_svg_path_d(polygon: Polygon) -> str:
    rings = [_ring_to_svg_path_d(polygon.exterior.coords)]
    rings.extend(_ring_to_svg_path_d(interior.coords) for interior in polygon.interiors)
    return " ".join(ring for ring in rings if ring)


def _ring_to_svg_path_d(coords: CoordinateSequence) -> str:
    points = [(float(x), float(y)) for x, y in coords]
    if len(points) < 2:
        return ""
    if points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 2:
        return ""
    commands = [f"M {points[0][0]:.4f} {points[0][1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in points[1:])
    commands.append("Z")
    return " ".join(commands)


def _line_string_to_svg_path_d(line: LineString) -> str:
    points = [(float(x), float(y)) for x, y in line.coords]
    if len(points) < 2:
        return ""
    commands = [f"M {points[0][0]:.4f} {points[0][1]:.4f}"]
    commands.extend(f"L {x:.4f} {y:.4f}" for x, y in points[1:])
    return " ".join(commands)
