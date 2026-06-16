"""Tests for KiCad source-to-public schematic resolution."""

import pytest

from phosphor_eda.domain.schematic import BusKind, Net, NetNameKind, ScopeId
from phosphor_eda.formats.common.resolved_graph import ResolutionInputError
from phosphor_eda.formats.kicad.resolver import resolve_kicad_source, select_kicad_net_name
from phosphor_eda.formats.kicad.source import (
    KiCadBusAlias,
    KiCadBusEntry,
    KiCadBusLabel,
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadLocalNet,
    KiCadPinOccurrence,
    KiCadPowerSymbol,
    KiCadSheetInstance,
    KiCadSheetPin,
    KiCadSheetSymbol,
    KiCadSourceDesign,
)


def _scope(*parts: str) -> ScopeId:
    return ScopeId(path=parts)


def _scope_key(scope_id: ScopeId) -> str:
    return "root" if not scope_id.path else "-".join(scope_id.path)


def _sheet(scope_id: ScopeId, name: str, *, sheet_symbol_id: str = "") -> KiCadSheetInstance:
    return KiCadSheetInstance(
        id=f"sheet:{_scope_key(scope_id)}",
        scope_id=scope_id,
        sheet_name=name,
        source_file=f"{name}.kicad_sch",
        parent_scope_id=None if not scope_id.path else _scope(*scope_id.path[:-1]),
        sheet_symbol_id=sheet_symbol_id,
    )


def _local_label(scope_id: ScopeId, net_id: str, name: str, index: int = 1) -> KiCadLocalLabel:
    return KiCadLocalLabel(
        id=f"{net_id}:local_label:{index}",
        scope_id=scope_id,
        source_index=index,
        name=name,
        location=(float(index), 10.0),
        local_net_id=net_id,
    )


def _global_label(scope_id: ScopeId, net_id: str, name: str, index: int = 1) -> KiCadGlobalLabel:
    return KiCadGlobalLabel(
        id=f"{net_id}:global_label:{index}",
        scope_id=scope_id,
        source_index=index,
        name=name,
        location=(float(index), 20.0),
        local_net_id=net_id,
    )


def _hier_label(
    scope_id: ScopeId,
    net_id: str,
    name: str,
    index: int = 1,
) -> KiCadHierarchicalLabel:
    return KiCadHierarchicalLabel(
        id=f"{net_id}:hierarchical_label:{index}",
        scope_id=scope_id,
        source_index=index,
        name=name,
        location=(float(index), 30.0),
        local_net_id=net_id,
    )


def _power_symbol(
    scope_id: ScopeId,
    net_id: str,
    name: str,
    index: int = 1,
    *,
    power_kind: str = "global",
) -> KiCadPowerSymbol:
    return KiCadPowerSymbol(
        id=f"{net_id}:power:{index}",
        scope_id=scope_id,
        source_index=index,
        name=name,
        reference=f"#PWR{index}",
        lib_id=f"power:{name}",
        power_kind=power_kind,
        location=(float(index), 40.0),
        local_net_id=net_id,
    )


def _sheet_pin(
    scope_id: ScopeId,
    net_id: str,
    name: str,
    sheet_symbol_id: str,
    child_scope_id: ScopeId,
    index: int = 1,
) -> KiCadSheetPin:
    return KiCadSheetPin(
        id=f"{net_id}:sheet_pin:{index}",
        scope_id=scope_id,
        source_index=index,
        sheet_symbol_id=sheet_symbol_id,
        child_scope_id=child_scope_id,
        name=name,
        direction="input",
        location=(float(index), 50.0),
        local_net_id=net_id,
    )


def _pin(
    scope_id: ScopeId,
    net_id: str,
    reference: str,
    *,
    designator: str = "1",
    pin_name: str | None = None,
    pin_net_name: str | None = None,
    component_source_id: str = "",
    component_identity_source_id: str = "",
    component_unit: int = 1,
    component_has_multiple_units: bool = False,
    index: int = 1,
) -> KiCadPinOccurrence:
    source_id = component_source_id or f"{_scope_key(scope_id)}:component:{reference}"
    identity_source_id = component_identity_source_id or source_id
    resolved_pin_name = pin_name if pin_name is not None else f"{reference}-{designator}"
    return KiCadPinOccurrence(
        id=f"{net_id}:pin:{reference}:{designator}:{index}",
        scope_id=scope_id,
        source_index=index,
        local_net_id=net_id,
        component_source_id=source_id,
        component_identity_source_id=identity_source_id,
        component_unit=component_unit,
        component_has_multiple_units=component_has_multiple_units,
        component_reference=reference,
        pin_designator=designator,
        pin_name=resolved_pin_name,
        pin_net_name=pin_net_name if pin_net_name is not None else resolved_pin_name,
        location=(float(index), 60.0),
    )


def _local_net(
    scope_id: ScopeId,
    key: str,
    *,
    local_labels: list[KiCadLocalLabel] | None = None,
    global_labels: list[KiCadGlobalLabel] | None = None,
    hierarchical_labels: list[KiCadHierarchicalLabel] | None = None,
    power_symbols: list[KiCadPowerSymbol] | None = None,
    sheet_pins: list[KiCadSheetPin] | None = None,
    bus_entries: list[KiCadBusEntry] | None = None,
    pins: list[KiCadPinOccurrence] | None = None,
    generated_name: str = "",
) -> KiCadLocalNet:
    net_id = f"{_scope_key(scope_id)}:local:{key}"
    return KiCadLocalNet(
        id=net_id,
        scope_id=scope_id,
        wire_points=set(),
        pin_ids=[pin.id for pin in pins or []],
        local_labels=local_labels or [],
        global_labels=global_labels or [],
        hierarchical_labels=hierarchical_labels or [],
        power_symbols=power_symbols or [],
        sheet_pins=sheet_pins or [],
        bus_entries=bus_entries or [],
        generated_name=generated_name or f"__auto_{_scope_key(scope_id)}_{key}",
    )


