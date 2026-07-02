"""Tests for OrCAD DSN source-to-public schematic resolution."""

import pytest

from phosphor_eda.domain.schematic import BusKind, Net, ScopeId
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.resolved_graph import ResolutionInputError
from phosphor_eda.formats.dsn.raw_models import (
    DsnNetBundleMap,
    DsnNetBundleMember,
    DsnPackage,
    DsnPackageDevice,
    DsnPackageDevicePin,
    GraphicInst,
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    SchematicPage,
)
from phosphor_eda.formats.dsn.resolver import resolve_dsn_source
from phosphor_eda.formats.dsn.source import (
    DsnBundleMember,
    DsnGlobal,
    DsnNetBundle,
    DsnOffPageConnector,
    DsnPageNet,
    DsnPageSource,
    DsnPinOccurrence,
    DsnSourceDesign,
    DsnWire,
    DsnWireAlias,
    dsn_name_key,
)
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design


def _scope(*parts: str) -> ScopeId:
    return ScopeId(path=parts)


def _page(
    name: str,
    scope_id: ScopeId,
    nets: list[DsnPageNet],
    *,
    pins: list[DsnPinOccurrence] | None = None,
    wires: list[DsnWire] | None = None,
    globals_: list[DsnGlobal] | None = None,
    off_page_connectors: list[DsnOffPageConnector] | None = None,
) -> DsnPageSource:
    return DsnPageSource(
        id=f"page:{name}",
        name=name,
        scope_id=scope_id,
        nets=nets,
        wires=wires or [],
        pin_occurrences=pins or [],
        ports=[],
        globals=globals_ or [],
        off_page_connectors=off_page_connectors or [],
    )


def _net(page_name: str, scope_id: ScopeId, net_id: int, name: str) -> DsnPageNet:
    return DsnPageNet(
        id=f"page:{page_name}:net:{net_id}",
        scope_id=scope_id,
        net_id=net_id,
        name=name,
        name_key=dsn_name_key(name),
    )


def _pin(
    page_name: str,
    scope_id: ScopeId,
    net_id: int,
    reference: str,
    *,
    component_source_id: str | None = None,
    designator: str = "1",
    pin_name: str = "",
    part: str = "Part",
) -> DsnPinOccurrence:
    local_net_id = f"page:{page_name}:net:{net_id}"
    return DsnPinOccurrence(
        id=f"{local_net_id}:pin:{reference}:{designator}",
        scope_id=scope_id,
        local_net_id=local_net_id,
        source_net_id=net_id,
        component_source_id=component_source_id or f"page:{page_name}:component:{reference}",
        component_reference=reference,
        component_part=part,
        pin_designator=designator,
        pin_name=pin_name,
        location=(net_id, net_id),
    )


def _global(
    page_name: str,
    scope_id: ScopeId,
    net_id: int,
    name: str,
) -> DsnGlobal:
    return DsnGlobal(
        id=f"page:{page_name}:global:{net_id}:{name}",
        scope_id=scope_id,
        local_net_id=f"page:{page_name}:net:{net_id}",
        source_net_id=net_id,
        name=name,
        name_key=dsn_name_key(name),
        location=(net_id, net_id),
    )


def _off_page(
    page_name: str,
    scope_id: ScopeId,
    net_id: int,
    name: str,
) -> DsnOffPageConnector:
    return DsnOffPageConnector(
        id=f"page:{page_name}:off_page:{net_id}:{name}",
        scope_id=scope_id,
        local_net_id=f"page:{page_name}:net:{net_id}",
        source_net_id=net_id,
        name=name,
        name_key=dsn_name_key(name),
        location=(net_id, net_id),
    )


def _wire_alias(
    page_name: str,
    scope_id: ScopeId,
    net_id: int,
    name: str,
    *,
    is_bus: bool = False,
) -> DsnWire:
    local_net_id = f"page:{page_name}:net:{net_id}"
    return DsnWire(
        id=f"page:{page_name}:wire:{net_id}",
        scope_id=scope_id,
        local_net_id=local_net_id,
        source_net_id=net_id,
        start=(0, 0),
        end=(1, 1),
        points=[],
        is_bus=is_bus,
        aliases=[
            DsnWireAlias(
                id=f"{local_net_id}:alias:1",
                scope_id=scope_id,
                name=name,
                name_key=dsn_name_key(name),
                location=(0, 0),
            )
        ],
    )


