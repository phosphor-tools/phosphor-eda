"""Altium net-scope regressions at the source resolver boundary.

The matching fixture README documents why these are not hand-authored
`.SchDoc` parser fixtures.
"""

from phosphor_eda.altium.project import AltiumHierarchyMode, AltiumProject
from phosphor_eda.altium.resolver import resolve_altium_source
from phosphor_eda.altium.source import (
    AltiumLocalNet,
    AltiumNetLabel,
    AltiumPinOccurrence,
    AltiumPort,
    AltiumPowerPort,
    AltiumSheetEntry,
    AltiumSheetSource,
    AltiumSheetSymbol,
    AltiumSourceDesign,
)
from phosphor_eda.schematic import Net, ScopeId


def _scope(name: str) -> ScopeId:
    return ScopeId(path=(name,))


def _label(sheet: str, name: str) -> AltiumNetLabel:
    return AltiumNetLabel(
        id=f"{sheet}:label:{name}",
        scope_id=_scope(sheet),
        source_index=1,
        name=name,
        location=(10, 10),
    )


def _port(sheet: str, name: str) -> AltiumPort:
    return AltiumPort(
        id=f"{sheet}:port:{name}",
        scope_id=_scope(sheet),
        source_index=1,
        name=name,
        location=(20, 20),
        wire_coord=(20, 20),
        harness_type="",
        io_type=0,
        style=0,
    )


def _power(sheet: str, name: str) -> AltiumPowerPort:
    return AltiumPowerPort(
        id=f"{sheet}:power:{name}",
        scope_id=_scope(sheet),
        source_index=1,
        name=name,
        location=(30, 30),
        style=0,
        orientation=0,
        show_net_name=True,
    )


def _symbol(sheet: str, child_source_file: str) -> AltiumSheetSymbol:
    return AltiumSheetSymbol(
        id=f"{sheet}:symbol:child",
        scope_id=_scope(sheet),
        source_index=1,
        name="Child",
        child_source_file=child_source_file,
        location=(40, 40),
        x_size=100,
        y_size=100,
    )


def _entry(sheet: str, name: str, sheet_symbol_id: str) -> AltiumSheetEntry:
    return AltiumSheetEntry(
        id=f"{sheet}:entry:{name}",
        scope_id=_scope(sheet),
        source_index=1,
        sheet_symbol_id=sheet_symbol_id,
        name=name,
        coord=(50, 50),
        side=0,
        distance_from_top=0,
        harness_type="",
        io_type=0,
    )


def _pin(sheet: str, local_net_id: str, reference: str) -> AltiumPinOccurrence:
    return AltiumPinOccurrence(
        id=f"{sheet}:pin:{reference}",
        scope_id=_scope(sheet),
        source_index=1,
        local_net_id=local_net_id,
        component_source_id=f"{sheet}:component:{reference}",
        component_reference=reference,
        pin_designator="1",
        pin_name=f"{reference}-1",
        location=(60, 60),
        tip=(60, 61),
    )


def _local_net(
    sheet: str,
    name: str,
    reference: str,
    *,
    labels: list[AltiumNetLabel] | None = None,
    powers: list[AltiumPowerPort] | None = None,
    ports: list[AltiumPort] | None = None,
    entries: list[AltiumSheetEntry] | None = None,
) -> tuple[AltiumLocalNet, list[AltiumPinOccurrence]]:
    local_net_id = f"{sheet}:local:{name}"
    pin = _pin(sheet, local_net_id, reference)
    return (
        AltiumLocalNet(
            id=local_net_id,
            scope_id=_scope(sheet),
            wire_points=set(),
            pin_ids=[pin.id],
            net_labels=labels or [],
            power_ports=powers or [],
            ports=ports or [],
            sheet_entries=entries or [],
            harness_members=[],
            generated_name=f"__auto_{sheet}_{name}",
        ),
        [pin],
    )


def _sheet(
    name: str,
    local_nets: list[AltiumLocalNet],
    pins: list[AltiumPinOccurrence],
    *,
    sheet_symbols: list[AltiumSheetSymbol] | None = None,
    sheet_entries: list[AltiumSheetEntry] | None = None,
    source_file: str = "",
) -> AltiumSheetSource:
    return AltiumSheetSource(
        id=f"sheet:{name}",
        name=name,
        source_file=source_file or f"{name}.SchDoc",
        scope_id=_scope(name),
        local_nets=local_nets,
        sheet_symbols=sheet_symbols or [],
        sheet_entries=sheet_entries or [],
        harness_connectors=[],
        harness_members=[],
        pin_occurrences=pins,
    )


def _source(
    sheets: list[AltiumSheetSource],
    *,
    mode: AltiumHierarchyMode,
    allow_port_net_names: bool = True,
    allow_sheet_entry_net_names: bool = True,
) -> AltiumSourceDesign:
    return AltiumSourceDesign(
        name="test",
        project=AltiumProject(
            hierarchy_mode=mode,
            allow_port_net_names=allow_port_net_names,
            allow_sheet_entry_net_names=allow_sheet_entry_net_names,
        ),
        sheets={sheet.name: sheet for sheet in sheets},
        root_sheet_name=sheets[0].name,
    )


