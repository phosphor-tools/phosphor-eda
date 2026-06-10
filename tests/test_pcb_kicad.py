"""Tests for the KiCad .kicad_pcb parser."""

from pathlib import Path

import pytest
import sexpdata

from phosphor_eda.kicad.pcb_parser import (
    _extract_value,  # pyright: ignore[reportPrivateUsage]
    parse_kicad_pcb,
    parse_kicad_pcb_from_sexpr,
    parse_kicad_stackup,
)
from phosphor_eda.pcb import (
    LayerRole,
    Pcb,
    PcbArtworkPurpose,
    PcbConductorKind,
    PcbDrillShape,
    PcbPadType,
    PcbPolygon,
    PcbText,
)
from phosphor_eda.pcb_render_inventory import InventoryItemKind, build_inventory
from phosphor_eda.project import Stackup
from phosphor_eda.sql.geometry import pad_polygon

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"
ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"
JETSON_ORIN_FIXTURE = (
    Path(__file__).parent / "fixtures" / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pcb"
)


@pytest.fixture(scope="module")
def board() -> Pcb:
    return parse_kicad_pcb(FIXTURE)


def test_kicad_parser_emits_typed_domain_collections(board: Pcb) -> None:
    assert not hasattr(board, "geometry")
    assert len(board.layers) == 31
    assert len(board.nets) == 27
    assert 0 not in board.nets
    assert len(board.footprints) == 28
    assert len(board.pads) == 120
    assert len(board.vias) == 49
    assert len(board.drills) == 71
    assert len(board.conductors) >= 280
    assert len(board.artwork) >= 300
    assert len(board.pours) == 2
    assert board.board_profile is not None
    assert len(board.board_profile.elements) > 0


def test_layer_definitions_are_normalized(board: Pcb) -> None:
    fcu = board.layer_for("F.Cu")
    assert fcu is not None
    assert set(fcu.roles) >= {
        LayerRole.COPPER,
        LayerRole.FRONT,
        LayerRole.OUTER,
        LayerRole.SIGNAL,
    }
    assert fcu.side == "front"
    assert fcu.number == 0

    edge = board.layer_for("Edge.Cuts")
    assert edge is not None
    assert edge.has_role(LayerRole.EDGE)


def test_footprints_reference_concrete_layers(board: Pcb) -> None:
    tp3 = board.footprint_by_ref("TP3")
    assert tp3 is not None
    assert tp3.layer.name == "B.Cu"

    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert d1.layer.name == "F.Cu"


def test_pads_resolve_nets_layers_and_drills(board: Pcb) -> None:
    tp3_pads = board.pads_for_footprint("TP3")
    assert len(tp3_pads) == 1
    pad = tp3_pads[0]

    assert pad.pad_type == PcbPadType.SMD
    assert pad.net is not None
    assert pad.net.name == "/SWD_EN_EXT"
    assert {layer.name for layer in pad.layers} == {"B.Cu", "B.Mask"}
    assert all("*" not in layer.name for item in board.pads for layer in item.layers)

    drilled_pad = next(item for item in board.pads if item.drill is not None)
    assert drilled_pad.pad_type == PcbPadType.THROUGH_HOLE
    assert drilled_pad.drill is not None
    assert drilled_pad.drill in board.drills
    assert drilled_pad.drill.owner is drilled_pad


def test_kicad_custom_pad_primitives_are_modeled() -> None:
    board = parse_kicad_pcb(ORANGECRAB_FIXTURE)
    custom_pads = [pad for pad in board.pads if pad.shape == "custom"]

    assert custom_pads
    assert any(pad.custom_shapes for pad in custom_pads)
    custom_pad = next(pad for pad in custom_pads if pad.custom_shapes)
    geometry = pad_polygon(custom_pad)
    min_x, min_y, max_x, max_y = geometry.bounds
    bbox_area = (max_x - min_x) * (max_y - min_y)

    assert geometry.area < bbox_area


