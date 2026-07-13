"""Tests for Altium -> schematic domain model conversion."""

from collections import Counter
from pathlib import Path

import pytest

from phosphor_eda.formats.altium.to_schematic import altium_to_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
QFSAE_PRJPCB = FIXTURES / "altium/qfsae-debugger/Debugger.PrjPcb"


@pytest.fixture(scope="module")
def design():
    return altium_to_design(QFSAE_PRJPCB, name="QFSAE")


def test_altium_to_design_has_pages(design):
    assert len(design.pages) == 4
    names = {p.name for p in design.pages}
    assert "TOP" in names
    assert "MCU" in names
    assert "Power" in names
    assert "Connectors" in names


def test_altium_to_design_has_components(design):
    assert len(design.components) == 39
    refs = {c.reference for c in design.components}
    assert "U1" in refs
    assert "VR1" in refs
    assert "J2" in refs


def test_altium_to_design_has_signal_nets(design):
    net_names = {n.name for n in design.nets}
    assert "OSC_IN" in net_names
    assert "GND" in net_names
    assert "VCC3V3" in net_names
    assert "USB_D_P" in net_names


def test_altium_to_design_u1_has_pins(design):
    u1 = next(c for c in design.components if c.reference == "U1")
    assert len(u1.pins) == 27


def test_altium_to_design_component_source_provenance(design):
    """Logical components preserve resolved source identity and block occurrences."""
    u1 = next(c for c in design.components if c.reference == "U1")
    source_ids = u1.metadata.get("altium_component_source_ids", "")
    assert source_ids == "altium:component:root:multipart:U1:STM32F103CBT6:3"
    occurrence_source_ids = {occurrence.source_id for occurrence in u1.occurrences}
    assert "sheet:MCU.SchDoc:component:63" in occurrence_source_ids
    assert "sheet:MCU.SchDoc:component:174" in occurrence_source_ids


def test_altium_components_preserve_source_part_and_description(design):
    """Altium source component records should populate public component fields."""
    u1 = next(c for c in design.components if c.reference == "U1")
    assert u1.part == "STM32F103CBT6"
    assert u1.description == "ARM"


def test_altium_to_design_pins_have_names(design):
    """RECORD=2 Name field should populate pin names."""
    u1 = next(c for c in design.components if c.reference == "U1")
    named_pins = [p for p in u1.pins if p.name]
    assert len(named_pins) > 0


def test_altium_to_design_cross_page_via_ports(design):
    """Ports should bridge nets across pages.

    ST_JTCK connects U1 (on the MCU page) to P2 (on the Connectors page)
    through matching ports on both pages.
    """
    st_jtck = next((n for n in design.nets if n.name == "ST_JTCK"), None)
    assert st_jtck is not None
    refs = {p.component.reference for p in st_jtck.pins}
    assert "U1" in refs, "U1 should be on ST_JTCK (MCU page)"
    assert "P2" in refs, "P2 should be on ST_JTCK (Connectors page)"
    # Should span multiple pages
    pages = set()
    for pin in st_jtck.pins:
        for pg in pin.component.pages:
            pages.add(pg.name)
    assert len(pages) >= 2, f"Expected >=2 pages, got {pages}"


def test_altium_design_metadata(design):
    """Design-level metadata records the configured and effective Altium net scope."""
    assert design.metadata["altium_hierarchy_mode"] == "SMART"
    assert design.metadata["altium_effective_hierarchy_mode"] == "HIERARCHICAL_POWER_GLOBAL"


def test_altium_page_metadata(design):
    """Pages preserve source file and scope provenance."""
    mcu_page = next(p for p in design.pages if p.name == "MCU")
    assert mcu_page.source_file == "MCU.SchDoc"
    assert str(mcu_page.scope_id) == "/MCU"


def test_altium_component_occurrence_source_id(design):
    """Component occurrences preserve source placement identity."""
    u1 = next(c for c in design.components if c.reference == "U1")
    source_ids = {occurrence.source_id for occurrence in u1.occurrences}
    assert "sheet:MCU.SchDoc:component:63" in source_ids
    assert "sheet:MCU.SchDoc:component:174" in source_ids


