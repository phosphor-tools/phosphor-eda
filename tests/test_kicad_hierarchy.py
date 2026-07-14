"""Tests for KiCad hierarchical (multi-sheet) schematic parsing."""

from pathlib import Path

import pytest

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.kicad import kicad_to_design
from phosphor_eda.formats.kicad.to_schematic import kicad_to_source
from phosphor_eda.query.validate import Severity, validate_design

HIERARCHY_DIR = Path(__file__).parent / "fixtures" / "kicad-hierarchy"
ROOT_SCH = HIERARCHY_DIR / "root.kicad_sch"

MISSING_DIR = Path(__file__).parent / "fixtures" / "kicad-hierarchy-missing"
MISSING_ROOT_SCH = MISSING_DIR / "root.kicad_sch"

SUBFOLDER_DIR = Path(__file__).parent / "fixtures" / "kicad-hierarchy-subfolder"
SUBFOLDER_ROOT_SCH = SUBFOLDER_DIR / "root.kicad_sch"
SUBFOLDER_WINSEP_ROOT_SCH = SUBFOLDER_DIR / "root-winsep.kicad_sch"


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
    assert r1_net.name == "/ChildSheet/SIG_IN"
    assert r2_net.name == "/SIG_IN"


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


# --- Subfolder child sheets (parent-relative resolution) ---


def test_subfolder_child_sheet_loads():
    """A child sheet referenced under sheets/ resolves relative to the parent."""
    design = kicad_to_design(SUBFOLDER_ROOT_SCH)
    names = {p.name for p in design.pages}
    assert {"root", "ChildSheet"} <= names
    refs = {c.reference for c in design.components}
    assert {"R1", "R2"} <= refs


def test_subfolder_child_sheet_windows_separator():
    """A backslash-separated child reference resolves the same as a slash."""
    design = kicad_to_design(SUBFOLDER_WINSEP_ROOT_SCH)
    names = {p.name for p in design.pages}
    assert "ChildSheet" in names, "backslash-separated child must still resolve"
    refs = {c.reference for c in design.components}
    assert {"R1", "R2"} <= refs


# --- KiCad 6 sheet property spellings ---

V6_DIR = Path(__file__).parent / "fixtures" / "kicad-hierarchy-v6"
V6_ROOT_SCH = V6_DIR / "root.kicad_sch"


def test_kicad6_sheet_file_property_loads_children():
    # KiCad 6 wrote "Sheet file" / "Sheet name" (with a space); v7+ writes
    # "Sheetfile" / "Sheetname". Both must load.
    design = kicad_to_design(V6_ROOT_SCH)

    assert {c.reference for c in design.components} == {"R1", "R2"}
    assert {p.name for p in design.pages} == {"root", "ChildSheet"}


