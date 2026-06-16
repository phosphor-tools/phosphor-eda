"""KiCad local wire connectivity helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.formats.common.spatial import UnionFind, point_on_segment

if TYPE_CHECKING:
    from phosphor_eda.formats.kicad.sexp import SExpNode
    from phosphor_eda.formats.kicad.source import KiCadPoint


@dataclass(slots=True)
class WireGraph:
    uf: UnionFind[KiCadPoint]
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

    def touches_wire(self, point: KiCadPoint) -> bool:
        return point_touches_segments(point, self.segments)

    def find(self, point: KiCadPoint) -> KiCadPoint:
        return self.uf.find(point)

    def root_to_points(self) -> dict[KiCadPoint, set[KiCadPoint]]:
        return group_wire_points(self.uf, self.points)


@dataclass(slots=True)
class BusGraph:
    uf: UnionFind[KiCadPoint]
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

    def touches_bus(self, point: KiCadPoint) -> bool:
        return point_touches_segments(point, self.segments)

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


def build_bus_graph(data: SExpNode) -> BusGraph:
    graph = BusGraph(uf=UnionFind(), segments=[], points=set())

    for bus_node in sexp.find_all(data[1:], "bus"):
        pts_node = sexp.find(bus_node[1:], "pts")
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


def points_from_pts_node(pts_node: SExpNode) -> list[KiCadPoint]:
    return [
        (round(sexp.num(xy, 1), 4), round(sexp.num(xy, 2), 4))
        for xy in sexp.find_all(pts_node[1:], "xy")
    ]


def point_touches_segments(
    point: KiCadPoint,
    segments: list[tuple[KiCadPoint, KiCadPoint]],
) -> bool:
    return any(point_on_segment(point, start, end, tol=0.01) for start, end in segments)


def connect_point(
    uf: UnionFind[KiCadPoint],
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
        if point_on_segment(point, seg_start, seg_end, tol=0.01):
            uf.union(point, seg_start)
            if not merge_all:
                return


def group_wire_points(
    uf: UnionFind[KiCadPoint],
    wire_points: set[KiCadPoint],
) -> dict[KiCadPoint, set[KiCadPoint]]:
    root_to_points: dict[KiCadPoint, set[KiCadPoint]] = {}
    for point in wire_points:
        root_to_points.setdefault(uf.find(point), set()).add(point)
    return root_to_points
