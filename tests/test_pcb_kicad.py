"""Tests for the KiCad .kicad_pcb parser."""

from pathlib import Path

import pytest
import sexpdata

from phosphor_eda.kicad.pcb_parser import (
    _extract_value,  # pyright: ignore[reportPrivateUsage]
    parse_kicad_pcb,
)
from phosphor_eda.pcb import LayerFunction, PcbBoard

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"


@pytest.fixture(scope="module")
def board() -> PcbBoard:
    return parse_kicad_pcb(FIXTURE)


# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------


def test_layer_definitions_populated(board: PcbBoard) -> None:
    """Parser should populate board.layers from the (layers ...) section."""
    assert len(board.layers) > 0


def test_copper_layer_function(board: PcbBoard) -> None:
    fcu = board.layer_for("F.Cu")
    assert fcu is not None
    assert fcu.function == LayerFunction.COPPER
    assert fcu.side == "front"
    assert fcu.number == 0


def test_back_copper_layer(board: PcbBoard) -> None:
    bcu = board.layer_for("B.Cu")
    assert bcu is not None
    assert bcu.function == LayerFunction.COPPER
    assert bcu.side == "back"


def test_inner_copper_layer(board: PcbBoard) -> None:
    in1 = board.layer_for("In1.Cu")
    assert in1 is not None
    assert in1.function == LayerFunction.COPPER
    assert in1.side == ""  # inner copper has no side


def test_silk_layer_function(board: PcbBoard) -> None:
    layers = board.layers_by_function(LayerFunction.SILKSCREEN)
    assert len(layers) >= 2
    names = {lyr.name for lyr in layers}
    assert "F.SilkS" in names


def test_fab_layer_function(board: PcbBoard) -> None:
    layers = board.layers_by_function(LayerFunction.FAB)
    names = {lyr.name for lyr in layers}
    assert "F.Fab" in names
    assert "B.Fab" in names


def test_edge_layer(board: PcbBoard) -> None:
    layers = board.layers_by_function(LayerFunction.EDGE)
    assert len(layers) == 1
    assert layers[0].name == "Edge.Cuts"


def test_layers_by_function_filters(board: PcbBoard) -> None:
    copper = board.layers_by_function(LayerFunction.COPPER)
    assert all(lyr.function == LayerFunction.COPPER for lyr in copper)
    assert len(copper) >= 2  # At least F.Cu and B.Cu


def test_layer_for_missing(board: PcbBoard) -> None:
    assert board.layer_for("Nonexistent") is None


# ---------------------------------------------------------------------------
# Board metadata
# ---------------------------------------------------------------------------


def test_board_name(board: PcbBoard) -> None:
    assert board.name == "Debugotron SWD Switch"


def test_net_count(board: PcbBoard) -> None:
    assert len(board.nets) == 28


def test_net_names(board: PcbBoard) -> None:
    assert board.nets[0].name == ""
    assert board.nets[1].name == "VCC"
    assert board.nets[2].name == "GND"


def test_footprint_count(board: PcbBoard) -> None:
    assert len(board.footprints) == 28


def test_footprint_refs_exist(board: PcbBoard) -> None:
    refs = {fp.reference for fp in board.footprints}
    assert "TP3" in refs
    assert "TP5" in refs
    assert "U5" in refs
    assert "D1" in refs


def test_footprint_layer(board: PcbBoard) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.layer == "B.Cu"
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.layer == "F.Cu"


def test_footprint_position(board: PcbBoard) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.x == pytest.approx(93.5)
    assert tp3.y == pytest.approx(64.5)


def test_pad_count(board: PcbBoard) -> None:
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert len(d1.pads) == 2
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert len(tp3.pads) == 1


def test_pad_net(board: PcbBoard) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.pads[0].net_name == "/SWD_EN_EXT"


def test_pad_absolute_coords(board: PcbBoard) -> None:
    """Pad coords should be in absolute board space, not footprint-local."""
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    pad = tp3.pads[0]
    # TP3 at (93.5, 64.5), pad at local (0, 0) -> absolute (93.5, 64.5)
    assert pad.x == pytest.approx(93.5, abs=0.1)
    assert pad.y == pytest.approx(64.5, abs=0.1)


def test_segment_count(board: PcbBoard) -> None:
    assert len(board.segments) == 276


def test_via_count(board: PcbBoard) -> None:
    assert len(board.vias) == 49


