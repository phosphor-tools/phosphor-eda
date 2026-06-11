"""Tests for Altium source-to-public schematic resolution."""

import pytest

from phosphor_eda.domain.schematic import Net, ScopeId
from phosphor_eda.formats.altium.annotation import AnnotationDesignator
from phosphor_eda.formats.altium.project import AltiumHierarchyMode, AltiumProject
from phosphor_eda.formats.altium.resolver import resolve_altium_source
from phosphor_eda.formats.altium.source import (
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
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.resolved_graph import ResolutionInputError


def _scope(name: str) -> ScopeId:
    return ScopeId(path=(name,))


def _label(sheet: str, name: str, index: int = 1) -> AltiumNetLabel:
    return AltiumNetLabel(
        id=f"{sheet}:label:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=name,
        location=(index, 10),
    )


def _power(sheet: str, name: str, index: int = 1) -> AltiumPowerPort:
    return AltiumPowerPort(
        id=f"{sheet}:power:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=name,
        location=(index, 20),
        style=0,
        orientation=0,
        show_net_name=True,
    )


def _port(sheet: str, name: str, index: int = 1) -> AltiumPort:
    return AltiumPort(
        id=f"{sheet}:port:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=name,
        location=(index, 30),
        wire_coord=(index, 30),
        harness_type="",
        io_type=0,
        style=0,
    )


def _entry(
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
        coord=(index, 40),
        side=0,
        distance_from_top=0,
        harness_type="",
        io_type=0,
    )


def _symbol(
    sheet: str,
    child_source_file: str,
    index: int = 1,
) -> AltiumSheetSymbol:
    return AltiumSheetSymbol(
        id=f"{sheet}:symbol:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=f"{sheet}-symbol-{index}",
        child_source_file=child_source_file,
        location=(index, 50),
        x_size=100,
        y_size=100,
    )


def _pin(
    sheet: str,
    local_net_id: str,
    reference: str,
    designator: str = "1",
    component_source_id: str = "",
    component_occurrence_source_id: str = "",
    index: int = 1,
) -> AltiumPinOccurrence:
    return AltiumPinOccurrence(
        id=f"{sheet}:pin:{reference}:{designator}:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        local_net_id=local_net_id,
        component_source_id=component_source_id or f"{sheet}:component:{reference}",
        component_occurrence_source_id=component_occurrence_source_id,
        component_reference=reference,
        pin_designator=designator,
        pin_name=f"{reference}-{designator}",
        location=(index, 60),
        tip=(index, 61),
    )


def _local_net(
    sheet: str,
    name: str,
    *,
    labels: list[AltiumNetLabel] | None = None,
    powers: list[AltiumPowerPort] | None = None,
    ports: list[AltiumPort] | None = None,
    entries: list[AltiumSheetEntry] | None = None,
    references: list[str] | None = None,
    component_source_ids: list[str] | None = None,
    pin_designators: list[str] | None = None,
) -> tuple[AltiumLocalNet, list[AltiumPinOccurrence]]:
    local_net_id = f"{sheet}:local:{name}"
    refs = references or []
    source_ids = component_source_ids or []
    designators = pin_designators or []
    pins = [
        _pin(
            sheet,
            local_net_id,
            reference,
            designator=designators[index] if index < len(designators) else "1",
            component_source_id=source_ids[index] if index < len(source_ids) else "",
            index=index + 1,
        )
        for index, reference in enumerate(refs)
    ]
    return (
        AltiumLocalNet(
            id=local_net_id,
            scope_id=_scope(sheet),
            wire_points=set(),
            pin_ids=[pin.id for pin in pins],
            net_labels=labels or [],
            power_ports=powers or [],
            ports=ports or [],
            sheet_entries=entries or [],
            harness_members=[],
            generated_name=f"__auto_{sheet}_{name}",
        ),
        pins,
    )


def _sheet(
    name: str,
    local_nets: list[AltiumLocalNet],
    pin_occurrences: list[AltiumPinOccurrence],
    *,
    sheet_symbols: list[AltiumSheetSymbol] | None = None,
    sheet_entries: list[AltiumSheetEntry] | None = None,
    source_file: str = "",
    scope_id: ScopeId | None = None,
) -> AltiumSheetSource:
    return AltiumSheetSource(
        id=f"sheet:{name}",
        name=name,
        source_file=source_file or f"{name}.SchDoc",
        scope_id=scope_id or _scope(name),
        local_nets=local_nets,
        sheet_symbols=sheet_symbols or [],
        sheet_entries=sheet_entries or [],
        harness_connectors=[],
        harness_members=[],
        pin_occurrences=pin_occurrences,
    )


def _source(
    sheets: list[AltiumSheetSource],
    *,
    mode: AltiumHierarchyMode = AltiumHierarchyMode.FLAT,
    allow_port_net_names: bool = False,
    allow_sheet_entry_net_names: bool = True,
    power_port_names_take_priority: bool = False,
    root_sheet_name: str = "",
) -> AltiumSourceDesign:
    return AltiumSourceDesign(
        name="test",
        project=AltiumProject(
            hierarchy_mode=mode,
            allow_port_net_names=allow_port_net_names,
            allow_sheet_entry_net_names=allow_sheet_entry_net_names,
            power_port_names_take_priority=power_port_names_take_priority,
        ),
        sheets={sheet.name: sheet for sheet in sheets},
        root_sheet_name=root_sheet_name or sheets[0].name,
    )


def _net_for_reference(nets: list[Net], reference: str) -> Net:
    for net in nets:
        if any(pin.component.reference == reference for pin in net.pins):
            return net
    raise AssertionError(f"No net found for {reference}")


def _refs(net: Net) -> set[str]:
    return {pin.component.reference for pin in net.pins}


def test_smart_effective_mode_chooses_hierarchical_power_global_when_root_has_entries():
    symbol = _symbol("Top", "Child.SchDoc")
    entry = _entry("Top", "SIG", symbol.id)
    parent_net, parent_pins = _local_net("Top", "parent", entries=[entry], references=["U_PARENT"])
    child_net, child_pins = _local_net(
        "Child",
        "child",
        ports=[_port("Child", "SIG")],
        references=["U_CHILD"],
    )

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
            mode=AltiumHierarchyMode.SMART,
            root_sheet_name="Top",
        ),
    )

    assert design.metadata["altium_effective_hierarchy_mode"] == "HIERARCHICAL_POWER_GLOBAL"
    assert _refs(_net_for_reference(design.nets, "U_PARENT")) == {"U_PARENT", "U_CHILD"}


