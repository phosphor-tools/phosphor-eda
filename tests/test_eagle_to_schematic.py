"""Tests for Eagle schematic parser."""

from pathlib import Path

import pytest
from phosphor_eda.validate import Severity, validate_design

BME280_SCH = Path("cli/tests/fixtures/eagle/SparkFun_BME280_Breakout.sch")
ADAFRUIT_SCH = Path("cli/tests/fixtures/eagle/adafruit_rgblcdshield.sch")


@pytest.fixture(scope="module")
def design():
    from phosphor_eda.eagle import eagle_to_design

    return eagle_to_design(BME280_SCH)


@pytest.fixture(scope="module")
def adafruit_design():
    from phosphor_eda.eagle import eagle_to_design

    return eagle_to_design(ADAFRUIT_SCH)


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


def test_bme280_component(design):
    u1 = _find_component(design, "U1")
    assert u1 is not None
    assert "BME280" in u1.part


def test_bme280_has_pins(design):
    u1 = _find_component(design, "U1")
    assert u1 is not None
    assert len(u1.pins) == 8


def test_power_symbols_filtered(design):
    """GND and SUPPLY power symbols should not appear as components."""
    for c in design.components:
        assert not c.reference.startswith("GND"), (
            f"power symbol {c.reference} in components"
        )
        assert not c.reference.startswith("SUPPLY"), (
            f"supply symbol {c.reference} in components"
        )


def test_component_value_metadata(design):
    r1 = _find_component(design, "R1")
    assert r1 is not None
    assert "Value" in r1.metadata
    assert r1.metadata["Value"] == "4.7K"


def test_component_description(design):
    """Descriptions come from deviceset description."""
    jp1 = _find_component(design, "JP1")
    assert jp1 is not None
    assert jp1.description  # should have a non-empty description


# --- Nets ---


def test_design_has_nets(design):
    assert len(design.nets) > 0


def test_gnd_net(design):
    gnd = _find_net(design, "GND")
    assert gnd is not None
    assert len(gnd.pins) > 3


def test_power_net(design):
    net_3v3 = _find_net(design, "3.3V")
    assert net_3v3 is not None
    assert len(net_3v3.pins) > 3


def test_signal_net(design):
    sda = _find_net(design, "SDI/SDA")
    assert sda is not None
    # U1, R1, JP1, JP2 should be connected
    refs = {p.component.reference for p in sda.pins}
    assert "U1" in refs
    assert "R1" in refs


def test_net_connects_correct_pins(design):
    """Verify specific pin connections for the !CS net."""
    cs = _find_net(design, "!CS")
    assert cs is not None
    pin_ids = {(p.component.reference, p.designator) for p in cs.pins}
    # U1 CS pin, R3 pin 1, JP2 pin 1
    assert ("R3", "1") in pin_ids
    assert ("JP2", "1") in pin_ids


# --- Pin metadata ---


def test_pin_electrical_metadata(adafruit_design):
    """Pins with non-passive direction should have electrical metadata."""
    # MCP23017 (IC1) in the Adafruit design has in/out/pwr pin directions
    ic1 = _find_component(adafruit_design, "IC1")
    assert ic1 is not None
    pins_with_electrical = [p for p in ic1.pins if "electrical" in p.metadata]
    assert len(pins_with_electrical) > 0


# --- Page ---


def test_single_page(design):
    assert len(design.pages) == 1


# --- Validation ---


def test_validation_no_errors(design):
    findings = validate_design(design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert errors == [], [f.message for f in errors]


# --- Adafruit board ---


def test_adafruit_components(adafruit_design):
    assert len(adafruit_design.components) > 10


def test_adafruit_nets(adafruit_design):
    assert len(adafruit_design.nets) > 10


def test_adafruit_no_errors(adafruit_design):
    findings = validate_design(adafruit_design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert errors == [], [f.message for f in errors]


# --- Convert API ---


def test_convert_api():
    from phosphor_eda.convert import convert

    text = convert(BME280_SCH)
    assert "DESIGN SUMMARY" in text
    assert "COMPONENTS" in text
    assert "NETS" in text
