"""Tests for the KiCad .kicad_pro parser (net classes)."""

from pathlib import Path

import pytest

from phosphor_eda.formats.kicad.pro_parser import parse_kicad_pro

FIXTURE = (
    Path(__file__).parent / "fixtures" / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pro"
)

requires_jetson_fixture = pytest.mark.skipif(not FIXTURE.exists(), reason="Fixture not available")


@pytest.fixture(scope="module")
def net_classes():
    return parse_kicad_pro(FIXTURE)


@requires_jetson_fixture
def test_net_class_count(net_classes) -> None:
    assert len(net_classes) == 8


@requires_jetson_fixture
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


@requires_jetson_fixture
def test_default_class_values(net_classes) -> None:
    default = next(nc for nc in net_classes if nc.name == "Default")
    assert default.clearance_mm == pytest.approx(0.125)
    assert default.trace_width_mm == pytest.approx(0.15)
    assert default.via_diameter_mm == pytest.approx(0.45)
    assert default.via_drill_mm == pytest.approx(0.1)


@requires_jetson_fixture
def test_poe_class_has_assignments(net_classes) -> None:
    """PoE class has 5 explicitly assigned nets."""
    poe = next(nc for nc in net_classes if nc.name == "PoE")
    # At least the 5 explicit assignments
    explicit = [m for m in poe.members if not m.startswith("/")]
    assert len(explicit) >= 5


@requires_jetson_fixture
def test_hdmi_class_has_patterns(net_classes) -> None:
    """HDMI class has pattern-based members."""
    hdmi = next(nc for nc in net_classes if nc.name == "100Ohm-diff_HDMI")
    assert len(hdmi.members) > 0
    # Patterns contain path-like net names
    assert any("HDMI" in m for m in hdmi.members)


def test_null_netclass_assignments_does_not_crash(tmp_path: Path) -> None:
    # KiCad 7+ writes "netclass_assignments": null when nothing is assigned.
    pro = tmp_path / "proj.kicad_pro"
    pro.write_text(
        '{"net_settings": {"classes": [{"name": "Default"}],'
        ' "netclass_assignments": null, "netclass_patterns": null}}',
        encoding="utf-8",
    )
    net_classes = parse_kicad_pro(pro)
    assert [nc.name for nc in net_classes] == ["Default"]


def test_string_netclass_assignments(tmp_path: Path) -> None:
    pro = tmp_path / "proj.kicad_pro"
    pro.write_text(
        '{"net_settings": {"classes": [{"name": "Default"}, {"name": "Power"}],'
        ' "netclass_assignments": {"VBUS": "Power"}}}',
        encoding="utf-8",
    )
    net_classes = parse_kicad_pro(pro)
    power = next(nc for nc in net_classes if nc.name == "Power")
    assert power.members == ["VBUS"]


def test_list_netclass_assignments(tmp_path: Path) -> None:
    # KiCad 9/10 write list-valued assignments (multiple classes per net).
    pro = tmp_path / "proj.kicad_pro"
    pro.write_text(
        '{"net_settings": {"classes": [{"name": "Default"}, {"name": "Power"}, {"name": "HV"}],'
        ' "netclass_assignments": {"VBUS": ["Power", "HV"]}}}',
        encoding="utf-8",
    )
    net_classes = parse_kicad_pro(pro)
    power = next(nc for nc in net_classes if nc.name == "Power")
    hv = next(nc for nc in net_classes if nc.name == "HV")
    assert power.members == ["VBUS"]
    assert hv.members == ["VBUS"]
