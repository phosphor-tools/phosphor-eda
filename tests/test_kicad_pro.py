"""Tests for the KiCad .kicad_pro parser (net classes)."""

from pathlib import Path

import pytest

from phosphor_eda.kicad.pro_parser import parse_kicad_pro

FIXTURE = (
    Path(__file__).parent / "fixtures" / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pro"
)

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="Fixture not available")


@pytest.fixture(scope="module")
def net_classes():
    return parse_kicad_pro(FIXTURE)


def test_net_class_count(net_classes) -> None:
    assert len(net_classes) == 8


def test_net_class_names(net_classes) -> None:
    names = {nc.name for nc in net_classes}
    assert "Default" in names
    assert "100Ohm-diff_HDMI" in names
    assert "100Ohm-diff_MDI" in names
    assert "85Ohm-diff_CSI" in names
    assert "85Ohm-diff_PCIE" in names
    assert "85Ohm-diff_USB_2.0" in names
    assert "85Ohm-diff_USB_SS" in names
    assert "PoE" in names


def test_default_class_values(net_classes) -> None:
    default = next(nc for nc in net_classes if nc.name == "Default")
    assert default.clearance_mm == pytest.approx(0.125)
    assert default.trace_width_mm == pytest.approx(0.15)
    assert default.via_diameter_mm == pytest.approx(0.45)
    assert default.via_drill_mm == pytest.approx(0.1)


def test_poe_class_has_assignments(net_classes) -> None:
    """PoE class has 5 explicitly assigned nets."""
    poe = next(nc for nc in net_classes if nc.name == "PoE")
    # At least the 5 explicit assignments
    explicit = [m for m in poe.members if not m.startswith("/")]
    assert len(explicit) >= 5


def test_hdmi_class_has_patterns(net_classes) -> None:
    """HDMI class has pattern-based members."""
    hdmi = next(nc for nc in net_classes if nc.name == "100Ohm-diff_HDMI")
    assert len(hdmi.members) > 0
    # Patterns contain path-like net names
    assert any("HDMI" in m for m in hdmi.members)
