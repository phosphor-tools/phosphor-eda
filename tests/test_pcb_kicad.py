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
    PcbArcGeometry,
    PcbGeometry,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbKeepoutPermission,
    PcbLineGeometry,
    PcbModel3DGeometry,
    PcbPadGeometry,
    PcbPolygonGeometry,
    PcbTextGeometry,
)
from phosphor_eda.project import Stackup

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"


@pytest.fixture(scope="module")
def board() -> Pcb:
    return parse_kicad_pcb(FIXTURE)


def _geometry(
    board: Pcb,
    *,
    object_type: PcbGeometryObject | None = None,
    shape: PcbGeometryShape | None = None,
    role: PcbGeometryRole | None = None,
    layer: str | None = None,
    footprint_ref: str | None = None,
    source_collection: str | None = None,
) -> list[PcbGeometry]:
    result = board.geometry
    if object_type is not None:
        result = [item for item in result if item.object_type == object_type]
    if shape is not None:
        result = [item for item in result if item.shape == shape]
    if role is not None:
        result = [item for item in result if item.has_role(role)]
    if layer is not None:
        result = [item for item in result if layer in item.layers]
    if footprint_ref is not None:
        result = [item for item in result if item.footprint_ref == footprint_ref]
    if source_collection is not None:
        result = [item for item in result if item.metadata.source_collection == source_collection]
    return result


def _pads(board: Pcb, ref: str | None = None) -> list[PcbGeometry]:
    return _geometry(board, object_type=PcbGeometryObject.PAD, footprint_ref=ref)


def _pad_data(item: PcbGeometry) -> PcbPadGeometry:
    assert isinstance(item.data, PcbPadGeometry)
    return item.data


def _line_data(item: PcbGeometry) -> PcbLineGeometry:
    assert isinstance(item.data, PcbLineGeometry)
    return item.data


def _arc_data(item: PcbGeometry) -> PcbArcGeometry:
    assert isinstance(item.data, PcbArcGeometry)
    return item.data


def _polygon_data(item: PcbGeometry) -> PcbPolygonGeometry:
    assert isinstance(item.data, PcbPolygonGeometry)
    return item.data


def _text_data(item: PcbGeometry) -> PcbTextGeometry:
    assert isinstance(item.data, PcbTextGeometry)
    return item.data


def _model_data(item: PcbGeometry) -> PcbModel3DGeometry:
    assert isinstance(item.data, PcbModel3DGeometry)
    return item.data


# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------


def test_layer_definitions_populated(board: Pcb) -> None:
    """Parser should populate board.layers from the (layers ...) section."""
    assert len(board.layers) > 0


def test_copper_layer_function(board: Pcb) -> None:
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


def test_back_copper_layer(board: Pcb) -> None:
    bcu = board.layer_for("B.Cu")
    assert bcu is not None
    assert set(bcu.roles) >= {
        LayerRole.COPPER,
        LayerRole.BACK,
        LayerRole.OUTER,
        LayerRole.SIGNAL,
    }
    assert bcu.side == "back"


def test_inner_copper_layer(board: Pcb) -> None:
    in1 = board.layer_for("In1.Cu")
    assert in1 is not None
    assert set(in1.roles) >= {LayerRole.COPPER, LayerRole.INNER, LayerRole.SIGNAL}
    assert in1.side == "inner"


def test_silk_layer_function(board: Pcb) -> None:
    layers = board.layers_by_role(LayerRole.SILKSCREEN)
    assert len(layers) >= 2
    names = {lyr.name for lyr in layers}
    assert "F.SilkS" in names


def test_fab_layer_function(board: Pcb) -> None:
    layers = board.layers_by_role(LayerRole.FABRICATION)
    names = {lyr.name for lyr in layers}
    assert "F.Fab" in names
    assert "B.Fab" in names


def test_edge_layer(board: Pcb) -> None:
    layers = board.layers_by_role(LayerRole.EDGE)
    assert len(layers) == 1
    assert layers[0].name == "Edge.Cuts"


def test_layers_by_role_filters(board: Pcb) -> None:
    copper = board.layers_by_role(LayerRole.COPPER)
    assert all(lyr.has_role(LayerRole.COPPER) for lyr in copper)
    assert len(copper) >= 2  # At least F.Cu and B.Cu


