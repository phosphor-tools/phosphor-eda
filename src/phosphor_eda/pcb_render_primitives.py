"""SVG primitive models and conversion helpers for PCB rendering."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from phosphor_eda.pcb import (
    Pcb,
    PcbArc,
    PcbCircle,
    PcbGraphicText,
    PcbKeepout,
    PcbLine,
    PcbPad,
    PcbPolygon,
    PcbText,
    PcbVia,
)
from phosphor_eda.pcb_render_drills import pad_drill_dimensions, pad_drill_geometry
from phosphor_eda.pcb_render_geometry import GeometryKind, RenderPoint
from phosphor_eda.pcb_render_skia import geometry_to_skia_artwork, skia_path_to_svg_d
from phosphor_eda.shapely_geometry import normalize_geometry
from phosphor_eda.sql.geometry import arc_to_polyline, board_outline_polygon
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


@dataclass(frozen=True)
class LayerClip:
    board: tuple[SvgPrimitive, ...] = ()


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


def drill_to_svg_primitive(item: RenderableGeometry) -> SvgPrimitive | None:
    """Convert one drill-capable source item into a drill-hole mask primitive."""
    payload = item.payload if item.payload is not None else item.source
    if item.kind is GeometryKind.DRILL and isinstance(payload, PcbPad):
        d = _pad_drill_path_d(payload)
    elif item.kind is GeometryKind.VIA and isinstance(payload, PcbVia):
        d = _circle_path_d(payload.x, payload.y, payload.drill / 2.0)
    else:
        return None
    if not d:
        return None
    return SvgPrimitive(
        d=d,
        source_id=item.id,
        source_layer="drills",
        kind=GeometryKind.DRILL,
        tags=item.tags,
    )


def visible_drill_to_svg_primitive(item: RenderableGeometry) -> SvgPrimitive | None:
    """Convert one drill source item into an EDA-style visible drill symbol."""
    payload = item.payload if item.payload is not None else item.source
    if item.kind is not GeometryKind.DRILL or not isinstance(payload, PcbPad):
        return None
    d = _pad_drill_symbol_path_d(payload)
    if not d:
        return None
    return SvgPrimitive(
        d=d,
        source_id=item.id,
        source_layer="drills",
        kind=GeometryKind.DRILL,
        tags=item.tags,
    )


def pad_solder_mask_opening_primitive(
    item: RenderableGeometry,
    *,
    side: str,
    target_layer_name: str,
) -> SvgPrimitive | None:
    """Convert a side-visible copper pad into a solder-mask opening primitive."""
    payload = item.payload if item.payload is not None else item.source
    if item.kind is not GeometryKind.PAD or not isinstance(payload, PcbPad):
        return None
    copper_layer_name = _pad_copper_target_layer_name(payload, item.layer.name, side)
    if copper_layer_name is None:
        return None

    expansion = payload.mask_expansion if payload.mask_expansion is not None else 0.0
    expanded = replace(
        payload,
        width=payload.width + 2 * expansion,
        height=payload.height + 2 * expansion,
        mid_width=None if payload.mid_width is None else payload.mid_width + 2 * expansion,
        mid_height=None if payload.mid_height is None else payload.mid_height + 2 * expansion,
        bot_width=None if payload.bot_width is None else payload.bot_width + 2 * expansion,
        bot_height=None if payload.bot_height is None else payload.bot_height + 2 * expansion,
    )
    if expanded.width <= 0.0 or expanded.height <= 0.0:
        return None

    temp_item = replace(item, payload=expanded, source=expanded)
    primitive = geometry_to_svg_primitive(temp_item, target_layer_name=copper_layer_name)
    if primitive is None:
        return None
    return SvgPrimitive(
        d=primitive.d,
        source_id=item.id,
        source_layer=target_layer_name,
        kind=GeometryKind.MASK,
        tags=item.tags,
        data={"source-copper-layer": copper_layer_name},
    )


def _pad_copper_target_layer_name(pad: PcbPad, fallback_layer_name: str, side: str) -> str | None:
    layer_names = {str(layer_name) for layer_name in pad.layers}
    if side == "front":
        for layer_name in ("F.Cu", "Top Layer", "Top"):
            if layer_name in layer_names:
                return layer_name
        if "*.Cu" in layer_names:
            return "F.Cu"
        if fallback_layer_name in {"F.Cu", "Top Layer", "Top"}:
            return fallback_layer_name
    if side == "back":
        for layer_name in ("B.Cu", "Bottom Layer", "Bottom"):
            if layer_name in layer_names:
                return layer_name
        if "*.Cu" in layer_names:
            return "B.Cu"
        if fallback_layer_name in {"B.Cu", "Bottom Layer", "Bottom"}:
            return fallback_layer_name
    return None


def _non_skia_svg_path_d(item: RenderableGeometry) -> str:
    if item.kind is GeometryKind.BOARD_MATERIAL:
        return _board_material_svg_path_d(item)
    if item.kind is GeometryKind.BOARD_OUTLINE:
        return _board_outline_svg_path_d(item)
    payload = item.payload if item.payload is not None else item.source
    if item.kind in _LINE_KINDS and isinstance(payload, PcbLine):
        return _line_svg_path_d(payload)
    if item.kind in _ARC_KINDS and isinstance(payload, PcbArc):
        return _arc_svg_path_d(payload)
    if item.kind in _CIRCLE_KINDS and isinstance(payload, PcbCircle):
        return _circle_svg_path_d(payload)
    if isinstance(payload, PcbPolygon):
        return _polygon_svg_path_d(payload)
    if isinstance(payload, PcbKeepout):
        return _keepout_svg_path_d(payload)
    if isinstance(payload, BaseGeometry):
        return " ".join(_geometry_to_svg_path_parts(payload))
    if isinstance(payload, PcbText | PcbGraphicText):
        return " ".join(_geometry_to_svg_path_parts(text_outline_geometry(payload)))
    return ""


def _board_material_svg_path_d(item: RenderableGeometry) -> str:
    outline = _outline_for_item(item)
    if outline is not None:
        lines, arcs = outline
        d = _filled_outline_svg_path_d(lines, arcs)
        if d:
            return d
    if item.bbox is not None:
        min_x, min_y, max_x, max_y = item.bbox
        return _closed_point_pairs_to_svg_path_d(
            ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y))
        )
    payload = item.payload if item.payload is not None else item.source
    bbox = _bbox_payload(payload)
    if bbox is None:
        return ""
    min_x, min_y, max_x, max_y = bbox
    return _closed_point_pairs_to_svg_path_d(
        ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y))
    )


def _board_outline_svg_path_d(item: RenderableGeometry) -> str:
    outline = _outline_for_item(item)
    if outline is None:
        if item.points:
            return _points_to_closed_svg_path_d(item.points)
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


def _polygon_svg_path_d(polygon: PcbPolygon) -> str:
    if len(polygon.points) < 3:
        return ""
    holes = [hole for hole in polygon.holes if len(hole) >= 3]
    geometry = normalize_geometry(Polygon(polygon.points, holes=holes or None))
    if geometry.is_empty:
        return ""
    return " ".join(_geometry_to_svg_path_parts(geometry))


def _keepout_svg_path_d(keepout: PcbKeepout) -> str:
    if len(keepout.boundary) < 3:
        return ""
    geometry = normalize_geometry(Polygon(keepout.boundary, holes=keepout.holes or None))
    if geometry.is_empty:
        return ""
    return " ".join(_geometry_to_svg_path_parts(geometry))


def _outline_for_item(item: RenderableGeometry) -> tuple[list[PcbLine], list[PcbArc]] | None:
    payload = item.payload if item.payload is not None else item.source
    outline = _outline_payload(payload)
    if outline is not None:
        return outline
    if isinstance(payload, Pcb):
        return payload.outline_lines, payload.outline_arcs
    if isinstance(item.source, Pcb):
        return item.source.outline_lines, item.source.outline_arcs
    return None


def _filled_outline_svg_path_d(lines: list[PcbLine], arcs: list[PcbArc]) -> str:
    outline_geometry = board_outline_polygon(lines, arcs)
    if outline_geometry is not None and not outline_geometry.is_empty:
        return " ".join(_geometry_to_svg_path_parts(outline_geometry))

    segments: list[tuple[tuple[float, float], ...]] = []
    for line in lines:
        segment = ((line.start_x, line.start_y), (line.end_x, line.end_y))
        if not _points_equal(segment[0], segment[1]):
            segments.append(segment)
    for arc in arcs:
        points = tuple(
            arc_to_polyline(
                arc.start_x,
                arc.start_y,
                arc.mid_x,
                arc.mid_y,
                arc.end_x,
                arc.end_y,
                num_points=32,
            )
        )
        if len(points) >= 2:
            segments.append(points)

    contours = _stitch_outline_segments(segments)
    return " ".join(_closed_point_pairs_to_svg_path_d(contour) for contour in contours)


def _stitch_outline_segments(
    segments: list[tuple[tuple[float, float], ...]],
) -> tuple[tuple[tuple[float, float], ...], ...]:
    unused = [list(segment) for segment in segments if len(segment) >= 2]
    contours: list[tuple[tuple[float, float], ...]] = []
    while unused:
        contour = unused.pop(0)
        extended = True
        while extended:
            extended = False
            end = contour[-1]
            for index, segment in enumerate(unused):
                if _points_equal(end, segment[0]):
                    contour.extend(segment[1:])
                    _ = unused.pop(index)
                    extended = True
                    break
                if _points_equal(end, segment[-1]):
                    contour.extend(reversed(segment[:-1]))
                    _ = unused.pop(index)
                    extended = True
                    break
        if len(contour) >= 3 and _points_equal(contour[0], contour[-1]):
            contour = contour[:-1]
        if len(contour) >= 3:
            contours.append(tuple(contour))
    return tuple(contours)


def _points_equal(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return math.isclose(first[0], second[0], abs_tol=1e-6) and math.isclose(
        first[1],
        second[1],
        abs_tol=1e-6,
    )


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


def _pad_drill_path_d(pad: PcbPad) -> str:
    geometry = pad_drill_geometry(pad)
    if geometry is None or geometry.is_empty:
        return ""
    return " ".join(_geometry_to_svg_path_parts(geometry))


def _pad_drill_symbol_path_d(pad: PcbPad) -> str:
    outline = _pad_drill_path_d(pad)
    if not outline:
        return ""
    width, height = pad_drill_dimensions(pad)
    mark_radius = min(width, height) * 0.175
    cross = (
        f"M {pad.x - mark_radius:.4f} {pad.y:.4f} L {pad.x + mark_radius:.4f} {pad.y:.4f} "
        f"M {pad.x:.4f} {pad.y - mark_radius:.4f} L {pad.x:.4f} {pad.y + mark_radius:.4f}"
    )
    return f"{outline} {cross}"


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


def _bbox_payload(payload: object) -> tuple[float, float, float, float] | None:
    if not isinstance(payload, tuple):
        return None
    payload_values = cast("tuple[object, ...]", payload)
    if len(payload_values) != 4:
        return None
    values: list[float] = []
    for value in payload_values:
        if not isinstance(value, int | float):
            return None
        values.append(float(value))
    return values[0], values[1], values[2], values[3]


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
