"""Tests for the public schematic graph model."""

import phosphor_eda.domain.schematic as schematic
from phosphor_eda.domain.schematic import (
    Component,
    ComponentOccurrence,
    Net,
    NetOccurrence,
    Page,
    Parameter,
    PartNumber,
    Pin,
    PinOccurrence,
    Schematic,
    SchematicDirective,
    SchematicDirectiveKind,
    ScopeId,
)
from phosphor_eda.formats.common.net_union import NetUnion
from phosphor_eda.formats.common.resolved_graph import (
    ResolvedComponentInfo,
    ResolvedComponentOccurrenceInput,
    ResolvedLocalNetInput,
    ResolvedNetInput,
    ResolvedPageInput,
    ResolvedPinInput,
    build_resolved_schematic,
)


def test_multi_page_logical_component_has_occurrences() -> None:
    scope = ScopeId(path=("root",))
    page_a = Page(id="page-a", name="MCU A", scope_id=scope)
    page_b = Page(id="page-b", name="MCU B", scope_id=scope)
    component = Component(id="component-u7", reference="U7", part="AD7768-1", description="ADC")
    occurrence_a = ComponentOccurrence(
        id="component-u7-a",
        component=component,
        page=page_a,
        scope_id=scope,
        source_id="source-u7-a",
        part_id="A",
    )
    occurrence_b = ComponentOccurrence(
        id="component-u7-b",
        component=component,
        page=page_b,
        scope_id=scope,
        source_id="source-u7-b",
        part_id="B",
    )

    component.pages.extend([page_a, page_b])
    component.occurrences.extend([occurrence_a, occurrence_b])
    page_a.components.append(component)
    page_b.components.append(component)
    design = Schematic(name="multi-part", pages=[page_a, page_b], components=[component])

    assert design.components == [component]
    assert component.occurrences == [occurrence_a, occurrence_b]
    assert occurrence_a.component is component
    assert occurrence_b.component is component
    assert occurrence_a.page is page_a
    assert occurrence_b.page is page_b


def test_duplicate_references_in_independent_scopes_are_separate_components() -> None:
    scope_a = ScopeId(path=("root", "sheet-a"))
    scope_b = ScopeId(path=("root", "sheet-b"))
    page_a = Page(id="page-a", name="Sheet A", scope_id=scope_a)
    page_b = Page(id="page-b", name="Sheet B", scope_id=scope_b)
    component_a = Component(id="sheet-a-u1", reference="U1", part="LM358", description="Op amp")
    component_b = Component(id="sheet-b-u1", reference="U1", part="LM358", description="Op amp")

    component_a.pages.append(page_a)
    component_b.pages.append(page_b)
    component_a.occurrences.append(
        ComponentOccurrence(
            id="sheet-a-u1-occurrence",
            component=component_a,
            page=page_a,
            scope_id=scope_a,
            source_id="source-u1-a",
        ),
    )
    component_b.occurrences.append(
        ComponentOccurrence(
            id="sheet-b-u1-occurrence",
            component=component_b,
            page=page_b,
            scope_id=scope_b,
            source_id="source-u1-b",
        ),
    )
    design = Schematic(
        name="repeated-sheet",
        pages=[page_a, page_b],
        components=[component_a, component_b],
    )

    assert component_a.reference == component_b.reference
    assert component_a.id != component_b.id
    assert design.components == [component_a, component_b]


def test_net_spans_pages_with_occurrence_records() -> None:
    scope = ScopeId(path=("root",))
    page_a = Page(id="page-a", name="MCU", scope_id=scope)
    page_b = Page(id="page-b", name="ADC", scope_id=scope)
    component_a = Component(id="u1", reference="U1", part="MCU", description="")
    component_b = Component(id="u7", reference="U7", part="ADC", description="")
    net = Net(id="net-reset", name="RESET")
    pin_a = Pin(id="u1-10", designator="10", name="GPIO_RESET", component=component_a, net=net)
    pin_b = Pin(id="u7-4", designator="4", name="RESET_N", component=component_b, net=net)
    occurrence_a = NetOccurrence(
        id="reset-mcu",
        net=net,
        page=page_a,
        scope_id=scope,
        source_local_net_id="mcu-local-reset",
        source_names={"RESET", "GPIO_RESET"},
    )
    occurrence_b = NetOccurrence(
        id="reset-adc",
        net=net,
        page=page_b,
        scope_id=scope,
        source_local_net_id="adc-local-reset",
        source_names={"RESET_N"},
    )

    component_a.pins.append(pin_a)
    component_b.pins.append(pin_b)
    component_a.pages.append(page_a)
    component_b.pages.append(page_b)
    page_a.components.append(component_a)
    page_b.components.append(component_b)
    page_a.nets.append(net)
    page_b.nets.append(net)
    net.pins.extend([pin_a, pin_b])
    net.pages.extend([page_a, page_b])
    net.occurrences.extend([occurrence_a, occurrence_b])

    assert net.pages == [page_a, page_b]
    assert net.occurrences == [occurrence_a, occurrence_b]
    assert occurrence_a.net is net
    assert occurrence_b.net is net
    assert occurrence_a.source_local_net_id == "mcu-local-reset"