def test_fabrication_and_user_layer_roles_are_normalized(board: Pcb) -> None:
    fcrtyd = board.layer_for("F.CrtYd")
    assert fcrtyd is not None
    assert set(fcrtyd.roles) >= {
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.FRONT,
    }
    assert fcrtyd.metadata.native_type == "user"
    assert fcrtyd.metadata.native_user_name == "F.Courtyard"

    ffab = board.layer_for("F.Fab")
    assert ffab is not None
    assert set(ffab.roles) >= {LayerRole.FABRICATION, LayerRole.FRONT}

    drawings = board.layer_for("Dwgs.User")
    assert drawings is not None
    assert set(drawings.roles) >= {LayerRole.DRAWING}


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
    assert len(_pads(board, "D1")) == 2
    assert len(_pads(board, "TP3")) == 1


def test_pad_net(board: Pcb) -> None:
    assert _pads(board, "TP3")[0].net_name == "/SWD_EN_EXT"


def test_pad_absolute_coords(board: Pcb) -> None:
    """Pad coords should be in absolute board space, not footprint-local."""
    pad = _pad_data(_pads(board, "TP3")[0])
    # TP3 at (93.5, 64.5), pad at local (0, 0) -> absolute (93.5, 64.5)
    assert pad.x == pytest.approx(93.5, abs=0.1)
    assert pad.y == pytest.approx(64.5, abs=0.1)


def test_segment_count(board: Pcb) -> None:
    segments = _geometry(
        board,
        object_type=PcbGeometryObject.TRACK,
        shape=PcbGeometryShape.LINE,
        source_collection="segments",
    )
    assert len(segments) == 276
    assert all(segment.has_role(PcbGeometryRole.TRACE) for segment in segments)


def test_via_count(board: Pcb) -> None:
    vias = _geometry(board, object_type=PcbGeometryObject.VIA)
    assert len(vias) == 49
    assert all(via.has_role(PcbGeometryRole.DRILL) for via in vias)


def test_board_outline(board: Pcb) -> None:
    outline_lines = [
        item for item in board.board_profile_geometry() if item.shape == PcbGeometryShape.LINE
    ]
    outline_arcs = [
        item for item in board.board_profile_geometry() if item.shape == PcbGeometryShape.ARC
    ]
    assert len(outline_lines) > 0
    assert len(outline_arcs) > 0
    # Outline includes both gr_line and fp_line on Edge.Cuts
    assert len(outline_lines) == 10
    assert len(outline_arcs) == 6  # 4 board corners + 2 USB notch corners


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
    assert (
        len(_geometry(board, object_type=PcbGeometryObject.REGION, role=PcbGeometryRole.POUR_FILL))
        > 0
    )


def test_polygon_layers(board: Pcb) -> None:
    """Zone polygons should be on inner copper layers."""
    layers = {item.primary_layer for item in _geometry(board, role=PcbGeometryRole.POUR_FILL)}
    assert "In1.Cu" in layers
    assert "In2.Cu" in layers


def test_polygon_net(board: Pcb) -> None:
    """Zone polygons should carry net info from their parent zone."""
    nets = {
        (item.net_number, item.net_name)
        for item in _geometry(board, role=PcbGeometryRole.POUR_FILL)
    }
    assert (2, "GND") in nets
    assert (1, "VCC") in nets


def test_polygon_has_points(board: Pcb) -> None:
    """Every polygon should have a non-empty points list."""
    for polygon in _geometry(board, role=PcbGeometryRole.POUR_FILL):
        assert len(_polygon_data(polygon).points) >= 3


def test_polygon_total_points(board: Pcb) -> None:
    """Sanity check: total filled_polygon points should be ~5726."""
    total = sum(
        len(_polygon_data(polygon).points)
        for polygon in _geometry(board, role=PcbGeometryRole.POUR_FILL)
    )
    assert 5000 < total < 7000


