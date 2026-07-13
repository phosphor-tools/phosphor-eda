"""Tests for Altium schematic parsing via altium_to_design."""

from pathlib import Path

from phosphor_eda.formats.altium.to_schematic import altium_to_design, altium_to_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MCU_SCHDOC = FIXTURES / "altium/qfsae-debugger/MCU.SchDoc"


def test_parse_mcu_sheet_components():
    design = altium_to_design(MCU_SCHDOC)
    refs = {c.reference for c in design.components}
    assert "U1" in refs
    # 24 component records, but U1 is multi-part (2 parts) so merge
    # produces 23 unique components
    assert len(design.components) == 23


def test_parse_mcu_sheet_pins():
    design = altium_to_design(MCU_SCHDOC)
    u1 = next((c for c in design.components if c.reference == "U1"), None)
    assert u1 is not None, "Expected component U1 not found"
    assert len(u1.pins) > 0


def test_parse_mcu_sheet_nets():
    design = altium_to_design(MCU_SCHDOC)
    assert len(design.nets) > 0


def test_parse_mcu_sheet_power_ports():
    design = altium_to_design(MCU_SCHDOC)
    net_names = {n.name for n in design.nets}
    assert "VCC3V3" in net_names
    assert "GND" in net_names


def test_parse_mcu_sheet_ports():
    source = altium_to_source(MCU_SCHDOC)
    sheet = next(sheet for sheet in source.sheets.values() if sheet.name == "MCU")
    port_names = {port.name for local_net in sheet.local_nets for port in local_net.ports}
    assert "USB_D_P" in port_names
    assert "ST_JTMS" in port_names
