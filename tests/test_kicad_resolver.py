"""Tests for KiCad source-to-public schematic resolution."""

import pytest

from phosphor_eda.formats.kicad.resolver import resolve_kicad_source, select_kicad_net_name
from phosphor_eda.formats.kicad.source import (
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
from phosphor_eda.formats.common.resolved_graph import ResolutionInputError
from phosphor_eda.domain.schematic import Net, ScopeId


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


def _power_symbol(scope_id: ScopeId, net_id: str, name: str, index: int = 1) -> KiCadPowerSymbol:
    return KiCadPowerSymbol(
        id=f"{net_id}:power:{index}",
        scope_id=scope_id,
        source_index=index,
        name=name,
        reference=f"#PWR{index}",
        lib_id=f"power:{name}",
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
    component_source_id: str = "",
    component_identity_source_id: str = "",
    component_unit: int = 1,
    index: int = 1,
) -> KiCadPinOccurrence:
    source_id = component_source_id or f"{_scope_key(scope_id)}:component:{reference}"
    identity_source_id = component_identity_source_id or source_id
    return KiCadPinOccurrence(
        id=f"{net_id}:pin:{reference}:{designator}:{index}",
        scope_id=scope_id,
        source_index=index,
        local_net_id=net_id,
        component_source_id=source_id,
        component_identity_source_id=identity_source_id,
        component_unit=component_unit,
        component_reference=reference,
        pin_designator=designator,
        pin_name=f"{reference}-{designator}",
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
        generated_name=generated_name or f"__auto_{_scope_key(scope_id)}_{key}",
    )


def _source(
    local_nets: list[KiCadLocalNet],
    pins: list[KiCadPinOccurrence],
    *,
    sheet_instances: list[KiCadSheetInstance] | None = None,
    sheet_symbols: list[KiCadSheetSymbol] | None = None,
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
        power_symbols=[symbol for net in local_nets for symbol in net.power_symbols],
        sheet_symbols=sheet_symbols or [],
        sheet_pins=[pin for net in local_nets for pin in net.sheet_pins],
    )


def _net_for_reference(nets: list[Net], reference: str) -> Net:
    for net in nets:
        if any(pin.component.reference == reference for pin in net.pins):
            return net
    raise AssertionError(f"No net found for {reference}")


def _refs(net: Net) -> set[str]:
    return {pin.component.reference for pin in net.pins}


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
    assert resolved.name == "SIG"


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
    assert {"VCC", "LOCAL", "HIER", "PIN", "__auto_root_mixed"}.issubset(resolved.aliases)


def test_name_priority_falls_back_to_generated_local_net_name() -> None:
    root = _scope()
    net_id = "root:local:anonymous"
    pin = _pin(root, net_id, "U1")

    design = resolve_kicad_source(
        _source(
            [_local_net(root, "anonymous", pins=[pin], generated_name="__auto_root_anon")], [pin]
        )
    )

    assert _net_for_reference(design.nets, "U1").name == "__auto_root_anon"


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
    assert select_kicad_net_name([local_net]) == "LOCAL"
    assert select_kicad_net_name([hierarchical_net]) == "HIER"
    assert select_kicad_net_name([sheet_pin_net]) == "PIN"


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
