"""Tests for Altium bus notation parsing and bus-typed source names.

Bus notation like ``D[0..7]`` expands into individual signal names
(D0, D1, ..., D7). Bus-typed sheet entries and ports remain source
evidence and must not create aggregate-name public connectivity.
"""

from __future__ import annotations

from phosphor_eda.domain.schematic import BusKind, ScopeId
from phosphor_eda.formats.altium._helpers import parse_bus_notation
from phosphor_eda.formats.altium.enums import SheetEntrySide
from phosphor_eda.formats.altium.project import AltiumHierarchyMode, AltiumProject
from phosphor_eda.formats.altium.records import (
    AltiumRecord,
    BusEntryRec,
    BusRec,
    ComponentRec,
    DesignatorRec,
    NetLabelRec,
    PinRec,
    PortRec,
    RecordType,
    SheetEntryRec,
    SheetSymbolRec,
    WireRec,
)
from phosphor_eda.formats.altium.resolver import resolve_altium_source
from phosphor_eda.formats.altium.sheet_builder import SheetRecords, resolve_local_net_groups
from phosphor_eda.formats.altium.source import (
    AltiumGenericBusLine,
    AltiumHarnessMember,
    AltiumLocalNet,
    AltiumPinOccurrence,
    AltiumPort,
    AltiumSheetEntry,
    AltiumSheetSource,
    AltiumSheetSymbol,
    AltiumSourceDesign,
)
from phosphor_eda.formats.common.spatial import WireIndex


def _make_sheet(name: str, records: list[AltiumRecord]) -> SheetRecords:
    """Build a SheetRecords from a flat record list (no parent/child linking)."""
    wire_recs = [r for r in records if isinstance(r, WireRec)]
    bus_recs = [r for r in records if isinstance(r, BusRec)]
    return SheetRecords(
        name=name,
        records=records,
        children={},
        wire_index=WireIndex(wire_recs),
        bus_index=WireIndex(bus_recs),
    )


def _scope(name: str) -> ScopeId:
    return ScopeId(path=(name,))


def _bus_port(sheet: str, name: str, index: int = 1) -> AltiumPort:
    return AltiumPort(
        id=f"{sheet}:port:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=name,
        location=(index, 10),
        wire_coord=(index, 10),
        harness_type="",
        io_type=0,
        style=0,
    )


def _bus_entry(
    sheet: str,
    name: str,
    sheet_symbol_id: str,
    index: int = 1,
) -> AltiumSheetEntry:
    return AltiumSheetEntry(
        id=f"{sheet}:entry:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        sheet_symbol_id=sheet_symbol_id,
        name=name,
        coord=(index, 20),
        side=SheetEntrySide.LEFT,
        distance_from_top=0,
        harness_type="",
        io_type=0,
    )


def _harness_member(sheet: str, bus_name: str, signal_name: str) -> AltiumHarnessMember:
    return AltiumHarnessMember(
        id=f"{sheet}:harness:{bus_name}:{signal_name}",
        scope_id=_scope(sheet),
        source_index=1,
        connector_id=f"{sheet}:connector:{bus_name}",
        port_name=bus_name,
        name=signal_name,
        coord=(1, 25),
        side=SheetEntrySide.LEFT,
        distance_from_top=0,
    )


def _bus_symbol(sheet: str, child_source_file: str) -> AltiumSheetSymbol:
    return AltiumSheetSymbol(
        id=f"{sheet}:symbol:1",
        scope_id=_scope(sheet),
        source_index=1,
        name="child",
        child_source_file=child_source_file,
        location=(1, 30),
        x_size=100,
        y_size=100,
    )


def _bus_local_net(
    sheet: str,
    name: str,
    reference: str,
    *,
    ports: list[AltiumPort] | None = None,
    entries: list[AltiumSheetEntry] | None = None,
    harness_members: list[AltiumHarnessMember] | None = None,
    generic_bus_members: list[str] | None = None,
) -> tuple[AltiumLocalNet, AltiumPinOccurrence]:
    local_net_id = f"{sheet}:local:{name}"
    pin = AltiumPinOccurrence(
        id=f"{sheet}:pin:{reference}",
        scope_id=_scope(sheet),
        source_index=1,
        local_net_id=local_net_id,
        component_source_id=f"{sheet}:component:{reference}",
        component_reference=reference,
        pin_designator="1",
        pin_name="",
        location=(1, 40),
        tip=(1, 41),
    )
    return (
        AltiumLocalNet(
            id=local_net_id,
            scope_id=_scope(sheet),
            wire_points=set(),
            pin_ids=[pin.id],
            net_labels=[],
            power_ports=[],
            ports=ports or [],
            sheet_entries=entries or [],
            harness_members=harness_members or [],
            generic_bus_members=generic_bus_members or [],
            generated_name=f"__auto_{sheet}_{name}",
        ),
        pin,
    )


