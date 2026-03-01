"""Tests for the schematic merge step."""

from ecad_tools.altium.record_factory import materialize_records, strip_overline
from ecad_tools.altium.records import ComponentRec, PinRec
from ecad_tools.schematic import Component, Net, Page, Pin, Port, merge_pages


def _make_pin(comp, designator, name, net):
    pin = Pin(designator=designator, name=name, component=comp, net=net, metadata={})
    comp.pins.append(pin)
    if net is not None:
        net.pins.append(pin)
    return pin


def test_merge_same_name_nets():
    """Nets with the same name on different pages merge into one."""
    page_a = Page(name="A")
    page_b = Page(name="B")
    net_a = Net(name="GND")
    net_b = Net(name="GND")
    comp_a = Component(reference="C1", part="Cap", description="", pages=[page_a])
    comp_b = Component(reference="C2", part="Cap", description="", pages=[page_b])
    _make_pin(comp_a, "1", "", net_a)
    _make_pin(comp_b, "1", "", net_b)
    page_a.components = [comp_a]
    page_a.nets = [net_a]
    page_b.components = [comp_b]
    page_b.nets = [net_b]

    design = merge_pages("test", [page_a, page_b])
    gnd_nets = [n for n in design.nets if n.name == "GND"]
    assert len(gnd_nets) == 1
    assert len(gnd_nets[0].pins) == 2


def test_merge_different_nets_stay_separate():
    page = Page(name="A")
    net_a = Net(name="VCC")
    net_b = Net(name="GND")
    comp = Component(reference="C1", part="Cap", description="", pages=[page])
    _make_pin(comp, "1", "", net_a)
    _make_pin(comp, "2", "", net_b)
    page.components = [comp]
    page.nets = [net_a, net_b]

    design = merge_pages("test", [page])
    assert len(design.nets) == 2


def test_merge_components_by_reference():
    """Same reference on different pages merges into one component."""
    page_a = Page(name="Core")
    page_b = Page(name="IO")
    net_a = Net(name="GND")
    net_b = Net(name="VCC")
    comp_a = Component(reference="U1", part="MCU", description="Processor", pages=[page_a])
    comp_b = Component(reference="U1", part="MCU", description="Processor", pages=[page_b])
    _make_pin(comp_a, "A1", "GND", net_a)
    _make_pin(comp_b, "B1", "VCC", net_b)
    page_a.components = [comp_a]
    page_a.nets = [net_a]
    page_b.components = [comp_b]
    page_b.nets = [net_b]

    design = merge_pages("test", [page_a, page_b])
    u1_list = [c for c in design.components if c.reference == "U1"]
    assert len(u1_list) == 1
    assert len(u1_list[0].pins) == 2
    assert set(p.designator for p in u1_list[0].pins) == {"A1", "B1"}
    assert len(u1_list[0].pages) == 2


def test_merge_ports_bridge_nets():
    """Ports with matching names on different pages bridge their nets."""
    page_a = Page(name="ADC")
    page_b = Page(name="TopLevel")
    net_a = Net(name="LOCAL_CLK")
    net_b = Net(name="LOCAL_CLK_2")
    comp_a = Component(reference="U7", part="ADC", description="", pages=[page_a])
    comp_b = Component(reference="U1", part="MCU", description="", pages=[page_b])
    _make_pin(comp_a, "10", "SCLK", net_a)
    _make_pin(comp_b, "L3", "LPSPI1_SCK", net_b)
    port_a = Port(name="SPI_CLK", page=page_a, net=net_a)
    port_b = Port(name="SPI_CLK", page=page_b, net=net_b)
    page_a.components = [comp_a]
    page_a.nets = [net_a]
    page_a.ports = [port_a]
    page_b.components = [comp_b]
    page_b.nets = [net_b]
    page_b.ports = [port_b]

    design = merge_pages("test", [page_a, page_b])
    # The two nets should have been merged (both pins on one net)
    u7_pin = [p for p in design.components if p.reference == "U7"][0].pins[0]
    u1_pin = [p for p in design.components if p.reference == "U1"][0].pins[0]
    assert u7_pin.net is u1_pin.net


def test_merge_unconnected_pins_stay_none():
    page = Page(name="A")
    comp = Component(reference="U1", part="MCU", description="", pages=[page])
    _make_pin(comp, "1", "NC_PIN", None)
    page.components = [comp]

    design = merge_pages("test", [page])
    assert design.components[0].pins[0].net is None


def test_merge_design_metadata():
    page = Page(name="A", metadata={"author": "test"})
    design = merge_pages("test", [page], metadata={"revision": "1.0"})
    assert design.metadata["revision"] == "1.0"
    assert design.name == "test"


# --- strip_overline tests ---


def test_strip_overline_active_low():
    clean, has_ol = strip_overline("D\\R\\D\\Y\\")
    assert clean == "DRDY"
    assert has_ol is True


def test_strip_overline_no_backslash():
    clean, has_ol = strip_overline("SCLK")
    assert clean == "SCLK"
    assert has_ol is False