def _source(
    local_nets: list[KiCadLocalNet],
    pins: list[KiCadPinOccurrence],
    *,
    bus_labels: list[KiCadBusLabel] | None = None,
    bus_aliases: list[KiCadBusAlias] | None = None,
    bus_entries: list[KiCadBusEntry] | None = None,
    sheet_instances: list[KiCadSheetInstance] | None = None,
    sheet_symbols: list[KiCadSheetSymbol] | None = None,
    schematic_version: int = 20231120,
) -> KiCadSourceDesign:
    instances = sheet_instances or [_sheet(_scope(), "Root")]
    return KiCadSourceDesign(
        name="test",
        root_source_file="Root.kicad_sch",
        root_scope_id=_scope(),
        sheet_instances=instances,
        local_nets=local_nets,
        pin_occurrences=pins,
        local_labels=[label for net in local_nets for label in net.local_labels],
        global_labels=[label for net in local_nets for label in net.global_labels],
        hierarchical_labels=[label for net in local_nets for label in net.hierarchical_labels],
        bus_labels=bus_labels or [],
        bus_aliases=bus_aliases or [],
        bus_entries=bus_entries or [],
        power_symbols=[symbol for net in local_nets for symbol in net.power_symbols],
        sheet_symbols=sheet_symbols or [],
        sheet_pins=[pin for net in local_nets for pin in net.sheet_pins],
        schematic_version=schematic_version,
    )


def _net_for_reference(nets: list[Net], reference: str) -> Net:
    for net in nets:
        if any(pin.component.reference == reference for pin in net.pins):
            return net
    raise AssertionError(f"No net found for {reference}")


def _refs(net: Net) -> set[str]:
    return {pin.component.reference for pin in net.pins}


def _bus_label(
    scope_id: ScopeId,
    name: str,
    index: int = 1,
    *,
    kind: str = "local_label",
) -> KiCadBusLabel:
    return KiCadBusLabel(
        id=f"{_scope_key(scope_id)}:bus_label:{index}",
        scope_id=scope_id,
        source_index=index,
        name=name,
        location=(float(index), 5.0),
        kind=kind,
    )


def _bus_entry(
    scope_id: ScopeId,
    local_net_id: str,
    member_name: str,
    member_label_id: str,
    index: int = 1,
) -> KiCadBusEntry:
    return KiCadBusEntry(
        id=f"{_scope_key(scope_id)}:bus_entry:{index}",
        scope_id=scope_id,
        source_index=index,
        start=(float(index), 1.0),
        end=(float(index), 2.0),
        wire_point=(float(index), 1.0),
        bus_point=(float(index), 2.0),
        local_net_id=local_net_id,
        bus_group_id=f"{_scope_key(scope_id)}:bus_group:{index}",
        member_name=member_name,
        member_label_id=member_label_id,
    )


def _bus_alias(scope_id: ScopeId, name: str, members: tuple[str, ...]) -> KiCadBusAlias:
    return KiCadBusAlias(
        id=f"{_scope_key(scope_id)}:bus_alias:{name}",
        scope_id=scope_id,
        name=name,
        members=members,
    )


def test_bus_labels_promote_to_resolved_buses() -> None:
    scope = _scope()
    d0_pin = _pin(scope, "root:local:d0", "U1", designator="1")
    d1_pin = _pin(scope, "root:local:d1", "U2", designator="1")
    clk_pin = _pin(scope, "root:local:clk", "U3", designator="1")
    d0 = _local_net(
        scope, "d0", local_labels=[_local_label(scope, "root:local:d0", "DATA0")], pins=[d0_pin]
    )
    d1 = _local_net(
        scope, "d1", local_labels=[_local_label(scope, "root:local:d1", "DATA1")], pins=[d1_pin]
    )
    clk = _local_net(
        scope,
        "clk",
        local_labels=[_local_label(scope, "root:local:clk", "SOC.CLK")],
        pins=[clk_pin],
    )

    design = resolve_kicad_source(
        _source(
            [d0, d1, clk],
            [d0_pin, d1_pin, clk_pin],
            bus_labels=[
                _bus_label(scope, "DATA[0..1]", 1),
                _bus_label(scope, "SOC{ADDR CLK}", 2),
            ],
            bus_aliases=[_bus_alias(scope, "ADDR", ("DATA[0..1]",))],
        )
    )

    vector_bus = next(bus for bus in design.buses if bus.name == "DATA[0..1]")
    group_bus = next(bus for bus in design.buses if bus.name == "SOC{ADDR CLK}")
    assert vector_bus.kind is BusKind.VECTOR
    assert {net.name for net in vector_bus.members} == {"/DATA0", "/DATA1"}
    assert group_bus.kind is BusKind.GROUP
    assert {net.name for net in group_bus.members} == {"/SOC.CLK"}
    assert all(net.name != "DATA[0..1]" for net in design.nets)


