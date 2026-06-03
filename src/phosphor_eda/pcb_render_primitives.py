"""SVG primitive models and conversion helpers for PCB rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon

from phosphor_eda.pcb_render_skia import geometry_to_skia_artwork, skia_path_to_svg_d

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from shapely.coords import CoordinateSequence
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb_render_geometry import GeometryKind, GeometryTags, RenderableGeometry


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
    if artwork is None:
        return None
    d = skia_path_to_svg_d(artwork.path)
    if not d:
        return None
    return SvgPrimitive(
        d=d,
        source_id=item.id,
        source_layer=target_layer_name,
        kind=item.kind,
        tags=item.tags,
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
