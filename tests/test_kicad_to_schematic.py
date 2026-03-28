"""Tests for KiCad schematic parser."""

from pathlib import Path

import pytest
from phosphor_eda.validate import Severity, validate_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINIMAL_SCH = FIXTURES / "kicad-minimal/RP2040_minimal_r2.kicad_sch"


@pytest.fixture(scope="module")
def design():
    from phosphor_eda.kicad import kicad_to_design

    return kicad_to_design(MINIMAL_SCH)


def _find_component(design, ref: str):
    for c in design.components:
        if c.reference == ref:
            return c
    return None


def _find_net(design, name: str):
    for n in design.nets:
        if n.name == name:
            return n
    return None


# --- Components ---


def test_design_has_components(design):
    assert len(design.components) > 0
    # RP2040 should be U3
    u3 = _find_component(design, "U3")
    assert u3 is not None
    assert "RP2040" in u3.part


def test_power_symbols_not_in_components(design):
    """Power symbols (#PWR, #FLG) should be filtered out."""
    for c in design.components:
        assert not c.reference.startswith("#"), (
            f"power symbol {c.reference} in components"
        )


def test_rp2040_has_pins(design):
    u3 = _find_component(design, "U3")
    assert u3 is not None
    assert len(u3.pins) == 57  # RP2040 QFN-56 + pad


def test_rp2040_pins_have_names(design):
    u3 = _find_component(design, "U3")
    named = [p for p in u3.pins if p.name]
    assert len(named) > 40  # most pins should have names


def test_component_metadata(design):
    u3 = _find_component(design, "U3")
    assert "Value" in u3.metadata
    assert u3.metadata["Value"] == "RP2040"


def test_component_description(design):
    """Descriptions come from ki_description in lib_symbols."""
    c1 = _find_component(design, "C1")
    assert c1 is not None
    assert "capacitor" in c1.description.lower()

    j1 = _find_component(design, "J1")
    assert j1 is not None
    assert "USB" in j1.description


def test_component_footprint(design):
    c1 = _find_component(design, "C1")
    assert c1.metadata.get("Footprint")
    assert "0805" in c1.metadata["Footprint"]


# --- Nets ---


def test_design_has_nets(design):
    assert len(design.nets) > 0
    gnd = _find_net(design, "GND")
    assert gnd is not None


def test_gnd_connects_multiple_pins(design):
    gnd = _find_net(design, "GND")
    assert len(gnd.pins) > 5


def test_3v3_net_exists(design):
    net = _find_net(design, "+3V3")
    assert net is not None
    assert len(net.pins) > 1


# --- Pin metadata ---


def test_pin_electrical_metadata(design):
    u3 = _find_component(design, "U3")
    pins_with_electrical = [p for p in u3.pins if "electrical" in p.metadata]
    assert len(pins_with_electrical) > 0


# --- No-connects ---


def test_no_connect_pins_marked(design):
    nc_pins = [p for c in design.components for p in c.pins if p.no_connect]
    assert len(nc_pins) > 0


# --- Page metadata ---


def test_single_page(design):
    assert len(design.pages) == 1


def test_page_metadata(design):
    page = design.pages[0]
    assert page.metadata.get("SheetSize") == "A3"


def test_design_metadata(design):
    assert design.metadata.get("Revision") == "REV2"
    assert "Raspberry Pi" in design.metadata.get("Organization", "")


# --- Validation ---


def test_validation_no_errors(design):
    findings = validate_design(design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert errors == [], [f.message for f in errors]