def test_global_bus_entry_member_uses_global_member_name() -> None:
    scope = _scope()
    net_id = "root:local:d0"
    pin = _pin(scope, net_id, "U1")
    bus_label = _bus_label(scope, "DATA[0..0]", 1, kind="global_label")
    bus_entry = _bus_entry(scope, net_id, "DATA0", bus_label.id)

    design = resolve_kicad_source(
        _source(
            [_local_net(scope, "d0", bus_entries=[bus_entry], pins=[pin])],
            [pin],
            bus_labels=[bus_label],
            bus_entries=[bus_entry],
        )
    )

    assert _net_for_reference(design.nets, "U1").name == "DATA0"
    bus = next(bus for bus in design.buses if bus.name == "DATA[0..0]")
    assert {net.name for net in bus.members} == {"DATA0"}


def test_kicad_local_label_names_are_path_qualified_and_escaped() -> None:
    child = _scope("child")
    net_id = "child:local:out"
    pin = _pin(child, net_id, "U1")

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    child,
                    "out",
                    local_labels=[_local_label(child, net_id, "OUT/N")],
                    pins=[pin],
                )
            ],
            [pin],
            sheet_instances=[_sheet(_scope(), "Root"), _sheet(child, "Amp/Left")],
        )
    )

    resolved = _net_for_reference(design.nets, "U1")
    assert resolved.name == "/Amp{slash}Left/OUT{slash}N"
    assert len(resolved.names) == 1
    assert resolved.names[0].name == "/Amp{slash}Left/OUT{slash}N"
    assert resolved.names[0].kind is NetNameKind.LABEL
    assert resolved.names[0].scope == child
    assert resolved.names[0].source == "local_label"


def test_kicad_local_driver_wins_across_hierarchy_with_winning_path() -> None:
    root = _scope()
    child = _scope("child")
    symbol_id = "root:sheet_symbol:child"
    parent_id = "root:local:parent"
    child_id = "child:local:child"
    parent_pin = _pin(root, parent_id, "PARENT")
    child_pin = _pin(child, child_id, "CHILD")
    sheet_pin = _sheet_pin(root, parent_id, "SIG", symbol_id, child)

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    root,
                    "parent",
                    local_labels=[_local_label(root, parent_id, "SIG")],
                    sheet_pins=[sheet_pin],
                    pins=[parent_pin],
                ),
                _local_net(
                    child,
                    "child",
                    hierarchical_labels=[_hier_label(child, child_id, "SIG")],
                    pins=[child_pin],
                ),
            ],
            [parent_pin, child_pin],
            sheet_instances=[
                _sheet(root, "Root"),
                _sheet(child, "Child", sheet_symbol_id=symbol_id),
            ],
        )
    )

    resolved = _net_for_reference(design.nets, "PARENT")
    assert _refs(resolved) == {"PARENT", "CHILD"}
    assert resolved.name == "/SIG"
    assert {entry.name for entry in resolved.names} >= {"/SIG", "/Child/SIG"}


def test_kicad_local_power_names_are_path_qualified_and_beat_local_labels() -> None:
    child = _scope("sheet-a")
    net_id = "sheet-a:local:rail"
    pin = _pin(child, net_id, "U1")

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    child,
                    "rail",
                    local_labels=[_local_label(child, net_id, "LOCAL")],
                    power_symbols=[
                        _power_symbol(child, net_id, "VCC", power_kind="local"),
                    ],
                    pins=[pin],
                )
            ],
            [pin],
            sheet_instances=[
                _sheet(_scope(), "Root"),
                _sheet(child, "Sheet/A"),
            ],
        )
    )

    resolved = _net_for_reference(design.nets, "U1")
    assert resolved.name == "/Sheet{slash}A/VCC"
    assert {entry.name for entry in resolved.names} >= {
        "/Sheet{slash}A/VCC",
        "/Sheet{slash}A/LOCAL",
    }


def test_kicad_local_power_symbols_do_not_merge_across_sibling_sheets() -> None:
    scope_a = _scope("sheet-a")
    scope_b = _scope("sheet-b")
    net_a_id = "sheet-a:local:vcc-a"
    net_b_id = "sheet-b:local:vcc-b"
    pin_a = _pin(scope_a, net_a_id, "A1")
    pin_b = _pin(scope_b, net_b_id, "B1")

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    scope_a,
                    "vcc-a",
                    power_symbols=[_power_symbol(scope_a, net_a_id, "VCC", power_kind="local")],
                    pins=[pin_a],
                ),
                _local_net(
                    scope_b,
                    "vcc-b",
                    power_symbols=[_power_symbol(scope_b, net_b_id, "VCC", power_kind="local")],
                    pins=[pin_b],
                ),
            ],
            [pin_a, pin_b],
            sheet_instances=[
                _sheet(_scope(), "Root"),
                _sheet(scope_a, "A"),
                _sheet(scope_b, "B"),
            ],
        )
    )

    net_a = _net_for_reference(design.nets, "A1")
    net_b = _net_for_reference(design.nets, "B1")
    assert net_a.name == "/A/VCC"
    assert net_b.name == "/B/VCC"
    assert _refs(net_a) == {"A1"}
    assert _refs(net_b) == {"B1"}


