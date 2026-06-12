"""Unit tests for the KiCad S-expression accessor helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sexpdata

import phosphor_eda.formats.kicad.sexp as sexp

if TYPE_CHECKING:
    from phosphor_eda.formats.kicad.sexp import SExpNode


def _parse(text: str) -> SExpNode:
    return list(sexpdata.loads(text))


def test_find_num_reads_value() -> None:
    node = _parse("(item (width 0.25))")
    assert sexp.find_num(node, "width") == 0.25


def test_find_num_default_when_missing() -> None:
    node = _parse("(item)")
    assert sexp.find_num(node, "width") == 0.0
    assert sexp.find_num(node, "width", 1.5) == 1.5


def test_find_num_int_coerced_to_float() -> None:
    node = _parse("(item (count 3))")
    result = sexp.find_num(node, "count")
    assert result == 3.0
    assert isinstance(result, float)


def test_find_str_reads_symbol_value() -> None:
    node = _parse("(item (type signal))")
    assert sexp.find_str(node, "type") == "signal"


def test_find_str_reads_quoted_string() -> None:
    node = _parse('(item (material "FR4"))')
    assert sexp.find_str(node, "material") == "FR4"


def test_find_str_default_when_missing() -> None:
    node = _parse("(item)")
    assert sexp.find_str(node, "type") == ""
    assert sexp.find_str(node, "type", "none") == "none"


def test_find_path_chases_nested_tags() -> None:
    node = _parse("(item (effects (font (size 1.0 1.0))))")
    size = sexp.find_path(node, "effects", "font", "size")
    assert size is not None
    assert sexp.num(size, 1) == 1.0


def test_find_path_returns_none_on_broken_chain() -> None:
    node = _parse("(item (effects (font)))")
    assert sexp.find_path(node, "effects", "font", "size") is None
    assert sexp.find_path(node, "effects", "missing", "size") is None
