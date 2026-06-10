"""Tests for KiCad schematic parser."""

from pathlib import Path

import pytest

from phosphor_eda.validate import Severity, validate_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINIMAL_SCH = FIXTURES / "kicad-minimal/RP2040_minimal_r2.kicad_sch"


@pytest.fixture(scope="module")
def design():
    from phosphor_eda.formats.kicad import kicad_to_design

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
        assert not c.reference.startswith("#"), f"power symbol {c.reference} in components"


def test_rp2040_has_pins(design):
    u3 = _find_component(design, "U3")
    assert u3 is not None
    assert len(u3.pins) == 57  # RP2040 QFN-56 + pad


def test_rp2040_pins_have_names(design):
    u3 = _find_component(design, "U3")
    assert u3 is not None
    named = [p for p in u3.pins if p.name]
    assert len(named) > 40  # most pins should have names


def _find_pin(component, designator: str):
    for p in component.pins:
        if p.designator == designator:
            return p
    return None


def test_pin_electrical_metadata(design):
    """Non-passive KiCad pin types map to canonical electrical strings."""
    u3 = _find_component(design, "U3")
    assert u3 is not None
    # Pin 1 (IOVDD) is power_in -> "power".
    power = _find_pin(u3, "1")
    assert power is not None
    assert power.metadata.get("electrical") == "power"
    # Pin 11 (GPIO8) is bidirectional -> "IO".
    io = _find_pin(u3, "11")
    assert io is not None
    assert io.metadata.get("electrical") == "IO"


def test_passive_pin_omits_electrical_metadata(design):
    """Passive is the default and is omitted from metadata."""
    u3 = _find_component(design, "U3")
    assert u3 is not None
    # Pin 19 (TESTEN) is passive.
    passive = _find_pin(u3, "19")
    assert passive is not None
    assert "electrical" not in passive.metadata


def test_component_source_metadata(design):
    u3 = _find_component(design, "U3")
    assert u3 is not None
    assert "kicad_component_source_ids" in u3.metadata
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
    assert c1 is not None
    assert c1.metadata.get("Footprint")
    assert "0805" in c1.metadata["Footprint"]


def test_component_occurrences(design):
    c1 = _find_component(design, "C1")
    assert c1 is not None
    assert c1.occurrences
    assert c1.occurrences[0].component is c1
    assert c1.occurrences[0].source_id

    j1 = _find_component(design, "J1")
    assert j1 is not None
    assert j1.occurrences


def test_component_pages_link_back_to_page_components(design):
    c1 = _find_component(design, "C1")
    assert c1 is not None
    assert c1.pages
    assert any(component is c1 for component in c1.pages[0].components)


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


def test_pin_source_metadata(design):
    u3 = _find_component(design, "U3")
    assert u3 is not None
    pins_with_source = [p for p in u3.pins if "kicad_pin_source_id" in p.metadata]
    assert len(pins_with_source) == len(u3.pins)


# --- No-connects ---


def test_no_connect_pins_marked(design):
    nc_pins = [p for c in design.components for p in c.pins if p.no_connect]
    assert len(nc_pins) > 0


# --- Page metadata ---


def test_single_page(design):
    assert len(design.pages) == 1


def test_page_metadata(design):
    assert design.pages
    page = design.pages[0]
    assert "kicad_sheet_symbol_id" in page.metadata
    assert page.source_file.endswith("RP2040_minimal_r2.kicad_sch")


def test_design_metadata(design):
    assert design.metadata["kicad_root_source_file"].endswith("RP2040_minimal_r2.kicad_sch")


# --- Component position ---


def test_component_placement_belongs_to_occurrence_model(design):
    c1 = _find_component(design, "C1")
    assert c1 is not None
    assert c1.occurrences
    assert not hasattr(c1, "x")
    assert c1.occurrences[0].x == pytest.approx(58.42)
    assert c1.occurrences[0].y == pytest.approx(49.53)


def test_component_rotation_belongs_to_occurrence_model(design):
    c1 = _find_component(design, "C1")
    assert c1 is not None
    assert c1.occurrences
    assert not hasattr(c1, "rotation")
    assert c1.occurrences[0].rotation == 0.0

    r1 = _find_component(design, "R1")
    assert r1 is not None
    assert r1.occurrences
    assert r1.occurrences[0].rotation == 270.0


# --- Validation ---


def test_validation_no_errors(design):
    findings = validate_design(design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert errors == [], [f.message for f in errors]