def _source(pages: list[DsnPageSource]) -> DsnSourceDesign:
    return DsnSourceDesign(name="Board", pages=pages, hierarchy_mappings=[], net_bundles=[])


def _net_for_reference(nets: list[Net], reference: str) -> Net:
    for net in nets:
        if any(pin.component.reference == reference for pin in net.pins):
            return net
    raise AssertionError(f"No net found for {reference}")


def _refs(net: Net) -> set[str]:
    return {pin.component.reference for pin in net.pins}


def test_bus_wire_alias_promotes_to_bus_without_naming_scalar_net() -> None:
    scope = _scope("Main")
    data0 = _net("Main", scope, 1, "DATA0")
    data1 = _net("Main", scope, 2, "DATA1")
    bus_carrier = _net("Main", scope, 3, "")
    pin0 = _pin("Main", scope, 1, "U1")
    pin1 = _pin("Main", scope, 2, "U2")

    design = resolve_dsn_source(
        _source(
            [
                _page(
                    "Main",
                    scope,
                    [data0, data1, bus_carrier],
                    pins=[pin0, pin1],
                    wires=[_wire_alias("Main", scope, 3, "DATA[0..1]", is_bus=True)],
                )
            ]
        )
    )

    bus = next(bus for bus in design.buses if bus.name == "DATA[0..1]")
    assert bus.kind is BusKind.VECTOR
    assert {net.name for net in bus.members} == {"DATA0", "DATA1"}
    assert all(net.name != "DATA[0..1]" for net in design.nets)


def test_net_bundle_map_promotes_group_bus_membership() -> None:
    scope = _scope("Main")
    sda = _net("Main", scope, 1, "SDA")
    scl = _net("Main", scope, 2, "SCL")

    source = DsnSourceDesign(
        name="Board",
        pages=[
            _page(
                "Main",
                scope,
                [sda, scl],
                pins=[_pin("Main", scope, 1, "U1"), _pin("Main", scope, 2, "U2")],
            )
        ],
        hierarchy_mappings=[],
        net_bundles=[
            DsnNetBundle(
                id="net_bundle_map:0",
                name="I2C",
                name_key=dsn_name_key("I2C"),
                members=(
                    DsnBundleMember(name="SDA", name_key=dsn_name_key("SDA"), wire_type=1),
                    DsnBundleMember(name="SCL", name_key=dsn_name_key("SCL"), wire_type=1),
                ),
                source_kind="net_bundle_map",
            )
        ],
    )

    design = resolve_dsn_source(source)

    bus = next(bus for bus in design.buses if bus.name == "I2C")
    assert bus.kind is BusKind.GROUP
    assert [net.name for net in bus.members] == ["SDA", "SCL"]
    assert bus.metadata["source_format"] == "dsn"
    assert bus.metadata["source_kind"] == "net_bundle_map"


def test_net_bundle_map_members_resolve_against_dsn_name_keys() -> None:
    scope = _scope("Main")

    source = DsnSourceDesign(
        name="Board",
        pages=[
            _page(
                "Main",
                scope,
                [_net("Main", scope, 1, "SDA"), _net("Main", scope, 2, "SCL")],
                pins=[_pin("Main", scope, 1, "U1"), _pin("Main", scope, 2, "U2")],
            )
        ],
        hierarchy_mappings=[],
        net_bundles=[
            DsnNetBundle(
                id="net_bundle_map:0",
                name="I2C",
                name_key=dsn_name_key("I2C"),
                members=(
                    DsnBundleMember(name="sda", name_key=dsn_name_key("sda"), wire_type=1),
                    DsnBundleMember(name="scl", name_key=dsn_name_key("scl"), wire_type=1),
                ),
            )
        ],
    )

    design = resolve_dsn_source(source)

    bus = next(bus for bus in design.buses if bus.name == "I2C")
    assert [net.name for net in bus.members] == ["SDA", "SCL"]


def test_dsn_to_design_carries_raw_net_bundle_maps_to_group_bus() -> None:
    raw = ParsedDesign(
        pages=[
            SchematicPage(
                name="Main",
                nets=[PageNetEntry(name="SIG_A", net_id=1), PageNetEntry(name="SIG_B", net_id=2)],
            )
        ],
        net_bundle_maps=[
            DsnNetBundleMap(
                name="PAIR",
                members=[
                    DsnNetBundleMember(name="SIG_A", wire_type=1),
                    DsnNetBundleMember(name="SIG_B", wire_type=1),
                ],
            )
        ],
    )

    design = dsn_to_design(raw, name="Board")

    bus = next(bus for bus in design.buses if bus.name == "PAIR")
    assert bus.kind is BusKind.GROUP
    assert {net.name for net in bus.members} == {"SIG_A", "SIG_B"}


