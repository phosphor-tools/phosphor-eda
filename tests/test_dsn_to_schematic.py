"""Tests for DSN -> schematic domain model conversion."""

from fixture_paths import FIXTURES

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.parser import DsnSchematicPage, RawTitleBlock, parse_dsn
from phosphor_eda.formats.dsn.raw_models import (
    GraphicInst,
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    Wire,
)
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design, dsn_to_source

PICOW_DSN = FIXTURES / "dsn/raspberry-pi-pico-w/RPI-PICOW-R2.DSN"


def _wire_page(name: str, *, ports: list[GraphicInst]) -> DsnSchematicPage:
    """A page with one named wire net at (0, 0) and the given port graphics."""
    page = DsnSchematicPage(name=name)
    page.nets = [PageNetEntry(name="SIG", net_id=1)]
    wire = Wire(db_id=5, wire_id=1, start_x=0, start_y=0, end_x=10, end_y=0)
    wire.points = [(0, 0), (10, 0)]
    page.wires = [wire]
    page.wire_net_map = {(0, 0): {1}, (10, 0): {1}}
    page.ports = ports
    return page


def test_dsn_to_design_has_pages():
    raw = parse_dsn(PICOW_DSN)
    design = dsn_to_design(raw, name="PicoW")
    assert len(design.pages) == 2
    assert design.name == "PicoW"


def test_dsn_to_design_has_components():
    raw = parse_dsn(PICOW_DSN)
    design = dsn_to_design(raw, name="PicoW")
    assert len(design.components) > 50
    refs = {c.reference for c in design.components}
    assert "U1" in refs


def test_dsn_to_design_has_nets():
    raw = parse_dsn(PICOW_DSN)
    design = dsn_to_design(raw, name="PicoW")
    assert len(design.nets) > 30
    net_names = {n.name for n in design.nets}
    assert "GND" in net_names


def test_dsn_to_design_pins_have_names():
    raw = parse_dsn(PICOW_DSN)
    design = dsn_to_design(raw, name="PicoW")
    u1 = next(c for c in design.components if c.reference == "U1")
    named_pins = [p for p in u1.pins if p.name]
    # RP2040 (U1): every one of its 57 pins carries a name.
    assert len(named_pins) == len(u1.pins) == 57


def test_dsn_to_design_gnd_has_many_pins():
    raw = parse_dsn(PICOW_DSN)
    design = dsn_to_design(raw, name="PicoW")
    gnd = next(n for n in design.nets if n.name == "GND")
    assert len(gnd.pins) > 10


def test_floating_port_with_unknown_net_name_warns() -> None:
    """B2: a port whose net name matches no wire/page/block name is diagnosed."""
    dangling = GraphicInst(name="PORTLEFT-L", loc_x=500, loc_y=500, props={"_net_name": "ORPHAN"})
    page = _wire_page("Main", ports=[dangling])
    ctx = ParseContext()
    _ = dsn_to_design(ParsedDesign(pages=[page]), ctx=ctx)
    floating = [issue for issue in ctx.issues if issue.category == "dsn_floating_port"]
    assert len(floating) == 1
    assert "ORPHAN" in floating[0].message
    assert "(500, 500)" in floating[0].message


def test_floating_port_matching_page_net_name_is_hierarchy_wired() -> None:
    """B2: a floating port whose net name matches a page net is not diagnosed."""
    hierarchy_port = GraphicInst(
        name="PORTLEFT-L", loc_x=500, loc_y=500, props={"_net_name": "SIG"}
    )
    page = _wire_page("Main", ports=[hierarchy_port])
    ctx = ParseContext()
    _ = dsn_to_design(ParsedDesign(pages=[page]), ctx=ctx)
    assert not any(issue.category == "dsn_floating_port" for issue in ctx.issues)


def test_port_matching_page_net_name_attaches_to_that_net() -> None:
    """A hierarchy-wired port keeps its source object on the named page net
    instead of being silently dropped after the floating-warn suppression."""
    hierarchy_port = GraphicInst(
        name="PORTLEFT-L", loc_x=500, loc_y=500, props={"_net_name": "SIG"}
    )
    page = _wire_page("Main", ports=[hierarchy_port])
    source = dsn_to_source(ParsedDesign(pages=[page]), ctx=ParseContext())
    page_source = source.pages[0]
    assert [port.name for port in page_source.ports] == ["SIG"]
    sig_net = next(net for net in page_source.nets if net.name == "SIG")
    assert sig_net.port_ids == [page_source.ports[0].id]


def test_extra_title_blocks_warn() -> None:
    """B6: only the first title block is mapped; extras raise a diagnostic."""
    page = DsnSchematicPage(name="Main")
    page.title_blocks = [
        RawTitleBlock(name="TitleBlock0", props={"Title": "First"}),
        RawTitleBlock(name="TitleBlock1", props={"Title": "Second"}),
    ]
    ctx = ParseContext()
    design = dsn_to_design(ParsedDesign(pages=[page]), ctx=ctx)
    assert design.pages[0].title_block is not None
    assert design.pages[0].title_block.title == "First"
    assert any(issue.category == "dsn_title_block" for issue in ctx.issues)


def test_marker_on_wired_pin_does_not_set_no_connect() -> None:
    """D2: a no-connect marker on a wired pin diagnoses but keeps the net."""
    pin = PinConnection(pin_number="1", pin_x=0, pin_y=0, net_id=1)
    pin.has_no_connect_marker = True
    page = DsnSchematicPage(name="Main")
    page.nets = [PageNetEntry(name="SIG", net_id=1)]
    page.wire_net_map = {(0, 0): {1}}
    page.instances = [
        PlacedInstance(package_name="R.Normal", db_id=1, reference="R1", pin_connections=[pin])
    ]
    ctx = ParseContext()
    design = dsn_to_design(ParsedDesign(pages=[page]), ctx=ctx)
    r1 = next(component for component in design.components if component.reference == "R1")
    assert r1.pins[0].no_connect is False
    assert any(issue.category == "dsn_marker_on_wired_pin" for issue in ctx.issues)