def _bus_sheet(
    name: str,
    local_nets: list[AltiumLocalNet],
    pins: list[AltiumPinOccurrence],
    *,
    symbols: list[AltiumSheetSymbol] | None = None,
    entries: list[AltiumSheetEntry] | None = None,
    generic_bus_lines: list[AltiumGenericBusLine] | None = None,
    source_file: str = "",
) -> AltiumSheetSource:
    return AltiumSheetSource(
        id=f"sheet:{name}",
        name=name,
        source_file=source_file or f"{name}.SchDoc",
        scope_id=_scope(name),
        local_nets=local_nets,
        sheet_symbols=symbols or [],
        sheet_entries=entries or [],
        generic_bus_lines=generic_bus_lines or [],
        harness_connectors=[],
        harness_members=[],
        pin_occurrences=pins,
    )


def _bus_source(
    sheets: list[AltiumSheetSource],
    mode: AltiumHierarchyMode,
) -> AltiumSourceDesign:
    return AltiumSourceDesign(
        name="bus",
        project=AltiumProject(hierarchy_mode=mode, allow_port_net_names=True),
        sheets={sheet.name: sheet for sheet in sheets},
        root_sheet_id=sheets[0].name,
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
# Bus-typed sheet entry/port behavior in local extraction and public resolver
# ---------------------------------------------------------------------------


def _generated_name_at(sheet: SheetRecords, coord: tuple[int, int]) -> str:
    """Resolve local net groups and return the generated name of the group at *coord*."""
    resolution = resolve_local_net_groups(sheet)
    root = resolution.coord_to_root[coord]
    group = next(group for group in resolution.groups if group.root == root)
    return group.generated_name


def test_bus_sheet_entry_skipped_in_generated_names():
    """A sheet entry with bus notation should not name a wire group 'D[0..7]'."""
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
        side=SheetEntrySide.LEFT,
        distance_from_top=200,
        coord=(200, 100),
    )

    sheet = _make_sheet("Test", [wire, sym, entry])

    # The wire group should NOT be named "D[0..7]"
    assert "D[0..7]" not in _generated_name_at(sheet, (0, 100))


def test_bus_port_skipped_in_generated_names():
    """A port with bus notation should not name a wire group 'D[0..7]'."""
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

    assert "D[0..7]" not in _generated_name_at(sheet, (0, 100))


def test_bus_sheet_entry_aggregate_name_does_not_merge_member_nets():
    """A bus-typed sheet entry is not used as an aggregate connectivity name."""
    symbol = _bus_symbol("Parent", "Child.SchDoc")
    entry = _bus_entry("Parent", "D[0..1]", symbol.id)
    parent_net, parent_pin = _bus_local_net("Parent", "bus", "PARENT", entries=[entry])
    child_d0_net, child_d0_pin = _bus_local_net(
        "Child",
        "d0",
        "D0_REF",
        ports=[_bus_port("Child", "D0")],
    )
    child_d1_net, child_d1_pin = _bus_local_net(
        "Child",
        "d1",
        "D1_REF",
        ports=[_bus_port("Child", "D1")],
    )

    design = resolve_altium_source(
        _bus_source(
            [
                _bus_sheet(
                    "Parent",
                    [parent_net],
                    [parent_pin],
                    symbols=[symbol],
                    entries=[entry],
                ),
                _bus_sheet(
                    "Child",
                    [child_d0_net, child_d1_net],
                    [child_d0_pin, child_d1_pin],
                    source_file="Child.SchDoc",
                ),
            ],
            AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        ),
    )

    parent_public_net = next(
        net for net in design.nets if any(pin.component.reference == "PARENT" for pin in net.pins)
    )
    assert {pin.component.reference for pin in parent_public_net.pins} == {"PARENT"}
    bus = next(bus for bus in design.buses if bus.name == "D[0..1]")
    assert bus.kind is BusKind.VECTOR
    assert {net.name for net in bus.members} == {"D0", "D1"}