def test_altium_component_occurrences_link_back_to_component_and_page(design):
    """Occurrence records use public component/page objects, not raw source records."""
    u1 = next(c for c in design.components if c.reference == "U1")
    assert u1.occurrences
    for occurrence in u1.occurrences:
        assert occurrence.component is u1
        assert occurrence.page in u1.pages


def test_altium_pin_source_provenance(design):
    """Pins preserve the source pin occurrence IDs they were built from."""
    u1 = next(c for c in design.components if c.reference == "U1")
    assert any(pin.metadata.get("altium_pin_source_id") for pin in u1.pins)


def test_altium_pin_unique_id_is_pin_metadata_not_component_parameter(design):
    """Hidden PinUniqueId rows are parser identity, not component parameters."""
    assert any(
        pin.metadata.get("altium_pin_unique_id")
        for component in design.components
        for pin in component.pins
    )
    assert all(
        parameter.name != "PinUniqueId"
        for component in design.components
        for parameter in component.parameters
    )


def test_altium_multipart_no_duplicate_pins(design):
    """Multi-part components should have exactly one pin per designator.

    U1 (STM32F103CBT6) appears as multiple Altium source components. The
    resolver groups those parts into one public component and deduplicates
    connected pins by designator.
    """
    u1 = next(c for c in design.components if c.reference == "U1")
    designators = [p.designator for p in u1.pins]
    unique_designators = set(designators)
    assert len(designators) == len(unique_designators), (
        f"U1 has duplicate pins: {len(designators)} entries for "
        f"{len(unique_designators)} unique designators"
    )
    assert len(unique_designators) == 27


def test_altium_components_use_public_occurrence_model(design):
    """Component placement data belongs to ComponentOccurrence, not Component."""
    vr1 = next(c for c in design.components if c.reference == "VR1")
    assert not hasattr(vr1, "x")
    assert not hasattr(vr1, "rotation")
    assert vr1.occurrences


def test_altium_overline_stripped_from_net_names(design):
    """Net names should have overline markup (backslashes) stripped."""
    for net in design.nets:
        assert "\\" not in net.name, f"Net '{net.name}' still has backslash overline markup"


def test_altium_overline_stripped_from_pin_names(design):
    """Pin names should have overline markup stripped."""
    for comp in design.components:
        for pin in comp.pins:
            assert "\\" not in pin.name, (
                f"{comp.reference}.{pin.designator} name '{pin.name}' "
                f"still has backslash overline markup"
            )


def test_altium_display_mode_no_duplicate_pins_on_capacitors(design):
    """Capacitors with display mode variants should not have duplicate pins.

    Altium components with DisplayModeCount > 1 have separate pin records
    per visual variant.  Only pins matching the active DisplayMode should
    be included.
    """
    dupes_found = []
    for comp in design.components:
        if not comp.reference.startswith("C"):
            continue
        desig_counts = Counter(p.designator for p in comp.pins)
        for desig, count in desig_counts.items():
            if count > 1:
                dupes_found.append(f"{comp.reference}.{desig} ×{count}")
    assert not dupes_found, (
        f"Capacitors with duplicate pins (display mode filtering failed): {dupes_found[:10]}"
    )


def test_altium_no_duplicate_pin_designators_on_any_component(design):
    """No component should have duplicate pin designators."""
    dupes_found = []
    for comp in design.components:
        desig_counts = Counter(p.designator for p in comp.pins)
        for desig, count in desig_counts.items():
            if count > 1:
                dupes_found.append(f"{comp.reference}.{desig} ×{count}")
    assert not dupes_found, f"Components with duplicate pin designators: {dupes_found[:20]}"