def test_smart_effective_mode_chooses_flat_when_only_ports_exist():
    net_a, pins_a = _local_net("A", "sig", ports=[_port("A", "SIG")], references=["U1"])
    net_b, pins_b = _local_net("B", "sig", ports=[_port("B", "SIG")], references=["U2"])

    design = resolve_altium_source(
        _source(
            [_sheet("A", [net_a], pins_a), _sheet("B", [net_b], pins_b)],
            mode=AltiumHierarchyMode.SMART,
        ),
    )

    assert design.metadata["altium_effective_hierarchy_mode"] == "FLAT"
    assert _refs(_net_for_reference(design.nets, "U1")) == {"U1", "U2"}


def test_smart_effective_mode_chooses_global_without_sheet_entries_or_ports():
    net_a, pins_a = _local_net("A", "sig", labels=[_label("A", "SIG")], references=["U1"])
    net_b, pins_b = _local_net("B", "sig", labels=[_label("B", "SIG")], references=["U2"])

    design = resolve_altium_source(
        _source(
            [_sheet("A", [net_a], pins_a), _sheet("B", [net_b], pins_b)],
            mode=AltiumHierarchyMode.SMART,
        ),
    )

    assert design.metadata["altium_effective_hierarchy_mode"] == "GLOBAL"
    assert _refs(_net_for_reference(design.nets, "U1")) == {"U1", "U2"}


def test_flat_merges_same_name_ports_but_not_same_name_net_labels_across_sheets():
    port_a, port_pins_a = _local_net("A", "port", ports=[_port("A", "SIG")], references=["P1"])
    port_b, port_pins_b = _local_net("B", "port", ports=[_port("B", "SIG")], references=["P2"])
    label_a, label_pins_a = _local_net(
        "A",
        "label",
        labels=[_label("A", "LOCAL")],
        references=["L1"],
    )
    label_b, label_pins_b = _local_net(
        "B",
        "label",
        labels=[_label("B", "LOCAL")],
        references=["L2"],
    )

    design = resolve_altium_source(
        _source(
            [
                _sheet("A", [port_a, label_a], [*port_pins_a, *label_pins_a]),
                _sheet("B", [port_b, label_b], [*port_pins_b, *label_pins_b]),
            ],
            mode=AltiumHierarchyMode.FLAT,
        ),
    )

    assert _refs(_net_for_reference(design.nets, "P1")) == {"P1", "P2"}
    assert _refs(_net_for_reference(design.nets, "L1")) == {"L1"}
    assert _refs(_net_for_reference(design.nets, "L2")) == {"L2"}


