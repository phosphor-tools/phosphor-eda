"""The full board build shares one object graph across its extractors."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

import phosphor_eda.formats.allegro.build as allegro_build
import phosphor_eda.formats.allegro.copper as allegro_copper
import phosphor_eda.formats.allegro.graphics as allegro_graphics
from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.copper import extract_allegro_copper
from phosphor_eda.formats.allegro.graph import build_allegro_object_graph
from phosphor_eda.formats.allegro.graphics import extract_allegro_graphics
from phosphor_eda.formats.allegro.layers import AllegroLayerMap
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

if TYPE_CHECKING:
    from pytest import MonkeyPatch

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
BREAKOUT_BOARD = (
    UPSTREAM_FIXTURES
    / "opencellular/electronics/breakout"
    / "board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)


def _record(tag: int, key: int) -> AllegroRecord:
    return AllegroRecord(
        tag=tag,
        offset=0,
        end_offset=0,
        key=key,
        next_key=None,
        payload=MappingProxyType({}),
    )


def test_build_allegro_board_builds_object_graph_once(monkeypatch: MonkeyPatch) -> None:
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    calls = {"count": 0}
    real_build_graph = build_allegro_object_graph

    def counting_build_graph(record_set: AllegroRecordSet) -> object:
        calls["count"] += 1
        return real_build_graph(record_set)

    monkeypatch.setattr(allegro_build, "build_allegro_object_graph", counting_build_graph)
    monkeypatch.setattr(allegro_graphics, "build_allegro_object_graph", counting_build_graph)
    monkeypatch.setattr(allegro_copper, "build_allegro_object_graph", counting_build_graph)

    build_allegro_board(source, name=BREAKOUT_BOARD.stem)

    assert calls["count"] == 1


def test_shared_graph_diagnostics_counted_by_owner_not_each_extractor() -> None:
    """A shared graph's diagnostics belong to the caller, not to every extractor."""
    # Two records with the same object key make the graph emit a duplicate-key
    # diagnostic; tag 0x99 is unhandled so the extractors add none of their own.
    record_set = AllegroRecordSet(
        header=None,
        string_table=None,
        records=(_record(0x99, 5), _record(0x99, 5)),
        end_offset=0,
    )
    layer_map = AllegroLayerMap(layers=(), stackup=None, by_class_subclass={})
    graph = build_allegro_object_graph(record_set)
    assert graph.diagnostics

    shared_graphics = extract_allegro_graphics(record_set, layer_map, graph)
    shared_copper = extract_allegro_copper(record_set, layer_map, graph)
    assert shared_graphics.diagnostics == ()
    assert shared_copper.diagnostics == ()

    # A standalone extraction still self-reports the graph diagnostics it built.
    assert extract_allegro_graphics(record_set, layer_map).diagnostics == graph.diagnostics
    assert extract_allegro_copper(record_set, layer_map).diagnostics == graph.diagnostics