def test_kicad_anonymous_net_uses_best_pin_auto_name() -> None:
    root = _scope()
    net_id = "root:local:anonymous"
    diode_pin = _pin(root, net_id, "D1", designator="1", pin_name="K", index=1)
    resistor_pin = _pin(root, net_id, "R1", designator="1", pin_name="1", index=2)

    design = resolve_kicad_source(
        _source(
            [_local_net(root, "anonymous", pins=[diode_pin, resistor_pin])],
            [diode_pin, resistor_pin],
        )
    )

    resolved = _net_for_reference(design.nets, "D1")
    assert resolved.name == "Net-(D1-K)"
    assert resolved.names[0].kind is NetNameKind.TOOL_AUTO
    assert resolved.names[0].source == "pin"


def test_kicad_pin_auto_name_preserves_raw_overline_markup() -> None:
    root = _scope()
    net_id = "root:local:anonymous"
    reset_pin = _pin(
        root,
        net_id,
        "U5",
        designator="23",
        pin_name="RESET",
        pin_net_name="~{RESET}",
        index=1,
    )
    pad_pin = _pin(root, net_id, "TP1", designator="1", pin_name="1", index=2)

    design = resolve_kicad_source(
        _source(
            [_local_net(root, "anonymous", pins=[reset_pin, pad_pin])],
            [reset_pin, pad_pin],
        )
    )

    resolved = _net_for_reference(design.nets, "U5")
    assert resolved.name == "Net-(U5-~{RESET})"
    assert {pin.name for pin in resolved.pins} >= {"RESET", "1"}


def test_kicad_pin_auto_name_adds_unit_letter_only_for_multi_unit_symbols() -> None:
    root = _scope()
    single_id = "root:local:single"
    multi_id = "root:local:multi"
    single_pin = _pin(
        root,
        single_id,
        "D1",
        designator="1",
        pin_name="K",
        component_source_id="root:component:d1",
        component_identity_source_id="root:component_instance:d1",
    )
    multi_pin = _pin(
        root,
        multi_id,
        "U1",
        designator="1",
        pin_name="OUT",
        component_source_id="root:component:u1-unit-a",
        component_identity_source_id="root:component_instance:u1",
        component_unit=1,
        component_has_multiple_units=True,
    )

    design = resolve_kicad_source(
        _source(
            [
                _local_net(root, "single", pins=[single_pin]),
                _local_net(root, "multi", pins=[multi_pin]),
            ],
            [single_pin, multi_pin],
        )
    )

    assert _net_for_reference(design.nets, "D1").name == "unconnected-(D1-K-Pad1)"
    assert _net_for_reference(design.nets, "U1").name == "unconnected-(U1A-OUT-Pad1)"


def test_kicad_single_pin_net_uses_versioned_unconnected_auto_name() -> None:
    root = _scope()
    net_id = "root:local:anonymous"
    pin = _pin(root, net_id, "U1", designator="6", pin_name="LV")

    design = resolve_kicad_source(
        _source(
            [_local_net(root, "anonymous", pins=[pin])],
            [pin],
            schematic_version=20231120,
        )
    )

    resolved = _net_for_reference(design.nets, "U1")
    assert resolved.name == "unconnected-(U1-LV-Pad6)"
    assert resolved.names[0].kind is NetNameKind.TOOL_AUTO
    assert resolved.names[0].source == "pin"


@pytest.mark.parametrize(
    ("schematic_version", "expected"),
    [
        (20211123, "unconnected-(U1-Pad6)"),
        (20230121, "unconnected-(U1-LV)"),
        (20231120, "unconnected-(U1-LV-Pad6)"),
    ],
)
def test_kicad_unconnected_pin_auto_names_follow_file_version(
    schematic_version: int,
    expected: str,
) -> None:
    root = _scope()
    net_id = "root:local:anonymous"
    pin = _pin(root, net_id, "U1", designator="6", pin_name="LV")

    design = resolve_kicad_source(
        _source(
            [_local_net(root, "anonymous", pins=[pin])],
            [pin],
            schematic_version=schematic_version,
        )
    )

    assert _net_for_reference(design.nets, "U1").name == expected


def test_bus_alias_expansion_uses_label_scope_aliases() -> None:
    scope_a = _scope("sheet-a")
    scope_b = _scope("sheet-b")
    pin_a = _pin(scope_a, "sheet-a:local:a0", "U1")
    pin_b = _pin(scope_b, "sheet-b:local:b0", "U2")
    net_a = _local_net(
        scope_a,
        "a0",
        local_labels=[_local_label(scope_a, "sheet-a:local:a0", "SOC.A0")],
        pins=[pin_a],
    )
    net_b = _local_net(
        scope_b,
        "b0",
        local_labels=[_local_label(scope_b, "sheet-b:local:b0", "SOC.B0")],
        pins=[pin_b],
    )

    design = resolve_kicad_source(
        _source(
            [net_a, net_b],
            [pin_a, pin_b],
            bus_labels=[_bus_label(scope_b, "SOC{ADDR}", 1)],
            bus_aliases=[
                _bus_alias(scope_b, "ADDR", ("B0",)),
                _bus_alias(scope_a, "ADDR", ("A0",)),
            ],
            sheet_instances=[_sheet(scope_a, "A"), _sheet(scope_b, "B")],
        )
    )

    bus = next(bus for bus in design.buses if bus.name == "SOC{ADDR}")

    assert {net.name for net in bus.members} == {"/B/SOC.B0"}


