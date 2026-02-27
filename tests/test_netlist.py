from pathlib import Path

from ecad_tools.dsn.netlist import build_netlist
from ecad_tools.dsn.parser import parse_dsn
from ecad_tools.dsn.to_schematic import dsn_to_design
from ecad_tools.serialize import write_design

DSN_FILE = Path("raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN")


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