def test_footprint_mask_polygon_is_preserved_as_board_polygon() -> None:
    data = sexpdata.loads(
        """
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (31 "B.Cu" signal)
            (36 "B.Mask" user)
            (37 "F.Mask" user)
          )
          (footprint "Antenna"
            (layer "F.Cu")
            (at 10 20 90)
            (property "Reference" "AE1")
            (property "Value" "chip")
            (fp_poly
              (pts
                (xy 0 0)
                (xy 2 0)
                (xy 2 1)
                (xy 0 1)
              )
              (layer "F.Mask")
              (width 0)
              (fill solid)
            )
          )
        )
        """
    )

    board = parse_kicad_pcb_from_sexpr(list(data[1:]), default_name="mask-poly")

    mask_polygons = _geometry(
        board,
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.POLYGON,
        layer="F.Mask",
        footprint_ref="AE1",
    )
    assert len(mask_polygons) == 1
    assert _polygon_data(mask_polygons[0]).points == [
        (10.0, 20.0),
        (10.0, 18.0),
        (11.0, 18.0),
        (11.0, 20.0),
    ]


def test_top_level_mask_lines_and_arcs_are_preserved_as_board_graphics() -> None:
    data = sexpdata.loads(
        """
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (31 "B.Cu" signal)
            (36 "B.Mask" user)
            (37 "F.Mask" user)
          )
          (gr_line
            (start 1 2)
            (end 3 2)
            (stroke (width 0.2) (type solid))
            (layer "F.Mask")
          )
          (gr_arc
            (start 1 4)
            (mid 2 5)
            (end 3 4)
            (stroke (width 0.15) (type solid))
            (layer "B.Mask")
          )
        )
        """
    )

    board = parse_kicad_pcb_from_sexpr(list(data[1:]), default_name="mask-graphics")

    lines = _geometry(
        board,
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.LINE,
        source_collection="graphic_lines",
    )
    arcs = _geometry(
        board,
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.ARC,
        source_collection="graphic_arcs",
    )
    assert [
        (
            line.primary_layer,
            _line_data(line).start_x,
            _line_data(line).start_y,
            _line_data(line).end_x,
            _line_data(line).end_y,
            _line_data(line).width,
        )
        for line in lines
    ] == [("F.Mask", 1.0, 2.0, 3.0, 2.0, 0.2)]
    assert [
        (
            arc.primary_layer,
            _arc_data(arc).start_x,
            _arc_data(arc).mid_x,
            _arc_data(arc).end_x,
            _arc_data(arc).width,
        )
        for arc in arcs
    ] == [("B.Mask", 1.0, 2.0, 3.0, 0.15)]


def test_footprint_mask_lines_and_arcs_are_preserved_as_board_graphics() -> None:
    data = sexpdata.loads(
        """
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (31 "B.Cu" signal)
            (36 "B.Mask" user)
            (37 "F.Mask" user)
          )
          (footprint "Antenna"
            (layer "F.Cu")
            (at 10 20 90)
            (property "Reference" "AE1")
            (property "Value" "chip")
            (fp_line
              (start 0 0)
              (end 2 0)
              (stroke (width 0.2) (type solid))
              (layer "F.Mask")
            )
            (fp_arc
              (start 0 0)
              (mid 1 1)
              (end 2 0)
              (stroke (width 0.15) (type solid))
              (layer "B.Mask")
            )
          )
        )
        """
    )

    board = parse_kicad_pcb_from_sexpr(list(data[1:]), default_name="footprint-mask-graphics")

    lines = _geometry(
        board,
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.LINE,
        footprint_ref="AE1",
    )
    arcs = _geometry(
        board,
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.ARC,
        footprint_ref="AE1",
    )
    assert [
        (
            line.primary_layer,
            line.footprint_ref,
            _line_data(line).start_x,
            _line_data(line).start_y,
            _line_data(line).end_x,
            _line_data(line).end_y,
            _line_data(line).width,
        )
        for line in lines
    ] == [("F.Mask", "AE1", 10.0, 20.0, 10.0, 18.0, 0.2)]
    assert [
        (
            arc.primary_layer,
            arc.footprint_ref,
            _arc_data(arc).start_x,
            _arc_data(arc).start_y,
            _arc_data(arc).mid_x,
            _arc_data(arc).mid_y,
            _arc_data(arc).end_x,
            _arc_data(arc).end_y,
            _arc_data(arc).width,
        )
        for arc in arcs
    ] == [("B.Mask", "AE1", 10.0, 20.0, 11.0, 19.0, 10.0, 18.0, 0.15)]


def test_trace_arc_count(board: Pcb) -> None:
    """swd_switch has no trace arcs."""
    assert (
        len(
            _geometry(
                board,
                object_type=PcbGeometryObject.TRACK,
                shape=PcbGeometryShape.ARC,
                source_collection="trace_arcs",
            )
        )
        == 0
    )