def test_local_labels_with_same_text_on_sibling_sheet_instances_do_not_merge() -> None:
    scope_a = _scope("sheet-a")
    scope_b = _scope("sheet-b")
    net_a_id = "sheet-a:local:sig"
    net_b_id = "sheet-b:local:sig"
    pin_a = _pin(scope_a, net_a_id, "A1")
    pin_b = _pin(scope_b, net_b_id, "B1")
    net_a = _local_net(
        scope_a,
        "sig",
        local_labels=[_local_label(scope_a, net_a_id, "SIG")],
        pins=[pin_a],
    )
    net_b = _local_net(
        scope_b,
        "sig",
        local_labels=[_local_label(scope_b, net_b_id, "SIG")],
        pins=[pin_b],
    )

    design = resolve_kicad_source(
        _source(
            [net_a, net_b],
            [pin_a, pin_b],
            sheet_instances=[_sheet(scope_a, "A"), _sheet(scope_b, "B")],
        )
    )

    assert _refs(_net_for_reference(design.nets, "A1")) == {"A1"}
    assert _refs(_net_for_reference(design.nets, "B1")) == {"B1"}


def test_local_labels_with_same_text_on_same_sheet_instance_merge() -> None:
    scope_id = _scope("sheet-a")
    first_id = "sheet-a:local:first"
    second_id = "sheet-a:local:second"
    pin_a = _pin(scope_id, first_id, "A1")
    pin_b = _pin(scope_id, second_id, "B1")
    first = _local_net(
        scope_id,
        "first",
        local_labels=[_local_label(scope_id, first_id, "SIG")],
        pins=[pin_a],
    )
    second = _local_net(
        scope_id,
        "second",
        local_labels=[_local_label(scope_id, second_id, "SIG")],
        pins=[pin_b],
    )

    design = resolve_kicad_source(
        _source([first, second], [pin_a, pin_b], sheet_instances=[_sheet(scope_id, "A")])
    )

    assert _refs(_net_for_reference(design.nets, "A1")) == {"A1", "B1"}


def test_local_and_hierarchical_labels_with_same_text_on_same_sheet_instance_merge() -> None:
    scope_id = _scope("sheet-a")
    local_id = "sheet-a:local:local"
    hierarchical_id = "sheet-a:local:hierarchical"
    local_pin = _pin(scope_id, local_id, "LOCAL1")
    hierarchical_pin = _pin(scope_id, hierarchical_id, "HIER1")
    local_net = _local_net(
        scope_id,
        "local",
        local_labels=[_local_label(scope_id, local_id, "SIG")],
        pins=[local_pin],
    )
    hierarchical_net = _local_net(
        scope_id,
        "hierarchical",
        hierarchical_labels=[_hier_label(scope_id, hierarchical_id, "SIG")],
        pins=[hierarchical_pin],
    )

    design = resolve_kicad_source(
        _source(
            [local_net, hierarchical_net],
            [local_pin, hierarchical_pin],
            sheet_instances=[_sheet(scope_id, "A")],
        )
    )

    resolved = _net_for_reference(design.nets, "LOCAL1")
    assert _refs(resolved) == {"LOCAL1", "HIER1"}
    assert resolved.name == "/A/SIG"


def test_global_labels_with_same_text_merge_across_design() -> None:
    scope_a = _scope("sheet-a")
    scope_b = _scope("sheet-b")
    net_a_id = "sheet-a:local:sync"
    net_b_id = "sheet-b:local:sync"
    pin_a = _pin(scope_a, net_a_id, "A1")
    pin_b = _pin(scope_b, net_b_id, "B1")

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    scope_a,
                    "sync",
                    global_labels=[_global_label(scope_a, net_a_id, "SYNC")],
                    pins=[pin_a],
                ),
                _local_net(
                    scope_b,
                    "sync",
                    global_labels=[_global_label(scope_b, net_b_id, "SYNC")],
                    pins=[pin_b],
                ),
            ],
            [pin_a, pin_b],
            sheet_instances=[_sheet(scope_a, "A"), _sheet(scope_b, "B")],
        )
    )

    assert _refs(_net_for_reference(design.nets, "A1")) == {"A1", "B1"}


def test_global_label_transitive_chains_merge_all_attached_names() -> None:
    root = _scope()
    scope_b = _scope("sheet-b")
    scope_c = _scope("sheet-c")
    bridge_id = "root:local:bridge"
    b_id = "sheet-b:local:b"
    c_id = "sheet-c:local:c"
    bridge_pin = _pin(root, bridge_id, "BRIDGE")
    pin_b = _pin(scope_b, b_id, "B1")
    pin_c = _pin(scope_c, c_id, "C1")

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    root,
                    "bridge",
                    global_labels=[
                        _global_label(root, bridge_id, "A", index=1),
                        _global_label(root, bridge_id, "B", index=2),
                    ],
                    pins=[bridge_pin],
                ),
                _local_net(
                    scope_b, "b", global_labels=[_global_label(scope_b, b_id, "A")], pins=[pin_b]
                ),
                _local_net(
                    scope_c, "c", global_labels=[_global_label(scope_c, c_id, "B")], pins=[pin_c]
                ),
            ],
            [bridge_pin, pin_b, pin_c],
            sheet_instances=[_sheet(root, "Root"), _sheet(scope_b, "B"), _sheet(scope_c, "C")],
        )
    )

    resolved = _net_for_reference(design.nets, "BRIDGE")
    assert _refs(resolved) == {"BRIDGE", "B1", "C1"}
    assert resolved.name == "A"
    assert "B" in resolved.aliases