def test_wire_alias_name_evidence_is_stripped() -> None:
    scope = _scope("Main")
    net = _net("Main", scope, 1, "")
    pin = _pin("Main", scope, 1, "U1")

    design = resolve_dsn_source(
        _source(
            [
                _page(
                    "Main",
                    scope,
                    [net],
                    pins=[pin],
                    wires=[_wire_alias("Main", scope, 1, " SIG ")],
                )
            ]
        )
    )

    assert _net_for_reference(design.nets, "U1").name == "SIG"


def test_distinct_page_net_ids_with_distinct_names_stay_separate() -> None:
    scope = _scope("Main")
    net_a = _net("Main", scope, 1, "SIG")
    net_b = _net("Main", scope, 2, "OTHER")
    pin_a = _pin("Main", scope, 1, "U1")
    pin_b = _pin("Main", scope, 2, "U2")

    design = resolve_dsn_source(
        _source([_page("Main", scope, [net_a, net_b], pins=[pin_a, pin_b])])
    )

    assert _refs(_net_for_reference(design.nets, "U1")) == {"U1"}
    assert _refs(_net_for_reference(design.nets, "U2")) == {"U2"}


def test_same_named_globals_merge_case_insensitively_and_preserve_spelling_aliases() -> None:
    # Entry-less page nets carry no stored name; the power symbols' net
    # names (Vcc/vCC) are the only name evidence and pick the canonical.
    scope_a = _scope("PowerA")
    scope_b = _scope("PowerB")
    net_a = _net("PowerA", scope_a, 1, "")
    net_b = _net("PowerB", scope_b, 2, "")
    pin_a = _pin("PowerA", scope_a, 1, "U1")
    pin_b = _pin("PowerB", scope_b, 2, "U2")

    design = resolve_dsn_source(
        _source(
            [
                _page(
                    "PowerA",
                    scope_a,
                    [net_a],
                    pins=[pin_a],
                    globals_=[_global("PowerA", scope_a, 1, "Vcc")],
                ),
                _page(
                    "PowerB",
                    scope_b,
                    [net_b],
                    pins=[pin_b],
                    globals_=[_global("PowerB", scope_b, 2, "vCC")],
                ),
            ]
        )
    )

    resolved = _net_for_reference(design.nets, "U1")
    assert _refs(resolved) == {"U1", "U2"}
    assert resolved.name == "Vcc"
    assert "vCC" in resolved.aliases


def test_off_page_connectors_merge_only_within_known_folder_scope() -> None:
    scope_a = _scope("Harness", "A")
    scope_b = _scope("Harness", "B")
    scope_c = _scope("Other", "C")
    net_a = _net("A", scope_a, 1, "BUS_A")
    net_b = _net("B", scope_b, 2, "BUS_B")
    net_c = _net("C", scope_c, 3, "BUS_C")
    pin_a = _pin("A", scope_a, 1, "J1")
    pin_b = _pin("B", scope_b, 2, "J2")
    pin_c = _pin("C", scope_c, 3, "J3")

    design = resolve_dsn_source(
        _source(
            [
                _page(
                    "A",
                    scope_a,
                    [net_a],
                    pins=[pin_a],
                    off_page_connectors=[_off_page("A", scope_a, 1, "HARNESS")],
                ),
                _page(
                    "B",
                    scope_b,
                    [net_b],
                    pins=[pin_b],
                    off_page_connectors=[_off_page("B", scope_b, 2, "HARNESS")],
                ),
                _page(
                    "C",
                    scope_c,
                    [net_c],
                    pins=[pin_c],
                    off_page_connectors=[_off_page("C", scope_c, 3, "HARNESS")],
                ),
            ]
        )
    )

    assert _refs(_net_for_reference(design.nets, "J1")) == {"J1", "J2"}
    assert _refs(_net_for_reference(design.nets, "J3")) == {"J3"}