def test_kicad_zone_keepout_is_parsed_as_keepout_not_copper_zone() -> None:
    data = sexpdata.loads(
        """
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (31 "B.Cu" signal)
          )
          (zone
            (net 0)
            (net_name "")
            (layers "F.Cu" "B.Cu")
            (keepout
              (tracks allowed)
              (vias not_allowed)
              (pads not_allowed)
              (copperpour not_allowed)
              (footprints allowed)
            )
            (polygon
              (pts
                (xy 1 1)
                (xy 4 1)
                (xy 4 3)
                (xy 1 3)
              )
            )
            (filled_polygon
              (layer "F.Cu")
              (pts
                (xy 1 1)
                (xy 4 1)
                (xy 4 3)
                (xy 1 3)
              )
            )
          )
        )
        """
    )
    board = parse_kicad_pcb_from_sexpr(list(data[1:]), default_name="keepout")

    keepouts = board.keepouts
    assert len(keepouts) == 1
    keepout = keepouts[0]
    assert keepout.layers == ("F.Cu", "B.Cu")
    assert list(keepout.boundary.points) == [
        (1.0, 1.0),
        (4.0, 1.0),
        (4.0, 3.0),
        (1.0, 3.0),
    ]
    assert keepout.rules.tracks == PcbKeepoutPermission.ALLOWED
    assert keepout.rules.vias == PcbKeepoutPermission.NOT_ALLOWED
    assert keepout.rules.pads == PcbKeepoutPermission.NOT_ALLOWED
    assert keepout.rules.copper_pours == PcbKeepoutPermission.NOT_ALLOWED
    assert keepout.rules.footprints == PcbKeepoutPermission.ALLOWED
    assert board.pours == []
    assert _geometry(board, object_type=PcbGeometryObject.REGION) == []


def test_kicad_footprint_keepout_is_parsed_with_footprint_reference() -> None:
    data = sexpdata.loads(
        """
        (kicad_pcb
          (layers
            (0 "F.Cu" signal)
            (31 "B.Cu" signal)
          )
          (footprint "Antenna"
            (layer "F.Cu")
            (at 10 20)
            (property "Reference" "AE1")
            (property "Value" "chip")
            (zone
              (net 0)
              (net_name "")
              (layer "F.Cu")
              (keepout
                (tracks not_allowed)
                (vias allowed)
                (pads allowed)
                (copperpour not_allowed)
                (footprints allowed)
              )
              (polygon
                (pts
                  (xy 0 0)
                  (xy 2 0)
                  (xy 2 1)
                  (xy 0 1)
                )
              )
            )
          )
        )
        """
    )
    board = parse_kicad_pcb_from_sexpr(list(data[1:]), default_name="footprint-keepout")

    keepouts = board.keepouts
    assert len(keepouts) == 1
    keepout = keepouts[0]
    assert keepout.footprint_ref == "AE1"
    assert keepout.layers == ("F.Cu",)
    assert list(keepout.boundary.points) == [
        (10.0, 20.0),
        (12.0, 20.0),
        (12.0, 21.0),
        (10.0, 21.0),
    ]
    assert keepout.rules.tracks == PcbKeepoutPermission.NOT_ALLOWED
    assert keepout.rules.copper_pours == PcbKeepoutPermission.NOT_ALLOWED


# ---------------------------------------------------------------------------
# OrangeCrab fixture (KiCad 5, complex board)
# ---------------------------------------------------------------------------

ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"


@pytest.fixture(scope="module")
def orangecrab_board() -> Pcb:
    return parse_kicad_pcb(ORANGECRAB_FIXTURE)


def test_orangecrab_polygon_count(orangecrab_board: Pcb) -> None:
    """OrangeCrab has 40 zones — should produce many polygons."""
    assert len(_geometry(orangecrab_board, role=PcbGeometryRole.POUR_FILL)) > 40


def test_orangecrab_polygon_layers(orangecrab_board: Pcb) -> None:
    """Zone polygons should span multiple copper layers."""
    layers = {
        item.primary_layer for item in _geometry(orangecrab_board, role=PcbGeometryRole.POUR_FILL)
    }
    assert "F.Cu" in layers
    assert "B.Cu" in layers