def test_strip_overline_empty():
    clean, has_ol = strip_overline("")
    assert clean == ""
    assert has_ol is False


def test_strip_overline_partial():
    clean, has_ol = strip_overline("ADC_D\\R\\D\\Y\\")
    assert clean == "ADC_DRDY"
    assert has_ol is True


def test_strip_overline_underscore_in_name():
    clean, has_ol = strip_overline("S\\Y\\N\\C\\_\\I\\N\\")
    assert clean == "SYNC_IN"
    assert has_ol is True


# --- Net unification duplicate-pin guard tests ---


def test_unify_nets_no_duplicate_pins():
    """_unify_nets should not append pins already present in the target.

    After a net is absorbed, its stale .pins list still references the
    moved pins.  If a later pass re-encounters the absorbed net, the
    guard should prevent the same Pin objects from appearing twice.
    """
    page = Page(name="A")
    net_a = Net(name="SIG")
    net_b = Net(name="SIG_alias")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = _make_pin(comp, "1", "X", net_a)

    # Simulate stale state: pin is in both nets (as happens after Pass 1
    # name-merge moves pins but doesn't clear the absorbed net's list).
    net_b.pins.append(pin)

    page.components = [comp]
    page.nets = [net_a]

    # Manually merge — should not duplicate pin
    from ecad_tools.schematic import _unify_nets
    merged_nets = {"SIG": net_a, "SIG_alias": net_b}
    _unify_nets(merged_nets, net_a, net_b)

    pin_ids = [id(p) for p in net_a.pins]
    assert len(pin_ids) == len(set(pin_ids)), (
        f"net_a.pins has duplicate Pin objects after _unify_nets"
    )


# --- Hierarchical net resolution tests ---


def test_merge_hierarchical_bridging_through_parent_net():
    """Two differently-named ports sharing a parent net should unify child nets.

    Parent page has ports DRDY and GPIO_B1_09 both on net ADC_DRDY.
    Child pages have corresponding ports with local nets.
    After merge, child nets should be unified.
    """
    parent = Page(name="TopLevel")
    child_a = Page(name="ADC")
    child_b = Page(name="IO")

    parent_net = Net(name="ADC_DRDY")
    child_net_a = Net(name="LOCAL_DRDY")
    child_net_b = Net(name="GPIO_B1_09")

    # Components on child pages
    comp_a = Component(reference="U7", part="ADC", description="", pages=[child_a])
    comp_b = Component(reference="U1", part="MCU", description="", pages=[child_b])
    _make_pin(comp_a, "19", "DRDY", child_net_a)
    _make_pin(comp_b, "A13", "GPIO_B1_09", child_net_b)

    # Ports on child pages
    port_child_a = Port(name="DRDY", page=child_a, net=child_net_a)
    port_child_b = Port(name="GPIO_B1_09", page=child_b, net=child_net_b)

    # Ports on parent page — both on the SAME net (parent_net)
    port_parent_a = Port(name="DRDY", page=parent, net=parent_net)
    port_parent_b = Port(name="GPIO_B1_09", page=parent, net=parent_net)

    parent.nets = [parent_net]
    parent.ports = [port_parent_a, port_parent_b]
    child_a.components = [comp_a]
    child_a.nets = [child_net_a]
    child_a.ports = [port_child_a]
    child_b.components = [comp_b]
    child_b.nets = [child_net_b]
    child_b.ports = [port_child_b]

    design = merge_pages("test", [parent, child_a, child_b])

    # U7 pin 19 and U1 pin A13 should be on the same net
    u7 = next(c for c in design.components if c.reference == "U7")
    u1 = next(c for c in design.components if c.reference == "U1")
    assert u7.pins[0].net is u1.pins[0].net


def test_merge_hierarchical_no_bridge_different_parent_nets():
    """Ports on different parent nets should NOT unify child nets."""
    parent = Page(name="TopLevel")
    child_a = Page(name="ADC")
    child_b = Page(name="IO")

    parent_net_a = Net(name="SIG_A")
    parent_net_b = Net(name="SIG_B")
    child_net_a = Net(name="LOCAL_A")
    child_net_b = Net(name="LOCAL_B")

    comp_a = Component(reference="U7", part="ADC", description="", pages=[child_a])
    comp_b = Component(reference="U1", part="MCU", description="", pages=[child_b])
    _make_pin(comp_a, "1", "A", child_net_a)
    _make_pin(comp_b, "1", "B", child_net_b)

    # Ports on parent page — on DIFFERENT nets
    port_parent_a = Port(name="PORT_A", page=parent, net=parent_net_a)
    port_parent_b = Port(name="PORT_B", page=parent, net=parent_net_b)
    port_child_a = Port(name="PORT_A", page=child_a, net=child_net_a)
    port_child_b = Port(name="PORT_B", page=child_b, net=child_net_b)

    parent.nets = [parent_net_a, parent_net_b]
    parent.ports = [port_parent_a, port_parent_b]
    child_a.components = [comp_a]
    child_a.nets = [child_net_a]
    child_a.ports = [port_child_a]
    child_b.components = [comp_b]
    child_b.nets = [child_net_b]
    child_b.ports = [port_child_b]

    design = merge_pages("test", [parent, child_a, child_b])

    u7 = next(c for c in design.components if c.reference == "U7")
    u1 = next(c for c in design.components if c.reference == "U1")
    assert u7.pins[0].net is not u1.pins[0].net