def test_aliases_are_provenance_not_global_merge_keys() -> None:
    scope_a = _scope("A")
    scope_b = _scope("B")
    net_a = _net("A", scope_a, 1, "")
    net_b = _net("B", scope_b, 2, "")
    pin_a = _pin("A", scope_a, 1, "U1")
    pin_b = _pin("B", scope_b, 2, "U2")

    design = resolve_dsn_source(
        _source(
            [
                _page(
                    "A",
                    scope_a,
                    [net_a],
                    pins=[pin_a],
                    wires=[_wire_alias("A", scope_a, 1, "SDA")],
                ),
                _page(
                    "B",
                    scope_b,
                    [net_b],
                    pins=[pin_b],
                    wires=[_wire_alias("B", scope_b, 2, "SDA")],
                ),
            ]
        )
    )

    assert _refs(_net_for_reference(design.nets, "U1")) == {"U1"}
    assert _refs(_net_for_reference(design.nets, "U2")) == {"U2"}


def test_distinct_page_net_names_do_not_merge_across_pages() -> None:
    scope_a = _scope("A")
    scope_b = _scope("B")
    net_a = _net("A", scope_a, 1, "RESET_A")
    net_b = _net("B", scope_b, 2, "RESET_B")
    pin_a = _pin("A", scope_a, 1, "U1")
    pin_b = _pin("B", scope_b, 2, "U2")

    design = resolve_dsn_source(
        _source(
            [
                _page("A", scope_a, [net_a], pins=[pin_a]),
                _page("B", scope_b, [net_b], pins=[pin_b]),
            ]
        )
    )

    assert _refs(_net_for_reference(design.nets, "U1")) == {"U1"}
    assert _refs(_net_for_reference(design.nets, "U2")) == {"U2"}


def test_multi_part_source_component_becomes_one_logical_component() -> None:
    scope_a = _scope("PartA")
    scope_b = _scope("PartB")
    shared_source_id = "capture:component:U1"
    pin_a = _pin("PartA", scope_a, 1, "U1", component_source_id=shared_source_id, designator="1")
    pin_b = _pin("PartB", scope_b, 2, "U1", component_source_id=shared_source_id, designator="8")

    design = resolve_dsn_source(
        _source(
            [
                _page("PartA", scope_a, [_net("PartA", scope_a, 1, "A")], pins=[pin_a]),
                _page("PartB", scope_b, [_net("PartB", scope_b, 2, "B")], pins=[pin_b]),
            ]
        )
    )

    u1_components = [component for component in design.components if component.reference == "U1"]
    assert len(u1_components) == 1
    assert {page.name for page in u1_components[0].pages} == {"PartA", "PartB"}
    assert {pin.designator for pin in u1_components[0].pins} == {"1", "8"}
    assert len(u1_components[0].occurrences) == 2


def test_repeated_independent_hierarchy_instances_with_same_reference_stay_distinct() -> None:
    scope_a = _scope("Root", "InstanceA")
    scope_b = _scope("Root", "InstanceB")
    pin_a = _pin("InstanceA", scope_a, 1, "U7", component_source_id="instance-a:component:U7")
    pin_b = _pin("InstanceB", scope_b, 2, "U7", component_source_id="instance-b:component:U7")

    design = resolve_dsn_source(
        _source(
            [
                _page("InstanceA", scope_a, [_net("InstanceA", scope_a, 1, "SIG")], pins=[pin_a]),
                _page("InstanceB", scope_b, [_net("InstanceB", scope_b, 2, "SIG")], pins=[pin_b]),
            ]
        )
    )

    u7_components = [component for component in design.components if component.reference == "U7"]
    assert len(u7_components) == 2
    assert len({component.id for component in u7_components}) == 2


def test_same_reference_without_source_identity_stays_scope_local_with_pin_occurrences() -> None:
    scope_a = _scope("Root", "InstanceA")
    scope_b = _scope("Root", "InstanceB")
    pin_a = _pin("InstanceA", scope_a, 1, "U7", component_source_id="")
    pin_b = _pin("InstanceB", scope_b, 2, "U7", component_source_id="")

    design = resolve_dsn_source(
        _source(
            [
                _page("InstanceA", scope_a, [_net("InstanceA", scope_a, 1, "SIG_A")], pins=[pin_a]),
                _page("InstanceB", scope_b, [_net("InstanceB", scope_b, 2, "SIG_B")], pins=[pin_b]),
            ]
        )
    )

    u7_components = [component for component in design.components if component.reference == "U7"]
    assert len(u7_components) == 2
    assert len({component.id for component in u7_components}) == 2

    pin_occurrences = [
        occurrence
        for component in u7_components
        for pin in component.pins
        for occurrence in pin.occurrences
    ]
    assert {occurrence.source_id for occurrence in pin_occurrences} == {pin_a.id, pin_b.id}
    assert {occurrence.scope_id for occurrence in pin_occurrences} == {scope_a, scope_b}


