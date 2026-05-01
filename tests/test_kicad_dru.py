"""Tests for the KiCad .kicad_dru design rules parser."""

from pathlib import Path

import pytest

from phosphor_eda.kicad.dru_parser import parse_kicad_dru

FIXTURE = (
    Path(__file__).parent / "fixtures" / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_dru"
)

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="Fixture not available")


@pytest.fixture(scope="module")
def rules():
    return parse_kicad_dru(FIXTURE)


def test_rule_count(rules) -> None:
    """Should parse a meaningful number of rules (excluding commented ones)."""
    assert len(rules) >= 15


def test_usb_20_inner_track_width(rules) -> None:
    """USB 2.0 inner track_width opt=0.16mm."""
    usb_inner_tw = [r for r in rules if "(usb_2.0_inner" in r.name and r.kind == "track_width"]
    assert len(usb_inner_tw) == 1
    assert usb_inner_tw[0].preferred_value_mm == pytest.approx(0.16)
    assert usb_inner_tw[0].layer_scope == "inner"


def test_usb_20_inner_diff_pair_gap(rules) -> None:
    """USB 2.0 inner diff_pair_gap opt=0.138mm."""
    usb_inner_gap = [r for r in rules if "(usb_2.0_inner" in r.name and r.kind == "diff_pair_gap"]
    assert len(usb_inner_gap) == 1
    assert usb_inner_gap[0].preferred_value_mm == pytest.approx(0.138)


def test_hdmi_clearance_rule(rules) -> None:
    """HDMI clearance rule has min=0.2mm and preserved condition."""
    hdmi_clr = [r for r in rules if "(hdmi_clearance" in r.name]
    assert len(hdmi_clr) == 1
    assert hdmi_clr[0].kind == "clearance"
    assert hdmi_clr[0].min_value_mm == pytest.approx(0.2)
    assert "100Ohm-diff_HDMI" in hdmi_clr[0].scope1


def test_commented_constraints_excluded(rules) -> None:
    """Commented-out constraints (lines starting with #) should not appear."""
    # The pcie_clearance rule has its constraint commented out
    pcie_clr = [r for r in rules if "(pcie_clearance" in r.name]
    assert len(pcie_clr) == 0  # No constraints → no rules emitted


def test_layer_scope_outer(rules) -> None:
    """Outer-layer rules have layer_scope='outer'."""
    outer_rules = [r for r in rules if r.layer_scope == "outer"]
    assert len(outer_rules) >= 5


def test_condition_preserved(rules) -> None:
    """Condition strings are preserved verbatim."""
    usb_inner = [r for r in rules if "(usb_2.0_inner" in r.name]
    assert usb_inner[0].scope1 != ""
    assert "85Ohm-diff_USB_2.0" in usb_inner[0].scope1
