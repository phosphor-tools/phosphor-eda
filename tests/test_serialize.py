"""Tests for the schematic text serializer."""

import pytest
from phosphor_eda.schematic import Component, Design, Net, Page, Pin
from phosphor_eda.serialize import (
    filter_components,
    filter_nets,
    filter_pages,
    format_component_detail,
    format_component_table,
    format_net_detail,
    format_net_table,
    format_page_detail,
    format_page_table,
    format_trace,
    serialize_design,
)


def _simple_design():
    """Build a minimal design for testing serialization."""
    page = Page(name="ADC")
    comp_u7 = Component(
        reference="U7",
        part="AD7768-1",
        description="IC - ADC - Single",
        pages=[page],
        metadata={"mfr": "Analog Devices", "mfr_pn": "AD7768-1BCPZ"},
    )
    comp_r1 = Component(
        reference="R1",
        part="10k",
        description="Resistor",
        pages=[page],
        metadata={"value": "10k"},
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
        name="TEST",
        pages=[page],
        nets=[net_gnd, net_sclk],
        components=[comp_r1, comp_u7],
        metadata={"Revision": "1.0"},
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
    assert "mfr: Analog Devices" in text
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
    lines_with_sclk = [line for line in text.splitlines() if "ADC_SCLK" in line]
    # Should appear in: component pin line(s) + net header + possibly net pin lines
    assert len(lines_with_sclk) >= 3  # U7 pin, R1 pin, NET header


def test_serialize_to_file(tmp_path):
    from phosphor_eda.serialize import write_design

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
        designator="1",
        name="A",
        component=comp,
        net=net,
        metadata={"electrical": "passive"},
    )
    # Power pin — SHOULD show electrical metadata
    pin_power = Pin(
        designator="2",
        name="VCC",
        component=comp,
        net=net,
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
        designator="1",
        name="CLK",
        component=comp,
        net=net,
        metadata={"electrical": "input", "owner_part_id": "2"},
    )
    comp.pins = [pin]
    net.pins = [pin]
    page.components = [comp]
    page.nets = [net]
    design = Design(name="T", pages=[page], nets=[net], components=[comp])

    text = serialize_design(design)
    pin_line = next(line for line in text.splitlines() if "Pin 1" in line)
    assert "electrical=input" in pin_line
    assert "owner_part_id=2" in pin_line


# ---- Metadata filtering tests ----


def test_passive_metadata_filtered():
    """Passives should only show value if not already in description."""
    page = Page(name="P")
    comp = Component(
        reference="R5",
        part="10k",
        description="Resistor 10k",
        pages=[page],
        metadata={"value": "10k", "Manufacturer": "Yageo", "mfr_pn": "XYZ"},
    )
    pin = Pin(designator="1", name="", component=comp, net=None, metadata={})
    comp.pins = [pin]
    page.components = [comp]
    design = Design(name="T", pages=[page], nets=[], components=[comp])

    text = serialize_design(design)
    # value "10k" is already in description "Resistor 10k", so no metadata shown
    assert "Manufacturer" not in text
    assert "mfr_pn" not in text


def test_passive_value_shown_when_not_in_description():
    """Passive value is shown when it doesn't appear in the description."""
    page = Page(name="P")
    comp = Component(
        reference="C3",
        part="100nF",
        description="Capacitor",
        pages=[page],
        metadata={"value": "100nF"},
    )
    pin = Pin(designator="1", name="", component=comp, net=None, metadata={})
    comp.pins = [pin]
    page.components = [comp]
    design = Design(name="T", pages=[page], nets=[], components=[comp])

    text = serialize_design(design)
    assert "value: 100nF" in text


def test_ic_metadata_allowlist():
    """IC metadata should only show allowlisted keys + URLs."""
    page = Page(name="P")
    comp = Component(
        reference="U1",
        part="LM358",
        description="Op-Amp",
        pages=[page],
        metadata={
            "mfr": "TI",
            "mfr_pn": "LM358DR",
            "Supplier": "Digi-Key",
            "SupplierPN": "296-1395-1-ND",
            "datasheet": "https://www.ti.com/lit/ds/symlink/lm358.pdf",
            "UniqueId": "ABCDEF123",
        },
    )
    pin = Pin(designator="1", name="", component=comp, net=None, metadata={})
    comp.pins = [pin]
    page.components = [comp]
    design = Design(name="T", pages=[page], nets=[], components=[comp])

    text = serialize_design(design)
    assert "mfr: TI" in text
    assert "mfr_pn: LM358DR" in text
    assert "https://www.ti.com" in text
    assert "Supplier" not in text
    assert "SupplierPN" not in text
    assert "UniqueId" not in text


