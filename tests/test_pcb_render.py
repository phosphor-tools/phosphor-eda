"""Tests for the PCB SVG renderer — structural and CSS assertions."""

from pathlib import Path

import pytest

from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb_render import render_pcb_svg

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"
ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"


@pytest.fixture(scope="module")
def board():
    return parse_kicad_pcb(FIXTURE)


@pytest.fixture(scope="module")
def orangecrab_board():
    return parse_kicad_pcb(ORANGECRAB_FIXTURE)


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


def test_valid_svg(board):
    svg = render_pcb_svg(board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_has_theme_style(board):
    svg = render_pcb_svg(board)
    assert '<style id="theme">' in svg


def test_has_board_clip(board):
    svg = render_pcb_svg(board)
    assert "board-clip" in svg


def test_has_drill_clip(board):
    svg = render_pcb_svg(board)
    assert "drill-clip" in svg


def test_has_copper_layer_groups(board):
    svg = render_pcb_svg(board)
    assert 'data-layer="F.Cu"' in svg
    assert 'data-layer="B.Cu"' in svg


def test_layer_paint_order(board):
    """B.Cu should appear before F.Cu in document order (painter's model)."""
    svg = render_pcb_svg(board)
    assert svg.index('data-layer="B.Cu"') < svg.index('data-layer="F.Cu"')


def test_silk_after_copper(board):
    """Silkscreen layer group appears after copper layer groups."""
    svg = render_pcb_svg(board)
    fcu_pos = svg.index('data-layer="F.Cu"')
    silk_names = ["F.SilkS", "F.Silkscreen", "B.SilkS", "B.Silkscreen"]
    found = False
    for name in silk_names:
        marker = f'data-layer="{name}"'
        if marker in svg:
            assert svg.index(marker) > fcu_pos
            found = True
    assert found, "No silkscreen layer group found"


# ---------------------------------------------------------------------------
# Data attribute tests
# ---------------------------------------------------------------------------


def test_pad_attributes(board):
    svg = render_pcb_svg(board)
    assert 'data-type="pad"' in svg
    assert 'data-component=' in svg
    assert 'data-pad=' in svg
    assert 'data-net=' in svg


def test_trace_attributes(board):
    """All traces are always present (visibility controlled via CSS)."""
    svg = render_pcb_svg(board)
    assert 'data-type="trace"' in svg
    assert 'data-net-number=' in svg


def test_via_attributes(board):
    svg = render_pcb_svg(board)
    assert 'data-type="via"' in svg
    assert 'class="via"' in svg


def test_zone_attributes(board):
    """swd_switch has zones on inner copper layers."""
    svg = render_pcb_svg(board)
    assert 'data-type="zone"' in svg


def test_component_body_attributes(board):
    svg = render_pcb_svg(board)
    assert 'data-type="body"' in svg


# ---------------------------------------------------------------------------
# Highlight tests
# ---------------------------------------------------------------------------


def test_highlight_adds_style(board):
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert '<style id="highlight">' in svg


def test_highlight_css_targets_net(board):
    """Highlight CSS should contain data-net-number selector for VCC (net 1)."""
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert 'data-net-number="1"' in svg


def test_highlight_component_box(board):
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    assert "highlight-box" in svg
    assert "TP3" in svg


def test_highlight_component_label(board):
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    assert "highlight-label" in svg


def test_no_highlight_without_args(board):
    svg = render_pcb_svg(board)
    assert '<style id="highlight">' not in svg


# ---------------------------------------------------------------------------
# Side tests
# ---------------------------------------------------------------------------


def test_back_mirror(board):
    svg = render_pcb_svg(board, side="back")
    assert "scale(-1" in svg


def test_front_no_mirror(board):
    svg = render_pcb_svg(board, side="front")
    assert "scale(-1" not in svg


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unknown_net_no_error(board):
    svg = render_pcb_svg(board, highlight_nets=["NONEXISTENT_NET_XYZ"])
    assert svg.startswith("<svg")


def test_unknown_component_no_error(board):
    svg = render_pcb_svg(board, highlight_components=["NONEXISTENT"])
    assert svg.startswith("<svg")


def test_both_highlight_types(board):
    svg = render_pcb_svg(board, highlight_nets=["GND"], highlight_components=["TP3"])
    assert svg.startswith("<svg")
    assert "TP3" in svg
    assert '<style id="highlight">' in svg


# ---------------------------------------------------------------------------
# OrangeCrab integration
# ---------------------------------------------------------------------------


def test_orangecrab_renders(orangecrab_board):
    svg = render_pcb_svg(orangecrab_board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_orangecrab_has_zones(orangecrab_board):
    svg = render_pcb_svg(orangecrab_board)
    assert 'data-type="zone"' in svg
