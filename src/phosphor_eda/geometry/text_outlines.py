"""Convert PCB-authored text into Shapely outline geometry."""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from fontTools.pens.basePen import BasePen  # pyright: ignore[reportMissingTypeStubs]
from fontTools.ttLib import TTFont  # pyright: ignore[reportMissingTypeStubs]
from shapely import GeometryCollection, Polygon
from shapely.affinity import rotate, scale, translate
from shapely.ops import unary_union

from phosphor_eda.geometry.fonts import INTER_REGULAR

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.domain.pcb import PcbText

_CURVE_STEPS = 8
_POINT_TOLERANCE = 1e-9


@functools.cache
def _font() -> TTFont:
    """Load the bundled Inter face once, lazily (not at import)."""
    return TTFont(INTER_REGULAR)


@functools.cache
def _glyph_set() -> object:
    return _font().getGlyphSet()


@functools.cache
def _cmap() -> dict[int, str]:
    return _font().getBestCmap() or {}


@functools.cache
def _units_per_em() -> float:
    head = _font()["head"]
    return float(head.unitsPerEm)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]


@dataclass(frozen=True)
class _TextSpec:
    text: str
    x: float
    y: float
    rotation: float
    font_size: float


class _OutlinePen(BasePen):
    def __init__(self) -> None:
        super().__init__(_glyph_set())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        self.contours: list[list[tuple[float, float]]] = []
        self._current: list[tuple[float, float]] = []
        self._position: tuple[float, float] | None = None

    @override
    def _moveTo(self, pt: tuple[float, float]) -> None:  # noqa: N802
        self._finish_current()
        point = _point(pt)
        self._current = [point]
        self._position = point

    @override
    def _lineTo(self, pt: tuple[float, float]) -> None:  # noqa: N802
        point = _point(pt)
        self._append_point(point)
        self._position = point

    @override
    def _qCurveToOne(  # noqa: N802
        self,
        pt1: tuple[float, float],
        pt2: tuple[float, float],
    ) -> None:
        start = self._position
        if start is None:
            self._lineTo(pt2)
            return
        control = _point(pt1)
        end = _point(pt2)
        for step in range(1, _CURVE_STEPS + 1):
            t = step / _CURVE_STEPS
            mt = 1.0 - t
            self._append_point(
                (
                    mt * mt * start[0] + 2.0 * mt * t * control[0] + t * t * end[0],
                    mt * mt * start[1] + 2.0 * mt * t * control[1] + t * t * end[1],
                )
            )
        self._position = end

    @override
    def _curveToOne(  # noqa: N802
        self,
        pt1: tuple[float, float],
        pt2: tuple[float, float],
        pt3: tuple[float, float],
    ) -> None:
        start = self._position
        if start is None:
            self._lineTo(pt3)
            return
        control_1 = _point(pt1)
        control_2 = _point(pt2)
        end = _point(pt3)
        for step in range(1, _CURVE_STEPS + 1):
            t = step / _CURVE_STEPS
            mt = 1.0 - t
            self._append_point(
                (
                    mt**3 * start[0]
                    + 3.0 * mt * mt * t * control_1[0]
                    + 3.0 * mt * t * t * control_2[0]
                    + t**3 * end[0],
                    mt**3 * start[1]
                    + 3.0 * mt * mt * t * control_1[1]
                    + 3.0 * mt * t * t * control_2[1]
                    + t**3 * end[1],
                )
            )
        self._position = end

    @override
    def _closePath(self) -> None:  # noqa: N802
        self._finish_current()

    @override
    def _endPath(self) -> None:  # noqa: N802
        self._finish_current()

    def finish(self) -> None:
        self._finish_current()

    def _finish_current(self) -> None:
        if len(self._current) >= 3:
            first = self._current[0]
            last = self._current[-1]
            if not _same_point(first, last):
                self._current.append(first)
            self.contours.append(self._current)
        self._current = []
        self._position = None

    def _append_point(self, point: tuple[float, float]) -> None:
        if not self._current or not _same_point(self._current[-1], point):
            self._current.append(point)