# ---- Inline destinations tests ----


def test_inline_destinations_signal_net():
    """Signal net pins should show inline destination refs with trace-through."""
    design = _simple_design()
    text = serialize_design(design)
    # U7 pin 10 on ADC_SCLK — R1 is a shunt to GND (pull-down)
    u7_sclk_line = next(
        line
        for line in text.splitlines()
        if "Pin 10" in line and "SCLK" in line and "COMPONENT" not in line
    )
    assert "(R1 to GND)" in u7_sclk_line


def test_inline_destinations_power_net_excluded():
    """Power net pins should NOT show inline destination refs."""
    design = _simple_design()
    text = serialize_design(design)
    # U7 pin 7 on GND — no inline refs
    u7_gnd_line = next(line for line in text.splitlines() if "Pin 7" in line and "DGND" in line)
    assert "[" not in u7_gnd_line


def test_is_power_net_classname():
    """ClassName=PWR metadata should mark a net as power."""
    from phosphor_eda.serialize import is_power_net

    net = Net(name="CUSTOM_RAIL", metadata={"ClassName": "PWR"})
    assert is_power_net("CUSTOM_RAIL", net)
    assert not is_power_net("CUSTOM_RAIL")


# ---- Table formatter tests ----


def test_format_component_table():
    design = _simple_design()
    table = format_component_table(design)
    assert "REF" in table
    assert "PART" in table
    assert "R1" in table
    assert "U7" in table
    assert "AD7768-1" in table


def test_format_net_table():
    design = _simple_design()
    table = format_net_table(design)
    assert "NET" in table
    assert "ADC_SCLK" in table
    assert "GND" in table


def test_format_page_table():
    design = _simple_design()
    table = format_page_table(design)
    assert "PAGE" in table
    assert "ADC" in table


# ---- Detail formatter tests ----


def test_format_component_detail():
    design = _simple_design()
    detail = format_component_detail(design, "U7")
    assert "COMPONENT: U7" in detail
    assert "Pin 10" in detail
    assert "SCLK" in detail
    # R1 is a shunt (pin 1 on ADC_SCLK, pin 2 on GND)
    assert "(R1 to GND)" in detail


def test_format_component_detail_not_found():
    design = _simple_design()
    with pytest.raises(ValueError, match="not found"):
        format_component_detail(design, "U99")


def test_format_net_detail():
    design = _simple_design()
    detail = format_net_detail(design, "ADC_SCLK")
    assert "NET: ADC_SCLK" in detail
    assert "U7.10" in detail
    assert "R1.1" in detail


def test_format_net_detail_not_found():
    design = _simple_design()
    with pytest.raises(ValueError, match="not found"):
        format_net_detail(design, "NONEXISTENT")


def test_format_page_detail():
    design = _simple_design()
    detail = format_page_detail(design, "ADC")
    assert "PAGE: ADC" in detail
    assert "U7" in detail
    assert "R1" in detail


def test_format_page_detail_not_found():
    design = _simple_design()
    with pytest.raises(ValueError, match="not found"):
        format_page_detail(design, "NONEXISTENT")


# ---- Filterable design helper ----