def test_pin_occurrence_records_source_pin_provenance() -> None:
    scope = ScopeId(path=("root", "adc"))
    page = Page(id="page-adc", name="ADC", scope_id=scope)
    component = Component(id="u7", reference="U7", part="ADC", description="")
    pin = Pin(id="u7-10", designator="10", name="SCLK", component=component)
    occurrence = PinOccurrence(
        id="u7-10-occurrence",
        pin=pin,
        page=page,
        scope_id=scope,
        source_id="source-u7-pin-10",
    )

    assert occurrence.pin is pin
    assert occurrence.page is page
    assert occurrence.scope_id is scope
    assert occurrence.source_id == "source-u7-pin-10"


def test_distinct_nets_can_share_name() -> None:
    scope_a = ScopeId(path=("root", "sheet-a"))
    scope_b = ScopeId(path=("root", "sheet-b"))
    page_a = Page(id="page-a", name="Sheet A", scope_id=scope_a)
    page_b = Page(id="page-b", name="Sheet B", scope_id=scope_b)
    net_a = Net(id="sheet-a-enable", name="ENABLE")
    net_b = Net(id="sheet-b-enable", name="ENABLE")
    net_a.pages.append(page_a)
    net_b.pages.append(page_b)
    page_a.nets.append(net_a)
    page_b.nets.append(net_b)
    design = Schematic(name="same-name", pages=[page_a, page_b], nets=[net_a, net_b])

    assert net_a.name == net_b.name
    assert net_a.id != net_b.id
    assert design.nets == [net_a, net_b]


def test_bidirectional_links_are_preserved() -> None:
    scope = ScopeId(path=("root",))
    page = Page(id="page", name="ADC", scope_id=scope)
    component = Component(id="u7", reference="U7", part="ADC", description="")
    net = Net(id="adc-sclk", name="ADC_SCLK")
    pin = Pin(id="u7-10", designator="10", name="SCLK", component=component, net=net)

    component.pins.append(pin)
    component.pages.append(page)
    page.components.append(component)
    page.nets.append(net)
    net.pins.append(pin)
    net.pages.append(page)

    assert pin.component is component
    assert pin.net is net
    assert component.pins == [pin]
    assert component.pages == [page]
    assert page.components == [component]
    assert page.nets == [net]
    assert net.pins == [pin]
    assert net.pages == [page]


def test_port_is_not_public_model() -> None:
    assert not hasattr(schematic, "Port")
    assert not hasattr(schematic, "merge_pages")
    assert not hasattr(schematic, "_unify_nets")
    assert not hasattr(schematic, "_resolve_net")


def test_resolved_graph_preserves_distinct_pin_ids_with_same_designator() -> None:
    scope = ScopeId(path=("root",))
    local_nets = [
        ResolvedLocalNetInput(id="local:first", scope_id=scope, source_names=frozenset({"FIRST"})),
        ResolvedLocalNetInput(
            id="local:second",
            scope_id=scope,
            source_names=frozenset({"SECOND"}),
        ),
    ]
    pins = [
        ResolvedPinInput(
            id="source:u1:a:pin-1",
            scope_id=scope,
            local_net_id="local:first",
            component_id="component:u1",
            component_reference="U1",
            component_part="MCU",
            component_description="Processor",
            pin_id="pin:u1:first:1",
            pin_designator="1",
            pin_name="GPIO_A",
            no_connect=False,
            component_occurrence=ResolvedComponentOccurrenceInput(source_id="source:u1:a"),
        ),
        ResolvedPinInput(
            id="source:u1:b:pin-1",
            scope_id=scope,
            local_net_id="local:second",
            component_id="component:u1",
            component_reference="U1",
            component_part="MCU",
            component_description="Processor",
            pin_id="pin:u1:second:1",
            pin_designator="1",
            pin_name="GPIO_B",
            no_connect=False,
            component_occurrence=ResolvedComponentOccurrenceInput(source_id="source:u1:b"),
        ),
    ]

    design = build_resolved_schematic(
        name="same-designator",
        pages=[ResolvedPageInput(id="page:root", name="Root", scope_id=scope)],
        local_nets=local_nets,
        pins=pins,
        net_union=NetUnion(local_net.id for local_net in local_nets),
        net_factory=lambda _index, union_id, grouped_nets: ResolvedNetInput(
            id=f"net:{union_id}",
            name=next(iter(grouped_nets[0].source_names)),
        ),
        include_net=lambda _union_id, _grouped_nets, _pins: True,
    )

    [component] = design.components

    assert [pin.id for pin in component.pins] == ["pin:u1:first:1", "pin:u1:second:1"]
    assert [pin.designator for pin in component.pins] == ["1", "1"]
    assert {pin.net.name for pin in component.pins if pin.net is not None} == {"FIRST", "SECOND"}