def test_merge_hierarchical_three_way_bridge():
    """Three ports sharing a parent net should unify all three child nets."""
    parent = Page(name="TopLevel")
    child_a = Page(name="A")
    child_b = Page(name="B")
    child_c = Page(name="C")

    parent_net = Net(name="SHARED")
    net_a = Net(name="LOCAL_A")
    net_b = Net(name="LOCAL_B")
    net_c = Net(name="LOCAL_C")

    comp_a = Component(reference="R1", part="R", description="", pages=[child_a])
    comp_b = Component(reference="R2", part="R", description="", pages=[child_b])
    comp_c = Component(reference="R3", part="R", description="", pages=[child_c])
    _make_pin(comp_a, "1", "", net_a)
    _make_pin(comp_b, "1", "", net_b)
    _make_pin(comp_c, "1", "", net_c)

    # Three ports on parent, all same net
    pp_a = Port(name="PA", page=parent, net=parent_net)
    pp_b = Port(name="PB", page=parent, net=parent_net)
    pp_c = Port(name="PC", page=parent, net=parent_net)
    cp_a = Port(name="PA", page=child_a, net=net_a)
    cp_b = Port(name="PB", page=child_b, net=net_b)
    cp_c = Port(name="PC", page=child_c, net=net_c)

    parent.nets = [parent_net]
    parent.ports = [pp_a, pp_b, pp_c]
    child_a.components, child_a.nets, child_a.ports = [comp_a], [net_a], [cp_a]
    child_b.components, child_b.nets, child_b.ports = [comp_b], [net_b], [cp_b]
    child_c.components, child_c.nets, child_c.ports = [comp_c], [net_c], [cp_c]

    design = merge_pages("test", [parent, child_a, child_b, child_c])

    r1 = next(c for c in design.components if c.reference == "R1")
    r2 = next(c for c in design.components if c.reference == "R2")
    r3 = next(c for c in design.components if c.reference == "R3")
    assert r1.pins[0].net is r2.pins[0].net
    assert r2.pins[0].net is r3.pins[0].net


# --- Display mode field materialization tests ---


def test_component_display_mode_fields_materialized():
    """ComponentRec should capture DisplayMode and DisplayModeCount from raw records."""
    raw = [
        {"RECORD": "0"},  # header at index 0
        {
            "RECORD": "1",
            "Location.X": "100",
            "Location.Y": "200",
            "LibReference": "CAP",
            "PartCount": "2",
            "CurrentPartId": "1",
            "DisplayMode": "0",
            "DisplayModeCount": "2",
        },
    ]
    records = materialize_records(raw)
    comp = records[1]
    assert isinstance(comp, ComponentRec)
    assert comp.display_mode == 0
    assert comp.display_mode_count == 2
    assert comp.part_count == 2
    assert comp.current_part_id == 1


def test_component_display_mode_defaults():
    """Missing DisplayMode/DisplayModeCount should default to 0/1."""
    raw = [
        {"RECORD": "0"},
        {"RECORD": "1", "Location.X": "0", "Location.Y": "0"},
    ]
    records = materialize_records(raw)
    comp = records[1]
    assert isinstance(comp, ComponentRec)
    assert comp.display_mode == 0
    assert comp.display_mode_count == 1


def test_pin_owner_part_display_mode_materialized():
    """PinRec should capture OwnerPartDisplayMode from raw records."""
    raw = [
        {"RECORD": "0"},
        {
            "RECORD": "2",
            "Location.X": "10",
            "Location.Y": "20",
            "PinLength": "30",
            "PinConglomerate": "0",
            "Designator": "1",
            "OwnerPartId": "1",
            "OwnerPartDisplayMode": "1",
            "OwnerIndex": "0",
        },
    ]
    records = materialize_records(raw)
    pin = records[1]
    assert isinstance(pin, PinRec)
    assert pin.owner_part_display_mode == 1
    assert pin.owner_part_id == 1


def test_pin_owner_part_display_mode_default():
    """Missing OwnerPartDisplayMode should default to 0 (Normal)."""
    raw = [
        {"RECORD": "0"},
        {
            "RECORD": "2",
            "Location.X": "0",
            "Location.Y": "0",
            "PinLength": "10",
            "PinConglomerate": "0",
            "Designator": "A",
            "OwnerIndex": "0",
        },
    ]
    records = materialize_records(raw)
    pin = records[1]
    assert isinstance(pin, PinRec)
    assert pin.owner_part_display_mode == 0