def test_orangecrab_keepouts_are_not_loaded_as_copper_zones(orangecrab_board: Pcb) -> None:
    keepouts = orangecrab_board.keepouts
    assert any(
        "F.Cu" in keepout.layers
        and keepout.rules.tracks == PcbKeepoutPermission.ALLOWED
        and keepout.rules.copper_pours == PcbKeepoutPermission.NOT_ALLOWED
        for keepout in keepouts
    )

    keepout_boundaries = {tuple(keepout.boundary.points) for keepout in keepouts}
    pour_boundaries = {tuple(pour.boundary.points) for pour in orangecrab_board.pours}

    assert keepout_boundaries
    assert keepout_boundaries.isdisjoint(pour_boundaries)


def test_orangecrab_pad_layers_are_plain_strings(orangecrab_board: Pcb) -> None:
    """KiCad symbolic layer names should be normalized before entering the domain model."""
    pad_layers = [layer for pad in _pads(orangecrab_board) for layer in pad.layers]
    via_layers = [
        layer
        for via in _geometry(orangecrab_board, object_type=PcbGeometryObject.VIA)
        for layer in via.layers
    ]

    assert pad_layers
    assert all(type(layer) is str for layer in pad_layers)
    assert all(type(layer) is str for layer in via_layers)
    assert "*.Cu" in pad_layers


def test_orangecrab_usb_mounting_pads_preserve_oval_drill_dimensions(
    orangecrab_board: Pcb,
) -> None:
    """USB shield mounting pads use oval drill slots, not circular holes."""
    usb = orangecrab_board.footprint_by_ref("J3")
    assert usb is not None

    shield_slots = [
        _pad_data(pad)
        for pad in _pads(orangecrab_board, "J3")
        if _pad_data(pad).number == "S1" and _pad_data(pad).drill_shape == "oval"
    ]

    assert len(shield_slots) == 4
    assert {round(pad.drill_width, 3) for pad in shield_slots} == {0.6}
    assert {round(pad.drill_height, 3) for pad in shield_slots} == {1.1, 1.5}
    assert {round(pad.drill, 3) for pad in shield_slots} == {0.6}


def test_orangecrab_usb_signal_pads_use_authored_board_rotation(orangecrab_board: Pcb) -> None:
    """KiCad board-file pad rotations are already in board orientation."""
    usb = orangecrab_board.footprint_by_ref("J3")
    assert usb is not None
    signal_pad = next(
        _pad_data(pad) for pad in _pads(orangecrab_board, "J3") if _pad_data(pad).number == "B7"
    )

    assert signal_pad.rotation == pytest.approx(270.0)


# ---------------------------------------------------------------------------
# 3D model parsing
# ---------------------------------------------------------------------------


def test_footprint_has_models(board: Pcb) -> None:
    """D1 (LED) should have exactly 1 model entry."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    assert len(_geometry(board, object_type=PcbGeometryObject.MODEL_3D, footprint_ref="D1")) == 1


def test_model_source_path(board: Pcb) -> None:
    """Model source should preserve the raw KiCad path with env vars."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    model = _model_data(
        _geometry(board, object_type=PcbGeometryObject.MODEL_3D, footprint_ref="D1")[0]
    )
    assert "${KICAD6_3DMODEL_DIR}" in model.source
    assert "LED_0603_1608Metric.wrl" in model.source


def test_model_rotation(board: Pcb) -> None:
    """U5 has (rotate (xyz 0 0 -90))."""
    u5 = board.footprint_by_ref("U5")
    assert u5 is not None
    models = _geometry(board, object_type=PcbGeometryObject.MODEL_3D, footprint_ref="U5")
    assert len(models) == 1
    assert _model_data(models[0]).rotation == (0.0, 0.0, -90.0)


def test_model_scale_default(board: Pcb) -> None:
    """Most models have (scale (xyz 1 1 1))."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    model = _model_data(
        _geometry(board, object_type=PcbGeometryObject.MODEL_3D, footprint_ref="D1")[0]
    )
    assert model.scale == (1.0, 1.0, 1.0)


def test_model_offset_default(board: Pcb) -> None:
    """Most models have (offset (xyz 0 0 0))."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    model = _model_data(
        _geometry(board, object_type=PcbGeometryObject.MODEL_3D, footprint_ref="D1")[0]
    )
    assert model.offset == (0.0, 0.0, 0.0)