def test_resolved_graph_recomputes_enrichment_when_part_is_backfilled() -> None:
    scope = ScopeId(path=("root",))
    info = ResolvedComponentInfo(
        parameters=(Parameter(name="MPN", value="SN74LVC2G66DCUR"),),
    )
    pins = [
        ResolvedPinInput(
            id="source:u1:a:pin-1",
            scope_id=scope,
            local_net_id=None,
            component_id="component:u1",
            component_reference="U1",
            component_part="",
            component_description="Switch",
            pin_id="pin:u1:1",
            pin_designator="1",
            pin_name="A",
            no_connect=False,
            component_info=info,
            component_occurrence=ResolvedComponentOccurrenceInput(source_id="source:u1:a"),
        ),
        ResolvedPinInput(
            id="source:u1:a:pin-2",
            scope_id=scope,
            local_net_id=None,
            component_id="component:u1",
            component_reference="U1",
            component_part="DNP",
            component_description="Switch",
            pin_id="pin:u1:2",
            pin_designator="2",
            pin_name="B",
            no_connect=False,
            component_info=info,
            component_occurrence=ResolvedComponentOccurrenceInput(source_id="source:u1:a"),
        ),
    ]

    design = build_resolved_schematic(
        name="late-part",
        pages=[ResolvedPageInput(id="page:root", name="Root", scope_id=scope)],
        local_nets=[],
        pins=pins,
        net_union=NetUnion(()),
        net_factory=lambda net_index, _union_id, _grouped_nets: ResolvedNetInput(
            id=f"net:{net_index}",
            name="",
        ),
        include_net=lambda _union_id, _grouped_nets, _pins: True,
    )

    [component] = design.components
    assert component.part == "DNP"
    assert component.dnp is True
    assert component.part_numbers == [PartNumber(manufacturer="", number="SN74LVC2G66DCUR")]


def test_resolved_graph_copies_local_net_directives_to_occurrences_and_net() -> None:
    scope = ScopeId(path=("root",))
    directive = SchematicDirective(
        kind=SchematicDirectiveKind.NET_CLASS,
        value="USB_DIFF",
        source="kicad",
        source_id="root:netclass_flag:flag-1",
        native_name="Netclass",
        x=5.0,
        y=0.0,
    )
    duplicate = SchematicDirective(
        kind=SchematicDirectiveKind.NET_CLASS,
        value="USB_DIFF",
        source="kicad",
        source_id="root:netclass_flag:flag-1",
        native_name="Netclass",
        x=99.0,
        y=99.0,
    )
    local_nets = [
        ResolvedLocalNetInput(
            id="local:first",
            scope_id=scope,
            source_names=frozenset({"D+"}),
            directives=(directive,),
        ),
        ResolvedLocalNetInput(
            id="local:second",
            scope_id=scope,
            source_names=frozenset({"D+"}),
            directives=(duplicate,),
        ),
    ]
    net_union = NetUnion(local_net.id for local_net in local_nets)
    _ = net_union.union("local:first", "local:second")

    design = build_resolved_schematic(
        name="directives",
        pages=[ResolvedPageInput(id="page:root", name="Root", scope_id=scope)],
        local_nets=local_nets,
        pins=[],
        net_union=net_union,
        net_factory=lambda net_index, _union_id, _grouped_nets: ResolvedNetInput(
            id=f"net:{net_index}",
            name="D+",
        ),
        include_net=lambda _union_id, _grouped_nets, _pins: True,
    )

    [net] = design.nets
    assert [occurrence.directives for occurrence in net.occurrences] == [
        [directive],
        [duplicate],
    ]
    assert net.directives == [directive]
