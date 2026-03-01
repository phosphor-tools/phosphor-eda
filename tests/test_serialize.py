"""Tests for the schematic text serializer."""

from ecad_tools.schematic import Component, Design, Net, Page, Pin
from ecad_tools.serialize import serialize_design


def _simple_design():
    """Build a minimal design for testing serialization."""
    page = Page(name="ADC")
    comp_u7 = Component(
        reference="U7", part="AD7768-1", description="IC - ADC - Single",
        pages=[page], metadata={"Manufacturer": "Analog Devices", "MPN": "AD7768-1BCPZ"},
    )
    comp_r1 = Component(
        reference="R1", part="10k", description="Resistor",
        pages=[page], metadata={},
    )
    net_sclk = Net(name="ADC_SCLK")
    net_gnd = Net(name="GND")

    pin_u7_10 = Pin(designator="10", name="SCLK", component=comp_u7, net=net_sclk, metadata={})
    pin_u7_7 = Pin(designator="7", name="DGND", component=comp_u7, net=net_gnd, metadata={})
    pin_u7_nc = Pin(designator="26", name="AIN-", component=comp_u7, no_connect=True, metadata={})
    pin_u7_uc = Pin(designator="28", name="VCM", component=comp_u7, metadata={})
    comp_u7.pins = [pin_u7_10, pin_u7_7, pin_u7_nc, pin_u7_uc]

    pin_r1_1 = Pin(designator="1", name="", component=comp_r1, net=net_sclk, metadata={})
    pin_r1_2 = Pin(designator="2", name="", component=comp_r1, net=net_gnd, metadata={})
    comp_r1.pins = [pin_r1_1, pin_r1_2]

    net_sclk.pins = [pin_u7_10, pin_r1_1]
    net_gnd.pins = [pin_u7_7, pin_r1_2]

    page.components = [comp_u7, comp_r1]
    page.nets = [net_sclk, net_gnd]

    return Design(
        name="TEST", pages=[page], nets=[net_gnd, net_sclk],
        components=[comp_r1, comp_u7], metadata={"Revision": "1.0"},
    )


def test_serialize_contains_summary():
    text = serialize_design(_simple_design())
    assert "=== DESIGN SUMMARY ===" in text
    assert "TEST" in text
    assert "2 components" in text
    assert "2 nets" in text


def test_serialize_contains_component_section():
    text = serialize_design(_simple_design())
    assert "COMPONENT: U7 | AD7768-1 | IC - ADC - Single | Pages: ADC" in text
    assert "Manufacturer: Analog Devices" in text
    assert "Pin 10" in text
    assert "-> ADC_SCLK" in text


def test_serialize_no_connect_vs_unconnected():
    text = serialize_design(_simple_design())
    assert "(no-connect)" in text
    assert "(unconnected)" in text


def test_serialize_contains_net_section():
    text = serialize_design(_simple_design())
    assert "NET: ADC_SCLK" in text
    assert "U7.10" in text
    assert "R1.1" in text


def test_serialize_grep_friendly():
    """Grepping for a net name should hit both component and net sections."""
    text = serialize_design(_simple_design())
    lines_with_sclk = [l for l in text.splitlines() if "ADC_SCLK" in l]
    # Should appear in: component pin line(s) + net header + possibly net pin lines
    assert len(lines_with_sclk) >= 3  # U7 pin, R1 pin, NET header


def test_serialize_to_file(tmp_path):
    from ecad_tools.serialize import write_design

    design = _simple_design()
    out = tmp_path / "test.txt"
    write_design(design, out)
    assert out.exists()
    text = out.read_text()
    assert "=== DESIGN SUMMARY ===" in text


def test_serialize_suppresses_electrical_passive():
    """electrical=passive should not appear in output (it's the default)."""
    page = Page(name="Test")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    net = Net(name="SIG")
    # Passive pin — should NOT show electrical metadata
    pin_passive = Pin(
        designator="1", name="A", component=comp, net=net,
        metadata={"electrical": "passive"},
    )
    # Power pin — SHOULD show electrical metadata
    pin_power = Pin(
        designator="2", name="VCC", component=comp, net=net,
        metadata={"electrical": "power"},
    )
    comp.pins = [pin_passive, pin_power]
    net.pins = [pin_passive, pin_power]
    page.components = [comp]
    page.nets = [net]
    design = Design(name="T", pages=[page], nets=[net], components=[comp])

    text = serialize_design(design)
    assert "electrical=passive" not in text
    assert "electrical=power" in text


def test_serialize_pin_metadata_inline():
    """Non-default pin metadata should appear inline on pin lines."""
    page = Page(name="Test")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    net = Net(name="SIG")
    pin = Pin(
        designator="1", name="CLK", component=comp, net=net,
        metadata={"electrical": "input", "owner_part_id": "2"},
    )
    comp.pins = [pin]
    net.pins = [pin]
    page.components = [comp]
    page.nets = [net]
    design = Design(name="T", pages=[page], nets=[net], components=[comp])

    text = serialize_design(design)
    pin_line = next(l for l in text.splitlines() if "Pin 1" in l)
    assert "electrical=input" in pin_line
    assert "owner_part_id=2" in pin_line
