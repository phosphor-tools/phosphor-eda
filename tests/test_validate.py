"""Tests for schematic validation smoke checks."""

from phosphor_eda.schematic import Component, Design, Net, Page, Pin, Port
from phosphor_eda.validate import Category, Severity, validate_design


def _make_pin(comp: Component, desig: str, name: str, net: Net | None) -> Pin:
    pin = Pin(designator=desig, name=name, component=comp, net=net, metadata={})
    comp.pins.append(pin)
    if net is not None:
        net.pins.append(pin)
    return pin


def _simple_design(
    nets: list[Net] | None = None,
    components: list[Component] | None = None,
    pages: list[Page] | None = None,
) -> Design:
    return Design(
        name="test",
        nets=nets or [],
        components=components or [],
        pages=pages or [Page(name="Main")],
    )


# --- Net checks ---


def test_empty_net():
    net = Net(name="ORPHAN")
    findings = validate_design(_simple_design(nets=[net]))
    cats = {f.category for f in findings}
    assert Category.EMPTY_NET in cats


def test_single_pin_net():
    page = Page(name="A")
    net = Net(name="LONELY")
    comp = Component(reference="R1", part="R", description="", pages=[page])
    _make_pin(comp, "1", "", net)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    single = [f for f in findings if f.category == Category.SINGLE_PIN_NET]
    assert len(single) == 1
    assert "LONELY" in single[0].message


def test_two_pin_net_is_fine():
    page = Page(name="A")
    net = Net(name="SIG")
    c1 = Component(reference="R1", part="R", description="", pages=[page])
    c2 = Component(reference="R2", part="R", description="", pages=[page])
    _make_pin(c1, "1", "", net)
    _make_pin(c2, "1", "", net)
    design = _simple_design(nets=[net], components=[c1, c2], pages=[page])
    findings = validate_design(design)
    net_findings = [
        f
        for f in findings
        if f.category
        in (
            Category.EMPTY_NET,
            Category.SINGLE_PIN_NET,
        )
    ]
    assert len(net_findings) == 0


def test_empty_net_name():
    net = Net(name="")
    findings = validate_design(_simple_design(nets=[net]))
    assert any(f.category == Category.EMPTY_NET_NAME for f in findings)


def test_duplicate_pin_on_net():
    page = Page(name="A")
    net = Net(name="SIG")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    # Manually add the same designator twice
    p1 = Pin(designator="1", name="A", component=comp, net=net, metadata={})
    p2 = Pin(designator="1", name="A", component=comp, net=net, metadata={})
    comp.pins.extend([p1, p2])
    net.pins.extend([p1, p2])
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.DUPLICATE_PIN_ON_NET for f in findings)


# --- Component checks ---


def test_component_no_pins():
    page = Page(name="A")
    comp = Component(reference="TP1", part="TestPoint", description="", pages=[page])
    design = _simple_design(components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.COMPONENT_NO_PINS for f in findings)


def test_duplicate_pin_designator():
    page = Page(name="A")
    net = Net(name="SIG")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    _make_pin(comp, "1", "A", net)
    _make_pin(comp, "1", "B", net)  # duplicate designator
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.DUPLICATE_PIN_DESIGNATOR for f in findings)


def test_all_pins_unconnected():
    page = Page(name="A")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    _make_pin(comp, "1", "A", None)
    _make_pin(comp, "2", "B", None)
    design = _simple_design(components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.COMPONENT_ALL_UNCONNECTED for f in findings)


def test_power_pin_unconnected():
    page = Page(name="A")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(
        designator="1",
        name="VCC",
        component=comp,
        net=None,
        metadata={"electrical": "power"},
    )
    comp.pins.append(pin)
    # Add a connected pin so it doesn't also trigger all-unconnected
    net = Net(name="SIG")
    _make_pin(comp, "2", "OUT", net)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.POWER_PIN_UNCONNECTED for f in findings)


def test_power_pin_connected_is_fine():
    page = Page(name="A")
    net = Net(name="VCC")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(
        designator="1",
        name="VCC",
        component=comp,
        net=net,
        metadata={"electrical": "power"},
    )
    comp.pins.append(pin)
    net.pins.append(pin)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert not any(f.category == Category.POWER_PIN_UNCONNECTED for f in findings)


# --- Name checks ---


def test_residual_backslash_in_net_name():
    net = Net(name="D\\R\\D\\Y\\")
    findings = validate_design(_simple_design(nets=[net]))
    assert any(f.category == Category.NAME_RESIDUAL_MARKUP for f in findings)