def test_board_outline(board: PcbBoard) -> None:
    assert len(board.outline_lines) > 0
    assert len(board.outline_arcs) > 0
    # Outline includes both gr_line and fp_line on Edge.Cuts
    assert len(board.outline_lines) == 10
    assert len(board.outline_arcs) == 6  # 4 board corners + 2 USB notch corners


def test_board_bbox(board: PcbBoard) -> None:
    min_x, min_y, max_x, max_y = board.bbox()
    assert min_x == pytest.approx(91.0, abs=1.0)
    assert min_y == pytest.approx(55.0, abs=1.0)
    assert max_x == pytest.approx(121.0, abs=1.0)
    assert max_y == pytest.approx(75.0, abs=1.0)


def test_nets_for_component(board: PcbBoard) -> None:
    nets = board.nets_for_component("TP3")
    assert 17 in nets  # /SWD_EN_EXT


def test_net_numbers_by_name(board: PcbBoard) -> None:
    vcc = board.net_numbers_by_name("VCC")
    assert 1 in vcc
    # Substring match
    swd = board.net_numbers_by_name("SWD")
    assert len(swd) >= 2  # Multiple SWD-related nets


def test_footprint_bbox(board: PcbBoard) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.bbox is not None
    min_x, min_y, max_x, max_y = tp3.bbox
    # Courtyard circle ~2mm radius around (93.5, 64.5)
    assert min_x < 93.5 < max_x
    assert min_y < 64.5 < max_y


def test_footprint_by_ref_case_insensitive(board: PcbBoard) -> None:
    assert board.footprint_by_ref("tp3") is not None
    assert board.footprint_by_ref("TP3") is not None


def test_footprint_by_ref_missing(board: PcbBoard) -> None:
    assert board.footprint_by_ref("NONEXISTENT") is None


# ---------------------------------------------------------------------------
# Zone / polygon parsing
# ---------------------------------------------------------------------------


def test_polygon_count(board: PcbBoard) -> None:
    """swd_switch has 2 zones with multiple filled_polygon entries."""
    assert len(board.polygons) > 0


def test_polygon_layers(board: PcbBoard) -> None:
    """Zone polygons should be on inner copper layers."""
    layers = {p.layer for p in board.polygons}
    assert "In1.Cu" in layers
    assert "In2.Cu" in layers


def test_polygon_net(board: PcbBoard) -> None:
    """Zone polygons should carry net info from their parent zone."""
    nets = {(p.net_number, p.net_name) for p in board.polygons}
    assert (2, "GND") in nets
    assert (1, "VCC") in nets


def test_polygon_has_points(board: PcbBoard) -> None:
    """Every polygon should have a non-empty points list."""
    for p in board.polygons:
        assert len(p.points) >= 3


def test_polygon_total_points(board: PcbBoard) -> None:
    """Sanity check: total filled_polygon points should be ~5726."""
    total = sum(len(p.points) for p in board.polygons)
    assert 5000 < total < 7000


def test_trace_arc_count(board: PcbBoard) -> None:
    """swd_switch has no trace arcs."""
    assert len(board.trace_arcs) == 0


# ---------------------------------------------------------------------------
# OrangeCrab fixture (KiCad 5, complex board)
# ---------------------------------------------------------------------------

ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"


@pytest.fixture(scope="module")
def orangecrab_board() -> PcbBoard:
    return parse_kicad_pcb(ORANGECRAB_FIXTURE)


def test_orangecrab_polygon_count(orangecrab_board: PcbBoard) -> None:
    """OrangeCrab has 40 zones — should produce many polygons."""
    assert len(orangecrab_board.polygons) > 40


def test_orangecrab_polygon_layers(orangecrab_board: PcbBoard) -> None:
    """Zone polygons should span multiple copper layers."""
    layers = {p.layer for p in orangecrab_board.polygons}
    assert "F.Cu" in layers
    assert "B.Cu" in layers


# ---------------------------------------------------------------------------
# 3D model parsing
# ---------------------------------------------------------------------------


def test_footprint_has_models(board: PcbBoard) -> None:
    """D1 (LED) should have exactly 1 model entry."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert len(d1.models_3d) == 1


def test_model_source_path(board: PcbBoard) -> None:
    """Model source should preserve the raw KiCad path with env vars."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    model = d1.models_3d[0]
    assert "${KICAD6_3DMODEL_DIR}" in model.source
    assert "LED_0603_1608Metric.wrl" in model.source


