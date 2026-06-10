from pathlib import Path

from phosphor_eda.formats.dsn.netlist import build_netlist
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design
from phosphor_eda.query.serialize import write_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DSN_FILE = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"


def test_build_netlist_has_known_nets():
    design = parse_dsn(DSN_FILE)
    netlist = build_netlist(design)
    assert "GND" in netlist
    assert "3V3" in netlist
    assert "GPIO0" in netlist


def test_build_netlist_gnd_has_many_pins():
    design = parse_dsn(DSN_FILE)
    netlist = build_netlist(design)
    # GND should have many connections
    assert len(netlist["GND"]) > 20


def test_build_netlist_strips_pin_name_overlines():
    # The QSPI flash symbol has overlined pin names (e.g. C\S\). Netlist pin
    # names use plain text, consistent with the schematic converter.
    design = parse_dsn(DSN_FILE)
    netlist = build_netlist(design)
    pin_names = {entry.pin_name for entries in netlist.values() for entry in entries}
    assert "CS" in pin_names
    assert not any("\\" in name for name in pin_names)


def test_write_design(tmp_path):
    raw = parse_dsn(DSN_FILE)
    design = dsn_to_design(raw, name="RPI-PICO")
    out = tmp_path / "netlist.txt"
    write_design(design, out)
    content = out.read_text()
    assert "DESIGN SUMMARY" in content
    assert "COMPONENTS" in content
    assert "NETS" in content
    assert "U1" in content
    assert "PAGE1" in content
