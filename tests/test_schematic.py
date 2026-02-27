"""Tests for the schematic domain model."""

from ecad_tools.schematic import Component, Design, Net, Page, Pin, Port


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
