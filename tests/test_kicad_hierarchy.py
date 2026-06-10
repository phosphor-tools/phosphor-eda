"""Tests for KiCad hierarchical (multi-sheet) schematic parsing."""

from pathlib import Path

import pytest

from phosphor_eda.kicad import kicad_to_design
from phosphor_eda.validate import Severity, validate_design

HIERARCHY_DIR = Path(__file__).parent / "fixtures" / "kicad-hierarchy"
ROOT_SCH = HIERARCHY_DIR / "root.kicad_sch"


@pytest.fixture(scope="module")
def design():
    return kicad_to_design(ROOT_SCH)


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


# --- Pages ---


def test_two_pages(design):
    assert len(design.pages) == 2


def test_page_names(design):
    names = {p.name for p in design.pages}
    assert "root" in names
    assert "ChildSheet" in names


# --- Components ---


def test_both_resistors_present(design):
    r1 = _find_component(design, "R1")
    r2 = _find_component(design, "R2")
    assert r1 is not None
    assert r2 is not None


def test_no_power_symbols(design):
    for c in design.components:
        assert not c.reference.startswith("#")


def test_component_count(design):
    assert len(design.components) == 2


# --- Net bridging ---


def test_unwired_same_name_sig_in_nets_stay_separate(design):
    """The fixture has separate unwired root and child SIG_IN local nets."""
    r1_net = next(
        pin.net
        for component in design.components
        if component.reference == "R1"
        for pin in component.pins
        if pin.designator == "1"
    )
    r2_net = next(
        pin.net
        for component in design.components
        if component.reference == "R2"
        for pin in component.pins
        if pin.designator == "2"
    )

    assert r1_net is not None
    assert r2_net is not None
    assert r1_net.id != r2_net.id
    assert r1_net.name == "SIG_IN"
    assert r2_net.name == "SIG_IN"


def test_vcc_net(design):
    net = _find_net(design, "VCC")
    assert net is not None
    assert any(p.component.reference == "R2" for p in net.pins)


def test_gnd_net(design):
    net = _find_net(design, "GND")
    assert net is not None
    assert any(p.component.reference == "R1" for p in net.pins)


def test_three_nets(design):
    assert len(design.nets) == 4


# --- Validation ---


def test_validation_no_errors(design):
    findings = validate_design(design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert errors == [], [f.message for f in errors]
