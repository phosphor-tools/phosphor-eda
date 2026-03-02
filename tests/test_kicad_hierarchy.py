"""Tests for KiCad hierarchical (multi-sheet) schematic parsing."""

from pathlib import Path

import pytest

from ecad_tools.kicad import kicad_to_design
from ecad_tools.validate import Severity, validate_design

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


def test_sig_in_bridges_pages(design):
    """SIG_IN net should contain pins from both root and child pages."""
    net = _find_net(design, "SIG_IN")
    assert net is not None
    refs = {p.component.reference for p in net.pins}
    assert "R1" in refs, "R1 (child) should be on SIG_IN"
    assert "R2" in refs, "R2 (root) should be on SIG_IN"


def test_vcc_net(design):
    net = _find_net(design, "VCC")
    assert net is not None
    assert any(p.component.reference == "R2" for p in net.pins)


def test_gnd_net(design):
    net = _find_net(design, "GND")
    assert net is not None
    assert any(p.component.reference == "R1" for p in net.pins)


def test_three_nets(design):
    assert len(design.nets) == 3


# --- Validation ---


def test_validation_no_errors(design):
    findings = validate_design(design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert errors == [], [f.message for f in errors]