def _filterable_design():
    """Build a 2-page design with mixed component types and net types."""
    page_power = Page(name="Power")
    page_spi = Page(name="SPI")

    # Components
    u1 = Component(
        reference="U1",
        part="MCU",
        description="Microcontroller",
        pages=[page_spi],
        metadata={},
    )
    u2 = Component(reference="U2", part="AD7768", description="ADC", pages=[page_spi], metadata={})
    r1 = Component(
        reference="R1",
        part="100R",
        description="Resistor",
        pages=[page_spi],
        metadata={},
    )
    r2 = Component(
        reference="R2",
        part="4k7",
        description="Resistor",
        pages=[page_spi],
        metadata={},
    )
    c1 = Component(
        reference="C1",
        part="100nF",
        description="Capacitor",
        pages=[page_power],
        metadata={},
    )
    tp1 = Component(
        reference="TP1",
        part="TestPoint",
        description="Test Point",
        pages=[page_spi],
        metadata={},
    )
    vreg = Component(
        reference="U3",
        part="LM1117",
        description="Regulator",
        pages=[page_power],
        metadata={},
    )

    # Nets
    spi_clk = Net(name="SPI_CLK")
    spi_mosi = Net(name="SPI_MOSI")
    p3v3 = Net(name="P3V3")
    gnd = Net(name="GND")

    # Wiring — SPI page
    # U1.1 -> SPI_CLK -> R1.1, R1.2 -> SPI_CLK_B -> U2.1 (series)
    # R2 pull-up: SPI_CLK -> R2.1, R2.2 -> P3V3
    # TP1 on SPI_CLK
    spi_clk_b = Net(name="SPI_CLK_B")

    def connect(pin, net):
        pin.net = net
        net.pins.append(pin)

    pin_u1_1 = Pin(designator="1", name="SCK", component=u1, metadata={})
    pin_u1_2 = Pin(designator="2", name="MOSI", component=u1, metadata={})
    pin_u1_3 = Pin(designator="3", name="VDD", component=u1, metadata={})
    u1.pins = [pin_u1_1, pin_u1_2, pin_u1_3]

    pin_u2_1 = Pin(designator="1", name="SCLK", component=u2, metadata={})
    pin_u2_2 = Pin(designator="2", name="DIN", component=u2, metadata={})
    pin_u2_3 = Pin(designator="3", name="GND", component=u2, metadata={})
    u2.pins = [pin_u2_1, pin_u2_2, pin_u2_3]

    pin_r1_1 = Pin(designator="1", name="", component=r1, metadata={})
    pin_r1_2 = Pin(designator="2", name="", component=r1, metadata={})
    r1.pins = [pin_r1_1, pin_r1_2]

    pin_r2_1 = Pin(designator="1", name="", component=r2, metadata={})
    pin_r2_2 = Pin(designator="2", name="", component=r2, metadata={})
    r2.pins = [pin_r2_1, pin_r2_2]

    pin_c1_1 = Pin(designator="1", name="", component=c1, metadata={})
    pin_c1_2 = Pin(designator="2", name="", component=c1, metadata={})
    c1.pins = [pin_c1_1, pin_c1_2]

    pin_tp1 = Pin(designator="1", name="", component=tp1, metadata={})
    tp1.pins = [pin_tp1]

    pin_vreg_1 = Pin(designator="1", name="IN", component=vreg, metadata={})
    pin_vreg_2 = Pin(designator="2", name="OUT", component=vreg, metadata={})
    pin_vreg_3 = Pin(designator="3", name="GND", component=vreg, metadata={})
    vreg.pins = [pin_vreg_1, pin_vreg_2, pin_vreg_3]

    connect(pin_u1_1, spi_clk)
    connect(pin_r1_1, spi_clk)
    connect(pin_r2_1, spi_clk)
    connect(pin_tp1, spi_clk)
    connect(pin_r1_2, spi_clk_b)
    connect(pin_u2_1, spi_clk_b)

    connect(pin_u1_2, spi_mosi)
    connect(pin_u2_2, spi_mosi)

    connect(pin_r2_2, p3v3)
    connect(pin_u1_3, p3v3)
    connect(pin_c1_1, p3v3)
    connect(pin_vreg_2, p3v3)

    connect(pin_u2_3, gnd)
    connect(pin_c1_2, gnd)
    connect(pin_vreg_3, gnd)

    page_spi.components = [u1, u2, r1, r2, tp1]
    page_spi.nets = [spi_clk, spi_clk_b, spi_mosi]
    page_power.components = [c1, vreg]
    page_power.nets = [p3v3, gnd]

    # P3V3 spans both pages (U1.VDD is on SPI page, vreg/C1 on Power page)
    all_nets = [spi_clk, spi_clk_b, spi_mosi, p3v3, gnd]
    all_comps = [u1, u2, r1, r2, c1, tp1, vreg]

    return Design(
        name="FILTER_TEST",
        pages=[page_power, page_spi],
        nets=all_nets,
        components=all_comps,
    )


# ---- Filter tests ----


def test_filter_nets_by_component():
    design = _filterable_design()
    result = filter_nets(design, components=["U1"])
    names = {n.name for n in result}
    assert "SPI_CLK" in names
    assert "SPI_MOSI" in names
    assert "P3V3" in names
    assert "GND" not in names


def test_filter_nets_component_intersection():
    design = _filterable_design()
    result = filter_nets(design, components=["U1", "U2"])
    names = {n.name for n in result}
    # U1 and U2 share SPI_MOSI directly
    assert "SPI_MOSI" in names
    # SPI_CLK only has U1 (not U2, which is on SPI_CLK_B via R1)
    assert "SPI_CLK" not in names


def test_filter_nets_component_intersection_with_trace():
    design = _filterable_design()
    result = filter_nets(design, components=["U1", "U2"], trace=True)
    names = {n.name for n in result}
    # With trace, SPI_CLK reaches U2 through R1
    assert "SPI_CLK" in names
    assert "SPI_MOSI" in names


