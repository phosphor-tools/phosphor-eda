"""Tests for KiCad hierarchical (multi-sheet) schematic parsing."""

from pathlib import Path

import pytest

from phosphor_eda.formats.kicad import kicad_to_design
from phosphor_eda.query.validate import Severity, validate_design

HIERARCHY_DIR = Path(__file__).parent / "fixtures" / "kicad-hierarchy"
ROOT_SCH = HIERARCHY_DIR / "root.kicad_sch"

MISSING_DIR = Path(__file__).parent / "fixtures" / "kicad-hierarchy-missing"
MISSING_ROOT_SCH = MISSING_DIR / "root.kicad_sch"


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


# --- Missing middle sheet (regression: surviving sheets stay aligned) ---


@pytest.fixture(scope="module")
def missing_middle_design():
    """Root references SheetA, SheetB (file missing), SheetC.

    A missing middle sheet must not shift the remaining sheets' data, which
    would attach the wrong page's components/nets to a surviving sheet.
    """
    return kicad_to_design(MISSING_ROOT_SCH)


def test_missing_middle_keeps_surviving_sheet_names(missing_middle_design):
    names = {p.name for p in missing_middle_design.pages}
    assert "SheetA" in names
    assert "SheetC" in names
    # The missing sheet must not appear, and must not displace SheetC's name.
    assert "SheetB" not in names


def test_missing_middle_components_on_correct_pages(missing_middle_design):
    page_by_name = {p.name: p for p in missing_middle_design.pages}
    sheet_a_refs = {c.reference for c in page_by_name["SheetA"].components}
    sheet_c_refs = {c.reference for c in page_by_name["SheetC"].components}
    assert "RA" in sheet_a_refs
    assert "RC" in sheet_c_refs
    # The missing sheet's parsed data must not leak onto a surviving page.
    assert "RC" not in sheet_a_refs
    assert "RA" not in sheet_c_refs


def test_missing_middle_surviving_nets_stay_isolated(missing_middle_design):
    """Each surviving sheet's nets connect only its own component's pins.

    A node-to-data misalignment would attach a surviving sheet pin to the wrong
    child page, cross-wiring RA's and RC's nets or bridging them through the
    gap left by the missing SheetB. Each child net stays scoped to its sheet.
    """
    page_by_name = {p.name: p for p in missing_middle_design.pages}
    for page_name, own_ref in (("SheetA", "RA"), ("SheetC", "RC")):
        for net in page_by_name[page_name].nets:
            refs = {pin.component.reference for pin in net.pins}
            assert refs <= {own_ref}, f"{page_name} net {net.name!r} leaked pins {refs - {own_ref}}"


def test_missing_sheet_contributes_nothing(missing_middle_design):
    """The missing SheetB leaves no page, component, or sheet-pin net behind."""
    assert "RB" not in {c.reference for c in missing_middle_design.components}
    # No net may reference the missing child's NET_B sheet pin.
    assert "NET_B" not in {n.name for n in missing_middle_design.nets}
