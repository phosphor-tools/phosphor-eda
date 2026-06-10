"""Shared S-expression helpers for KiCad parsers.

Used by both the schematic (to_schematic.py) and PCB (pcb_parser.py)
parsers to navigate the nested-list structure returned by sexpdata.
"""

from __future__ import annotations

import sexpdata

# S-expression element: sexpdata.loads returns nested lists of symbols,
# strings, ints, floats, and sub-lists.
type SExpItem = sexpdata.Symbol | str | int | float | list["SExpItem"]
type SExpNode = list[SExpItem]


def tag(item: object) -> str | None:
    """Return the tag name of an S-expression list, or None."""
    if isinstance(item, list) and item and isinstance(item[0], sexpdata.Symbol):
        return item[0].value()
    return None


def find(items: SExpNode, tag_name: str) -> SExpNode | None:
    """Find the first child with the given tag."""
    for item in items:
        if tag(item) == tag_name and isinstance(item, list):
            return item
    return None


def find_all(items: SExpNode, tag_name: str) -> list[SExpNode]:
    """Find all children with the given tag."""
    return [item for item in items if tag(item) == tag_name and isinstance(item, list)]


def val(item: SExpNode) -> str:
    """Return the string value of item[1]."""
    if len(item) > 1:
        v = item[1]
        return v.value() if isinstance(v, sexpdata.Symbol) else str(v)
    return ""


def find_property(items: SExpNode, name: str) -> str:
    """Get a named property value from S-expression children."""
    for item in items:
        if (
            tag(item) == "property"
            and isinstance(item, list)
            and len(item) > 2
            and str(item[1]) == name
        ):
            return str(item[2])
    return ""


def num(node: SExpNode, index: int) -> float:
    """Extract a numeric value from a sexp node at the given index."""
    v = node[index]
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v))


def find_num(items: SExpNode, tag_name: str, default: float = 0.0) -> float:
    """Find the first child with ``tag_name`` and return its item[1] as a float."""
    node = find(items, tag_name)
    return num(node, 1) if node else default


def find_str(items: SExpNode, tag_name: str, default: str = "") -> str:
    """Find the first child with ``tag_name`` and return its item[1] as a string."""
    node = find(items, tag_name)
    return val(node) if node else default


def find_path(items: SExpNode, *tags: str) -> SExpNode | None:
    """Chase a chain of tags down nested children, returning the final node."""
    node: SExpNode | None = items
    for tag_name in tags:
        if node is None:
            return None
        node = find(node, tag_name)
    return node