def test_kicad_pad_rotation_uses_board_coordinate_orientation() -> None:
    parsed = sexpdata.loads(
        """
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (29 "F.Mask" user)
            (44 "Edge.Cuts" user)
          )
          (footprint "Test:Part"
            (layer "F.Cu")
            (at 10 10 90)
            (property "Reference" "U1")
            (pad "1" smd rect (at -1 0 90) (size 0.5 2.0) (layers "F.Cu" "F.Mask"))
          )
          (gr_line (start 0 0) (end 1 0) (layer "Edge.Cuts") (width 0.1))
        )
        """
    )
    board = parse_kicad_pcb_from_sexpr(list(parsed[1:]), default_name="rotated-pad")
    pad = board.pads[0]

    min_x, min_y, max_x, max_y = pad_polygon(pad).bounds

    assert pad.x == pytest.approx(10.0)
    assert pad.y == pytest.approx(11.0)
    assert pad.rotation == pytest.approx(90.0)
    assert max_x - min_x == pytest.approx(2.0)
    assert max_y - min_y == pytest.approx(0.5)


def test_kicad_layer_selectors_resolve_to_concrete_layer_references(board: Pcb) -> None:
    through_hole = next(
        pad for pad in board.pads if pad.drill is not None and pad.footprint is not None
    )

    assert "*.Cu" not in {layer.name for layer in through_hole.layers}
    assert {layer.name for layer in through_hole.layers if layer.has_role(LayerRole.COPPER)} == {
        "F.Cu",
        "In1.Cu",
        "In2.Cu",
        "B.Cu",
    }


def test_vias_have_first_class_drills_and_nullable_nets(board: Pcb) -> None:
    via = board.vias[0]

    assert via.drill in board.drills
    assert via.drill.owner is via
    assert via.net is None or via.net.number in board.nets
    assert {layer.name for layer in via.layers} >= {"F.Cu", "B.Cu"}


def test_drill_slots_are_modeled(board: Pcb) -> None:
    slots = [drill for drill in board.drills if drill.shape == PcbDrillShape.SLOT]

    assert slots
    assert all(drill.width > 0 for drill in slots)
    assert all(drill.height > 0 for drill in slots)


def test_segments_trace_arcs_and_pour_fills_are_conductors(board: Pcb) -> None:
    traces = [item for item in board.conductors if item.kind == PcbConductorKind.TRACE]
    fills = [item for item in board.conductors if item.kind == PcbConductorKind.POUR_FILL]

    assert len(traces) >= 270
    assert len(fills) > 0
    assert all(item.layer in board.layers for item in board.conductors)
    assert all(item.net is None or item.net.number in board.nets for item in board.conductors)
    assert all(fill.pour in board.pours for fill in fills)
    assert all(isinstance(fill.data, PcbPolygon) for fill in fills)


def test_kicad_zones_produce_pours_with_fill_conductors(board: Pcb) -> None:
    gnd = board.nets[2]
    gnd_pours = board.pours_for_net(gnd)

    assert gnd_pours
    assert gnd_pours[0].fills
    assert board.conductors_for_pour(gnd_pours[0]) == list(gnd_pours[0].fills)


def test_artwork_tracks_footprint_ownership_and_purpose(board: Pcb) -> None:
    footprint_artwork = [item for item in board.artwork if item.footprint is not None]
    board_artwork = [item for item in board.artwork if item.footprint is None]
    designators = [item for item in board.artwork if item.purpose == PcbArtworkPurpose.DESIGNATOR]

    assert footprint_artwork
    assert board_artwork
    assert designators
    assert all(item.footprint in board.footprints for item in footprint_artwork)
    assert any(isinstance(item.data, PcbText) for item in designators)


def test_hidden_footprint_text_is_not_render_inventory() -> None:
    parsed = sexpdata.loads(
        """
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (37 "F.SilkS" user)
            (44 "Edge.Cuts" user)
          )
          (footprint "Test:Part"
            (layer "F.Cu")
            (at 10 10)
            (fp_text reference "U1" (at 0 0) (layer "F.SilkS") (hide yes))
            (fp_text value "MCU" (at 0 1) (layer "F.SilkS"))
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
            (model "part.step" (hide yes))
          )
          (gr_line (start 0 0) (end 1 0) (layer "Edge.Cuts") (width 0.1))
        )
        """
    )
    board = parse_kicad_pcb_from_sexpr(list(parsed[1:]), default_name="hidden-text")

    hidden = next(item for item in board.artwork if item.id == "fp_text:U1:0:reference")
    visible = next(item for item in board.artwork if item.id == "fp_text:U1:1:value")
    hidden_model = next(item for item in board.artwork if item.id == "model_3d:U1:0")
    inventory = build_inventory(board, side="front")
    inventory_ids = {
        item.id for item in inventory.items if item.item_kind == InventoryItemKind.ARTWORK
    }

    assert hidden.metadata.hidden
    assert not visible.metadata.hidden
    assert hidden_model.metadata.hidden
    assert hidden.id not in inventory_ids
    assert hidden_model.id not in inventory_ids
    assert visible.id in inventory_ids