def test_dsn_to_design_routes_through_source_resolver() -> None:
    raw = ParsedDesign(
        pages=[
            SchematicPage(
                name="Main",
                nets=[PageNetEntry(name="SIG", net_id=1)],
                instances=[
                    PlacedInstance(
                        package_name="U.Normal",
                        db_id=100,
                        reference="U1",
                        pin_connections=[PinConnection(pin_number="1", pin_x=1, pin_y=1, net_id=1)],
                    )
                ],
            )
        ]
    )

    design = dsn_to_design(raw, name="Board")

    assert design.metadata["dsn_resolver"] == "source"


def test_source_metadata_cannot_override_resolver_owned_keys() -> None:
    clean_source = DsnSourceDesign(
        name="Board",
        pages=[],
        hierarchy_mappings=[],
        metadata={
            "dsn_resolver": "spoofed",
            "parse_issue_count": "999",
        },
    )

    clean_design = resolve_dsn_source(clean_source)

    assert clean_design.metadata["dsn_resolver"] == "source"
    assert "parse_issue_count" not in clean_design.metadata

    ctx = ParseContext()
    ctx.warn("fixture_warning", "synthetic warning")
    source = DsnSourceDesign(
        name="Board",
        pages=[],
        hierarchy_mappings=[],
        metadata={
            "dsn_library_version": "3.2",
            "dsn_resolver": "spoofed",
            "parse_issue_count": "999",
        },
    )

    design = resolve_dsn_source(source, ctx=ctx)

    assert design.metadata["dsn_library_version"] == "3.2"
    assert design.metadata["dsn_resolver"] == "source"
    assert design.metadata["parse_issue_count"] == "1"


def test_dsn_to_design_preserves_native_package_pin_evidence_as_metadata() -> None:
    raw = ParsedDesign(
        pages=[
            SchematicPage(
                name="Main",
                nets=[PageNetEntry(name="SIG", net_id=1)],
                instances=[
                    PlacedInstance(
                        package_name="SYNTH.Normal",
                        source_package="SYNTH",
                        db_id=100,
                        reference="U1",
                        pin_connections=[
                            PinConnection(pin_number="1", pin_x=1, pin_y=1, net_id=1),
                            PinConnection(pin_number="2", pin_x=2, pin_y=2, net_id=1),
                        ],
                    )
                ],
            )
        ],
        packages={
            "Packages/SYNTH": DsnPackage(
                stream_path="Packages/SYNTH",
                name="SYNTH",
                source_library="synthetic.olb",
                devices=[
                    DsnPackageDevice(
                        refdes_suffix="SYNTH",
                        pins=[
                            DsnPackageDevicePin(
                                order=0,
                                package_pin="A1",
                                group="5",
                            ),
                            DsnPackageDevicePin(
                                order=1,
                                package_pin="B2",
                                ignored=True,
                            ),
                        ],
                    )
                ],
            )
        },
        symbol_pin_names={"SYNTH": ["IN", "OUT"]},
    )

    design = dsn_to_design(raw, name="Board")

    component = next(component for component in design.components if component.reference == "U1")
    assert component.metadata["dsn_package_name"] == "SYNTH"
    assert component.metadata["dsn_source_library"] == "synthetic.olb"
    pin_by_designator = {pin.designator: pin for pin in component.pins}
    assert set(pin_by_designator) == {"1", "2"}
    first_pin = pin_by_designator["1"]
    assert first_pin.id.endswith(":pin:1")
    assert first_pin.metadata["dsn_symbol_pin_order"] == "0"
    assert first_pin.metadata["dsn_package_pin"] == "A1"
    assert first_pin.metadata["dsn_package_pin_name"] == "IN"
    assert first_pin.metadata["dsn_pin_group"] == "5"
    assert first_pin.metadata["dsn_pin_ignored"] == "false"
    assert first_pin.occurrences[0].metadata["dsn_package_device"] == "SYNTH"
    assert pin_by_designator["2"].metadata["dsn_pin_ignored"] == "true"


def test_pin_occurrence_with_unknown_scope_fails_resolution() -> None:
    page_scope = _scope("Main")
    pin_scope = _scope("Missing")
    pin = _pin("Main", pin_scope, 1, "U1")

    with pytest.raises(ResolutionInputError, match=r"pin .* unknown scope"):
        _ = resolve_dsn_source(
            _source([_page("Main", page_scope, [_net("Main", page_scope, 1, "SIG")], pins=[pin])])
        )


