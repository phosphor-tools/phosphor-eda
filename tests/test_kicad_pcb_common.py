"""Tests for shared KiCad PCB item-flag accessors."""

from __future__ import annotations

import sexpdata

from phosphor_eda.formats.kicad import pcb_common


def _item(text: str) -> list[object]:
    return sexpdata.loads(text)


def test_item_locked_reads_bare_symbol() -> None:
    assert pcb_common.item_locked(_item("(footprint locked)"))


def test_item_locked_reads_list_form_yes() -> None:
    assert pcb_common.item_locked(_item("(footprint (locked yes))"))


def test_item_locked_reads_list_form_no() -> None:
    assert not pcb_common.item_locked(_item("(footprint (locked no))"))


def test_item_locked_absent() -> None:
    assert not pcb_common.item_locked(_item("(footprint (layer F.Cu))"))


def test_item_hidden_reads_bare_symbol() -> None:
    assert pcb_common.item_hidden(_item("(fp_text hide)"))


def test_item_hidden_reads_list_form_yes() -> None:
    assert pcb_common.item_hidden(_item("(fp_text (hide yes))"))


def test_item_hidden_reads_list_form_no() -> None:
    assert not pcb_common.item_hidden(_item("(fp_text (hide no))"))
