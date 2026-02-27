"""Tests for the schematic merge step."""

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
