"""Tests for the KiCad .kicad_pcb parser."""

from pathlib import Path

import pytest
import sexpdata

from phosphor_eda.kicad.pcb_parser import (
    _extract_value,  # pyright: ignore[reportPrivateUsage]
    parse_kicad_pcb,
    parse_kicad_stackup,
)
from phosphor_eda.pcb import LayerFunction, Pcb
from phosphor_eda.project import Stackup

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"


@pytest.fixture(scope="module")
def board() -> Pcb:
    return parse_kicad_pcb(FIXTURE)


# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------


def test_layer_definitions_populated(board: Pcb) -> None:
    """Parser should populate board.layers from the (layers ...) section."""
    assert len(board.layers) > 0


def test_copper_layer_function(board: Pcb) -> None:
    fcu = board.layer_for("F.Cu")
    assert fcu is not None
    assert fcu.function == LayerFunction.COPPER
    assert fcu.side == "front"
    assert fcu.number == 0


def test_back_copper_layer(board: Pcb) -> None:
    bcu = board.layer_for("B.Cu")
    assert bcu is not None
    assert bcu.function == LayerFunction.COPPER
    assert bcu.side == "back"


def test_inner_copper_layer(board: Pcb) -> None:
    in1 = board.layer_for("In1.Cu")
    assert in1 is not None
    assert in1.function == LayerFunction.COPPER
    assert in1.side == ""  # inner copper has no side


def test_silk_layer_function(board: Pcb) -> None:
    layers = board.layers_by_function(LayerFunction.SILKSCREEN)
    assert len(layers) >= 2
    names = {lyr.name for lyr in layers}
    assert "F.SilkS" in names


def test_fab_layer_function(board: Pcb) -> None:
    layers = board.layers_by_function(LayerFunction.FAB)
    names = {lyr.name for lyr in layers}
    assert "F.Fab" in names
    assert "B.Fab" in names


def test_edge_layer(board: Pcb) -> None:
    layers = board.layers_by_function(LayerFunction.EDGE)
    assert len(layers) == 1
    assert layers[0].name == "Edge.Cuts"


def test_layers_by_function_filters(board: Pcb) -> None:
    copper = board.layers_by_function(LayerFunction.COPPER)
    assert all(lyr.function == LayerFunction.COPPER for lyr in copper)
    assert len(copper) >= 2  # At least F.Cu and B.Cu


def test_layer_for_missing(board: Pcb) -> None:
    assert board.layer_for("Nonexistent") is None


# ---------------------------------------------------------------------------
# Board metadata
# ---------------------------------------------------------------------------


def test_board_name(board: Pcb) -> None:
    assert board.name == "Debugotron SWD Switch"


def test_net_count(board: Pcb) -> None:
    assert len(board.nets) == 28


def test_net_names(board: Pcb) -> None:
    assert board.nets[0].name == ""
    assert board.nets[1].name == "VCC"
    assert board.nets[2].name == "GND"


def test_footprint_count(board: Pcb) -> None:
    assert len(board.footprints) == 28


def test_footprint_refs_exist(board: Pcb) -> None:
    refs = {fp.reference for fp in board.footprints}
    assert "TP3" in refs
    assert "TP5" in refs
    assert "U5" in refs
    assert "D1" in refs


def test_footprint_layer(board: Pcb) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.layer == "B.Cu"
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.layer == "F.Cu"


def test_footprint_position(board: Pcb) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.x == pytest.approx(93.5)
    assert tp3.y == pytest.approx(64.5)


def test_pad_count(board: Pcb) -> None:
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert len(d1.pads) == 2
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert len(tp3.pads) == 1


def test_pad_net(board: Pcb) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.pads[0].net_name == "/SWD_EN_EXT"


def test_pad_absolute_coords(board: Pcb) -> None:
    """Pad coords should be in absolute board space, not footprint-local."""
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    pad = tp3.pads[0]
    # TP3 at (93.5, 64.5), pad at local (0, 0) -> absolute (93.5, 64.5)
    assert pad.x == pytest.approx(93.5, abs=0.1)
    assert pad.y == pytest.approx(64.5, abs=0.1)


def test_segment_count(board: Pcb) -> None:
    assert len(board.segments) == 276


def test_via_count(board: Pcb) -> None:
    assert len(board.vias) == 49


def test_board_outline(board: Pcb) -> None:
    assert len(board.outline_lines) > 0
    assert len(board.outline_arcs) > 0
    # Outline includes both gr_line and fp_line on Edge.Cuts
    assert len(board.outline_lines) == 10
    assert len(board.outline_arcs) == 6  # 4 board corners + 2 USB notch corners