def test_board_profile_comes_from_edge_cuts(board: Pcb) -> None:
    assert board.board_profile is not None
    edge = board.layer_for("Edge.Cuts")
    assert edge is not None

    assert board.bbox() == (91.0, 55.0, 121.0, 75.0)
    assert all(element.layer is edge for element in board.board_profile.elements)


def test_board_name(board: Pcb) -> None:
    assert board.name == "Debugotron SWD Switch"


def test_extract_value_kicad8() -> None:
    fp = _make_fp_sexpr('(property "Value" "100nF" (at 0 0))')
    assert _extract_value(fp) == "100nF"


def test_extract_value_kicad6() -> None:
    fp = _make_fp_sexpr('(fp_text value "100nF" (at 0 0))')
    assert _extract_value(fp) == "100nF"


def test_extract_value_missing() -> None:
    fp = _make_fp_sexpr('(property "Reference" "U1" (at 0 0))')
    assert _extract_value(fp) == ""


def test_parse_kicad_pcb_from_sexpr_rejects_unresolved_layers() -> None:
    parsed = sexpdata.loads(
        """
        (kicad_pcb
          (layers (0 "F.Cu" signal))
          (footprint "Test:Part"
            (layer "F.Cu")
            (at 0 0)
            (property "Reference" "U1")
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "Missing.Layer"))
          )
          (gr_line (start 0 0) (end 1 0) (layer "F.Cu") (width 0.1))
        )
        """
    )

    with pytest.raises(ValueError, match="unknown layer"):
        parse_kicad_pcb_from_sexpr(list(parsed[1:]), default_name="bad")


def test_stackup_swd_switch() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    data = sexpdata.loads(text)
    stackup = parse_kicad_stackup(list(data[1:]))

    assert stackup is not None
    assert stackup.copper_finish == "ENIG"
    copper_layers = [layer for layer in stackup.layers if layer.layer_type == "copper"]
    assert len(copper_layers) == 4


@pytest.fixture(scope="module")
def jetson_orin_stackup() -> Stackup:
    text = JETSON_ORIN_FIXTURE.read_text(encoding="utf-8")
    start = text.index("(setup")
    depth = 0
    end = start
    for index in range(start, min(start + 50_000, len(text))):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    setup_expr = sexpdata.loads(text[start:end])
    result = parse_kicad_stackup([setup_expr])
    assert result is not None
    return result


@pytest.mark.skipif(not JETSON_ORIN_FIXTURE.exists(), reason="Jetson Orin fixture not available")
def test_jetson_orin_stackup_layers(jetson_orin_stackup: Stackup) -> None:
    copper_layers = [layer for layer in jetson_orin_stackup.layers if layer.layer_type == "copper"]
    assert len(copper_layers) == 8


@pytest.mark.skipif(not JETSON_ORIN_FIXTURE.exists(), reason="Jetson Orin fixture not available")
def test_jetson_orin_stackup_finish(jetson_orin_stackup: Stackup) -> None:
    assert jetson_orin_stackup.copper_finish == "ENIG"


@pytest.mark.skipif(not JETSON_ORIN_FIXTURE.exists(), reason="Jetson Orin fixture not available")
def test_jetson_orin_stackup_prepreg(jetson_orin_stackup: Stackup) -> None:
    prepreg = [layer for layer in jetson_orin_stackup.layers if layer.layer_type == "prepreg"]
    assert len(prepreg) >= 4
    assert any(layer.material for layer in prepreg)
    assert any(layer.epsilon_r > 0 for layer in prepreg)


def _make_fp_sexpr(body: str) -> list[object]:
    parsed = sexpdata.loads(f'(footprint "test:Pkg" {body})')
    return list(parsed)
