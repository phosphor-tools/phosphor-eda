"""Tests for the KiCad .kicad_pcb parser."""

from pathlib import Path

import pytest

from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"


@pytest.fixture(scope="module")
def board():
    return parse_kicad_pcb(FIXTURE)


def test_board_name(board):
    assert board.name == "Debugotron SWD Switch"


def test_net_count(board):
    assert len(board.nets) == 28


def test_net_names(board):
    assert board.nets[0].name == ""
    assert board.nets[1].name == "VCC"
    assert board.nets[2].name == "GND"


def test_footprint_count(board):
    assert len(board.footprints) == 28


def test_footprint_refs_exist(board):
    refs = {fp.reference for fp in board.footprints}
    assert "TP3" in refs
    assert "TP5" in refs
    assert "U5" in refs
    assert "D1" in refs


def test_footprint_layer(board):
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.layer == "B.Cu"
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.layer == "F.Cu"


def test_footprint_position(board):
    tp3 = board.footprint_by_ref("TP3")
    assert tp3.x == pytest.approx(93.5)
    assert tp3.y == pytest.approx(64.5)


def test_pad_count(board):
    d1 = board.footprint_by_ref("D1")
    assert len(d1.pads) == 2
    tp3 = board.footprint_by_ref("TP3")
    assert len(tp3.pads) == 1


def test_pad_net(board):
    tp3 = board.footprint_by_ref("TP3")
    assert tp3.pads[0].net_name == "/SWD_EN_EXT"


def test_pad_absolute_coords(board):
    """Pad coords should be in absolute board space, not footprint-local."""
    tp3 = board.footprint_by_ref("TP3")
    pad = tp3.pads[0]
    # TP3 at (93.5, 64.5), pad at local (0, 0) -> absolute (93.5, 64.5)
    assert pad.x == pytest.approx(93.5, abs=0.1)
    assert pad.y == pytest.approx(64.5, abs=0.1)


def test_segment_count(board):
    assert len(board.segments) == 276


def test_via_count(board):
    assert len(board.vias) == 49


def test_board_outline(board):
    assert len(board.outline_lines) > 0
    assert len(board.outline_arcs) > 0
    # Outline includes both gr_line and fp_line on Edge.Cuts
    assert len(board.outline_lines) == 10
    assert len(board.outline_arcs) == 4


def test_board_bbox(board):
    min_x, min_y, max_x, max_y = board.bbox()
    assert min_x == pytest.approx(91.0, abs=1.0)
    assert min_y == pytest.approx(55.0, abs=1.0)
    assert max_x == pytest.approx(121.0, abs=1.0)
    assert max_y == pytest.approx(75.0, abs=1.0)


def test_nets_for_component(board):
    nets = board.nets_for_component("TP3")
    assert 17 in nets  # /SWD_EN_EXT


def test_net_numbers_by_name(board):
    vcc = board.net_numbers_by_name("VCC")
    assert 1 in vcc
    # Substring match
    swd = board.net_numbers_by_name("SWD")
    assert len(swd) >= 2  # Multiple SWD-related nets


def test_footprint_bbox(board):
    tp3 = board.footprint_by_ref("TP3")
    assert tp3.bbox is not None
    min_x, min_y, max_x, max_y = tp3.bbox
    # Courtyard circle ~2mm radius around (93.5, 64.5)
    assert min_x < 93.5 < max_x
    assert min_y < 64.5 < max_y


def test_footprint_by_ref_case_insensitive(board):
    assert board.footprint_by_ref("tp3") is not None
    assert board.footprint_by_ref("TP3") is not None


def test_footprint_by_ref_missing(board):
    assert board.footprint_by_ref("NONEXISTENT") is None
