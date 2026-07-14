"""Tests for the KiCad .kicad_dru design rules parser."""

from pathlib import Path

import pytest

from phosphor_eda.formats.kicad.dru_parser import parse_kicad_dru

FIXTURE = Path(__file__).parent / "upstream" / "jetson-orin" / "jetson-orin-baseboard.kicad_dru"

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


@pytest.mark.parametrize(
    ("value_text", "expected_mm"),
    [
        ("8mil", 8 * 0.0254),
        ("0.01in", 0.254),
        ("200um", 0.2),
        ("0.25mm", 0.25),
        ("1.5", 1.5),
    ],
)
def test_constraint_values_accept_all_units(tmp_path, value_text: str, expected_mm: float) -> None:
    """Non-mm units (and bare values) convert to millimetres."""
    dru = tmp_path / "units.kicad_dru"
    dru.write_text(
        f'(rule "u"\n  (constraint track_width (min {value_text})))\n',
        encoding="utf-8",
    )

    rules = parse_kicad_dru(dru)

    assert len(rules) == 1
    assert rules[0].min_value_mm == pytest.approx(expected_mm)


def test_wrapped_constraint_value_list_is_parsed(tmp_path) -> None:
    """A constraint whose min/opt/max span several lines keeps every value."""
    dru = tmp_path / "wrapped.kicad_dru"
    dru.write_text(
        '(rule "wrapped"\n'
        "  (constraint track_width\n"
        "    (min 8mil)\n"
        "    (opt 10mil)\n"
        "    (max 12mil)))\n",
        encoding="utf-8",
    )

    rules = parse_kicad_dru(dru)

    assert len(rules) == 1
    rule = rules[0]
    assert rule.kind == "track_width"
    assert rule.min_value_mm == pytest.approx(8 * 0.0254)
    assert rule.preferred_value_mm == pytest.approx(10 * 0.0254)
    assert rule.max_value_mm == pytest.approx(12 * 0.0254)