def test_global_merges_same_name_ports_and_same_name_net_labels_across_sheets():
    port_a, port_pins_a = _local_net("A", "port", ports=[_port("A", "SIG")], references=["P1"])
    port_b, port_pins_b = _local_net("B", "port", ports=[_port("B", "SIG")], references=["P2"])
    label_a, label_pins_a = _local_net(
        "A",
        "label",
        labels=[_label("A", "LOCAL")],
        references=["L1"],
    )
    label_b, label_pins_b = _local_net(
        "B",
        "label",
        labels=[_label("B", "LOCAL")],
        references=["L2"],
    )

    design = resolve_altium_source(
        _source(
            [
                _sheet("A", [port_a, label_a], [*port_pins_a, *label_pins_a]),
                _sheet("B", [port_b, label_b], [*port_pins_b, *label_pins_b]),
            ],
            mode=AltiumHierarchyMode.GLOBAL,
        ),
    )

    assert _refs(_net_for_reference(design.nets, "P1")) == {"P1", "P2"}
    assert _refs(_net_for_reference(design.nets, "L1")) == {"L1", "L2"}


def test_hierarchical_power_global_merges_ports_only_through_matching_sheet_entries():
    symbol = _symbol("Top", "ChildA.SchDoc")
    entry = _entry("Top", "SIG", symbol.id)
    parent_net, parent_pins = _local_net("Top", "parent", entries=[entry], references=["PARENT"])
    child_a_net, child_a_pins = _local_net(
        "ChildA",
        "sig",
        ports=[_port("ChildA", "SIG")],
        references=["A1"],
    )
    child_b_net, child_b_pins = _local_net(
        "ChildB",
        "sig",
        ports=[_port("ChildB", "SIG")],
        references=["B1"],
    )
    power_a_net, power_a_pins = _local_net(
        "ChildA",
        "gnd",
        powers=[_power("ChildA", "GND")],
        references=["GA"],
    )
    power_b_net, power_b_pins = _local_net(
        "ChildB",
        "gnd",
        powers=[_power("ChildB", "GND")],
        references=["GB"],
    )

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
                _sheet(
                    "ChildA",
                    [child_a_net, power_a_net],
                    [*child_a_pins, *power_a_pins],
                    source_file="ChildA.SchDoc",
                ),
                _sheet(
                    "ChildB",
                    [child_b_net, power_b_net],
                    [*child_b_pins, *power_b_pins],
                    source_file="ChildB.SchDoc",
                ),
            ],
            mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
            root_sheet_name="Top",
        ),
    )

    assert _refs(_net_for_reference(design.nets, "PARENT")) == {"PARENT", "A1"}
    assert _refs(_net_for_reference(design.nets, "B1")) == {"B1"}
    assert _refs(_net_for_reference(design.nets, "GA")) == {"GA", "GB"}