def test_board_bbox(board: Pcb) -> None:
    min_x, min_y, max_x, max_y = board.bbox()
    assert min_x == pytest.approx(91.0, abs=1.0)
    assert min_y == pytest.approx(55.0, abs=1.0)
    assert max_x == pytest.approx(121.0, abs=1.0)
    assert max_y == pytest.approx(75.0, abs=1.0)


def test_nets_for_component(board: Pcb) -> None:
    nets = board.nets_for_component("TP3")
    assert 17 in nets  # /SWD_EN_EXT


def test_net_numbers_by_name(board: Pcb) -> None:
    vcc = board.net_numbers_by_name("VCC")
    assert 1 in vcc
    # Exact match — does not return substring matches
    swd = board.net_numbers_by_name("/SWDIO_TMS")
    assert len(swd) == 1
    assert board.net_numbers_by_name("SWD") == set()


def test_footprint_bbox(board: Pcb) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.bbox is not None
    min_x, min_y, max_x, max_y = tp3.bbox
    # Courtyard circle ~2mm radius around (93.5, 64.5)
    assert min_x < 93.5 < max_x
    assert min_y < 64.5 < max_y


def test_footprint_by_ref_case_insensitive(board: Pcb) -> None:
    assert board.footprint_by_ref("tp3") is not None
    assert board.footprint_by_ref("TP3") is not None


def test_footprint_by_ref_missing(board: Pcb) -> None:
    assert board.footprint_by_ref("NONEXISTENT") is None


# ---------------------------------------------------------------------------
# Zone / polygon parsing
# ---------------------------------------------------------------------------


def test_polygon_count(board: Pcb) -> None:
    """swd_switch has 2 zones with multiple filled_polygon entries."""
    assert len(board.polygons) > 0


def test_polygon_layers(board: Pcb) -> None:
    """Zone polygons should be on inner copper layers."""
    layers = {p.layer for p in board.polygons}
    assert "In1.Cu" in layers
    assert "In2.Cu" in layers


def test_polygon_net(board: Pcb) -> None:
    """Zone polygons should carry net info from their parent zone."""
    nets = {(p.net_number, p.net_name) for p in board.polygons}
    assert (2, "GND") in nets
    assert (1, "VCC") in nets


def test_polygon_has_points(board: Pcb) -> None:
    """Every polygon should have a non-empty points list."""
    for p in board.polygons:
        assert len(p.points) >= 3


def test_polygon_total_points(board: Pcb) -> None:
    """Sanity check: total filled_polygon points should be ~5726."""
    total = sum(len(p.points) for p in board.polygons)
    assert 5000 < total < 7000


def test_trace_arc_count(board: Pcb) -> None:
    """swd_switch has no trace arcs."""
    assert len(board.trace_arcs) == 0


# ---------------------------------------------------------------------------
# OrangeCrab fixture (KiCad 5, complex board)
# ---------------------------------------------------------------------------

ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"


@pytest.fixture(scope="module")
def orangecrab_board() -> Pcb:
    return parse_kicad_pcb(ORANGECRAB_FIXTURE)


def test_orangecrab_polygon_count(orangecrab_board: Pcb) -> None:
    """OrangeCrab has 40 zones — should produce many polygons."""
    assert len(orangecrab_board.polygons) > 40


def test_orangecrab_polygon_layers(orangecrab_board: Pcb) -> None:
    """Zone polygons should span multiple copper layers."""
    layers = {p.layer for p in orangecrab_board.polygons}
    assert "F.Cu" in layers
    assert "B.Cu" in layers


def test_orangecrab_pad_layers_are_plain_strings(orangecrab_board: Pcb) -> None:
    """KiCad symbolic layer names should be normalized before entering the domain model."""
    pad_layers = [
        layer
        for footprint in orangecrab_board.footprints
        for pad in footprint.pads
        for layer in pad.layers
    ]
    via_layers = [layer for via in orangecrab_board.vias for layer in via.layers]

    assert pad_layers
    assert all(type(layer) is str for layer in pad_layers)
    assert all(type(layer) is str for layer in via_layers)
    assert "*.Cu" in pad_layers


# ---------------------------------------------------------------------------
# 3D model parsing
# ---------------------------------------------------------------------------