def text_outline_geometry(text: PcbText) -> BaseGeometry:
    """Return filled glyph outlines for a PCB text primitive in board units."""
    spec = _text_spec(text)
    if not spec.text or spec.font_size <= 0:
        return GeometryCollection()

    cmap = _cmap()
    space_glyph = cmap.get(ord(" "))
    glyph_geometries: list[BaseGeometry] = []
    cursor_x = 0.0
    for char in spec.text:
        glyph_name = cmap.get(ord(char))
        if glyph_name is None:
            cursor_x += _glyph_advance(space_glyph)
            continue
        glyph_geometry = _glyph_geometry(glyph_name)
        if not glyph_geometry.is_empty:
            glyph_geometries.append(translate(glyph_geometry, xoff=cursor_x))
        cursor_x += _glyph_advance(glyph_name)

    if not glyph_geometries:
        return GeometryCollection()

    geometry = unary_union(glyph_geometries)
    font_scale = spec.font_size / _units_per_em()
    geometry = scale(geometry, xfact=font_scale, yfact=-font_scale, origin=(0.0, 0.0))
    min_x, min_y, max_x, max_y = geometry.bounds
    geometry = translate(
        geometry,
        xoff=spec.x - (min_x + max_x) / 2.0,
        yoff=spec.y - (min_y + max_y) / 2.0,
    )
    if spec.rotation:
        geometry = rotate(geometry, spec.rotation, origin=(spec.x, spec.y), use_radians=False)
    return geometry


def _text_spec(text: PcbText) -> _TextSpec:
    return _TextSpec(
        text=text.text,
        x=text.x,
        y=text.y,
        rotation=text.rotation,
        font_size=text.font_size,
    )


def _glyph_geometry(glyph_name: str) -> BaseGeometry:
    glyph = _glyph_set()[glyph_name]  # pyright: ignore[reportUnknownVariableType, reportIndexIssue]
    pen = _OutlinePen()
    glyph.draw(pen)  # pyright: ignore[reportUnknownMemberType]
    pen.finish()
    return _contours_to_geometry(pen.contours)


def _glyph_advance(glyph_name: str | None) -> float:
    if glyph_name is None:
        return _units_per_em() * 0.5
    advance = _font()["hmtx"][glyph_name][0]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(advance, int | float):
        return float(advance)
    return _units_per_em() * 0.5


def _contours_to_geometry(contours: list[list[tuple[float, float]]]) -> BaseGeometry:
    polygons = [_clean_polygon(contour) for contour in contours]
    valid_polygons = [polygon for polygon in polygons if polygon is not None]
    if not valid_polygons:
        return GeometryCollection()

    outers: list[BaseGeometry] = []
    for polygon in valid_polygons:
        containing_count = sum(
            1
            for other in valid_polygons
            if other is not polygon and abs(float(other.area)) > abs(float(polygon.area))
            if other.contains(polygon.representative_point())
        )
        if containing_count % 2 == 0:
            geometry: BaseGeometry = polygon
            for hole in valid_polygons:
                if hole is polygon:
                    continue
                hole_container_count = sum(
                    1
                    for other in valid_polygons
                    if other is not hole and abs(float(other.area)) > abs(float(hole.area))
                    if other.contains(hole.representative_point())
                )
                if hole_container_count == containing_count + 1 and polygon.contains(
                    hole.representative_point()
                ):
                    geometry = geometry.difference(hole)
            outers.append(geometry)

    if not outers:
        return GeometryCollection()
    return unary_union(outers)


def _clean_polygon(contour: list[tuple[float, float]]) -> Polygon | None:
    polygon = Polygon(contour)
    if polygon.is_empty or math.isclose(float(polygon.area), 0.0, abs_tol=_POINT_TOLERANCE):
        return None
    if polygon.is_valid:
        return polygon
    cleaned = polygon.buffer(0)
    if cleaned.geom_type == "Polygon" and not cleaned.is_empty:
        return cleaned
    return None


def _point(pt: tuple[float, float]) -> tuple[float, float]:
    return float(pt[0]), float(pt[1])


def _same_point(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return math.isclose(first[0], second[0], abs_tol=_POINT_TOLERANCE) and math.isclose(
        first[1], second[1], abs_tol=_POINT_TOLERANCE
    )
