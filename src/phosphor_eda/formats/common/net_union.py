"""Union-find helper for resolver-local net grouping."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class NetUnion:
    """Track equivalence classes for a fixed set of net IDs."""

    _parent: dict[str, str]
    _ids: list[str]

    def __init__(self, ids: Iterable[str]) -> None:
        self._ids = []
        self._parent = {}
        for id_ in ids:
            if id_ in self._parent:
                msg = f"Duplicate net id: {id_}"
                raise ValueError(msg)
            self._ids.append(id_)
            self._parent[id_] = id_

    def find(self, id_: str) -> str:
        """Return the representative ID for a known net ID."""
        self._require_known(id_)

        root = id_
        while self._parent[root] != root:
            root = self._parent[root]

        current = id_
        while self._parent[current] != current:
            parent = self._parent[current]
            self._parent[current] = root
            current = parent

        return root

    def union(self, left: str, right: str) -> bool:
        """Merge two known IDs and return whether this changed the grouping."""
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False

        self._parent[right_root] = left_root
        return True

    def groups(self) -> dict[str, list[str]]:
        """Return known IDs grouped by their current representative."""
        grouped: dict[str, list[str]] = {}
        for id_ in self._ids:
            root = self.find(id_)
            grouped.setdefault(root, []).append(id_)
        return grouped

    def _require_known(self, id_: str) -> None:
        if id_ not in self._parent:
            msg = f"Unknown net id: {id_}"
            raise KeyError(msg)