def test_hierarchical_labels_connect_only_through_matching_sheet_pins_on_parent_sheet_symbol() -> (
    None
):
    root = _scope()
    child = _scope("child")
    symbol_id = "root:sheet_symbol:child"
    parent_id = "root:local:parent"
    child_match_id = "child:local:match"
    child_other_id = "child:local:other"
    parent_pin = _pin(root, parent_id, "PARENT")
    match_pin = _pin(child, child_match_id, "MATCH")
    other_pin = _pin(child, child_other_id, "OTHER")
    sheet_pin = _sheet_pin(root, parent_id, "SIG", symbol_id, child)

    design = resolve_kicad_source(
        _source(
            [
                _local_net(root, "parent", sheet_pins=[sheet_pin], pins=[parent_pin]),
                _local_net(
                    child,
                    "match",
                    hierarchical_labels=[_hier_label(child, child_match_id, "SIG")],
                    pins=[match_pin],
                ),
                _local_net(
                    child,
                    "other",
                    hierarchical_labels=[_hier_label(child, child_other_id, "OTHER")],
                    pins=[other_pin],
                ),
            ],
            [parent_pin, match_pin, other_pin],
            sheet_instances=[
                _sheet(root, "Root"),
                _sheet(child, "Child", sheet_symbol_id=symbol_id),
            ],
        )
    )

    assert _refs(_net_for_reference(design.nets, "PARENT")) == {"PARENT", "MATCH"}
    assert _refs(_net_for_reference(design.nets, "OTHER")) == {"OTHER"}


def test_sibling_child_hierarchical_labels_do_not_merge_without_parent_wiring() -> None:
    child_a = _scope("child-a")
    child_b = _scope("child-b")
    net_a_id = "child-a:local:sig"
    net_b_id = "child-b:local:sig"
    pin_a = _pin(child_a, net_a_id, "A1")
    pin_b = _pin(child_b, net_b_id, "B1")

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    child_a,
                    "sig",
                    hierarchical_labels=[_hier_label(child_a, net_a_id, "SIG")],
                    pins=[pin_a],
                ),
                _local_net(
                    child_b,
                    "sig",
                    hierarchical_labels=[_hier_label(child_b, net_b_id, "SIG")],
                    pins=[pin_b],
                ),
            ],
            [pin_a, pin_b],
            sheet_instances=[_sheet(child_a, "A"), _sheet(child_b, "B")],
        )
    )

    assert _refs(_net_for_reference(design.nets, "A1")) == {"A1"}
    assert _refs(_net_for_reference(design.nets, "B1")) == {"B1"}


def test_repeated_sheet_instances_stay_distinct_unless_parent_sheet_pins_are_wired_together() -> (
    None
):
    root = _scope()
    child_a = _scope("child-a")
    child_b = _scope("child-b")
    symbol_a = "root:sheet_symbol:a"
    symbol_b = "root:sheet_symbol:b"
    parent_id = "root:local:shared-parent"
    child_a_id = "child-a:local:sig"
    child_b_id = "child-b:local:sig"
    parent_pin = _pin(root, parent_id, "PARENT")
    pin_a = _pin(child_a, child_a_id, "A1")
    pin_b = _pin(child_b, child_b_id, "B1")

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    root,
                    "shared-parent",
                    sheet_pins=[
                        _sheet_pin(root, parent_id, "SIG", symbol_a, child_a, index=1),
                        _sheet_pin(root, parent_id, "SIG", symbol_b, child_b, index=2),
                    ],
                    pins=[parent_pin],
                ),
                _local_net(
                    child_a,
                    "sig",
                    hierarchical_labels=[_hier_label(child_a, child_a_id, "SIG")],
                    pins=[pin_a],
                ),
                _local_net(
                    child_b,
                    "sig",
                    hierarchical_labels=[_hier_label(child_b, child_b_id, "SIG")],
                    pins=[pin_b],
                ),
            ],
            [parent_pin, pin_a, pin_b],
            sheet_instances=[
                _sheet(root, "Root"),
                _sheet(child_a, "ChildA", sheet_symbol_id=symbol_a),
                _sheet(child_b, "ChildB", sheet_symbol_id=symbol_b),
            ],
        )
    )

    assert _refs(_net_for_reference(design.nets, "PARENT")) == {"PARENT", "A1", "B1"}


def test_power_symbols_merge_by_power_name_across_design() -> None:
    scope_a = _scope("sheet-a")
    scope_b = _scope("sheet-b")
    net_a_id = "sheet-a:local:gnd"
    net_b_id = "sheet-b:local:gnd"
    pin_a = _pin(scope_a, net_a_id, "A1")
    pin_b = _pin(scope_b, net_b_id, "B1")

    design = resolve_kicad_source(
        _source(
            [
                _local_net(
                    scope_a,
                    "gnd",
                    power_symbols=[_power_symbol(scope_a, net_a_id, "GND")],
                    pins=[pin_a],
                ),
                _local_net(
                    scope_b,
                    "gnd",
                    power_symbols=[_power_symbol(scope_b, net_b_id, "GND")],
                    pins=[pin_b],
                ),
            ],
            [pin_a, pin_b],
            sheet_instances=[_sheet(scope_a, "A"), _sheet(scope_b, "B")],
        )
    )

    resolved = _net_for_reference(design.nets, "A1")
    assert _refs(resolved) == {"A1", "B1"}
    assert resolved.name == "GND"