def test_hierarchical_repeated_sheet_symbols_use_specific_child_instance_scope():
    symbol_a = _symbol("Top", "Child.SchDoc", index=1)
    symbol_b = _symbol("Top", "Child.SchDoc", index=2)
    entry_a = _entry("Top", "SIG", symbol_a.id, index=1)
    entry_b = _entry("Top", "SIG", symbol_b.id, index=2)
    parent_a_net, parent_a_pins = _local_net(
        "Top",
        "parent_a",
        entries=[entry_a],
        references=["PARENT_A"],
    )
    parent_b_net, parent_b_pins = _local_net(
        "Top",
        "parent_b",
        entries=[entry_b],
        references=["PARENT_B"],
    )
    child_a_net, child_a_pins = _local_net(
        "ChildA",
        "sig",
        ports=[_port("ChildA", "SIG")],
        references=["A1"],
    )
    child_b_net, child_b_pins = _local_net(
        "ChildB",
        "sig",
        ports=[_port("ChildB", "SIG")],
        references=["B1"],
    )
    child_a_scope = ScopeId(path=("Top", symbol_a.id, "ChildA"))
    child_b_scope = ScopeId(path=("Top", symbol_b.id, "ChildB"))
    child_a_net.scope_id = child_a_scope
    child_a_net.ports[0].scope_id = child_a_scope
    child_a_pins[0].scope_id = child_a_scope
    child_b_net.scope_id = child_b_scope
    child_b_net.ports[0].scope_id = child_b_scope
    child_b_pins[0].scope_id = child_b_scope

    design = resolve_altium_source(
        _source(
            [
                _sheet(
                    "Top",
                    [parent_a_net, parent_b_net],
                    [*parent_a_pins, *parent_b_pins],
                    sheet_symbols=[symbol_a, symbol_b],
                    sheet_entries=[entry_a, entry_b],
                ),
                _sheet(
                    "ChildA",
                    [child_a_net],
                    child_a_pins,
                    source_file="Child.SchDoc",
                    scope_id=child_a_scope,
                ),
                _sheet(
                    "ChildB",
                    [child_b_net],
                    child_b_pins,
                    source_file="Child.SchDoc",
                    scope_id=child_b_scope,
                ),
            ],
            mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
            root_sheet_name="Top",
        ),
    )

    assert _refs(_net_for_reference(design.nets, "PARENT_A")) == {"PARENT_A", "A1"}
    assert _refs(_net_for_reference(design.nets, "PARENT_B")) == {"PARENT_B", "B1"}


def test_hierarchical_power_local_does_not_merge_same_name_power_ports_across_sheets():
    symbol = _symbol("Top", "Child.SchDoc")
    entry = _entry("Top", "SIG", symbol.id)
    parent_net, parent_pins = _local_net("Top", "parent", entries=[entry], references=["PARENT"])
    child_net, child_pins = _local_net(
        "Child",
        "child",
        ports=[_port("Child", "SIG")],
        references=["CHILD"],
    )
    power_a_net, power_a_pins = _local_net(
        "Top",
        "gnd",
        powers=[_power("Top", "GND")],
        references=["GA"],
    )
    power_b_net, power_b_pins = _local_net(
        "Child",
        "gnd",
        powers=[_power("Child", "GND")],
        references=["GB"],
    )

    design = resolve_altium_source(
        _source(
            [
                _sheet(
                    "Top",
                    [parent_net, power_a_net],
                    [*parent_pins, *power_a_pins],
                    sheet_symbols=[symbol],
                    sheet_entries=[entry],
                ),
                _sheet(
                    "Child",
                    [child_net, power_b_net],
                    [*child_pins, *power_b_pins],
                    source_file="Child.SchDoc",
                ),
            ],
            mode=AltiumHierarchyMode.HIERARCHICAL_POWER_LOCAL,
            root_sheet_name="Top",
        ),
    )

    assert _refs(_net_for_reference(design.nets, "PARENT")) == {"PARENT", "CHILD"}
    assert _refs(_net_for_reference(design.nets, "GA")) == {"GA"}
    assert _refs(_net_for_reference(design.nets, "GB")) == {"GB"}


def test_net_labels_do_not_connect_to_same_name_ports_by_name():
    label_net, label_pins = _local_net("A", "label", labels=[_label("A", "SIG")], references=["L1"])
    port_net, port_pins = _local_net("B", "port", ports=[_port("B", "SIG")], references=["P1"])

    design = resolve_altium_source(
        _source(
            [_sheet("A", [label_net], label_pins), _sheet("B", [port_net], port_pins)],
            mode=AltiumHierarchyMode.GLOBAL,
        ),
    )

    assert _refs(_net_for_reference(design.nets, "L1")) == {"L1"}
    assert _refs(_net_for_reference(design.nets, "P1")) == {"P1"}


def test_allow_port_net_names_false_keeps_port_connectivity_without_using_port_final_name():
    net_a, pins_a = _local_net("A", "sig", ports=[_port("A", "SIG")], references=["U1"])
    net_b, pins_b = _local_net("B", "sig", ports=[_port("B", "SIG")], references=["U2"])

    design = resolve_altium_source(
        _source(
            [_sheet("A", [net_a], pins_a), _sheet("B", [net_b], pins_b)],
            mode=AltiumHierarchyMode.FLAT,
            allow_port_net_names=False,
        ),
    )

    resolved = _net_for_reference(design.nets, "U1")
    assert _refs(resolved) == {"U1", "U2"}
    assert resolved.name != "SIG"