def test_pin_occurrence_with_unknown_local_net_fails_resolution() -> None:
    scope = _scope("Main")
    pin = _pin("Main", scope, 2, "U1")

    with pytest.raises(ResolutionInputError, match=r"pin .* unknown local net"):
        _ = resolve_dsn_source(
            _source([_page("Main", scope, [_net("Main", scope, 1, "SIG")], pins=[pin])])
        )


def test_pin_occurrence_scope_must_match_local_net_scope() -> None:
    net_scope = _scope("A")
    pin_scope = _scope("B")
    pin = _pin("A", pin_scope, 1, "U1")

    with pytest.raises(ResolutionInputError, match=r"pin .* scope .* local net"):
        _ = resolve_dsn_source(
            _source(
                [
                    _page("A", net_scope, [_net("A", net_scope, 1, "SIG")]),
                    _page("B", pin_scope, [], pins=[pin]),
                ]
            )
        )


def test_local_net_with_unknown_scope_fails_resolution() -> None:
    page_scope = _scope("Main")
    net_scope = _scope("Missing")

    with pytest.raises(ResolutionInputError, match=r"local net .* unknown scope"):
        _ = resolve_dsn_source(
            _source([_page("Main", page_scope, [_net("Main", net_scope, 1, "SIG")])])
        )


def test_global_with_unknown_scope_fails_resolution() -> None:
    page_scope = _scope("Main")
    global_scope = _scope("Missing")

    with pytest.raises(ResolutionInputError, match=r"global .* unknown scope"):
        _ = resolve_dsn_source(
            _source(
                [
                    _page(
                        "Main",
                        page_scope,
                        [_net("Main", page_scope, 1, "SIG")],
                        globals_=[_global("Main", global_scope, 1, "VCC")],
                    )
                ]
            )
        )


def test_global_with_unknown_local_net_fails_resolution() -> None:
    scope = _scope("Main")

    with pytest.raises(ResolutionInputError, match=r"global .* unknown local net"):
        _ = resolve_dsn_source(
            _source(
                [
                    _page(
                        "Main",
                        scope,
                        [_net("Main", scope, 1, "SIG")],
                        globals_=[_global("Main", scope, 2, "VCC")],
                    )
                ]
            )
        )


def test_sentinel_pin_net_id_resolves_through_global_at_pin_location() -> None:
    # Pin net_id 0xFFFFFFFF is a "no assignment" sentinel, not a net. The
    # 8Mics corpus board has R3.1 carrying the sentinel while a 3.3V power
    # symbol sits on the pin coordinate — the pstxnet oracle puts the pin
    # on 3.3V. Sentinels must never materialize as N4294967295 nets.
    page = SchematicPage(
        name="Main",
        instances=[
            PlacedInstance(
                package_name="R.Normal",
                db_id=1,
                reference="R3",
                pin_connections=[
                    PinConnection(pin_number="1", pin_x=10, pin_y=20, net_id=0xFFFFFFFF),
                ],
            ),
        ],
        globals=[GraphicInst(name="3.3V", db_id=2, loc_x=10, loc_y=20)],
    )
    design = dsn_to_design(ParsedDesign(pages=[page]))

    net_names = {net.name for net in design.nets}
    assert "N4294967295" not in net_names
    assert "N00000000" not in net_names

    component = next(c for c in design.components if c.reference == "R3")
    pin = component.pins[0]
    assert pin.net is not None
    assert pin.net.name == "3.3V"


def test_sentinel_pin_without_anchor_is_netless_with_diagnostic() -> None:
    page = SchematicPage(
        name="Main",
        instances=[
            PlacedInstance(
                package_name="R.Normal",
                db_id=1,
                reference="R9",
                pin_connections=[
                    PinConnection(pin_number="2", pin_x=5, pin_y=5, net_id=0),
                ],
            ),
        ],
    )
    ctx = ParseContext()
    design = dsn_to_design(ParsedDesign(pages=[page]), ctx=ctx)

    assert {net.name for net in design.nets} == set()
    component = next(c for c in design.components if c.reference == "R9")
    assert component.pins[0].net is None
    assert any(
        issue.category == "dsn_netless_pin" and "R9" in issue.message for issue in ctx.issues
    )
