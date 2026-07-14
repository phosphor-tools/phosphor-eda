"""Diagnostics threading for the OrCAD DSN pipeline.

Verifies that the parser/converter record non-fatal issues on a
ParseContext instead of printing to stdout or swallowing them.
"""

from pathlib import Path

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.netlist import build_netlist
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.raw_models import (
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    SchematicPage,
)
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DSN_FILE = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"


def _design_with_bad_pin() -> ParsedDesign:
    inst = PlacedInstance(
        package_name="PART",
        reference="U1",
        pin_connections=[PinConnection(pin_number="A", pin_x=0, pin_y=0, net_id=1)],
    )
    page = SchematicPage(name="PAGE1", instances=[inst])
    design = ParsedDesign(pages=[page], symbol_pin_names={"PART": ["VCC"]})
    return design


def test_parse_dsn_accepts_parse_context() -> None:
    """parse_dsn threads a caller-supplied ParseContext without error."""
    ctx = ParseContext()
    design = parse_dsn(DSN_FILE, ctx)
    assert len(design.pages) == 1
    # The real fixture parses cleanly; the context is simply available.
    assert isinstance(ctx.issues, list)


def test_dsn_to_design_warns_on_non_numeric_pin() -> None:
    """A non-numeric pin number records a warning and surfaces parse_issue_count."""
    ctx = ParseContext()
    design = dsn_to_design(_design_with_bad_pin(), name="d", ctx=ctx)
    assert any("non-numeric pin" in issue.message for issue in ctx.issues)
    assert design.metadata.get("parse_issue_count") == str(len(ctx.issues))


def test_build_netlist_warns_on_non_numeric_pin() -> None:
    """build_netlist records the same non-numeric pin warning when given a ctx."""
    ctx = ParseContext()
    page = _design_with_bad_pin().pages[0]
    page.wire_net_map = {(0, 0): {1}}
    page.nets = []
    _ = build_netlist(ParsedDesign(pages=[page], symbol_pin_names={"PART": ["VCC"]}), ctx)
    assert any("non-numeric pin" in issue.message for issue in ctx.issues)


def test_build_netlist_uses_pin_net_id_when_coordinate_unmatched() -> None:
    """A pin whose net_id names a known page net lands on it even with no wire."""
    inst = PlacedInstance(
        package_name="PART",
        reference="U1",
        pin_connections=[PinConnection(pin_number="1", pin_x=5, pin_y=5, net_id=7)],
    )
    page = SchematicPage(name="P", instances=[inst], nets=[PageNetEntry(name="SIG", net_id=7)])

    netlist = build_netlist(ParsedDesign(pages=[page], symbol_pin_names={"PART": ["A"]}))

    assert "SIG" in netlist
    assert netlist["SIG"][0].reference == "U1"


def test_build_netlist_warns_on_net_id_without_stored_name() -> None:
    """A net id with no stored page-net name records a diagnostic, not silence."""
    ctx = ParseContext()
    inst = PlacedInstance(
        package_name="PART",
        reference="U1",
        pin_connections=[PinConnection(pin_number="1", pin_x=0, pin_y=0, net_id=0)],
    )
    page = SchematicPage(name="P", instances=[inst])
    page.wire_net_map = {(0, 0): {42}}

    build_netlist(ParsedDesign(pages=[page], symbol_pin_names={"PART": ["A"]}), ctx)

    assert any(issue.category == "dsn_netlist" for issue in ctx.issues)