def test_allow_sheet_entry_net_names_false_keeps_hierarchy_without_using_entry_final_name():
    symbol = _symbol("Top", "Child.SchDoc")
    entry = _entry("Top", "SIG", symbol.id)
    parent_net, parent_pins = _local_net("Top", "parent", entries=[entry], references=["PARENT"])
    child_net, child_pins = _local_net(
        "Child",
        "child",
        ports=[_port("Child", "SIG")],
        references=["CHILD"],
    )

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
        ),
    )

    resolved = _net_for_reference(design.nets, "PARENT")
    assert _refs(resolved) == {"PARENT", "CHILD"}
    assert resolved.name != "SIG"


def test_power_port_names_take_priority_changes_final_name_priority():
    low_priority_net, low_priority_pins = _local_net(
        "A",
        "mixed",
        labels=[_label("A", "LOCAL")],
        powers=[_power("A", "GND")],
        references=["U1"],
    )
    high_priority_net, high_priority_pins = _local_net(
        "A",
        "mixed",
        labels=[_label("A", "LOCAL")],
        powers=[_power("A", "GND")],
        references=["U1"],
    )

    low_priority = resolve_altium_source(
        _source(
            [_sheet("A", [low_priority_net], low_priority_pins)],
            power_port_names_take_priority=False,
        ),
    )
    high_priority = resolve_altium_source(
        _source(
            [_sheet("A", [high_priority_net], high_priority_pins)],
            power_port_names_take_priority=True,
        ),
    )

    assert _net_for_reference(low_priority.nets, "U1").name == "LOCAL"
    assert _net_for_reference(high_priority.nets, "U1").name == "GND"


def test_multipart_components_across_pages_use_source_component_identity():
    part_a_net, part_a_pins = _local_net(
        "A",
        "a",
        labels=[_label("A", "A")],
        references=["U1"],
        component_source_ids=["source:logical:u1"],
        pin_designators=["1"],
    )
    part_b_net, part_b_pins = _local_net(
        "B",
        "b",
        labels=[_label("B", "B")],
        references=["U1"],
        component_source_ids=["source:logical:u1"],
        pin_designators=["2"],
    )

    design = resolve_altium_source(
        _source([_sheet("A", [part_a_net], part_a_pins), _sheet("B", [part_b_net], part_b_pins)]),
    )

    assert len(design.components) == 1
    assert design.components[0].reference == "U1"
    assert {pin.designator for pin in design.components[0].pins} == {"1", "2"}
    assert {page.name for page in design.components[0].pages} == {"A", "B"}


def test_multipart_component_occurrences_use_source_block_identity_and_pin_provenance():
    part_a_net, part_a_pins = _local_net(
        "A",
        "a",
        labels=[_label("A", "A")],
        references=["U1"],
        component_source_ids=["altium:component:/A:uid:ABC123"],
        pin_designators=["1"],
    )
    part_a_pins[0].component_occurrence_source_id = "sheet:A:component:10"
    part_b_net, part_b_pins = _local_net(
        "A",
        "b",
        labels=[_label("A", "B")],
        references=["U1"],
        component_source_ids=["altium:component:/A:uid:ABC123"],
        pin_designators=["2"],
    )
    part_b_pins[0].component_occurrence_source_id = "sheet:A:component:22"

    design = resolve_altium_source(
        _source([_sheet("A", [part_a_net, part_b_net], [*part_a_pins, *part_b_pins])]),
    )

    assert len(design.components) == 1
    component = design.components[0]
    assert component.reference == "U1"
    assert {occurrence.source_id for occurrence in component.occurrences} == {
        "sheet:A:component:10",
        "sheet:A:component:22",
    }
    assert {pin.designator for pin in component.pins} == {"1", "2"}
    assert all(len(pin.occurrences) == 1 for pin in component.pins)
    assert {pin.occurrences[0].source_id for pin in component.pins} == {
        "A:pin:U1:1:1",
        "A:pin:U1:2:1",
    }


def test_repeated_independent_sheet_instances_with_same_reference_stay_distinct():
    sheet_a_net, sheet_a_pins = _local_net(
        "A",
        "a",
        labels=[_label("A", "A")],
        references=["U1"],
        component_source_ids=["instance:a:u1"],
    )
    sheet_b_net, sheet_b_pins = _local_net(
        "B",
        "b",
        labels=[_label("B", "B")],
        references=["U1"],
        component_source_ids=["instance:b:u1"],
    )

    design = resolve_altium_source(
        _source(
            [
                _sheet("A", [sheet_a_net], sheet_a_pins),
                _sheet("B", [sheet_b_net], sheet_b_pins),
            ],
        ),
    )

    assert len(design.components) == 2
    assert {component.reference for component in design.components} == {"U1"}
    assert len({component.id for component in design.components}) == 2


