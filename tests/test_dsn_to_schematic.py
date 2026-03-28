"""Tests for DSN -> schematic domain model conversion."""

from pathlib import Path

import pytest
from phosphor_eda.dsn.parser import parse_dsn
from phosphor_eda.dsn.to_schematic import dsn_to_design

PICOW_DSN = Path("raspberry-pi-pico-w/picow_design_files/RPI-PICOW-R2.DSN")

pytestmark = pytest.mark.skipif(
    not PICOW_DSN.exists(), reason="Pi Pico W DSN not available"
)


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
    assert len(named_pins) > 0


def test_dsn_to_design_gnd_has_many_pins():
    raw = parse_dsn(PICOW_DSN)
    design = dsn_to_design(raw, name="PicoW")
    gnd = next(n for n in design.nets if n.name == "GND")
    assert len(gnd.pins) > 10
