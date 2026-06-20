from __future__ import annotations

from pathlib import Path

import pytest

from phosphor_eda.formats.allegro.graph import build_allegro_object_graph
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

FIXTURES = Path(__file__).resolve().parent / "fixtures"

COMMITTED_RECORD_INVENTORY = (
    (
        FIXTURES
        / "orcad"
        / "opencellular-breakout"
        / "allegro/OpenCellular/electronics/breakout/board"
        / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd",
        65_280,
        {0x06: 26, 0x1B: 105, 0x2A: 6, 0x2D: 80, 0x32: 595},
    ),
    (
        FIXTURES
        / "orcad"
        / "opencellular-sync"
        / "allegro/OpenCellular/electronics/sync/board"
        / "Fb_Connect1_SYNC_Life-3.brd",
        100_970,
        {0x06: 70, 0x1B: 189, 0x2A: 6, 0x2D: 286, 0x32: 1_109},
    ),
    (
        FIXTURES
        / "orcad"
        / "cp-smartgarden-launchxl-cc1310"
        / "Document/Hardware/mcu/swrc319/Cadence/Allegro"
        / "LAUNCHXL-CC1310.brd",
        62_648,
        {0x06: 68, 0x1B: 211, 0x2A: 6, 0x2D: 172, 0x32: 999},
    ),
    (
        FIXTURES
        / "orcad"
        / "rohm-stepper-driver-ctrl"
        / "Design Files for Rev 1.0"
        / "STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd",
        20_428,
        {0x06: 41, 0x1B: 127, 0x2A: 4, 0x2D: 176, 0x32: 658},
    ),
)


@pytest.mark.parametrize(
    ("path", "expected_record_count", "expected_tag_counts"),
    COMMITTED_RECORD_INVENTORY,
    ids=[path.name for path, *_ in COMMITTED_RECORD_INVENTORY],
)
def test_committed_allegro_record_inventory_is_locked(
    path: Path,
    expected_record_count: int,
    expected_tag_counts: dict[int, int],
) -> None:
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)

    assert len(record_set.records) == expected_record_count
    for tag, count in expected_tag_counts.items():
        assert record_set.tag_counts[tag] == count


def test_allegro_object_graph_indexes_records_and_walks_header_lists() -> None:
    path = COMMITTED_RECORD_INVENTORY[0][0]
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)

    graph = build_allegro_object_graph(record_set)
    walk = graph.walk_linked_list(record_set.header.linked_lists[5])

    assert graph.by_key[632_553_504].tag == 0x06
    assert len(walk.records) == record_set.tag_counts[0x1B]
    assert {record.tag for record in walk.records} == {0x1B}
    assert walk.diagnostics == ()


def test_allegro_object_graph_reports_dangling_and_cyclic_linked_lists() -> None:
    record_set = AllegroRecordSet(
        header=None,
        string_table=None,
        records=(
            AllegroRecord(tag=0x1B, offset=0, end_offset=12, key=10, next_key=20, payload={}),
            AllegroRecord(tag=0x1B, offset=12, end_offset=24, key=20, next_key=20, payload={}),
        ),
        end_offset=24,
    )

    graph = build_allegro_object_graph(record_set)
    cyclic = graph.walk_key_chain(head_key=10)
    dangling = graph.walk_key_chain(head_key=99)

    assert [record.key for record in cyclic.records] == [10, 20]
    assert [diagnostic.code for diagnostic in cyclic.diagnostics] == ["linked-list-cycle"]
    assert [diagnostic.code for diagnostic in dangling.diagnostics] == ["unresolved-reference"]