def test_final_name_priority_and_aliases_are_isolated() -> None:
    root = _scope()
    net_id = "root:local:mixed"
    pin = _pin(root, net_id, "U1")
    local_net = _local_net(
        root,
        "mixed",
        local_labels=[_local_label(root, net_id, "LOCAL")],
        global_labels=[_global_label(root, net_id, "GLOBAL")],
        hierarchical_labels=[_hier_label(root, net_id, "HIER")],
        power_symbols=[_power_symbol(root, net_id, "VCC")],
        sheet_pins=[_sheet_pin(root, net_id, "PIN", "root:sheet_symbol:child", _scope("child"))],
        pins=[pin],
        generated_name="__auto_root_mixed",
    )

    design = resolve_kicad_source(
        _source(
            [local_net],
            [pin],
            sheet_instances=[_sheet(root, "Root"), _sheet(_scope("child"), "Child")],
        )
    )
    resolved = _net_for_reference(design.nets, "U1")

    assert resolved.name == "GLOBAL"
    assert {"VCC", "/LOCAL", "/HIER", "/PIN"}.issubset(resolved.aliases)
    assert "__auto_root_mixed" not in resolved.aliases


def test_name_priority_falls_back_to_kicad_pin_auto_name() -> None:
    root = _scope()
    net_id = "root:local:anonymous"
    pin = _pin(root, net_id, "U1", pin_name="1")

    design = resolve_kicad_source(
        _source(
            [_local_net(root, "anonymous", pins=[pin], generated_name="__auto_root_anon")], [pin]
        )
    )

    assert _net_for_reference(design.nets, "U1").name == "unconnected-(U1-Pad1)"


def test_name_priority_falls_back_through_each_evidence_class() -> None:
    root = _scope()
    child = _scope("child")

    power_id = "root:local:power"
    power_net = _local_net(
        root,
        "power",
        local_labels=[_local_label(root, power_id, "LOCAL")],
        power_symbols=[_power_symbol(root, power_id, "VCC")],
    )

    local_id = "root:local:local"
    local_net = _local_net(
        root,
        "local",
        local_labels=[_local_label(root, local_id, "LOCAL")],
        hierarchical_labels=[_hier_label(root, local_id, "HIER")],
        sheet_pins=[_sheet_pin(root, local_id, "PIN", "root:sheet_symbol:child", child)],
    )

    hierarchical_id = "root:local:hier"
    hierarchical_net = _local_net(
        root,
        "hier",
        hierarchical_labels=[_hier_label(root, hierarchical_id, "HIER")],
        sheet_pins=[_sheet_pin(root, hierarchical_id, "PIN", "root:sheet_symbol:child", child)],
    )

    sheet_pin_id = "root:local:sheet-pin"
    sheet_pin_net = _local_net(
        root,
        "sheet-pin",
        sheet_pins=[_sheet_pin(root, sheet_pin_id, "PIN", "root:sheet_symbol:child", child)],
    )

    assert select_kicad_net_name([power_net]) == "VCC"
    assert select_kicad_net_name([local_net]) == "/LOCAL"
    assert select_kicad_net_name([hierarchical_net]) == "/HIER"
    assert select_kicad_net_name([sheet_pin_net]) == "/PIN"


def test_multi_unit_symbols_become_one_logical_component_when_source_identifiers_match() -> None:
    root = _scope()
    net_a_id = "root:local:a"
    net_b_id = "root:local:b"
    pin_a = _pin(
        root,
        net_a_id,
        "U1",
        designator="1",
        component_source_id="root:component:u1-unit-a",
        component_identity_source_id="root:component_instance:u1",
        component_unit=1,
    )
    pin_b = _pin(
        root,
        net_b_id,
        "U1",
        designator="2",
        component_source_id="root:component:u1-unit-b",
        component_identity_source_id="root:component_instance:u1",
        component_unit=2,
    )

    design = resolve_kicad_source(
        _source(
            [_local_net(root, "a", pins=[pin_a]), _local_net(root, "b", pins=[pin_b])],
            [pin_a, pin_b],
        )
    )

    assert len(design.components) == 1
    assert design.components[0].reference == "U1"
    assert {pin.designator for pin in design.components[0].pins} == {"1", "2"}
    assert {occurrence.source_id for occurrence in design.components[0].occurrences} == {
        "root:component:u1-unit-a",
        "root:component:u1-unit-b",
    }


def test_same_scope_reference_uses_kicad_source_identity_for_separate_components() -> None:
    root = _scope()
    net_a_id = "root:local:a"
    net_b_id = "root:local:b"
    pin_a = _pin(
        root,
        net_a_id,
        "U1",
        designator="1",
        component_source_id="root:component:first-symbol",
    )
    pin_b = _pin(
        root,
        net_b_id,
        "U1",
        designator="1",
        component_source_id="root:component:second-symbol",
    )

    design = resolve_kicad_source(
        _source(
            [_local_net(root, "a", pins=[pin_a]), _local_net(root, "b", pins=[pin_b])],
            [pin_a, pin_b],
        )
    )

    assert len(design.components) == 2
    assert {component.reference for component in design.components} == {"U1"}
    assert {
        component.metadata["kicad_component_source_ids"] for component in design.components
    } == {"root:component:first-symbol", "root:component:second-symbol"}
    assert all(len(component.occurrences) == 1 for component in design.components)
    assert all(len(component.pins) == 1 for component in design.components)
    assert {
        pin.occurrences[0].source_id for component in design.components for pin in component.pins
    } == {pin_a.id, pin_b.id}


