"""Tests for net/component classification heuristics."""

import pytest

from phosphor_eda.classify import is_power_net
from phosphor_eda.schematic import Net

# name -> expected is_power_net result.
#
# Policy: named rails/grounds match on the *whole* name (case-insensitive), so
# decorations like GND_DETECT or VCC_SENSE are treated as signals, not rails.
# Voltage-rail patterns accept an optional +/-/P prefix and an optional decimal
# point (3V3, 3.3V, +5V, -12V, P3V3, 12V0).
_CASES = [
    # Named rails
    ("GND", True),
    ("VCC", True),
    ("VDD", True),
    ("VSS", True),
    ("VBAT", True),
    ("VBUS", True),
    ("VIN", True),
    ("VEE", True),
    ("AVDD", True),
    ("DVDD", True),
    ("AGND", True),
    ("PGND", True),
    ("DGND", True),
    # Case-insensitive
    ("gnd", True),
    ("Vbus", True),
    # Voltage-rail patterns
    ("3V3", True),
    ("5V", True),
    ("12V0", True),
    ("P3V3", True),
    ("+3V3", True),
    ("+5V", True),
    ("-12V", True),
    ("3.3V", True),
    ("1.8V", True),
    ("+1V8", True),
    # Altium/OrCAD rail-first forms
    ("V3P3", True),
    ("V1P8", True),
    ("V5P0", True),
    # Negatives — signals that look adjacent to rails
    ("GND_DETECT", False),
    ("VCC_SENSE", False),
    ("SIG_A", False),
    ("SPI_CLK", False),
    ("RESET", False),
    ("V", False),
    ("3VV3", False),
    ("DATA3V3BUS", False),
]


@pytest.mark.parametrize(("name", "expected"), _CASES)
def test_is_power_net_by_name(name: str, expected: bool):
    assert is_power_net(name) is expected


def test_is_power_net_classname_escape_hatch():
    """Altium nets tagged ClassName=PWR are power regardless of name."""
    net = Net(id="net:custom_rail", name="CUSTOM_RAIL", metadata={"ClassName": "PWR"})
    assert is_power_net("CUSTOM_RAIL", net)
    assert not is_power_net("CUSTOM_RAIL")


def test_is_power_net_classname_non_pwr():
    net = Net(id="net:sig", name="SIG", metadata={"ClassName": "DIFF"})
    assert not is_power_net("SIG", net)
