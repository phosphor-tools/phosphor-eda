"""Tests for the KiCad .kicad_pcb parser."""

from pathlib import Path

import pytest
import sexpdata

from phosphor_eda.domain.pcb import (
    LayerRole,
    Pcb,
    PcbArtworkPurpose,
    PcbConductorKind,
    PcbDrillShape,
    PcbPadType,
    PcbPolygon,
    PcbText,
)
from phosphor_eda.domain.project import Stackup
from phosphor_eda.formats.kicad.board import parse_kicad_pcb, parse_kicad_pcb_from_sexpr
from phosphor_eda.formats.kicad.footprint import extract_value
from phosphor_eda.formats.kicad.sexp import SExpNode
from phosphor_eda.formats.kicad.stackup import parse_kicad_stackup
from phosphor_eda.geometry.pcb_geometry import pad_polygon
from phosphor_eda.render.drills import drill_geometry
from phosphor_eda.render.inventory import InventoryItemKind, build_inventory

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


def test_kicad_pad_slot_drills_use_board_coordinate_orientation() -> None:
    parsed = sexpdata.loads(
        """
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (31 "B.Cu" signal)
            (29 "F.Mask" user)
            (30 "B.Mask" user)
            (44 "Edge.Cuts" user)
          )
          (footprint "Test:Part"
            (layer "F.Cu")
            (at 10 10)
            (property "Reference" "J1")
            (pad "1" thru_hole oval
              (at 0 0 45)
              (size 1.0 3.0)
              (drill oval 0.6 2.2)
              (layers "*.Cu" "*.Mask")
            )
          )
          (gr_line (start 0 0) (end 1 0) (layer "Edge.Cuts") (width 0.1))
        )
        """
    )
    board = parse_kicad_pcb_from_sexpr(list(parsed[1:]), default_name="rotated-slot")
    pad = board.pads[0]
    assert pad.drill is not None

    drill_cutout = drill_geometry(pad.drill)

    assert pad.rotation == pytest.approx(45.0)
    assert pad.drill.rotation == pytest.approx(pad.rotation)
    assert drill_cutout is not None
    assert pad_polygon(pad).covers(drill_cutout)


def _board_with_via(tenting_clause: str) -> Pcb:
    parsed = sexpdata.loads(
        f"""
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (31 "B.Cu" signal)
            (44 "Edge.Cuts" user)
          )
          (via (at 5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") {tenting_clause})
          (gr_line (start 0 0) (end 1 0) (layer "Edge.Cuts") (width 0.1))
        )
        """
    )
    return parse_kicad_pcb_from_sexpr(list(parsed[1:]), default_name="tenting")


def test_kicad_via_tenting_front_and_back() -> None:
    via = _board_with_via("(tenting front back)").vias[0]
    assert via.tented_front is True
    assert via.tented_back is True


def test_kicad_via_tenting_front_only() -> None:
    via = _board_with_via("(tenting front)").vias[0]
    assert via.tented_front is True
    assert via.tented_back is False


def test_kicad_via_tenting_none() -> None:
    via = _board_with_via("(tenting none)").vias[0]
    assert via.tented_front is False
    assert via.tented_back is False


def test_kicad_via_without_tenting_defaults_false() -> None:
    via = _board_with_via("").vias[0]
    assert via.tented_front is False
    assert via.tented_back is False


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
    assert extract_value(fp) == "100nF"


def test_extract_value_kicad6() -> None:
    fp = _make_fp_sexpr('(fp_text value "100nF" (at 0 0))')
    assert extract_value(fp) == "100nF"


def test_extract_value_missing() -> None:
    fp = _make_fp_sexpr('(property "Reference" "U1" (at 0 0))')
    assert extract_value(fp) == ""


def _parse_pcb_snippet(body: str, name: str = "test") -> Pcb:
    parsed = sexpdata.loads(f"(kicad_pcb {body})")
    return parse_kicad_pcb_from_sexpr(list(parsed[1:]), default_name=name)


