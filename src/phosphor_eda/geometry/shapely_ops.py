"""Precision-aware Shapely helpers for PCB geometry.

Parsed PCB coordinates are faithful board-space millimetres, but topology
operations need a small precision grid because EDA file formats often store
integer database units that become near-identical floats after conversion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import shapely  # pyright: ignore[reportUnknownVariableType]
from shapely import GeometryCollection, LineString, Polygon
from shapely.errors import GEOSException
from shapely.ops import polygonize, unary_union
from shapely.validation import make_valid

if TYPE_CHECKING:
    from collections.abc import Iterable

    from shapely.geometry.base import BaseGeometry

PCB_GEOMETRY_GRID_MM = 1e-5


def normalize_geometry(geometry: BaseGeometry) -> BaseGeometry:
    """Snap geometry to the PCB topology grid and repair invalid output."""
    if geometry.is_empty:
        return geometry
    repaired = geometry if geometry.is_valid else make_valid(geometry)
    try:
        snapped = shapely.set_precision(repaired, PCB_GEOMETRY_GRID_MM)  # pyright: ignore[reportUnknownMemberType]
        return snapped if snapped.is_valid else make_valid(snapped)
    except GEOSException:
        return repaired


def robust_union(
    geometries: Iterable[BaseGeometry],
    *,
    prefer_disjoint_subsets: bool = False,
) -> BaseGeometry:
    """Union geometries after applying the PCB topology grid."""
    geometry_tuple = tuple(geometry for geometry in geometries if not geometry.is_empty)
    if not geometry_tuple:
        return GeometryCollection()
    if len(geometry_tuple) == 1:
        return normalize_geometry(geometry_tuple[0])
    if prefer_disjoint_subsets:
        try:
            valid_geometries = tuple(
                geometry if geometry.is_valid else make_valid(geometry)
                for geometry in geometry_tuple
            )
            return normalize_geometry(
                shapely.disjoint_subset_union_all(  # pyright: ignore[reportUnknownMemberType]
                    valid_geometries
                )
            )
        except GEOSException:
            pass
    try:
        return normalize_geometry(unary_union(tuple(normalize_geometry(g) for g in geometry_tuple)))
    except GEOSException:
        return normalize_geometry(unary_union(tuple(make_valid(g) for g in geometry_tuple)))


def robust_polygonize(lines: Iterable[LineString]) -> Polygon | None:
    """Polygonize near-closed linework after snapping to the PCB topology grid."""
    line_tuple = tuple(line for line in lines if not line.is_empty)
    if not line_tuple:
        return None
    merged = robust_union(line_tuple)
    polygons = tuple(polygonize(merged))
    if not polygons:
        return None
    return max(polygons, key=lambda polygon: polygon.area)