def test_filter_nets_by_page():
    design = _filterable_design()
    result = filter_nets(design, pages=["Power"])
    names = {n.name for n in result}
    assert "P3V3" in names
    assert "GND" in names
    assert "SPI_CLK" not in names


def test_filter_nets_power_only():
    design = _filterable_design()
    result = filter_nets(design, power=True)
    names = {n.name for n in result}
    assert names == {"P3V3", "GND"}


def test_filter_nets_no_power():
    design = _filterable_design()
    result = filter_nets(design, power=False)
    names = {n.name for n in result}
    assert "P3V3" not in names
    assert "GND" not in names
    assert "SPI_CLK" in names


def test_filter_nets_min_pins():
    design = _filterable_design()
    result = filter_nets(design, min_pins=3)
    # SPI_CLK has 4 pins (U1, R1, R2, TP1), P3V3 has 4 pins
    names = {n.name for n in result}
    assert "SPI_CLK" in names
    assert "P3V3" in names
    # SPI_MOSI has 2 pins, SPI_CLK_B has 2
    assert "SPI_MOSI" not in names


def test_filter_nets_multi_page():
    design = _filterable_design()
    result = filter_nets(design, multi_page=True)
    names = {n.name for n in result}
    # P3V3 spans Power (vreg, C1) and SPI (U1) pages
    assert "P3V3" in names


def test_filter_nets_composable():
    design = _filterable_design()
    result = filter_nets(design, components=["U1"], power=False)
    names = {n.name for n in result}
    assert "SPI_CLK" in names
    assert "P3V3" not in names


def test_filter_components_by_page():
    design = _filterable_design()
    result = filter_components(design, pages=["Power"])
    refs = {c.reference for c in result}
    assert refs == {"C1", "U3"}


def test_filter_components_by_prefix():
    design = _filterable_design()
    result = filter_components(design, prefixes=["U"])
    refs = {c.reference for c in result}
    assert refs == {"U1", "U2", "U3"}


def test_filter_components_by_prefix_tp():
    design = _filterable_design()
    result = filter_components(design, prefixes=["TP"])
    refs = {c.reference for c in result}
    assert refs == {"TP1"}


def test_filter_components_passive_only():
    design = _filterable_design()
    result = filter_components(design, passive=True)
    refs = {c.reference for c in result}
    assert refs == {"R1", "R2", "C1"}


def test_filter_components_no_passive():
    design = _filterable_design()
    result = filter_components(design, passive=False)
    refs = {c.reference for c in result}
    assert "R1" not in refs
    assert "U1" in refs
    assert "TP1" in refs


def test_filter_components_min_pins():
    design = _filterable_design()
    result = filter_components(design, min_pins=3)
    refs = {c.reference for c in result}
    assert "U1" in refs  # 3 pins
    assert "R1" not in refs  # 2 pins


def test_filter_components_by_net():
    design = _filterable_design()
    result = filter_components(design, net="SPI_CLK")
    refs = {c.reference for c in result}
    assert "U1" in refs
    assert "R1" in refs
    assert "TP1" in refs
    assert "U2" not in refs  # U2 is on SPI_CLK_B


def test_filter_pages_by_net():
    design = _filterable_design()
    result = filter_pages(design, nets=["P3V3"])
    names = {p.name for p in result}
    assert "Power" in names


def test_filter_pages_by_component():
    design = _filterable_design()
    result = filter_pages(design, components=["U1"])
    names = {p.name for p in result}
    assert "SPI" in names
    assert "Power" not in names


# ---- Trace formatting tests ----


def test_format_trace_series():
    design = _filterable_design()
    output = format_trace(design, "U1", "U2")
    # Should show SPI_CLK path through R1
    assert "R1" in output
    assert "U1" in output
    assert "U2" in output


def test_format_trace_direct():
    design = _filterable_design()
    output = format_trace(design, "U1", "U2")
    # SPI_MOSI is a direct connection
    assert "MOSI" in output


def test_format_trace_no_connection():
    design = _filterable_design()
    output = format_trace(design, "U2", "U3")
    assert "No signal paths" in output


def test_format_trace_shunts_shown():
    design = _filterable_design()
    output = format_trace(design, "U1", "U2")
    # R2 is a pull-up on SPI_CLK
    assert "R2" in output
    assert "P3V3" in output


def test_format_component_detail_trace_through():
    """show component should trace through series passives."""
    design = _filterable_design()
    detail = format_component_detail(design, "U1")
    # SCK pin: R1 is series to U2, R2 is shunt to P3V3, TP1 is direct
    assert "R1 -> U2.1" in detail
    assert "R2 to P3V3" in detail
    assert "TP1.1" in detail
