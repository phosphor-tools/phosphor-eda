"""Tests for the PCB SVG renderer."""

from pathlib import Path

import pytest

from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb_render import render_pcb_svg

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"


@pytest.fixture(scope="module")
def board():
    return parse_kicad_pcb(FIXTURE)


def test_base_render_is_valid_svg(board):
    svg = render_pcb_svg(board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_base_render_no_traces(board):
    """Base render (no highlights) should not contain trace-coloured lines."""
    svg = render_pcb_svg(board)
    assert "#ff4444" not in svg  # front highlight traces
    assert "#5577ff" not in svg  # back highlight traces


def test_base_render_has_board_outline(board):
    svg = render_pcb_svg(board)
    # Board outline uses BOARD_EDGE colour
    assert "#0d3015" in svg


def test_base_render_has_pads(board):
    svg = render_pcb_svg(board)
    # Copper-coloured pads
    assert "#b87333" in svg


def test_base_render_has_silkscreen(board):
    svg = render_pcb_svg(board)
    # Silkscreen is white-ish
    assert "#ffffffcc" in svg


def test_base_render_has_vias(board):
    svg = render_pcb_svg(board)
    # Via silver colour
    assert "#c0c0c0" in svg


def test_highlight_net_shows_traces(board):
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    # VCC traces on front copper should appear in bright red
    assert "#ff4444" in svg


def test_highlight_net_shows_highlighted_vias(board):
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert "#ffdd44" in svg  # highlighted via colour


def test_highlight_component_bbox(board):
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    # Yellow highlight box
    assert "#ffff00" in svg
    # Reference label
    assert "TP3" in svg


def test_highlight_component_also_highlights_nets(board):
    """Highlighting a component should also show its net traces."""
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    # TP3 is on B.Cu, its net traces should show on back (blue)
    assert "#5577ff" in svg


def test_back_side_mirror(board):
    svg = render_pcb_svg(board, side="back")
    assert "scale(-1" in svg


def test_front_side_no_mirror(board):
    svg = render_pcb_svg(board, side="front")
    assert "scale(-1" not in svg


def test_unknown_net_no_error(board):
    """Highlighting a nonexistent net should render without error."""
    svg = render_pcb_svg(board, highlight_nets=["NONEXISTENT_NET_XYZ"])
    assert svg.startswith("<svg")
    # No traces should appear
    assert "#ff4444" not in svg


def test_unknown_component_no_error(board):
    """Highlighting a nonexistent component should render without error."""
    svg = render_pcb_svg(board, highlight_components=["NONEXISTENT"])
    assert svg.startswith("<svg")


def test_both_highlight_types(board):
    """Can highlight both nets and components simultaneously."""
    svg = render_pcb_svg(board, highlight_nets=["GND"], highlight_components=["TP3"])
    assert svg.startswith("<svg")
    assert "TP3" in svg