def test_sheet_without_file_property_warns(tmp_path):
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.kicad.to_schematic import kicad_to_source

    sch = tmp_path / "root.kicad_sch"
    sch.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
          (uuid 77777777-0000-0000-0000-000000000001)
          (paper "A4")
          (lib_symbols)
          (sheet (at 20.32 33.02) (size 12.7 10.16)
            (uuid 77777777-0000-0000-0000-000000000002)
            (property "Sheetname" "Orphan" (at 20.32 32.3 0)
              (effects (font (size 1.27 1.27)))
            )
          )
          (sheet_instances (path "/" (page "1")))
        )
        """,
        encoding="utf-8",
    )
    ctx = ParseContext()
    kicad_to_source(sch, ctx=ctx)

    assert any(
        issue.category == "missing_sheet" and "Orphan" in issue.message for issue in ctx.issues
    )


# --- Per-instance references on reused sheets ---

INSTANCE_REFS_DIR = Path(__file__).parent / "fixtures" / "kicad-instance-refs"
INSTANCE_REFS_ROOT = INSTANCE_REFS_DIR / "root.kicad_sch"


def test_reused_sheet_instances_get_per_instance_references():
    # Fixture mirrors the complex_hierarchy KiCad demo: one child file
    # instantiated by two sheet symbols, with per-path (instances) reference
    # assignments R201/R301 on the shared symbol.
    design = kicad_to_design(INSTANCE_REFS_ROOT)

    refs = sorted(c.reference for c in design.components)
    assert refs == ["R201", "R301"]

    for ref in ("R201", "R301"):
        component = next(c for c in design.components if c.reference == ref)
        pin1 = next(p for p in component.pins if p.designator == "1")
        assert pin1.net is not None, f"{ref} pin 1 has no net"
        assert "LOAD" in pin1.net.name or any(
            "LOAD" in occ.source_names for occ in pin1.net.occurrences
        ), f"{ref} pin 1 net {pin1.net.name!r} not the labeled LOAD net"

    # The two instances' LOAD nets are local labels on sibling scopes and
    # must remain separate nets.
    load_nets = {
        c.reference: next(p for p in c.pins if p.designator == "1").net for c in design.components
    }
    assert load_nets["R201"] is not load_nets["R301"]


# --- Bus sheet pins ---

BUS_HIERARCHY_DIR = Path(__file__).parent / "fixtures" / "kicad-bus-hierarchy"
BUS_HIERARCHY_ROOT = BUS_HIERARCHY_DIR / "root.kicad_sch"


def test_bus_sheet_pin_connects_members_across_scopes():
    # A D[0..1] bus runs through a sheet pin: root taps D0 (J1) and D1 (J2),
    # the child taps D0 (J3) and D1 (J4) below a hierarchical bus label. KiCad
    # connects each member net across the sheet-pin boundary, so J1/J3 share
    # one net and J2/J4 another — not four split nets.
    design = kicad_to_design(BUS_HIERARCHY_ROOT)

    net_by_pin = {
        (pin.component.reference, pin.designator): net for net in design.nets for pin in net.pins
    }
    assert net_by_pin[("J1", "1")] is net_by_pin[("J3", "1")]
    assert net_by_pin[("J2", "1")] is net_by_pin[("J4", "1")]
    assert net_by_pin[("J1", "1")] is not net_by_pin[("J2", "1")]


def test_dangling_bus_sheet_pin_warns(tmp_path):
    # A bus-syntax sheet pin that touches no bus cannot be connected; it must
    # be reported, not silently dropped.
    root = tmp_path / "root.kicad_sch"
    root.write_text(
        """(kicad_sch (version 20231120) (generator eeschema)
          (uuid 88888888-0000-0000-0000-000000000001)
          (paper "A4")
          (lib_symbols)
          (sheet (at 20 40) (size 12.7 10.16)
            (uuid 88888888-0000-0000-0000-000000000002)
            (property "Sheetname" "ChildSheet" (at 20 39.3 0)
              (effects (font (size 1.27 1.27)))
            )
            (property "Sheetfile" "child.kicad_sch" (at 20 50.9 0)
              (effects (font (size 1.27 1.27)))
            )
            (pin "D[0..1]" input (at 25 40 90)
              (effects (font (size 1.27 1.27)))
              (uuid 88888888-0000-0000-0000-000000000003)
            )
          )
          (sheet_instances (path "/" (page "1")))
        )
        """,
        encoding="utf-8",
    )
    child = tmp_path / "child.kicad_sch"
    child.write_text(
        """(kicad_sch (version 20231120) (generator eeschema)
          (uuid 88888888-0000-0000-0000-000000000010)
          (paper "A4")
          (lib_symbols)
          (sheet_instances (path "/" (page "2")))
        )
        """,
        encoding="utf-8",
    )
    ctx = ParseContext()

    kicad_to_source(root, ctx=ctx)

    assert any(
        issue.category == "kicad_dangling_bus_sheet_pin" and "D[0..1]" in issue.message
        for issue in ctx.issues
    )
