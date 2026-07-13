"""Tests for Altium net resolution via the resolved Schematic."""

from fixture_paths import UPSTREAM_FIXTURES

from phosphor_eda.formats.altium.to_schematic import altium_to_design

MCU_SCHDOC = UPSTREAM_FIXTURES / "qfsae-pcb/Debugger/MCU.SchDoc"


def test_single_sheet_netlist():
    design = altium_to_design(MCU_SCHDOC)
    assert len(design.nets) > 0
    gnd = next((n for n in design.nets if n.name == "GND"), None)
    assert gnd is not None
    gnd_refs = {p.component.reference for p in gnd.pins}
    assert len(gnd_refs) > 0


def test_single_sheet_vcc3v3_net():
    design = altium_to_design(MCU_SCHDOC)
    assert any(n.name == "VCC3V3" for n in design.nets)
