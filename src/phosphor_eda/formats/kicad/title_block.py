"""KiCad ``(title_block …)`` parsing for schematic sheets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import phosphor_eda.formats.common.sexp as sexp
from phosphor_eda.domain.schematic import TitleBlock

if TYPE_CHECKING:
    from phosphor_eda.formats.common.sexp import SExpNode

_TITLE_BLOCK_PLACEHOLDERS = frozenset({"", "*", "~"})


def _clean_title_value(value: object) -> str:
    text = str(value).strip()
    return "" if text in _TITLE_BLOCK_PLACEHOLDERS else text


def parse_kicad_title_block(items: SExpNode) -> TitleBlock | None:
    """Parse a sheet's title block from its top-level S-expression items.

    Returns ``None`` when the sheet has no ``(title_block …)`` node.
    """
    node = sexp.find(items, "title_block")
    if node is None:
        return None
    block = TitleBlock()
    for item in node[1:]:
        tag = sexp.tag(item)
        if not isinstance(item, list) or len(item) < 2:
            continue
        if tag == "title":
            block.title = block.title or _clean_title_value(item[1])
        elif tag == "rev":
            block.revision = block.revision or _clean_title_value(item[1])
        elif tag == "date":
            block.date = block.date or _clean_title_value(item[1])
        elif tag == "company":
            block.organization = block.organization or _clean_title_value(item[1])
        elif tag == "comment" and len(item) > 2:
            value = _clean_title_value(item[2])
            if value:
                block.comments[str(item[1])] = value
    return block