def test_residual_backslash_in_pin_name():
    page = Page(name="A")
    net = Net(name="SIG")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(
        designator="1",
        name="D\\R\\D\\Y\\",
        component=comp,
        net=net,
        metadata={},
    )
    comp.pins.append(pin)
    net.pins.append(pin)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.NAME_RESIDUAL_MARKUP for f in findings)


def test_clean_names_no_markup_findings():
    page = Page(name="A")
    net = Net(name="ADC_SCLK")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    _make_pin(comp, "1", "SCLK", net)
    _make_pin(comp, "2", "MISO", net)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert not any(f.category == Category.NAME_RESIDUAL_MARKUP for f in findings)


# --- Port checks ---


def test_orphan_port():
    page_a = Page(name="ADC")
    net = Net(name="SIG")
    port = Port(name="SCLK", page=page_a, net=net)
    page_a.ports = [port]
    page_a.nets = [net]
    design = _simple_design(nets=[net], pages=[page_a])
    findings = validate_design(design)
    assert any(f.category == Category.ORPHAN_PORT for f in findings)


def test_bridged_port_not_orphan():
    page_a = Page(name="ADC")
    page_b = Page(name="TopLevel")
    net_a = Net(name="SIG_A")
    net_b = Net(name="SIG_B")
    port_a = Port(name="SCLK", page=page_a, net=net_a)
    port_b = Port(name="SCLK", page=page_b, net=net_b)
    page_a.ports = [port_a]
    page_a.nets = [net_a]
    page_b.ports = [port_b]
    page_b.nets = [net_b]
    design = _simple_design(nets=[net_a, net_b], pages=[page_a, page_b])
    findings = validate_design(design)
    assert not any(f.category == Category.ORPHAN_PORT for f in findings)


# --- Sorting ---


def test_findings_sorted_errors_first():
    page = Page(name="A")
    net_empty_name = Net(name="")  # error: empty name
    net_single = Net(name="LONELY")  # warning: single-pin
    comp = Component(reference="R1", part="R", description="", pages=[page])
    _make_pin(comp, "1", "", net_single)
    design = _simple_design(
        nets=[net_empty_name, net_single],
        components=[comp],
        pages=[page],
    )
    findings = validate_design(design)
    severities = [f.severity for f in findings]
    # All errors should come before all warnings
    error_indices = [i for i, s in enumerate(severities) if s == Severity.ERROR]
    warning_indices = [i for i, s in enumerate(severities) if s == Severity.WARNING]
    if error_indices and warning_indices:
        assert max(error_indices) < min(warning_indices)


# --- Suppression tests ---


def test_component_no_pins_suppressed_for_dni():
    """DNI (Do Not Install) components with 0 pins should not warn."""
    page = Page(name="A")
    comp = Component(
        reference="FD1",
        part="Fiducial",
        description="",
        pages=[page],
        metadata={"dni": "true"},
    )
    design = _simple_design(components=[comp], pages=[page])
    findings = validate_design(design)
    assert not any(f.category == Category.COMPONENT_NO_PINS for f in findings)


def test_component_no_pins_still_warns_without_dni():
    """Non-DNI components with 0 pins should still warn."""
    page = Page(name="A")
    comp = Component(
        reference="TP1",
        part="TestPoint",
        description="",
        pages=[page],
    )
    design = _simple_design(components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.COMPONENT_NO_PINS for f in findings)


def test_single_pin_net_suppressed_for_no_connect():
    """Single-pin nets where the pin is no_connect should not warn."""
    page = Page(name="A")
    net = Net(name="JTAG_TDI")
    comp = Component(reference="U1", part="MCU", description="", pages=[page])
    pin = Pin(
        designator="F14",
        name="TDI",
        component=comp,
        net=net,
        no_connect=True,
        metadata={},
    )
    comp.pins.append(pin)
    net.pins.append(pin)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert not any(f.category == Category.SINGLE_PIN_NET for f in findings)


def test_single_pin_net_still_warns_without_no_connect():
    """Single-pin nets without no_connect should still warn."""
    page = Page(name="A")
    net = Net(name="ORPHAN_SIG")
    comp = Component(reference="U1", part="MCU", description="", pages=[page])
    _make_pin(comp, "1", "SIG", net)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.SINGLE_PIN_NET for f in findings)