def test_footprint_has_models(board: Pcb) -> None:
    """D1 (LED) should have exactly 1 model entry."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert len(d1.models_3d) == 1


def test_model_source_path(board: Pcb) -> None:
    """Model source should preserve the raw KiCad path with env vars."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    model = d1.models_3d[0]
    assert "${KICAD6_3DMODEL_DIR}" in model.source
    assert "LED_0603_1608Metric.wrl" in model.source


def test_model_rotation(board: Pcb) -> None:
    """U5 has (rotate (xyz 0 0 -90))."""
    u5 = board.footprint_by_ref("U5")
    assert u5 is not None
    assert len(u5.models_3d) == 1
    assert u5.models_3d[0].rotation == (0.0, 0.0, -90.0)


def test_model_scale_default(board: Pcb) -> None:
    """Most models have (scale (xyz 1 1 1))."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.models_3d[0].scale == (1.0, 1.0, 1.0)


def test_model_offset_default(board: Pcb) -> None:
    """Most models have (offset (xyz 0 0 0))."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.models_3d[0].offset == (0.0, 0.0, 0.0)


def test_footprint_without_model(board: Pcb) -> None:
    """Test points have no (model ...) entry."""
    tp5 = board.footprint_by_ref("TP5")
    assert tp5 is not None
    assert tp5.models_3d == []


def test_multiple_models_per_footprint(orangecrab_board: Pcb) -> None:
    """OrangeCrab FPGA footprint (U3) has 2 model entries."""
    u3 = orangecrab_board.footprint_by_ref("U3")
    assert u3 is not None
    assert len(u3.models_3d) == 2


def test_kicad5_at_vs_kicad6_offset(orangecrab_board: Pcb) -> None:
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


def test_all_pads_have_footprint_ref(board: Pcb) -> None:
    """Every pad should have footprint_ref matching its parent footprint."""
    for fp in board.footprints:
        for pad in fp.pads:
            assert pad.footprint_ref == fp.reference, (
                f"Pad {pad.number} on {fp.reference} has footprint_ref={pad.footprint_ref!r}"
            )


def test_silkscreen_lines_have_footprint_ref(board: Pcb) -> None:
    """Silkscreen lines from footprints carry the parent's ref."""
    for fp in board.footprints:
        for line in fp.silkscreen_lines:
            assert line.footprint_ref == fp.reference


def test_fab_lines_have_footprint_ref(board: Pcb) -> None:
    """Fab lines from footprints carry the parent's ref."""
    for fp in board.footprints:
        for line in fp.fab_lines:
            assert line.footprint_ref == fp.reference


# ---------------------------------------------------------------------------
# footprint_lib and value (real fixture)
# ---------------------------------------------------------------------------


def test_d1_footprint_lib_is_led(board: Pcb) -> None:
    """D1 is an LED — its footprint_lib should contain 'LED'."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert "LED" in d1.footprint_lib


def test_d1_has_value(board: Pcb) -> None:
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.value != ""


def test_multiple_footprints_have_lib(board: Pcb) -> None:
    """Most footprints should have a non-empty footprint_lib."""
    with_lib = [fp for fp in board.footprints if fp.footprint_lib]
    assert len(with_lib) >= 5


# ---------------------------------------------------------------------------
# Graphic text
# ---------------------------------------------------------------------------


def test_graphic_text_count(board: Pcb) -> None:
    assert len(board.graphic_texts) == 8


def test_graphic_text_content(board: Pcb) -> None:
    texts = {gt.text for gt in board.graphic_texts}
    assert "SWD Switch 2.1" in texts
    assert "DEBUGOTRON" in texts


def test_graphic_text_layer(board: Pcb) -> None:
    for gt in board.graphic_texts:
        assert gt.layer != ""


# ---------------------------------------------------------------------------
# Pad enrichment
# ---------------------------------------------------------------------------


def test_roundrect_rratio(board: Pcb) -> None:
    """swd_switch has 73 roundrect pads with rratio=0.25."""
    rr = [p for fp in board.footprints for p in fp.pads if p.roundrect_rratio > 0]
    assert len(rr) == 73
    assert rr[0].roundrect_rratio == pytest.approx(0.25)


def test_pad_pin_function(board: Pcb) -> None:
    """D1 has pads with pin_function 'K' and 'A'."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    functions = {p.pin_function for p in d1.pads}
    assert "K" in functions
    assert "A" in functions


def test_pad_pin_type(board: Pcb) -> None:
    """Pads have pin_type populated."""
    pads_with_type = [p for fp in board.footprints for p in fp.pads if p.pin_type]
    assert len(pads_with_type) > 0
    assert pads_with_type[0].pin_type == "passive"


