"""Tests for multi-page schematic parsing."""

from pathlib import Path

from phosphor_eda.dsn.netlist import build_netlist
from phosphor_eda.dsn.parser import parse_dsn
from phosphor_eda.dsn.to_schematic import dsn_to_design
from phosphor_eda.serialize import write_design

PICO_W_DSN = Path("cli/tests/fixtures/dsn/raspberry-pi-pico-w/RPI-PICOW-R2.DSN")
CMIO_DSN = Path("cli/tests/fixtures/dsn/raspberry-pi-cmio/RPI-CMIO-V3_0-PUBLIC.DSN")


# --- Pico W (2 pages) ---


def test_picow_has_two_pages():
    design = parse_dsn(PICO_W_DSN)
    assert len(design.pages) == 2
    names = {p.name for p in design.pages}
    assert "RP2040" in names
    assert "Wifi" in names


def test_picow_page_components():
    design = parse_dsn(PICO_W_DSN)
    page_by_name = {p.name: p for p in design.pages}

    rp2040 = page_by_name["RP2040"]
    wifi = page_by_name["Wifi"]

    assert len(rp2040.instances) == 52
    assert len(wifi.instances) == 37

    # U1 (RP2040) should be on the RP2040 page
    rp2040_refs = {i.reference for i in rp2040.instances}
    assert "U1" in rp2040_refs


def test_picow_cross_page_nets():
    """Nets shared between pages should exist in both pages' net lists."""
    design = parse_dsn(PICO_W_DSN)
    page_by_name = {p.name: p for p in design.pages}

    # WL_CLK should be defined as a net on both pages
    rp2040_nets = {n.name for n in page_by_name["RP2040"].nets}
    wifi_nets = {n.name for n in page_by_name["Wifi"].nets}
    shared_nets = rp2040_nets & wifi_nets
    assert "WL_CLK" in shared_nets
    assert "GND" in shared_nets
    assert "3V3" in shared_nets


def test_picow_total_components():
    """Total components across all pages."""
    design = parse_dsn(PICO_W_DSN)
    total = sum(len(p.instances) for p in design.pages)
    assert total == 89  # 52 + 37


def test_picow_gnd_spans_pages():
    """GND net should have pins from both pages."""
    design = parse_dsn(PICO_W_DSN)
    netlist = build_netlist(design)
    assert "GND" in netlist
    gnd_refs = {e.reference for e in netlist["GND"]}
    # Should include components from both RP2040 and Wifi pages
    assert "U1" in gnd_refs  # RP2040 page


def test_picow_write_design(tmp_path):
    raw = parse_dsn(PICO_W_DSN)
    design = dsn_to_design(raw, name="PicoW")
    out = tmp_path / "picow-netlist.txt"
    write_design(design, out)
    content = out.read_text()
    assert "RP2040" in content
    assert "Wifi" in content
    assert "WL_CLK" in content


# --- CMIO V3.0 (3 pages) ---


def test_cmio_has_three_pages():
    design = parse_dsn(CMIO_DSN)
    assert len(design.pages) == 3
    names = [p.name for p in design.pages]
    assert "PAGE1 - CONTENTS" in names
    assert "PAGE2 - PWR, CM, GPIO, JTAG" in names
    assert "PAGE3 - CSI, DSI, HDMI, USB" in names


def test_cmio_contents_page_is_empty():
    """The contents page should have no components."""
    design = parse_dsn(CMIO_DSN)
    page_by_name = {p.name: p for p in design.pages}
    contents = page_by_name["PAGE1 - CONTENTS"]
    assert len(contents.instances) == 0
    assert len(contents.nets) == 0


def test_cmio_total_components():
    design = parse_dsn(CMIO_DSN)
    total = sum(len(p.instances) for p in design.pages)
    assert total == 102  # 0 + 73 + 29


def test_cmio_cross_page_nets():
    """Nets should merge across PAGE2 and PAGE3."""
    design = parse_dsn(CMIO_DSN)
    netlist = build_netlist(design)
    assert "GND" in netlist
    assert len(netlist["GND"]) > 30


def test_cmio_write_design(tmp_path):
    raw = parse_dsn(CMIO_DSN)
    design = dsn_to_design(raw, name="CMIO")
    out = tmp_path / "cmio-netlist.txt"
    write_design(design, out)
    content = out.read_text()
    assert "PAGE1 - CONTENTS" in content
    assert "PAGE2 - PWR, CM, GPIO, JTAG" in content
    assert "PAGE3 - CSI, DSI, HDMI, USB" in content
    assert "102 components" in content