def test_footprint_without_model(board: Pcb) -> None:
    """Test points have no (model ...) entry."""
    tp5 = board.footprint_by_ref("TP5")
    assert tp5 is not None
    assert _geometry(board, object_type=PcbGeometryObject.MODEL_3D, footprint_ref="TP5") == []


def test_multiple_models_per_footprint(orangecrab_board: Pcb) -> None:
    """OrangeCrab FPGA footprint (U3) has 2 model entries."""
    u3 = orangecrab_board.footprint_by_ref("U3")
    assert u3 is not None
    assert (
        len(
            _geometry(
                orangecrab_board,
                object_type=PcbGeometryObject.MODEL_3D,
                footprint_ref="U3",
            )
        )
        == 2
    )


def test_kicad5_at_vs_kicad6_offset(orangecrab_board: Pcb) -> None:
    """OrangeCrab uses (at (xyz ...)) instead of (offset (xyz ...)).

    Both should parse as the model offset.
    """
    u3 = orangecrab_board.footprint_by_ref("U3")
    assert u3 is not None
    models = _geometry(
        orangecrab_board,
        object_type=PcbGeometryObject.MODEL_3D,
        footprint_ref="U3",
    )
    assert len(models) == 2
    for model_geometry in models:
        model = _model_data(model_geometry)
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
    footprint_refs = {fp.reference for fp in board.footprints}
    for pad in _pads(board):
        assert pad.footprint_ref in footprint_refs
        assert pad.footprint_ref, f"Pad {_pad_data(pad).number} has no footprint_ref"


def test_silkscreen_lines_have_footprint_ref(board: Pcb) -> None:
    """Silkscreen lines from footprints carry the parent's ref."""
    silkscreen_lines = _geometry(
        board,
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.LINE,
        role=PcbGeometryRole.SILKSCREEN,
        source_collection="footprint_graphics",
    )
    assert silkscreen_lines
    assert all(line.footprint_ref for line in silkscreen_lines)


def test_fab_lines_have_footprint_ref(board: Pcb) -> None:
    """Fab lines from footprints carry the parent's ref."""
    fab_lines = _geometry(
        board,
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.LINE,
        role=PcbGeometryRole.FABRICATION,
        source_collection="footprint_graphics",
    )
    assert fab_lines
    assert all(line.footprint_ref for line in fab_lines)


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
    assert (
        len(
            _geometry(
                board,
                object_type=PcbGeometryObject.TEXT,
                source_collection="graphic_texts",
            )
        )
        == 8
    )


def test_graphic_text_content(board: Pcb) -> None:
    texts = {
        _text_data(text).text
        for text in _geometry(
            board,
            object_type=PcbGeometryObject.TEXT,
            source_collection="graphic_texts",
        )
    }
    assert "SWD Switch 2.1" in texts
    assert "DEBUGOTRON" in texts


def test_graphic_text_layer(board: Pcb) -> None:
    for text in _geometry(
        board,
        object_type=PcbGeometryObject.TEXT,
        source_collection="graphic_texts",
    ):
        assert text.primary_layer != ""


# ---------------------------------------------------------------------------
# Pad enrichment
# ---------------------------------------------------------------------------


def test_roundrect_rratio(board: Pcb) -> None:
    """swd_switch has 73 roundrect pads with rratio=0.25."""
    rr = [_pad_data(pad) for pad in _pads(board) if _pad_data(pad).roundrect_rratio > 0]
    assert len(rr) == 73
    assert rr[0].roundrect_rratio == pytest.approx(0.25)


def test_pad_pin_function(board: Pcb) -> None:
    """D1 has pads with pin_function 'K' and 'A'."""
    d1 = board.footprint_by_ref("D1")
    assert d1 is not None
    functions = {_pad_data(pad).pin_function for pad in _pads(board, "D1")}
    assert "K" in functions
    assert "A" in functions


def test_pad_pin_type(board: Pcb) -> None:
    """Pads have pin_type populated."""
    pads_with_type = [_pad_data(pad) for pad in _pads(board) if _pad_data(pad).pin_type]
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
    assert len(board.pours) == 2


def test_zone_has_boundary(board: Pcb) -> None:
    for pour in board.pours:
        assert len(pour.boundary.segments) >= 3


def test_zone_net_name(board: Pcb) -> None:
    net_names = {pour.net_name for pour in board.pours}
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