def _keepout_with_layer_selector(selector: str) -> Pcb:
    return _parse_pcb_snippet(
        f"""
        (layers
          (0 "F.Cu" signal)
          (31 "B.Cu" signal)
          (34 "B.Paste" user)
          (35 "F.Paste" user)
          (36 "B.SilkS" user)
          (37 "F.SilkS" user)
          (38 "B.Mask" user)
          (39 "F.Mask" user)
          (40 "Dwgs.User" user)
          (44 "Edge.Cuts" user)
          (46 "B.CrtYd" user)
          (47 "F.CrtYd" user)
          (48 "B.Fab" user)
          (49 "F.Fab" user)
          (54 "F.Adhes" user)
          (55 "B.Adhes" user)
        )
        (gr_line (start 0 0) (end 10 0) (layer "Edge.Cuts") (width 0.1))
        (gr_line (start 10 0) (end 10 10) (layer "Edge.Cuts") (width 0.1))
        (gr_line (start 10 10) (end 0 10) (layer "Edge.Cuts") (width 0.1))
        (gr_line (start 0 10) (end 0 0) (layer "Edge.Cuts") (width 0.1))
        (zone
          (layers "{selector}")
          (keepout (tracks not_allowed))
          (polygon (pts (xy 1 1) (xy 2 1) (xy 2 2) (xy 1 2)))
        )
        """
    )


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        # Courtyard layers also carry the FABRICATION role, so *.Fab includes them.
        ("*.Fab", {"F.Fab", "B.Fab", "F.CrtYd", "B.CrtYd"}),
        ("*.Adhes", {"F.Adhes", "B.Adhes"}),
        ("*.CrtYd", {"F.CrtYd", "B.CrtYd"}),
    ],
)
def test_wildcard_selector_expands_aux_layers(selector: str, expected: set[str]) -> None:
    """``*.Fab``/``*.Adhes``/``*.CrtYd`` expand to all matching layers, not a single literal."""
    board = _keepout_with_layer_selector(selector)
    assert len(board.keepouts) == 1
    names = {layer.name for layer in board.keepouts[0].layers}
    assert names == expected


def test_kicad_trace_arc_missing_mid_raises() -> None:
    """A trace arc without a mid point is malformed and must raise, not be dropped."""
    with pytest.raises(ValueError, match="Trace arc missing required"):
        _parse_pcb_snippet(
            """
            (layers (0 "F.Cu" signal))
            (arc (start 0 0) (end 1 1) (width 0.1) (layer "F.Cu"))
            """
        )


def test_kicad_malformed_layer_def_raises() -> None:
    """A layer row with too few fields is malformed and must raise."""
    with pytest.raises(ValueError, match="layer definition is malformed"):
        _parse_pcb_snippet('(layers (0 "F.Cu"))')


def test_kicad_layer_number_is_none_for_non_integer() -> None:
    """A non-integer layer id yields number=None, not 0 (which collides with F.Cu)."""
    board = _parse_pcb_snippet(
        """
        (layers (0 "F.Cu" signal) (bogus "User.1" user) (44 "Edge.Cuts" user))
        (gr_line (start 0 0) (end 1 0) (layer "Edge.Cuts") (width 0.1))
        """
    )
    user_layer = board.layer_for("User.1")
    assert user_layer is not None
    assert user_layer.number is None


def test_kicad_gr_text_missing_layer_raises() -> None:
    """Board text without a layer is malformed and must raise."""
    with pytest.raises(ValueError, match="gr_text missing required layer"):
        _parse_pcb_snippet(
            """
            (layers (0 "F.Cu" signal))
            (gr_text "hi" (at 0 0))
            """
        )


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


def _make_fp_sexpr(body: str) -> SExpNode:
    parsed = sexpdata.loads(f'(footprint "test:Pkg" {body})')
    return list(parsed)