def test_model_rotation(board: PcbBoard) -> None:
    """U5 has (rotate (xyz 0 0 -90))."""
    u5 = board.footprint_by_ref("U5")
    assert u5 is not None
    assert len(u5.models_3d) == 1
    assert u5.models_3d[0].rotation == (0.0, 0.0, -90.0)


def test_model_scale_default(board: PcbBoard) -> None:
    """Most models have (scale (xyz 1 1 1))."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.models_3d[0].scale == (1.0, 1.0, 1.0)


def test_model_offset_default(board: PcbBoard) -> None:
    """Most models have (offset (xyz 0 0 0))."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.models_3d[0].offset == (0.0, 0.0, 0.0)


def test_footprint_without_model(board: PcbBoard) -> None:
    """Test points have no (model ...) entry."""
    tp5 = board.footprint_by_ref("TP5")
    assert tp5 is not None
    assert tp5.models_3d == []


def test_multiple_models_per_footprint(orangecrab_board: PcbBoard) -> None:
    """OrangeCrab FPGA footprint (U3) has 2 model entries."""
    u3 = orangecrab_board.footprint_by_ref("U3")
    assert u3 is not None
    assert len(u3.models_3d) == 2


def test_kicad5_at_vs_kicad6_offset(orangecrab_board: PcbBoard) -> None:
    """OrangeCrab uses (at (xyz ...)) instead of (offset (xyz ...)).

    Both should parse as the model offset.
    """
    u3 = orangecrab_board.footprint_by_ref("U3")
    assert u3 is not None
    assert len(u3.models_3d) == 2
    for model in u3.models_3d:
        assert model.offset == (0.0, 0.0, 0.0)
        assert model.scale == (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# Value extraction (_extract_value)
# ---------------------------------------------------------------------------


def _make_fp_sexpr(body: str) -> list[object]:
    """Build a minimal footprint s-expression with given inner body."""
    raw = f'(footprint "test:Pkg" {body})'
    parsed = sexpdata.loads(raw)
    return list(parsed)


def test_extract_value_kicad8() -> None:
    """KiCad 8 format uses (property "Value" "100nF" ...)."""
    fp = _make_fp_sexpr('(property "Value" "100nF" (at 0 0))')
    assert _extract_value(fp) == "100nF"


def test_extract_value_kicad6() -> None:
    """KiCad 6 format uses (fp_text value "100nF" ...)."""
    fp = _make_fp_sexpr('(fp_text value "100nF" (at 0 0))')
    assert _extract_value(fp) == "100nF"


def test_extract_value_missing() -> None:
    """No value property or fp_text → empty string."""
    fp = _make_fp_sexpr('(property "Reference" "U1" (at 0 0))')
    assert _extract_value(fp) == ""


# ---------------------------------------------------------------------------
# footprint_ref threading
# ---------------------------------------------------------------------------


def test_all_pads_have_footprint_ref(board: PcbBoard) -> None:
    """Every pad should have footprint_ref matching its parent footprint."""
    for fp in board.footprints:
        for pad in fp.pads:
            assert pad.footprint_ref == fp.reference, (
                f"Pad {pad.number} on {fp.reference} has footprint_ref={pad.footprint_ref!r}"
            )


def test_silkscreen_lines_have_footprint_ref(board: PcbBoard) -> None:
    """Silkscreen lines from footprints carry the parent's ref."""
    for fp in board.footprints:
        for line in fp.silkscreen_lines:
            assert line.footprint_ref == fp.reference


def test_fab_lines_have_footprint_ref(board: PcbBoard) -> None:
    """Fab lines from footprints carry the parent's ref."""
    for fp in board.footprints:
        for line in fp.fab_lines:
            assert line.footprint_ref == fp.reference


# ---------------------------------------------------------------------------
# footprint_lib and value (real fixture)
# ---------------------------------------------------------------------------


def test_d1_footprint_lib_is_led(board: PcbBoard) -> None:
    """D1 is an LED — its footprint_lib should contain 'LED'."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert "LED" in d1.footprint_lib


def test_d1_has_value(board: PcbBoard) -> None:
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.value != ""


def test_multiple_footprints_have_lib(board: PcbBoard) -> None:
    """Most footprints should have a non-empty footprint_lib."""
    with_lib = [fp for fp in board.footprints if fp.footprint_lib]
    assert len(with_lib) >= 5
