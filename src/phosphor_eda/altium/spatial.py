"""Spatial data structures for Altium schematic records.

Provides efficient coordinate-based lookups for typed records:

- ``UnionFind`` — generic union-find for connectivity grouping
- ``PointIndex`` — hash-based point lookup
- ``WireIndex`` — axis-aligned segment index with binary search
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from typing import TypeVar

from phosphor_eda.altium.records import WireRec

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

    def groups(self) -> dict[T, list[T]]:
        """Return root → [members] for all items."""
        result: dict[T, list[T]] = {}
        for item in list(self):
            root = self.find(item)
            result.setdefault(root, []).append(item)
        return result


# ---------------------------------------------------------------------------
# Segment stored in a WireIndex row/column bucket
# ---------------------------------------------------------------------------

class _Segment:
    """A wire segment projected onto one axis for binary search."""

    __slots__ = ("lo", "hi", "wire", "seg_idx")

    def __init__(self, lo: int, hi: int, wire: WireRec, seg_idx: int) -> None:
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

    def __init__(self, wires: Iterable[WireRec]) -> None:
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
        self, x: int, y: int,
    ) -> list[tuple[WireRec, int]]:
        """Find all wire segments that contain the point (x, y).

        Returns a list of (wire, segment_index) tuples.
        """
        result: list[tuple[WireRec, int]] = []
        self._query_axis(self._by_row.get(y), x, result)
        self._query_axis(self._by_col.get(x), y, result)
        return result

    @staticmethod
    def _query_axis(
        bucket: list[_Segment] | None,
        val: int,
        out: list[tuple[WireRec, int]],
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
    px: int, py: int, x1: int, y1: int, x2: int, y2: int,
) -> bool:
    """Check if point (px,py) lies on the axis-aligned segment (x1,y1)-(x2,y2)."""
    if y1 == y2 == py:
        return min(x1, x2) <= px <= max(x1, x2)
    if x1 == x2 == px:
        return min(y1, y2) <= py <= max(y1, y2)
    return False