# ---------------------------------------------------------------------------
# Footprint custom properties
# ---------------------------------------------------------------------------


def test_footprint_properties_mpn(board: Pcb) -> None:
    """U5 has DKPN and MPN properties."""
    u5 = board.footprint_by_ref("U5")
    assert u5 is not None
    assert u5.properties["MPN"] == "SN74LVC2G66DCUR"
    assert u5.properties["DKPN"] == "296-13272-1-ND"


def test_footprint_properties_exclude_builtins(board: Pcb) -> None:
    """Built-in properties (Reference, Value, etc.) are excluded."""
    for fp in board.footprints:
        assert "Reference" not in fp.properties
        assert "Value" not in fp.properties
        assert "Footprint" not in fp.properties


# ---------------------------------------------------------------------------
# Zone boundaries
# ---------------------------------------------------------------------------


def test_zone_count(board: Pcb) -> None:
    assert len(board.zones) == 2


def test_zone_has_boundary(board: Pcb) -> None:
    for zone in board.zones:
        assert len(zone.boundary) >= 3


def test_zone_net_name(board: Pcb) -> None:
    net_names = {z.net_name for z in board.zones}
    assert "GND" in net_names


# ---------------------------------------------------------------------------
# Stackup (swd_switch — 4-layer board)
# ---------------------------------------------------------------------------


def test_stackup_swd_switch(board: Pcb) -> None:
    """swd_switch has a 4-layer stackup with ENIG finish."""
    import sexpdata as _sexpdata

    from phosphor_eda.kicad.pcb_parser import parse_kicad_stackup

    text = FIXTURE.read_text(encoding="utf-8")
    data = _sexpdata.loads(text)
    sexpr = list(data[1:])
    stackup = parse_kicad_stackup(sexpr)
    assert stackup is not None
    assert stackup.copper_finish == "ENIG"
    copper_layers = [ly for ly in stackup.layers if ly.layer_type == "copper"]
    assert len(copper_layers) == 4


# ---------------------------------------------------------------------------
# Stackup (jetson-orin — 8-layer board)
# ---------------------------------------------------------------------------

JETSON_ORIN_FIXTURE = (
    Path(__file__).parent / "fixtures" / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pcb"
)


@pytest.fixture(scope="module")
def jetson_orin_stackup() -> Stackup:
    """Parse just the stackup from the large jetson-orin board."""
    import re

    import sexpdata as _sexpdata

    text = JETSON_ORIN_FIXTURE.read_text(encoding="utf-8")
    # Extract just the (setup ...) section for performance
    m = re.search(r"\(setup", text)
    assert m is not None
    start = m.start()
    depth = 0
    end = start
    for i in range(start, min(start + 50000, len(text))):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    setup_expr = _sexpdata.loads(text[start:end])
    result = parse_kicad_stackup([setup_expr])
    assert result is not None, "parse_kicad_stackup returned None"
    return result


@pytest.mark.skipif(not JETSON_ORIN_FIXTURE.exists(), reason="Jetson Orin fixture not available")
def test_jetson_orin_stackup_layers(jetson_orin_stackup: Stackup) -> None:
    copper_layers = [ly for ly in jetson_orin_stackup.layers if ly.layer_type == "copper"]
    assert len(copper_layers) == 8


@pytest.mark.skipif(not JETSON_ORIN_FIXTURE.exists(), reason="Jetson Orin fixture not available")
def test_jetson_orin_stackup_finish(jetson_orin_stackup: Stackup) -> None:
    assert jetson_orin_stackup.copper_finish == "ENIG"


@pytest.mark.skipif(not JETSON_ORIN_FIXTURE.exists(), reason="Jetson Orin fixture not available")
def test_jetson_orin_stackup_prepreg(jetson_orin_stackup: Stackup) -> None:
    """Prepreg layers have epsilon_r and material."""
    prepreg = [ly for ly in jetson_orin_stackup.layers if ly.layer_type == "prepreg"]
    assert len(prepreg) >= 4
    assert prepreg[0].epsilon_r > 0
    assert prepreg[0].material != ""


@pytest.mark.skipif(not JETSON_ORIN_FIXTURE.exists(), reason="Jetson Orin fixture not available")
def test_jetson_orin_stackup_core(jetson_orin_stackup: Stackup) -> None:
    """Core layers have epsilon_r."""
    core = [ly for ly in jetson_orin_stackup.layers if ly.layer_type == "core"]
    assert len(core) >= 2
    assert core[0].epsilon_r > 0
