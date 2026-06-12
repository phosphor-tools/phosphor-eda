"""KiCad ``(title_block …)`` parsing for schematic sheets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.domain.schematic import TitleBlock

if TYPE_CHECKING:
    from phosphor_eda.formats.kicad.sexp import SExpNode


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
            block.title = str(item[1])
        elif tag == "rev":
            block.revision = str(item[1])
        elif tag == "date":
            block.date = str(item[1])
        elif tag == "company":
            block.company = str(item[1])
        elif tag == "comment" and len(item) > 2:
            block.comments[str(item[1])] = str(item[2])
    return block