def test_pin_occurrence_with_unknown_scope_fails_resolution():
    net, _pins = _local_net("A", "sig")
    pin = _pin("Missing", net.id, "U1")

    with pytest.raises(ResolutionInputError, match="pin .* unknown scope"):
        resolve_altium_source(_source([_sheet("A", [net], [pin])]))


def test_pin_occurrence_with_unknown_local_net_fails_resolution():
    net, _pins = _local_net("A", "sig")
    pin = _pin("A", "A:local:missing", "U1")

    with pytest.raises(ResolutionInputError, match="pin .* unknown local net"):
        resolve_altium_source(_source([_sheet("A", [net], [pin])]))


def test_net_label_scope_must_match_containing_local_net_scope():
    attached_net, attached_pins = _local_net(
        "A",
        "attached",
        labels=[_label("B", "SIG")],
        references=["U1"],
    )
    other_net, other_pins = _local_net("A", "other", references=["U2"])

    with pytest.raises(
        ResolutionInputError,
        match="net label .* scope .* local net",
    ):
        resolve_altium_source(
            _source(
                [
                    _sheet(
                        "A",
                        [attached_net, other_net],
                        [*attached_pins, *other_pins],
                    ),
                    _sheet("B", [], []),
                ]
            )
        )


def test_unattached_top_level_sheet_entry_is_allowed_and_ignored():
    symbol = _symbol("Top", "Child.SchDoc")
    entry = _entry("Top", "SIG", symbol.id)
    local_net, pins = _local_net("Top", "attached", references=["U1"])

    design = resolve_altium_source(
        _source(
            [
                _sheet(
                    "Top",
                    [local_net],
                    pins,
                    sheet_symbols=[symbol],
                    sheet_entries=[entry],
                ),
                _sheet("Child", [], [], source_file="Child.SchDoc"),
            ],
            mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
            root_sheet_name="Top",
        )
    )

    [net] = design.nets
    assert _refs(net) == {"U1"}


def test_repeated_logical_pin_preserves_first_no_connect_state_and_dedupes_source():
    first_net, first_pins = _local_net("A", "first", references=["U1"])
    second_net, second_pins = _local_net("A", "second", references=["U1"])
    first_pins[0].id = "A:pin:U1:1:shared"
    second_pins[0].id = "A:pin:U1:1:shared"
    second_pins[0].no_connect = True

    design = resolve_altium_source(
        _source(
            [
                _sheet(
                    "A",
                    [first_net, second_net],
                    [*first_pins, *second_pins],
                )
            ]
        )
    )

    [component] = design.components
    [pin] = component.pins
    assert pin.no_connect is False
    assert [occurrence.source_id for occurrence in pin.occurrences] == ["A:pin:U1:1:shared"]


def _instance_pin(
    sheet: str,
    scope_path: tuple[str, ...],
    component_uid: str,
    reference: str = "U1",
) -> AltiumPinOccurrence:
    return AltiumPinOccurrence(
        id=f"{sheet}:pin:{reference}:{component_uid}",
        scope_id=ScopeId(path=scope_path),
        source_index=1,
        local_net_id=f"{sheet}:local:net",
        component_source_id=f"comp:{sheet}:{component_uid}",
        component_reference=reference,
        pin_designator="2",
        pin_name="A",
        location=(0, 0),
        tip=(0, 1),
        component_metadata={"altium_component_unique_id": component_uid},
    )


def _instance_sheet(
    sheet: str,
    scope_path: tuple[str, ...],
    pin: AltiumPinOccurrence,
    sheet_symbols: list[AltiumSheetSymbol] | None = None,
) -> AltiumSheetSource:
    scope = ScopeId(path=scope_path)
    local_net = AltiumLocalNet(
        id=pin.local_net_id,
        scope_id=scope,
        wire_points=set(),
        pin_ids=[pin.id],
        net_labels=[
            AltiumNetLabel(
                id=f"{sheet}:label:1",
                scope_id=scope,
                source_index=1,
                name=f"{sheet}_NET",
                location=(1, 10),
            )
        ],
        power_ports=[],
        ports=[],
        sheet_entries=[],
        harness_members=[],
        generated_name=f"__auto_{sheet}",
    )
    return AltiumSheetSource(
        id=f"sheet:{sheet}",
        name=sheet,
        source_file=f"{sheet}.SchDoc",
        scope_id=scope,
        local_nets=[local_net],
        sheet_symbols=sheet_symbols or [],
        sheet_entries=[],
        harness_connectors=[],
        harness_members=[],
        pin_occurrences=[pin],
    )


