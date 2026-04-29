"""Tests for Altium bus notation parsing and bus-typed port expansion.

Bus notation like ``D[0..7]`` expands into individual signal names
(D0, D1, ..., D7). Bus-typed sheet entries and ports use this expansion
to create per-member Port objects for cross-sheet connectivity.
"""

from phosphor_eda.altium._helpers import parse_bus_notation
from phosphor_eda.altium.records import AltiumRecord, WireRec
from phosphor_eda.altium.sheet_builder import SheetRecords
from phosphor_eda.altium.spatial import WireIndex


def _make_sheet(name: str, records: list[AltiumRecord]) -> SheetRecords:
    """Build a SheetRecords from a flat record list (no parent/child linking)."""
    wire_recs = [r for r in records if isinstance(r, WireRec)]
    return SheetRecords(
        name=name,
        records=records,
        children={},
        wire_index=WireIndex(wire_recs),
    )


# ---------------------------------------------------------------------------
# parse_bus_notation — notation expansion
# ---------------------------------------------------------------------------


def test_simple_ascending_range():
    assert parse_bus_notation("D[0..7]") == [
        "D0",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
    ]


def test_simple_descending_range():
    assert parse_bus_notation("D[7..0]") == [
        "D7",
        "D6",
        "D5",
        "D4",
        "D3",
        "D2",
        "D1",
        "D0",
    ]


def test_wide_range():
    result = parse_bus_notation("A[0..15]")
    assert result is not None
    assert len(result) == 16
    assert result[0] == "A0"
    assert result[15] == "A15"


def test_single_element_range():
    assert parse_bus_notation("CLK[3..3]") == ["CLK3"]


def test_mixed_range_and_plain():
    assert parse_bus_notation("D[0..3],CLK,RESET") == [
        "D0",
        "D1",
        "D2",
        "D3",
        "CLK",
        "RESET",
    ]


def test_multiple_ranges():
    assert parse_bus_notation("D[0..1],E[0..1]") == ["D0", "D1", "E0", "E1"]


def test_no_prefix():
    """Range with empty prefix — valid Altium notation."""
    assert parse_bus_notation("[0..2]") == ["0", "1", "2"]


def test_plain_name_returns_none():
    assert parse_bus_notation("CLK") is None


def test_empty_string_returns_none():
    assert parse_bus_notation("") is None


def test_plain_names_comma_separated_returns_none():
    """Comma-separated plain names without ranges are NOT bus notation."""
    assert parse_bus_notation("CLK,RESET") is None


# ---------------------------------------------------------------------------
# Bus-typed sheet entry/port expansion in resolve_nets + build_page
# ---------------------------------------------------------------------------


def test_bus_sheet_entry_skipped_in_resolve_nets():
    """A sheet entry with bus notation should not name a wire group 'D[0..7]'."""
    from phosphor_eda.altium.records import RecordType, SheetEntryRec, SheetSymbolRec

    # Wire at y=100 from x=0 to x=200
    wire = WireRec(
        record_type=RecordType.WIRE,
        index=0,
        owner_index=-1,
        points=[(0, 100), (200, 100)],
    )
    # Sheet symbol at (200, 120) with size 100x40
    sym = SheetSymbolRec(
        record_type=RecordType.SHEET_SYMBOL,
        index=1,
        owner_index=-1,
        location=(200, 120),
        x_size=100,
        y_size=40,
    )
    # Bus-typed sheet entry on left side, coord at wire endpoint
    entry = SheetEntryRec(
        record_type=RecordType.SHEET_ENTRY,
        index=2,
        owner_index=1,
        name="D[0..7]",
        side=0,
        distance_from_top=200,
        coord=(200, 100),
    )

    sheet = _make_sheet("Test", [wire, sym, entry])
    from phosphor_eda.altium.sheet_builder import resolve_nets

    coord_to_net, _nc = resolve_nets(sheet)

    # The wire group should NOT be named "D[0..7]"
    net_at_wire = coord_to_net.get((0, 100))
    assert net_at_wire is not None
    assert "D[0..7]" not in net_at_wire


def test_bus_port_skipped_in_resolve_nets():
    """A port with bus notation should not name a wire group 'D[0..7]'."""
    from phosphor_eda.altium.records import PortRec, RecordType

    wire = WireRec(
        record_type=RecordType.WIRE,
        index=0,
        owner_index=-1,
        points=[(0, 100), (200, 100)],
    )
    port = PortRec(
        record_type=RecordType.PORT,
        index=1,
        owner_index=-1,
        name="D[0..7]",
        location=(200, 100),
        width=50,
    )

    sheet = _make_sheet("Test", [wire, port])
    from phosphor_eda.altium.sheet_builder import resolve_nets

    coord_to_net, _nc = resolve_nets(sheet)

    net_at_wire = coord_to_net.get((0, 100))
    assert net_at_wire is not None
    assert "D[0..7]" not in net_at_wire


