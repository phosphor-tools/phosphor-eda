"""Shared spatial data structures for schematic wire connectivity.

Used across the schematic parsers and resolvers:

- ``UnionFind`` — generic disjoint-set with an explicit, strict membership API
- ``WireIndex`` — axis-aligned segment index with binary search (Altium)
- ``point_on_segment`` — axis-aligned point-on-segment test
"""

from __future__ import annotations

import bisect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.formats.altium.records import BusRec, WireRec

    AxisAlignedRec = WireRec | BusRec


class UnionFind[T]:
    """Generic union-find (disjoint set) with path compression.

    Membership is explicit: an item must be added — via the constructor or
    :meth:`add` — before it can be looked up. :meth:`find` and :meth:`union`
    raise ``KeyError`` for unknown items so a stray lookup surfaces instead of
    silently creating a singleton. Callers that want auto-insert semantics add
    the item first. The constructor rejects duplicate items.

    ``union(a, b)`` keeps ``a``'s root as the merged set's representative.
    """

    _parent: dict[T, T]
    _ordered: list[T]

    def __init__(self, items: Iterable[T] = ()) -> None:
        self._parent = {}
        self._ordered = []
        for item in items:
            if item in self._parent:
                msg = f"duplicate union-find item: {item!r}"
                raise ValueError(msg)
            self._parent[item] = item
            self._ordered.append(item)

    def __contains__(self, item: T) -> bool:
        return item in self._parent

    def add(self, item: T) -> None:
        """Add ``item`` as its own singleton set if not already present."""
        if item not in self._parent:
            self._parent[item] = item
            self._ordered.append(item)

    def find(self, item: T) -> T:
        """Return the representative of ``item``'s set (raises if unknown)."""
        if item not in self._parent:
            msg = f"unknown union-find item: {item!r}"
            raise KeyError(msg)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        current = item
        while self._parent[current] != current:
            parent = self._parent[current]
            self._parent[current] = root  # path compression
            current = parent
        return root

    def union(self, a: T, b: T) -> bool:
        """Merge the sets of ``a`` and ``b``; ``a``'s root wins.

        Returns whether the two were in different sets (i.e. state changed).
        """
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a == root_b:
            return False
        self._parent[root_b] = root_a
        return True

    def groups(self) -> dict[T, list[T]]:
        """Return known items grouped by their current representative.

        Groups and their members keep insertion order.
        """
        grouped: dict[T, list[T]] = {}
        for item in self._ordered:
            grouped.setdefault(self.find(item), []).append(item)
        return grouped


# ---------------------------------------------------------------------------
# Segment stored in a WireIndex row/column bucket
# ---------------------------------------------------------------------------


class _Segment:
    """A wire segment projected onto one axis for binary search."""

    __slots__: tuple[str, ...] = ("lo", "hi", "wire", "seg_idx")

    lo: int
    hi: int
    wire: AxisAlignedRec
    seg_idx: int

    def __init__(self, lo: int, hi: int, wire: AxisAlignedRec, seg_idx: int) -> None:
        self.lo = lo
        self.hi = hi
        self.wire = wire
        self.seg_idx = seg_idx


class WireIndex:
    """Axis-aligned wire segment index.

    Indexes horizontal segments by Y coordinate and vertical segments by
    X coordinate. Point-on-segment queries use binary search instead of
    iterating all segments.
    """

    def __init__(self, wires: Iterable[AxisAlignedRec]) -> None:
        # _by_row[y] = sorted list of (x_min, x_max, wire, seg_idx)
        self._by_row: dict[int, list[_Segment]] = {}
        # _by_col[x] = sorted list of (y_min, y_max, wire, seg_idx)
        self._by_col: dict[int, list[_Segment]] = {}

        for wire in wires:
            for si, ((x1, y1), (x2, y2)) in enumerate(wire.segments):
                if y1 == y2:
                    # Horizontal segment
                    lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
                    seg = _Segment(lo, hi, wire, si)
                    self._by_row.setdefault(y1, []).append(seg)
                elif x1 == x2:
                    # Vertical segment
                    lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
                    seg = _Segment(lo, hi, wire, si)
                    self._by_col.setdefault(x1, []).append(seg)
                # Diagonal segments are ignored (Altium wires are axis-aligned)

        # Sort each bucket by lo for binary search
        for segs in self._by_row.values():
            segs.sort(key=lambda s: s.lo)
        for segs in self._by_col.values():
            segs.sort(key=lambda s: s.lo)

    def segments_touching(
        self,
        x: int,
        y: int,
    ) -> list[tuple[AxisAlignedRec, int]]:
        """Find all wire segments that contain the point (x, y).

        Returns a list of (wire, segment_index) tuples.
        """
        result: list[tuple[AxisAlignedRec, int]] = []
        self._query_axis(self._by_row.get(y), x, result)
        self._query_axis(self._by_col.get(x), y, result)
        return result

    @staticmethod
    def _query_axis(
        bucket: list[_Segment] | None,
        val: int,
        out: list[tuple[AxisAlignedRec, int]],
    ) -> None:
        """Find segments in a sorted bucket where lo <= val <= hi."""
        if not bucket:
            return
        # All segments with lo <= val are at indices < bisect_right position
        idx = bisect.bisect_right(bucket, val, key=lambda s: s.lo)
        for i in range(idx):
            seg = bucket[i]
            if seg.hi >= val:
                out.append((seg.wire, seg.seg_idx))

    def point_on_any_segment(self, x: int, y: int) -> bool:
        """Check if (x, y) lies on any indexed wire segment."""
        return len(self.segments_touching(x, y)) > 0


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
