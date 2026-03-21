"""Tests for the schematic domain model."""

from phosphor_eda.schematic import Component, Design, Net, Page, Pin, Port


def test_pin_defaults():
    comp = Component(reference="U1", part="MCU", description="", pins=[], pages=[], metadata={})
    pin = Pin(designator="1", name="VCC", component=comp, net=None, no_connect=False, metadata={})
    assert pin.designator == "1"
    assert pin.name == "VCC"
    assert pin.component is comp
    assert pin.net is None
    assert pin.no_connect is False


def test_component_with_pins():
    comp = Component(reference="U7", part="AD7768-1", description="ADC", pins=[], pages=[], metadata={})
    pin = Pin(designator="10", name="SCLK", component=comp, net=None, no_connect=False, metadata={})
    comp.pins.append(pin)
    assert len(comp.pins) == 1
    assert comp.pins[0].component is comp


def test_net_connects_pins():
    comp_a = Component(reference="U1", part="MCU", description="", pins=[], pages=[], metadata={})
    comp_b = Component(reference="U7", part="ADC", description="", pins=[], pages=[], metadata={})
    net = Net(name="ADC_SCLK", pins=[], bus=None, metadata={})
    pin_a = Pin(designator="L3", name="LPSPI1_SCK", component=comp_a, net=net, no_connect=False, metadata={})
    pin_b = Pin(designator="10", name="SCLK", component=comp_b, net=net, no_connect=False, metadata={})
    net.pins.extend([pin_a, pin_b])
    assert len(net.pins) == 2
    assert net.pins[0].component.reference == "U1"
    assert net.pins[1].component.reference == "U7"


def test_page_holds_components_and_nets():
    page = Page(name="ADC", components=[], ports=[], nets=[], metadata={})
    assert page.name == "ADC"
    assert page.components == []


def test_design_holds_pages():
    design = Design(name="TEST", pages=[], nets=[], components=[], metadata={})
    assert design.name == "TEST"
    assert design.pages == []


def test_port_bridges_net():
    page = Page(name="ADC", components=[], ports=[], nets=[], metadata={})
    net = Net(name="SPI_CLK", pins=[], bus=None, metadata={})
    port = Port(name="SPI", page=page, net=net, harness="SPI")
    assert port.harness == "SPI"
    assert port.net is net


def test_pin_no_connect():
    comp = Component(reference="U7", part="ADC", description="", pins=[], pages=[], metadata={})
    pin = Pin(designator="26", name="AIN-", component=comp, net=None, no_connect=True, metadata={})
    assert pin.no_connect is True
    assert pin.net is None


def test_net_bus_property():
    net = Net(name="DATA0", pins=[], bus="DATA[0..7]", metadata={})
    assert net.bus == "DATA[0..7]"


def test_merge_pages_deduplicates_pins():
    """merge_pages should keep one pin per designator, preferring connected."""
    from phosphor_eda.schematic import merge_pages

    net_a = Net(name="SIG")

    # Page 1: U1 has pin "1" connected to SIG
    page1 = Page(name="P1")
    comp1 = Component(reference="U1", part="IC", description="", pages=[page1])
    pin1_connected = Pin(designator="1", name="A", component=comp1, net=net_a)
    pin1_unconnected = Pin(designator="2", name="B", component=comp1)
    comp1.pins = [pin1_connected, pin1_unconnected]
    net_a.pins = [pin1_connected]
    page1.components = [comp1]
    page1.nets = [net_a]

    # Page 2: U1 has pin "1" unconnected, pin "2" connected
    net_b = Net(name="SIG2")
    page2 = Page(name="P2")
    comp2 = Component(reference="U1", part="IC", description="", pages=[page2])
    pin2_unconnected = Pin(designator="1", name="A", component=comp2)
    pin2_connected = Pin(designator="2", name="B", component=comp2, net=net_b)
    comp2.pins = [pin2_unconnected, pin2_connected]
    net_b.pins = [pin2_connected]
    page2.components = [comp2]
    page2.nets = [net_b]

    design = merge_pages("T", [page1, page2])
    u1 = next(c for c in design.components if c.reference == "U1")

    assert len(u1.pins) == 2, f"Expected 2 unique pins, got {len(u1.pins)}"
    pin1 = next(p for p in u1.pins if p.designator == "1")
    pin2 = next(p for p in u1.pins if p.designator == "2")
    # Each should have the connected version
    assert pin1.net is not None, "Pin 1 should keep its SIG connection"
    assert pin2.net is not None, "Pin 2 should keep its SIG2 connection"