def test_altium_no_duplicate_pins_on_nets(design):
    """No net should contain the same Pin object twice.

    Multi-pass merge (name merge → port bridge → hierarchical bridge)
    can re-encounter absorbed nets whose stale .pins lists still reference
    moved pins.  The _unify_nets guard should prevent duplicate appends.
    """
    dupes_found = []
    for net in design.nets:
        pin_ids = [id(p) for p in net.pins]
        if len(pin_ids) != len(set(pin_ids)):
            seen: set[int] = set()
            for p in net.pins:
                if id(p) in seen:
                    dupes_found.append(f"{net.name}: {p.component.reference}.{p.designator}")
                seen.add(id(p))
    assert not dupes_found, f"Nets with duplicate Pin objects: {dupes_found[:20]}"


def test_altium_unnamed_wire_groups_connect_components(design):
    """Components on unnamed wire groups should be connected via auto-named nets.

    The MCU page has wire groups connecting passives (R1, R2, etc.) to U1
    without explicit net labels. These get Altium's ``Net<ref>_<pin>``
    autoname from the natural-sort minimum member pin.
    """
    auto_nets = [
        n for n in design.nets if any(name.source == "altium:autoname" for name in n.names)
    ]
    assert len(auto_nets) > 0, "Expected auto-named nets from unnamed wire groups"
    # R1 and R2 should be connected to U1 via an auto net
    r1_r2_net = next(
        (n for n in auto_nets if {"R1", "R2"} <= {p.component.reference for p in n.pins}),
        None,
    )
    assert r1_r2_net is not None, "R1 and R2 should share an auto-named net"
    # Members are (R1,1), (R2,2), (U1,10); the natural-sort minimum is R1 pin 1.
    assert r1_r2_net.name == "NetR1_1"


def test_altium_port_bridges_uart(design):
    """Ports wired on child pages should bridge nets across pages.

    UART_TX connects U1 (MCU page) to P3 (Connectors page) through
    matching port entries.
    """
    uart_tx = next((n for n in design.nets if n.name == "UART_TX"), None)
    assert uart_tx is not None
    refs = {p.component.reference for p in uart_tx.pins}
    assert "U1" in refs, "U1 should be on UART_TX net"
    assert "P3" in refs, "P3 should be on UART_TX net"


def test_altium_gnd_spans_all_pages(design):
    """GND should connect components from all populated pages."""
    gnd = next(n for n in design.nets if n.name == "GND")
    pages = set()
    for pin in gnd.pins:
        for pg in pin.component.pages:
            pages.add(pg.name)
    assert "MCU" in pages
    assert "Power" in pages
    assert "Connectors" in pages


def test_altium_component_has_stable_public_identity(design):
    """Logical component identity is source-scope based, not just reference text."""
    c1 = next(c for c in design.components if c.reference == "C1")
    assert c1.id.startswith("altium:component:/MCU:uid:")
    assert c1.pages[0].name == "MCU"


def test_altium_differential_pair_metadata(design):
    """USB differential nets resolve as distinct named nets with expected pins."""
    usb_dp = next(n for n in design.nets if n.name == "USB_D_P")
    usb_dn = next(n for n in design.nets if n.name == "USB_D_N")
    assert {"U1", "J2"} <= {pin.component.reference for pin in usb_dp.pins}
    assert {"U1", "J2"} <= {pin.component.reference for pin in usb_dn.pins}


def test_component_occurrences_are_on_component_pages(design):
    """Component occurrence pages are consistent with Component.pages."""
    c1 = next(c for c in design.components if c.reference == "C1")
    assert c1.occurrences
    page_ids = {page.id for page in c1.pages}
    assert {occurrence.page.id for occurrence in c1.occurrences} <= page_ids


def test_component_occurrences_preserve_scope(design):
    """Component occurrences preserve their source scope."""
    c10 = next(c for c in design.components if c.reference == "C10")
    assert {str(occurrence.scope_id) for occurrence in c10.occurrences} == {"/Power"}


def test_altium_validation_zero_errors(design):
    """Full validation should produce 0 errors."""
    from phosphor_eda.query.validate import Severity, validate_design

    findings = validate_design(design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not errors, f"Validation errors: {[e.message for e in errors]}"