def test_generic_bus_line_entries_create_vector_bus_without_merging_members():
    """Generic BusRec/BusEntryRec geometry contributes bus evidence only."""
    d0_component = ComponentRec(
        record_type=RecordType.COMPONENT,
        index=1,
        owner_index=-1,
        lib_reference="TP",
    )
    d0_designator = DesignatorRec(
        record_type=RecordType.DESIGNATOR,
        index=2,
        owner_index=d0_component.owner_key,
        text="D0_REF",
    )
    d0_pin = PinRec(
        record_type=RecordType.PIN,
        index=3,
        owner_index=d0_component.owner_key,
        designator="1",
        tip=(0, 10),
    )
    d1_component = ComponentRec(
        record_type=RecordType.COMPONENT,
        index=4,
        owner_index=-1,
        lib_reference="TP",
    )
    d1_designator = DesignatorRec(
        record_type=RecordType.DESIGNATOR,
        index=5,
        owner_index=d1_component.owner_key,
        text="D1_REF",
    )
    d1_pin = PinRec(
        record_type=RecordType.PIN,
        index=6,
        owner_index=d1_component.owner_key,
        designator="1",
        tip=(0, 20),
    )
    d0_wire = WireRec(
        record_type=RecordType.WIRE,
        index=7,
        owner_index=-1,
        points=[(0, 10), (50, 10)],
    )
    d1_wire = WireRec(
        record_type=RecordType.WIRE,
        index=8,
        owner_index=-1,
        points=[(0, 20), (50, 20)],
    )
    bus = BusRec(
        record_type=RecordType.BUS,
        index=9,
        owner_index=-1,
        points=[(100, 0), (100, 30)],
    )
    d0_entry = BusEntryRec(
        record_type=RecordType.BUS_ENTRY,
        index=10,
        owner_index=-1,
        location=(100, 10),
        corner=(50, 10),
    )
    d1_entry = BusEntryRec(
        record_type=RecordType.BUS_ENTRY,
        index=11,
        owner_index=-1,
        location=(100, 20),
        corner=(50, 20),
    )
    bus_label = NetLabelRec(
        record_type=RecordType.NET_LABEL,
        index=12,
        owner_index=-1,
        location=(100, 15),
        text="D[0..1]",
    )
    sheet = _make_sheet(
        "Bus",
        [
            d0_component,
            d0_designator,
            d0_pin,
            d1_component,
            d1_designator,
            d1_pin,
            d0_wire,
            d1_wire,
            bus,
            d0_entry,
            d1_entry,
            bus_label,
        ],
    )
    resolution = resolve_local_net_groups(sheet)
    assert len(resolution.generic_bus_groups) == 1
    bus_group = resolution.generic_bus_groups[0]
    assert bus_group.name == "D[0..1]"
    assert set(bus_group.member_roots_by_name) == {"D0", "D1"}
    assert bus_group.member_roots_by_name["D0"] != bus_group.member_roots_by_name["D1"]

    d0_net, d0_pin = _bus_local_net(
        "Bus",
        "D0",
        "D0_REF",
        generic_bus_members=["D0"],
    )
    d1_net, d1_pin = _bus_local_net(
        "Bus",
        "D1",
        "D1_REF",
        generic_bus_members=["D1"],
    )
    source_sheet = _bus_sheet(
        "Bus",
        [d0_net, d1_net],
        [d0_pin, d1_pin],
        generic_bus_lines=[
            AltiumGenericBusLine(
                id="sheet:Bus:generic_bus:0000:12",
                scope_id=_scope("Bus"),
                source_index=12,
                name="D[0..1]",
                location=(100, 15),
                member_local_net_ids={"D0": d0_net.id, "D1": d1_net.id},
            )
        ],
    )

    design = resolve_altium_source(
        _bus_source([source_sheet], AltiumHierarchyMode.FLAT),
    )

    assert {net.name: {pin.component.reference for pin in net.pins} for net in design.nets} == {
        "D0": {"D0_REF"},
        "D1": {"D1_REF"},
    }
    bus_result = next(bus for bus in design.buses if bus.name == "D[0..1]")
    assert bus_result.kind is BusKind.VECTOR
    assert {net.name for net in bus_result.members} == {"D0", "D1"}


def test_harness_bus_members_keep_resolved_net_identity_for_duplicate_names():
    harness_net, harness_pin = _bus_local_net(
        "Harness",
        "harness",
        "H1",
        harness_members=[_harness_member("Harness", "CTRL", "SIG")],
    )
    unrelated_net, unrelated_pin = _bus_local_net(
        "Harness",
        "unrelated",
        "U1",
        generic_bus_members=["SIG"],
    )

    design = resolve_altium_source(
        _bus_source(
            [_bus_sheet("Harness", [harness_net, unrelated_net], [harness_pin, unrelated_pin])],
            AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        )
    )

    bus = next(bus for bus in design.buses if bus.name == "CTRL")

    assert [net.name for net in bus.members] == ["CTRL.SIG"]


def test_bus_port_aggregate_name_does_not_merge_member_port_nets():
    """A bus-typed port is not merged with individual member ports by aggregate name."""
    aggregate_net, aggregate_pin = _bus_local_net(
        "A",
        "bus",
        "BUS",
        ports=[_bus_port("A", "D[0..1]")],
    )
    member_net, member_pin = _bus_local_net(
        "B",
        "d0",
        "D0_REF",
        ports=[_bus_port("B", "D0")],
    )

    design = resolve_altium_source(
        _bus_source(
            [
                _bus_sheet("A", [aggregate_net], [aggregate_pin]),
                _bus_sheet("B", [member_net], [member_pin]),
            ],
            AltiumHierarchyMode.FLAT,
        ),
    )

    bus_net = next(
        net for net in design.nets if any(pin.component.reference == "BUS" for pin in net.pins)
    )
    assert {pin.component.reference for pin in bus_net.pins} == {"BUS"}
    assert bus_net.name != "D[0..1]"


def test_bus_aggregate_name_is_not_used_as_final_net_name():
    """Aggregate bus text is source evidence only, not a final member net name."""
    aggregate_net, aggregate_pin = _bus_local_net(
        "A",
        "bus",
        "BUS",
        ports=[_bus_port("A", "D[0..1]")],
    )

    design = resolve_altium_source(
        _bus_source([_bus_sheet("A", [aggregate_net], [aggregate_pin])], AltiumHierarchyMode.FLAT),
    )

    # The aggregate name is rejected, so the net falls through to Altium's
    # autoname from its single member pin (BUS, 1).
    assert design.nets[0].name == "NetBUS_1"