def _net_for_reference(nets: list[Net], reference: str) -> Net:
    for net in nets:
        if any(pin.component.reference == reference for pin in net.pins):
            return net
    raise AssertionError(f"No net found for {reference}")


def _refs(net: Net) -> set[str]:
    return {pin.component.reference for pin in net.pins}


def test_flat_port_same_name_merges() -> None:
    net_a, pins_a = _local_net("A", "sig", "P1", ports=[_port("A", "SIG")])
    net_b, pins_b = _local_net("B", "sig", "P2", ports=[_port("B", "SIG")])

    design = resolve_altium_source(
        _source(
            [_sheet("A", [net_a], pins_a), _sheet("B", [net_b], pins_b)],
            mode=AltiumHierarchyMode.FLAT,
        )
    )

    assert _refs(_net_for_reference(design.nets, "P1")) == {"P1", "P2"}


def test_global_net_label_same_name_merges() -> None:
    net_a, pins_a = _local_net("A", "sig", "L1", labels=[_label("A", "SIG")])
    net_b, pins_b = _local_net("B", "sig", "L2", labels=[_label("B", "SIG")])

    design = resolve_altium_source(
        _source(
            [_sheet("A", [net_a], pins_a), _sheet("B", [net_b], pins_b)],
            mode=AltiumHierarchyMode.GLOBAL,
        )
    )

    assert _refs(_net_for_reference(design.nets, "L1")) == {"L1", "L2"}


def test_hierarchical_sheet_entry_to_child_port_vertical_connection() -> None:
    symbol = _symbol("Top", "Child.SchDoc")
    entry = _entry("Top", "SIG", symbol.id)
    parent_net, parent_pins = _local_net("Top", "parent", "PARENT", entries=[entry])
    child_net, child_pins = _local_net("Child", "child", "CHILD", ports=[_port("Child", "SIG")])

    design = resolve_altium_source(
        _source(
            [
                _sheet(
                    "Top",
                    [parent_net],
                    parent_pins,
                    sheet_symbols=[symbol],
                    sheet_entries=[entry],
                ),
                _sheet("Child", [child_net], child_pins, source_file="Child.SchDoc"),
            ],
            mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        )
    )

    assert _refs(_net_for_reference(design.nets, "PARENT")) == {"PARENT", "CHILD"}


def test_strict_hierarchical_local_power_stays_sheet_local() -> None:
    top_power, top_power_pins = _local_net("Top", "gnd", "TOP_GND", powers=[_power("Top", "GND")])
    child_power, child_power_pins = _local_net(
        "Child",
        "gnd",
        "CHILD_GND",
        powers=[_power("Child", "GND")],
    )

    design = resolve_altium_source(
        _source(
            [
                _sheet("Top", [top_power], top_power_pins),
                _sheet("Child", [child_power], child_power_pins),
            ],
            mode=AltiumHierarchyMode.HIERARCHICAL_POWER_LOCAL,
        )
    )

    assert _refs(_net_for_reference(design.nets, "TOP_GND")) == {"TOP_GND"}
    assert _refs(_net_for_reference(design.nets, "CHILD_GND")) == {"CHILD_GND"}


def test_allow_port_net_names_false_keeps_merge_but_not_port_name() -> None:
    net_a, pins_a = _local_net("A", "sig", "P1", ports=[_port("A", "SIG")])
    net_b, pins_b = _local_net("B", "sig", "P2", ports=[_port("B", "SIG")])

    design = resolve_altium_source(
        _source(
            [_sheet("A", [net_a], pins_a), _sheet("B", [net_b], pins_b)],
            mode=AltiumHierarchyMode.FLAT,
            allow_port_net_names=False,
        )
    )
    resolved = _net_for_reference(design.nets, "P1")

    assert _refs(resolved) == {"P1", "P2"}
    assert resolved.name != "SIG"


def test_allow_sheet_entry_net_names_false_keeps_hierarchy_but_not_entry_name() -> None:
    symbol = _symbol("Top", "Child.SchDoc")
    entry = _entry("Top", "SIG", symbol.id)
    parent_net, parent_pins = _local_net("Top", "parent", "PARENT", entries=[entry])
    child_net, child_pins = _local_net("Child", "child", "CHILD", ports=[_port("Child", "SIG")])

    design = resolve_altium_source(
        _source(
            [
                _sheet(
                    "Top",
                    [parent_net],
                    parent_pins,
                    sheet_symbols=[symbol],
                    sheet_entries=[entry],
                ),
                _sheet("Child", [child_net], child_pins, source_file="Child.SchDoc"),
            ],
            mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
            allow_sheet_entry_net_names=False,
        )
    )
    resolved = _net_for_reference(design.nets, "PARENT")

    assert _refs(resolved) == {"PARENT", "CHILD"}