def test_bus_sheet_entry_expands_to_member_ports():
    """A bus-typed sheet entry creates individual Port objects for each member."""
    from phosphor_eda.altium.records import (
        NetLabelRec,
        RecordType,
        SheetEntryRec,
        SheetSymbolRec,
    )
    from phosphor_eda.altium.sheet_builder import build_page, resolve_nets

    # Individual wires with labels D0, D1 on this sheet
    wire0 = WireRec(
        record_type=RecordType.WIRE,
        index=0,
        owner_index=-1,
        points=[(0, 100), (100, 100)],
    )
    wire1 = WireRec(
        record_type=RecordType.WIRE,
        index=1,
        owner_index=-1,
        points=[(0, 200), (100, 200)],
    )
    label0 = NetLabelRec(
        record_type=RecordType.NET_LABEL,
        index=2,
        owner_index=-1,
        location=(50, 100),
        text="D0",
    )
    label1 = NetLabelRec(
        record_type=RecordType.NET_LABEL,
        index=3,
        owner_index=-1,
        location=(50, 200),
        text="D1",
    )
    # Sheet symbol with bus entry "D[0..1]"
    sym = SheetSymbolRec(
        record_type=RecordType.SHEET_SYMBOL,
        index=4,
        owner_index=-1,
        location=(300, 300),
        x_size=100,
        y_size=40,
    )
    entry = SheetEntryRec(
        record_type=RecordType.SHEET_ENTRY,
        index=5,
        owner_index=4,
        name="D[0..1]",
        coord=(300, 250),  # Not touching any wire — typical for bus entries
    )

    sheet = _make_sheet("Parent", [wire0, wire1, label0, label1, sym, entry])
    coord_to_net, _nc = resolve_nets(sheet)
    page = build_page(sheet, coord_to_net, {}, {})

    # Should have ports for D0 and D1 (expanded from D[0..1])
    port_names = [p.name for p in page.ports]
    assert "D0" in port_names
    assert "D1" in port_names


def test_bus_port_expands_to_member_ports():
    """A bus-typed port creates individual Port objects for each member."""
    from phosphor_eda.altium.records import NetLabelRec, PortRec, RecordType
    from phosphor_eda.altium.sheet_builder import build_page, resolve_nets

    wire0 = WireRec(
        record_type=RecordType.WIRE,
        index=0,
        owner_index=-1,
        points=[(0, 100), (100, 100)],
    )
    wire1 = WireRec(
        record_type=RecordType.WIRE,
        index=1,
        owner_index=-1,
        points=[(0, 200), (100, 200)],
    )
    label0 = NetLabelRec(
        record_type=RecordType.NET_LABEL,
        index=2,
        owner_index=-1,
        location=(50, 100),
        text="D0",
    )
    label1 = NetLabelRec(
        record_type=RecordType.NET_LABEL,
        index=3,
        owner_index=-1,
        location=(50, 200),
        text="D1",
    )
    # Bus port "D[0..1]" — not touching any wire
    port = PortRec(
        record_type=RecordType.PORT,
        index=4,
        owner_index=-1,
        name="D[0..1]",
        location=(300, 300),
        width=50,
    )

    sheet = _make_sheet("Child", [wire0, wire1, label0, label1, port])
    coord_to_net, _nc = resolve_nets(sheet)
    page = build_page(sheet, coord_to_net, {}, {})

    port_names = [p.name for p in page.ports]
    assert "D0" in port_names
    assert "D1" in port_names


def test_bus_member_nets_get_bus_field():
    """Nets created via bus expansion should have their bus field set."""
    from phosphor_eda.altium.records import NetLabelRec, PortRec, RecordType
    from phosphor_eda.altium.sheet_builder import build_page, resolve_nets

    wire0 = WireRec(
        record_type=RecordType.WIRE,
        index=0,
        owner_index=-1,
        points=[(0, 100), (100, 100)],
    )
    label0 = NetLabelRec(
        record_type=RecordType.NET_LABEL,
        index=1,
        owner_index=-1,
        location=(50, 100),
        text="D0",
    )
    port = PortRec(
        record_type=RecordType.PORT,
        index=2,
        owner_index=-1,
        name="D[0..1]",
        location=(300, 300),
        width=50,
    )

    sheet = _make_sheet("Child", [wire0, label0, port])
    coord_to_net, _nc = resolve_nets(sheet)
    page = build_page(sheet, coord_to_net, {}, {})

    d0_ports = [p for p in page.ports if p.name == "D0"]
    assert len(d0_ports) == 1
    assert d0_ports[0].net.bus == "D[0..1]"
