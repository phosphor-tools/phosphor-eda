"""KiCad local wire connectivity helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.kicad import sexp

if TYPE_CHECKING:
    from phosphor_eda.kicad.sexp import SExpNode
    from phosphor_eda.kicad.source import KiCadPoint


class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[KiCadPoint, KiCadPoint] = {}

    def find(self, p: KiCadPoint) -> KiCadPoint:
        if p not in self._parent:
            self._parent[p] = p
        while self._parent[p] != p:
            self._parent[p] = self._parent[self._parent[p]]
            p = self._parent[p]
        return p

    def union(self, a: KiCadPoint, b: KiCadPoint) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


@dataclass(slots=True)
class WireGraph:
    uf: UnionFind
    segments: list[tuple[KiCadPoint, KiCadPoint]]
    points: set[KiCadPoint]

    def connect_point(self, point: KiCadPoint, *, merge_all: bool = False) -> None:
        connect_point(
            self.uf,
            point,
            self.segments,
            self.points,
            merge_all=merge_all,
        )

    def find(self, point: KiCadPoint) -> KiCadPoint:
        return self.uf.find(point)

    def root_to_points(self) -> dict[KiCadPoint, set[KiCadPoint]]:
        return group_wire_points(self.uf, self.points)


def build_wire_graph(data: SExpNode) -> WireGraph:
    graph = WireGraph(uf=UnionFind(), segments=[], points=set())

    for wire_node in sexp.find_all(data[1:], "wire"):
        pts_node = sexp.find(wire_node[1:], "pts")
        if pts_node is None:
            continue
        points: list[KiCadPoint] = []
        for xy in sexp.find_all(pts_node[1:], "xy"):
            points.append((round(sexp.num(xy, 1), 4), round(sexp.num(xy, 2), 4)))
        for index in range(len(points) - 1):
            graph.uf.union(points[index], points[index + 1])
            graph.segments.append((points[index], points[index + 1]))
            graph.points.add(points[index])
            graph.points.add(points[index + 1])

    for junc in sexp.find_all(data[1:], "junction"):
        at_node = sexp.find(junc[1:], "at")
        if at_node is not None:
            graph.connect_point(point_from_at(at_node), merge_all=True)

    return graph


def point_from_at(at_node: SExpNode) -> KiCadPoint:
    return round(sexp.num(at_node, 1), 4), round(sexp.num(at_node, 2), 4)


def point_on_segment(
    point: KiCadPoint,
    seg_start: KiCadPoint,
    seg_end: KiCadPoint,
    tol: float = 0.01,
) -> bool:
    """Check if a point lies on a horizontal or vertical line segment."""
    px, py = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    if abs(y1 - y2) < tol and abs(py - y1) < tol:
        lo, hi = (min(x1, x2) - tol, max(x1, x2) + tol)
        return lo <= px <= hi
    if abs(x1 - x2) < tol and abs(px - x1) < tol:
        lo, hi = (min(y1, y2) - tol, max(y1, y2) + tol)
        return lo <= py <= hi
    return False


def connect_point(
    uf: UnionFind,
    point: KiCadPoint,
    wire_segments: list[tuple[KiCadPoint, KiCadPoint]],
    wire_points: set[KiCadPoint],
    *,
    merge_all: bool = False,
) -> None:
    """Connect a point to the local wire network."""
    wire_points.add(point)
    for wp in wire_points:
        if wp != point and abs(wp[0] - point[0]) < 0.01 and abs(wp[1] - point[1]) < 0.01:
            uf.union(point, wp)
            if not merge_all:
                return
    for seg_start, seg_end in wire_segments:
        if point_on_segment(point, seg_start, seg_end):
            uf.union(point, seg_start)
            if not merge_all:
                return


def group_wire_points(
    uf: UnionFind,
    wire_points: set[KiCadPoint],
) -> dict[KiCadPoint, set[KiCadPoint]]:
    root_to_points: dict[KiCadPoint, set[KiCadPoint]] = {}
    for point in wire_points:
        root_to_points.setdefault(uf.find(point), set()).add(point)
    return root_to_points
