from pathlib import Path

from ecad_tools.dsn.parser import parse_dsn

DSN_FILE = Path("raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN")


def test_parse_dsn_components():
    """Parse the Pico DSN and verify known components exist."""
    design = parse_dsn(DSN_FILE)
    refs = {inst.reference for inst in design.instances}
    assert "U1" in refs  # RP2040
    assert "U2" in refs  # RT6150B
    assert "U3" in refs  # W25Q16JV
    assert len(design.instances) == 51


def test_parse_dsn_page_info():
    design = parse_dsn(DSN_FILE)
    assert design.page_name == "PAGE1"
    assert design.page_size == "A3"


def test_parse_dsn_nets():
    design = parse_dsn(DSN_FILE)
    net_names = {n.name for n in design.page_nets}
    assert "GND" in net_names
    assert "3V3" in net_names
    assert "VBUS" in net_names


def test_parse_dsn_globals():
    design = parse_dsn(DSN_FILE)
    assert len(design.globals) == 52


def test_parse_dsn_string_list():
    design = parse_dsn(DSN_FILE)
    assert len(design.string_list) > 0


def test_public_api_imports():
    from ecad_tools.dsn import build_netlist, parse_dsn, write_netlist

    assert callable(parse_dsn)
    assert callable(build_netlist)
    assert callable(write_netlist)