def test_repeated_sheet_instances_with_same_references_produce_separate_logical_component_ids() -> (
    None
):
    child_a = _scope("child-a")
    child_b = _scope("child-b")
    net_a_id = "child-a:local:sig"
    net_b_id = "child-b:local:sig"
    pin_a = _pin(child_a, net_a_id, "R1", component_source_id="child-a:component:shared-symbol")
    pin_b = _pin(child_b, net_b_id, "R1", component_source_id="child-b:component:shared-symbol")

    design = resolve_kicad_source(
        _source(
            [_local_net(child_a, "sig", pins=[pin_a]), _local_net(child_b, "sig", pins=[pin_b])],
            [pin_a, pin_b],
            sheet_instances=[_sheet(child_a, "A"), _sheet(child_b, "B")],
        )
    )

    assert len(design.components) == 2
    assert {component.reference for component in design.components} == {"R1"}
    assert len({component.id for component in design.components}) == 2


def test_local_net_with_unknown_scope_fails_resolution() -> None:
    missing_scope = _scope("missing")
    net_id = "missing:local:sig"
    pin = _pin(missing_scope, net_id, "U1")

    with pytest.raises(ResolutionInputError, match=r"local net .* unknown scope"):
        resolve_kicad_source(
            _source(
                [_local_net(missing_scope, "sig", pins=[pin])],
                [pin],
                sheet_instances=[_sheet(_scope(), "Root")],
            )
        )


def test_pin_occurrence_with_unknown_scope_fails_resolution() -> None:
    root = _scope()
    missing_scope = _scope("missing")
    net_id = "root:local:sig"
    pin = _pin(missing_scope, net_id, "U1")

    with pytest.raises(ResolutionInputError, match=r"pin .* unknown scope"):
        resolve_kicad_source(
            _source(
                [_local_net(root, "sig", pins=[pin])],
                [pin],
                sheet_instances=[_sheet(root, "Root")],
            )
        )


def test_pin_occurrence_with_unknown_local_net_fails_resolution() -> None:
    root = _scope()
    missing_net_id = "root:local:missing"
    pin = _pin(root, missing_net_id, "U1")

    with pytest.raises(ResolutionInputError, match=r"pin .* unknown local net"):
        resolve_kicad_source(
            _source(
                [_local_net(root, "sig")],
                [pin],
                sheet_instances=[_sheet(root, "Root")],
            )
        )


def test_repeated_pin_occurrence_with_unknown_local_net_fails_resolution() -> None:
    root = _scope()
    valid_net_id = "root:local:sig"
    missing_net_id = "root:local:missing"
    first_pin = _pin(root, valid_net_id, "U1")
    second_pin = _pin(root, missing_net_id, "U1", index=2)

    with pytest.raises(ResolutionInputError, match=r"pin .* unknown local net"):
        resolve_kicad_source(
            _source(
                [_local_net(root, "sig", pins=[first_pin])],
                [first_pin, second_pin],
                sheet_instances=[_sheet(root, "Root")],
            )
        )


def test_repeated_logical_pin_preserves_first_no_connect_state() -> None:
    root = _scope()
    first_net_id = "root:local:first"
    second_net_id = "root:local:second"
    first_pin = _pin(root, first_net_id, "U1", designator="1", index=1)
    second_pin = _pin(root, second_net_id, "U1", designator="1", index=2)
    second_pin.no_connect = True

    design = resolve_kicad_source(
        _source(
            [
                _local_net(root, "first", pins=[first_pin]),
                _local_net(root, "second", pins=[second_pin]),
            ],
            [first_pin, second_pin],
        )
    )

    [component] = design.components
    [pin] = component.pins
    assert pin.no_connect is False


def test_global_label_with_unknown_local_net_fails_resolution() -> None:
    root = _scope()
    net_id = "root:local:sig"
    label = _global_label(root, "root:local:missing", "VCC")

    with pytest.raises(ResolutionInputError, match=r"global label .* unknown local net"):
        resolve_kicad_source(
            _source(
                [_local_net(root, "sig", global_labels=[label])],
                [_pin(root, net_id, "U1")],
                sheet_instances=[_sheet(root, "Root")],
            )
        )


def test_global_label_scope_must_match_local_net_scope() -> None:
    root = _scope()
    child = _scope("child")
    net_id = "root:local:sig"
    label = _global_label(child, net_id, "VCC")
    pin = _pin(root, net_id, "U1")

    with pytest.raises(ResolutionInputError, match=r"global label .* scope .* local net"):
        resolve_kicad_source(
            _source(
                [_local_net(root, "sig", global_labels=[label], pins=[pin])],
                [pin],
                sheet_instances=[_sheet(root, "Root"), _sheet(child, "Child")],
            )
        )


def test_top_level_sheet_pin_with_unknown_local_net_fails_resolution() -> None:
    root = _scope()
    child = _scope("child")
    symbol_id = "root:sheet_symbol:child"
    child_net_id = "child:local:sig"
    child_pin = _pin(child, child_net_id, "U1")
    source = _source(
        [
            _local_net(
                child,
                "sig",
                hierarchical_labels=[_hier_label(child, child_net_id, "SIG")],
                pins=[child_pin],
            )
        ],
        [child_pin],
        sheet_instances=[_sheet(root, "Root"), _sheet(child, "Child")],
    )
    source.sheet_pins.append(_sheet_pin(root, "root:local:missing", "SIG", symbol_id, child))

    with pytest.raises(ResolutionInputError, match=r"sheet pin .* unknown local net"):
        resolve_kicad_source(source)
