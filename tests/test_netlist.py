from pathlib import Path

from ecad_tools.dsn.netlist import build_netlist, write_netlist
from ecad_tools.dsn.parser import parse_dsn

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


def test_write_netlist(tmp_path):
    design = parse_dsn(DSN_FILE)
    out = tmp_path / "netlist.txt"
    write_netlist(design, out)
    content = out.read_text()
    assert "PARSED DESIGN SUMMARY" in content
    assert "Components" in content
    assert "Netlist" in content
    assert "U1" in content
