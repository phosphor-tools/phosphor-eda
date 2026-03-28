from pathlib import Path

from phosphor_eda.dsn.parser import parse_dsn

DSN_FILE = Path("cli/tests/fixtures/dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN")


def test_parse_dsn_single_page():
    """Single-page design should have one page."""
    design = parse_dsn(DSN_FILE)
    assert len(design.pages) == 1
    assert design.pages[0].name == "PAGE1"
    assert design.pages[0].size == "A3"


def test_parse_dsn_components():
    """Parse the Pico DSN and verify known components exist."""
    design = parse_dsn(DSN_FILE)
    refs = {inst.reference for inst in design.pages[0].instances}
    assert "U1" in refs  # RP2040
    assert "U2" in refs  # RT6150B
    assert "U3" in refs  # W25Q16JV
    assert len(design.pages[0].instances) == 51


def test_parse_dsn_nets():
    design = parse_dsn(DSN_FILE)
    net_names = {n.name for n in design.pages[0].nets}
    assert "GND" in net_names
    assert "3V3" in net_names
    assert "VBUS" in net_names


def test_parse_dsn_globals():
    design = parse_dsn(DSN_FILE)
    assert len(design.pages[0].globals) == 52


def test_parse_dsn_string_list():
    design = parse_dsn(DSN_FILE)
    assert len(design.string_list) > 0


def test_public_api_imports():
    from phosphor_eda.dsn import (
        ParsedDesign,
        SchematicPage,
        build_netlist,
        dsn_to_design,
        parse_dsn,
    )

    assert callable(parse_dsn)
    assert callable(build_netlist)
    assert callable(dsn_to_design)
    assert ParsedDesign is not None
    assert SchematicPage is not None
