"""SVG primitive models and conversion helpers for PCB rendering."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from phosphor_eda.pcb import (
    PcbArcGeometry,
    PcbCircleGeometry,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbLineGeometry,
    PcbPadGeometry,
    PcbPolygonGeometry,
    PcbTextGeometry,
    PcbViaGeometry,
)
from phosphor_eda.pcb import (
    PcbGeometry as DomainPcbGeometry,
)
from phosphor_eda.pcb_render_drills import pad_drill_dimensions, pad_drill_geometry
from phosphor_eda.pcb_render_geometry import (
    SYNTHETIC_BOARD_MATERIAL_ROLE,
    SYNTHETIC_DRILL_ROLE,
)
from phosphor_eda.pcb_render_skia import geometry_to_skia_artwork, skia_path_to_svg_d
from phosphor_eda.shapely_geometry import normalize_geometry
from phosphor_eda.sql.geometry import (
    arc_center_from_three_points,
    arc_sweep_angle,
    arc_to_polyline,
    pad_polygon,
)
from phosphor_eda.text_outlines import text_outline_geometry

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from shapely.coords import CoordinateSequence

    from phosphor_eda.pcb_render_geometry import GeometryTags, RenderableItem


def _empty_data() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class SvgPrimitive:
    d: str
    source_id: str
    source_layer: str
    kind: str
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


_PROFILE_ENDPOINT_TOLERANCE_MM = 1e-5


@dataclass(frozen=True)
class _ProfilePathSegment:
    start: tuple[float, float]
    end: tuple[float, float]
    forward: str
    reverse: str


@dataclass(frozen=True)
class _OrientedProfilePathSegment:
    start: tuple[float, float]
    end: tuple[float, float]
    command: str


def geometry_to_svg_primitive(
    item: RenderableItem,
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
        kind=item.display_role,
        tags=item.tags,
    )


def drill_to_svg_primitive(item: RenderableItem) -> SvgPrimitive | None:
    """Convert one drill-capable source item into a drill-hole mask primitive."""
    payload = item.payload if item.payload is not None else item.source
    if item.display_role == SYNTHETIC_DRILL_ROLE and isinstance(payload, PcbPadGeometry):
        d = _pad_drill_path_d(payload)
    elif item.object_type == PcbGeometryObject.VIA and isinstance(payload, PcbViaGeometry):
        d = _circle_path_d(payload.x, payload.y, payload.drill / 2.0)
    else:
        return None
    if not d:
        return None
    return SvgPrimitive(
        d=d,
        source_id=item.id,
        source_layer="drills",
        kind=SYNTHETIC_DRILL_ROLE,
        tags=item.tags,
    )


def visible_drill_to_svg_primitive(item: RenderableItem) -> SvgPrimitive | None:
    """Convert one drill source item into an EDA-style visible drill symbol."""
    payload = item.payload if item.payload is not None else item.source
    if item.display_role != SYNTHETIC_DRILL_ROLE or not isinstance(payload, PcbPadGeometry):
        return None
    d = _pad_drill_symbol_path_d(payload)
    if not d:
        return None
    return SvgPrimitive(
        d=d,
        source_id=item.id,
        source_layer="drills",
        kind=SYNTHETIC_DRILL_ROLE,
        tags=item.tags,
    )


def pad_solder_mask_opening_primitive(
    item: RenderableItem,
    *,
    side: str,
    target_layer_name: str,
) -> SvgPrimitive | None:
    """Convert a side-visible copper pad into a solder-mask opening primitive."""
    payload = item.payload if item.payload is not None else item.source
    if item.object_type != PcbGeometryObject.PAD or not isinstance(payload, PcbPadGeometry):
        return None
    if payload.mask_aperture_width is not None and payload.mask_aperture_height is not None:
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

    opening_geometry = _pad_solder_mask_opening_geometry(expanded, expansion)
    temp_item = replace(
        item,
        roles=(*item.roles, PcbGeometryRole.SOLDER_MASK),
        display_role=PcbGeometryRole.SOLDER_MASK.value,
        payload=opening_geometry,
        source=opening_geometry,
    )
    primitive = geometry_to_svg_primitive(temp_item, target_layer_name=copper_layer_name)
    if primitive is None:
        return None
    return SvgPrimitive(
        d=primitive.d,
        source_id=item.id,
        source_layer=target_layer_name,
        kind=PcbGeometryRole.SOLDER_MASK.value,
        tags=item.tags,
        data={"source-copper-layer": copper_layer_name},
    )


def _pad_solder_mask_opening_geometry(pad: PcbPadGeometry, expansion: float) -> BaseGeometry:
    opening = pad_polygon(pad)
    drill = pad_drill_geometry(pad)
    if drill is None or drill.is_empty:
        return opening
    if expansion > 0.0:
        drill = drill.buffer(expansion)
    return normalize_geometry(opening.union(drill))


def _pad_copper_target_layer_name(
    _pad: PcbPadGeometry, fallback_layer_name: str, side: str
) -> str | None:
    layer_names = {fallback_layer_name}
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


def _non_skia_svg_path_d(item: RenderableItem) -> str:
    if item.display_role == SYNTHETIC_BOARD_MATERIAL_ROLE:
        return _board_material_svg_path_d(item)
    payload = item.payload if item.payload is not None else item.source
    if _is_line_renderable(item) and isinstance(payload, PcbLineGeometry):
        return _line_svg_path_d(payload)
    if _is_arc_renderable(item) and isinstance(payload, PcbArcGeometry):
        return _arc_svg_path_d(payload)
    if _is_circle_renderable(item) and isinstance(payload, PcbCircleGeometry):
        return _circle_svg_path_d(payload)
    if isinstance(payload, PcbPolygonGeometry):
        return _polygon_svg_path_d(payload)
    if isinstance(payload, BaseGeometry):
        return " ".join(_geometry_to_svg_path_parts(payload))
    if isinstance(payload, PcbTextGeometry):
        return " ".join(_geometry_to_svg_path_parts(text_outline_geometry(payload)))
    return ""


def _board_material_svg_path_d(item: RenderableItem) -> str:
    outline = _outline_for_item(item)
    if outline is not None:
        d = _filled_outline_svg_path_d(outline)
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


def _polygon_svg_path_d(polygon: PcbPolygonGeometry) -> str:
    if len(polygon.points) < 3:
        return ""
    holes = [hole for hole in polygon.holes if len(hole) >= 3]
    geometry = normalize_geometry(Polygon(polygon.points, holes=holes or None))
    if geometry.is_empty:
        return ""
    return " ".join(_geometry_to_svg_path_parts(geometry))


def _outline_for_item(item: RenderableItem) -> list[DomainPcbGeometry] | None:
    payload = item.payload if item.payload is not None else item.source
    outline = _outline_payload(payload)
    if outline is not None:
        return outline
    if isinstance(item.source, tuple):
        return _outline_payload(cast("tuple[object, ...]", item.source))
    return None


def _filled_outline_svg_path_d(outline: list[DomainPcbGeometry]) -> str:
    profile_path = _profile_geometry_svg_path_d(outline)
    if profile_path:
        return profile_path

    segments: list[tuple[tuple[float, float], ...]] = []
    for item in outline:
        if isinstance(item.data, PcbLineGeometry):
            line = item.data
            segment = ((line.start_x, line.start_y), (line.end_x, line.end_y))
            if not _points_equal(segment[0], segment[1]):
                segments.append(segment)
        elif isinstance(item.data, PcbArcGeometry):
            arc = item.data
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


def _profile_geometry_svg_path_d(outline: list[DomainPcbGeometry]) -> str:
    segments: list[_ProfilePathSegment] = []
    polygon_paths: list[str] = []
    for item in outline:
        if isinstance(item.data, PcbLineGeometry):
            segment = _line_profile_segment(item.data)
            if segment is not None:
                segments.append(segment)
        elif isinstance(item.data, PcbArcGeometry):
            segment = _arc_profile_segment(item.data)
            if segment is not None:
                segments.append(segment)
        elif isinstance(item.data, PcbPolygonGeometry):
            path = _polygon_svg_path_d(item.data)
            if path:
                polygon_paths.append(path)

    paths = [path for path in _stitch_profile_segments(segments) if path]
    paths.extend(polygon_paths)
    return " ".join(paths)


def _line_profile_segment(line: PcbLineGeometry) -> _ProfilePathSegment | None:
    start = (line.start_x, line.start_y)
    end = (line.end_x, line.end_y)
    if _points_equal(start, end):
        return None
    return _ProfilePathSegment(
        start=start,
        end=end,
        forward=f"L {end[0]:.4f} {end[1]:.4f}",
        reverse=f"L {start[0]:.4f} {start[1]:.4f}",
    )


def _arc_profile_segment(arc: PcbArcGeometry) -> _ProfilePathSegment | None:
    start = (arc.start_x, arc.start_y)
    end = (arc.end_x, arc.end_y)
    if _points_equal(start, end):
        return None
    forward = _arc_profile_command(arc, reverse=False)
    reverse = _arc_profile_command(arc, reverse=True)
    if not forward or not reverse:
        return None
    return _ProfilePathSegment(start=start, end=end, forward=forward, reverse=reverse)


def _arc_profile_command(arc: PcbArcGeometry, *, reverse: bool) -> str:
    cx, cy, radius = arc_center_from_three_points(
        arc.start_x,
        arc.start_y,
        arc.mid_x,
        arc.mid_y,
        arc.end_x,
        arc.end_y,
    )
    if not all(math.isfinite(value) for value in (cx, cy, radius)) or radius <= 0:
        return ""
    sweep = arc_sweep_angle(
        arc.start_x,
        arc.start_y,
        arc.mid_x,
        arc.mid_y,
        arc.end_x,
        arc.end_y,
        cx,
        cy,
    )
    if not math.isfinite(sweep) or math.isclose(sweep, 0.0, abs_tol=1e-9):
        return ""
    target = (arc.start_x, arc.start_y) if reverse else (arc.end_x, arc.end_y)
    effective_sweep = -sweep if reverse else sweep
    large_arc = 1 if abs(effective_sweep) > 180.0 else 0
    sweep_flag = 1 if effective_sweep > 0 else 0
    return f"A {radius:.4f} {radius:.4f} 0 {large_arc} {sweep_flag} {target[0]:.4f} {target[1]:.4f}"


def _stitch_profile_segments(segments: list[_ProfilePathSegment]) -> tuple[str, ...]:
    unused = list(segments)
    paths: list[str] = []
    while unused:
        segment = unused.pop(0)
        contour = [
            _OrientedProfilePathSegment(
                start=segment.start,
                end=segment.end,
                command=segment.forward,
            )
        ]
        extended = True
        while extended:
            extended = False
            start = contour[0].start
            end = contour[-1].end
            for index, candidate in enumerate(unused):
                if _points_equal(end, candidate.start):
                    contour.append(
                        _OrientedProfilePathSegment(
                            start=candidate.start,
                            end=candidate.end,
                            command=candidate.forward,
                        )
                    )
                    _ = unused.pop(index)
                    extended = True
                    break
                if _points_equal(end, candidate.end):
                    contour.append(
                        _OrientedProfilePathSegment(
                            start=candidate.end,
                            end=candidate.start,
                            command=candidate.reverse,
                        )
                    )
                    _ = unused.pop(index)
                    extended = True
                    break
                if _points_equal(start, candidate.end):
                    contour.insert(
                        0,
                        _OrientedProfilePathSegment(
                            start=candidate.start,
                            end=candidate.end,
                            command=candidate.forward,
                        ),
                    )
                    _ = unused.pop(index)
                    extended = True
                    break
                if _points_equal(start, candidate.start):
                    contour.insert(
                        0,
                        _OrientedProfilePathSegment(
                            start=candidate.end,
                            end=candidate.start,
                            command=candidate.reverse,
                        ),
                    )
                    _ = unused.pop(index)
                    extended = True
                    break
        start = contour[0].start
        end = contour[-1].end
        if not _points_equal(start, end):
            continue
        commands = [f"M {start[0]:.4f} {start[1]:.4f}"]
        commands.extend(item.command for item in contour)
        commands.append("Z")
        paths.append(" ".join(commands))
    return tuple(paths)


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
        if len(contour) < 3 or not _points_equal(contour[0], contour[-1]):
            continue
        contour = contour[:-1]
        if len(contour) >= 3:
            contours.append(tuple(contour))
    return tuple(contours)


def _points_equal(first: tuple[float, float], second: tuple[float, float]) -> bool:
    # Altium lines carry integer endpoints, while arcs carry integer center/radius
    # plus float angles and are reconstructed with trig. Endpoint differences can
    # exceed one Altium coordinate unit (2.54e-6 mm) before rendering transforms.
    return math.isclose(
        first[0],
        second[0],
        abs_tol=_PROFILE_ENDPOINT_TOLERANCE_MM,
    ) and math.isclose(
        first[1],
        second[1],
        abs_tol=_PROFILE_ENDPOINT_TOLERANCE_MM,
    )


def _line_svg_path_d(line: PcbLineGeometry) -> str:
    return _stroked_polyline_path_d(
        ((line.start_x, line.start_y), (line.end_x, line.end_y)),
        line.width,
    )


def _arc_svg_path_d(arc: PcbArcGeometry) -> str:
    arc_path = _stroked_arc_svg_path_d(arc)
    if arc_path:
        return arc_path
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


def _stroked_arc_svg_path_d(arc: PcbArcGeometry) -> str:
    if arc.width <= 0:
        return ""
    cx, cy, radius = arc_center_from_three_points(
        arc.start_x,
        arc.start_y,
        arc.mid_x,
        arc.mid_y,
        arc.end_x,
        arc.end_y,
    )
    if not all(math.isfinite(value) for value in (cx, cy, radius)) or radius <= 0:
        return ""

    half_width = arc.width / 2.0
    outer_radius = radius + half_width
    inner_radius = radius - half_width
    if inner_radius <= 0:
        return ""

    sweep = arc_sweep_angle(
        arc.start_x,
        arc.start_y,
        arc.mid_x,
        arc.mid_y,
        arc.end_x,
        arc.end_y,
        cx,
        cy,
    )
    if not math.isfinite(sweep) or math.isclose(sweep, 0.0, abs_tol=1e-9):
        return ""

    start_angle = math.atan2(arc.start_y - cy, arc.start_x - cx)
    end_angle = math.atan2(arc.end_y - cy, arc.end_x - cx)
    outer_start = _arc_point(cx, cy, outer_radius, start_angle)
    outer_end = _arc_point(cx, cy, outer_radius, end_angle)
    inner_end = _arc_point(cx, cy, inner_radius, end_angle)
    inner_start = _arc_point(cx, cy, inner_radius, start_angle)
    large_arc = 1 if abs(sweep) > 180.0 else 0
    sweep_flag = 1 if sweep > 0 else 0
    inner_sweep_flag = 0 if sweep > 0 else 1

    return " ".join(
        (
            f"M {outer_start[0]:.4f} {outer_start[1]:.4f}",
            f"A {outer_radius:.4f} {outer_radius:.4f} 0 {large_arc} {sweep_flag} "
            f"{outer_end[0]:.4f} {outer_end[1]:.4f}",
            f"L {inner_end[0]:.4f} {inner_end[1]:.4f}",
            f"A {inner_radius:.4f} {inner_radius:.4f} 0 {large_arc} {inner_sweep_flag} "
            f"{inner_start[0]:.4f} {inner_start[1]:.4f}",
            "Z",
        )
    )


def _arc_point(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def _circle_svg_path_d(circle: PcbCircleGeometry) -> str:
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


def _pad_drill_path_d(pad: PcbPadGeometry) -> str:
    geometry = pad_drill_geometry(pad)
    if geometry is None or geometry.is_empty:
        return ""
    return " ".join(_geometry_to_svg_path_parts(geometry))


def _pad_drill_symbol_path_d(pad: PcbPadGeometry) -> str:
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


def _outline_payload(payload: object) -> list[DomainPcbGeometry] | None:
    if not isinstance(payload, tuple):
        return None
    payload_tuple = cast("tuple[object, ...]", payload)
    outline: list[DomainPcbGeometry] = []
    for item in payload_tuple:
        if not isinstance(item, DomainPcbGeometry):
            return None
        outline.append(item)
    return outline


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


_GRAPHIC_RENDER_ROLES = frozenset(
    {
        PcbGeometryRole.SILKSCREEN.value,
        PcbGeometryRole.FABRICATION.value,
        PcbGeometryRole.COMPONENT_BODY.value,
        PcbGeometryRole.SOLDER_MASK.value,
        PcbGeometryRole.SOLDER_PASTE.value,
        PcbGeometryRole.MECHANICAL.value,
        PcbGeometryRole.EDGE.value,
        PcbGeometryRole.COURTYARD.value,
        PcbGeometryRole.DESIGNATOR.value,
        PcbGeometryRole.VALUE.value,
    }
)


def _is_line_renderable(item: RenderableItem) -> bool:
    return item.shape == PcbGeometryShape.LINE and item.display_role in _GRAPHIC_RENDER_ROLES


def _is_arc_renderable(item: RenderableItem) -> bool:
    return item.shape == PcbGeometryShape.ARC and item.display_role in _GRAPHIC_RENDER_ROLES


def _is_circle_renderable(item: RenderableItem) -> bool:
    return item.shape == PcbGeometryShape.CIRCLE and item.display_role in _GRAPHIC_RENDER_ROLES


def svg_primitives_from_geometry(
    geometry: BaseGeometry,
    *,
    source_ids: Iterable[str],
    source_layers: Iterable[str],
    kind: str,
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
