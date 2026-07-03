"""Unit tests for the shared Allegro linked-list walker.

Allegro chains are circular linked lists that terminate by pointing back at
their owning record; the walker is the single place that encodes that
termination rule plus the cycle/missing/wrong-tag anomaly handling every
chain consumer shares.
"""

from __future__ import annotations

from types import MappingProxyType

from phosphor_eda.formats.allegro.graph import AllegroObjectGraph
from phosphor_eda.formats.allegro.records import AllegroPayloadValue, AllegroRecord


def _record(
    key: int,
    *,
    tag: int = 0x15,
    next_key: int | None = None,
    payload: dict[str, AllegroPayloadValue] | None = None,
) -> AllegroRecord:
    return AllegroRecord(
        tag=tag,
        offset=key,
        end_offset=key + 4,
        key=key,
        next_key=next_key,
        payload=MappingProxyType(payload or {}),
    )


def _graph(*records: AllegroRecord) -> AllegroObjectGraph:
    return AllegroObjectGraph(
        records=records,
        by_key=MappingProxyType({r.key: r for r in records if r.key is not None}),
        diagnostics=(),
    )


def test_walk_terminates_cleanly_at_owner_ring() -> None:
    graph = _graph(
        _record(10, tag=0x14),
        _record(1, next_key=2),
        _record(2, next_key=10),
    )

    walk = graph.walk_key_chain(head_key=1, owner_key=10)

    assert [r.key for r in walk.records] == [1, 2]
    assert walk.diagnostics == ()


def test_walk_reports_cycle() -> None:
    graph = _graph(_record(1, next_key=2), _record(2, next_key=1))

    walk = graph.walk_key_chain(head_key=1, owner_key=10)

    assert [r.key for r in walk.records] == [1, 2]
    assert [d.code for d in walk.diagnostics] == ["linked-list-cycle"]
    assert walk.diagnostics[0].reference_key == 1


def test_walk_reports_missing_record() -> None:
    graph = _graph(_record(1, next_key=99))

    walk = graph.walk_key_chain(head_key=1, owner_key=10)

    assert [r.key for r in walk.records] == [1]
    assert [d.code for d in walk.diagnostics] == ["unresolved-reference"]
    assert walk.diagnostics[0].reference_key == 99


def test_walk_reports_unexpected_tag_and_stops() -> None:
    graph = _graph(
        _record(1, next_key=2),
        _record(2, tag=0x30, next_key=3),
        _record(3),
    )

    walk = graph.walk_key_chain(head_key=1, owner_key=10, expected_tags=frozenset({0x15}))

    assert [r.key for r in walk.records] == [1]
    assert [d.code for d in walk.diagnostics] == ["unexpected-record-tag"]
    assert walk.diagnostics[0].reference_key == 2


def test_walk_guard_rejection_stops_chain() -> None:
    graph = _graph(
        _record(1, next_key=2, payload={"parent_key": 10}),
        _record(2, next_key=10, payload={"parent_key": 77}),
    )

    def owned_by_10(record: AllegroRecord) -> str | None:
        parent = record.payload.get("parent_key")
        return None if parent == 10 else f"owned by {parent}"

    walk = graph.walk_key_chain(head_key=1, owner_key=10, guard=owned_by_10)

    assert [r.key for r in walk.records] == [1]
    assert [d.code for d in walk.diagnostics] == ["chain-guard-rejected"]
    assert walk.diagnostics[0].message.endswith("owned by 77")
    assert walk.diagnostics[0].reference_key == 2


def test_walk_follows_payload_next_field() -> None:
    graph = _graph(
        _record(7, tag=0x2D),
        _record(1, tag=0x32, payload={"next_in_footprint_key": 2}),
        _record(2, tag=0x32, payload={"next_in_footprint_key": 7}),
    )

    def next_in_footprint(record: AllegroRecord) -> int:
        value = record.payload.get("next_in_footprint_key")
        return value if isinstance(value, int) else 0

    walk = graph.walk_key_chain(
        head_key=1,
        owner_key=7,
        expected_tags=frozenset({0x32}),
        next_key_of=next_in_footprint,
    )

    assert [r.key for r in walk.records] == [1, 2]
    assert walk.diagnostics == ()


def test_walk_tail_key_still_terminates() -> None:
    graph = _graph(_record(1, next_key=2), _record(2, next_key=3), _record(3))

    walk = graph.walk_key_chain(head_key=1, tail_key=2)

    assert [r.key for r in walk.records] == [1, 2]
    assert walk.diagnostics == ()


def test_walk_empty_head_returns_nothing() -> None:
    graph = _graph()

    walk = graph.walk_key_chain(head_key=0, owner_key=10)

    assert walk.records == ()
    assert walk.diagnostics == ()