def _symbol_with_uid(symbol_id: str, unique_id: str) -> AltiumSheetSymbol:
    return AltiumSheetSymbol(
        id=symbol_id,
        scope_id=ScopeId(path=("Title",)),
        source_index=1,
        name=symbol_id,
        child_source_file="child.SchDoc",
        location=(0, 0),
        x_size=100,
        y_size=100,
        unique_id=unique_id,
    )


def test_repeated_instance_occurrence_carries_physical_designator():
    """Two instances of the same logical U1 get their own physical designators.

    The logical reference stays U1; each occurrence carries the .Annotation
    physical designator resolved from its hierarchical unique-id path. The path
    is built from the sheet-symbol unique IDs of the scope's symbol ids.
    """
    sym_a = _symbol_with_uid("sym_a", "FEHIXTLT")
    sym_b = _symbol_with_uid("sym_b", "TPZRYUFR")
    parent = _instance_sheet(
        "Title",
        ("Title",),
        _instance_pin("Title", ("Title",), "PARENT", reference="J1"),
        sheet_symbols=[sym_a, sym_b],
    )
    pin_a = _instance_pin("A", ("Title", "sym_a", "child"), "VIIQXJDH")
    pin_b = _instance_pin("B", ("Title", "sym_b", "child"), "VIIQXJDH")
    sheet_a = _instance_sheet("A", ("Title", "sym_a", "child"), pin_a)
    sheet_b = _instance_sheet("B", ("Title", "sym_b", "child"), pin_b)

    source = _source([parent, sheet_a, sheet_b])
    source.physical_designators = {
        "\\FEHIXTLT\\VIIQXJDH": AnnotationDesignator(physical_designator="U1.1"),
        "\\TPZRYUFR\\VIIQXJDH": AnnotationDesignator(physical_designator="U1.3"),
    }

    design = resolve_altium_source(source)

    designators: set[str] = set()
    for component in design.components:
        if component.reference != "U1":
            continue
        for occurrence in component.occurrences:
            designators.add(occurrence.physical_designator)
    assert designators == {"U1.1", "U1.3"}


def test_hierarchy_merge_warns_on_ambiguous_child_basename():
    """A child reference matching multiple documents by basename warns on ctx."""
    symbol = _symbol("Top", "Child.SchDoc")
    entry = _entry("Top", "SIG", symbol.id)
    parent_net, parent_pins = _local_net("Top", "parent", entries=[entry], references=["PARENT"])
    child_a_net, child_a_pins = _local_net(
        "ChildA",
        "sig",
        ports=[_port("ChildA", "SIG")],
        references=["A1"],
    )
    child_b_net, child_b_pins = _local_net(
        "ChildB",
        "sig",
        ports=[_port("ChildB", "SIG")],
        references=["B1"],
    )

    ctx = ParseContext()
    _ = resolve_altium_source(
        _source(
            [
                _sheet(
                    "Top",
                    [parent_net],
                    parent_pins,
                    sheet_symbols=[symbol],
                    sheet_entries=[entry],
                ),
                _sheet("ChildA", [child_a_net], child_a_pins, source_file="A/Child.SchDoc"),
                _sheet("ChildB", [child_b_net], child_b_pins, source_file="B/Child.SchDoc"),
            ],
            mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
            root_sheet_name="Top",
        ),
        ctx,
    )

    ambiguous = [issue for issue in ctx.issues if issue.category == "ambiguous_document_reference"]
    assert ambiguous
    assert "Child.SchDoc" in ambiguous[0].message


def test_unannotated_occurrence_has_empty_physical_designator():
    """With no .Annotation data, occurrences carry no physical designator."""
    pin = _instance_pin("A", ("Title", "sym_a", "child"), "VIIQXJDH", reference="U6")
    sheet = _instance_sheet("A", ("Title", "sym_a", "child"), pin)

    design = resolve_altium_source(_source([sheet]))

    for component in design.components:
        for occurrence in component.occurrences:
            assert occurrence.physical_designator == ""
