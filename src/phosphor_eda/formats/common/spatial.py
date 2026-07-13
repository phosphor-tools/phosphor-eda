"""Shared spatial data structures for schematic wire connectivity.

Used by both the Altium and KiCad parsers:

- ``UnionFind`` — generic union-find for connectivity grouping
- ``point_on_segment`` — axis-aligned point-on-segment test
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


class UnionFind(dict[T, T]):
    """Generic union-find with path compression and union by arbitrary root.

    Subclasses dict so ``item in uf`` works for checking membership.
    """

    def find(self, item: T) -> T:
        if item not in self:
            self[item] = item
        while self[item] != item:
            self[item] = self[self[item]]  # path compression
            item = self[item]
        return item

    def union(self, a: T, b: T) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self[ra] = rb


def point_on_segment(
    point: tuple[float, float],
    seg_start: tuple[float, float],
    seg_end: tuple[float, float],
    tol: float = 0.0,
) -> bool:
    """Check if a point lies on a horizontal or vertical line segment.

    With ``tol=0.0`` (the default) the test is exact, suitable for integer
    Altium coordinates. KiCad passes ``tol=0.01`` to absorb float rounding.
    Diagonal segments are never matched — both producers emit axis-aligned
    wires only.
    """
    px, py = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    if abs(y1 - y2) <= tol and abs(py - y1) <= tol:
        return min(x1, x2) - tol <= px <= max(x1, x2) + tol
    if abs(x1 - x2) <= tol and abs(px - x1) <= tol:
        return min(y1, y2) - tol <= py <= max(y1, y2) + tol
    return False
