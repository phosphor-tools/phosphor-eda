"""Tests for PCB annotation data model, parsing, and placement."""

import pytest

from phosphor_eda.domain.pcb import (
    LayerRole,
    Pcb,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbNet,
    PcbPad,
    PcbPadType,
)
from phosphor_eda.render.annotation_placement import (
    auto_assign_margin,
    compute_connector,
    measure_label,
)
from phosphor_eda.render.annotation_spec import (
    AnnotationSpec,
    BoxSpec,
    LabelSpec,
    LegendEntry,
    LegendSpec,
    PointerSpec,
    parse_annotations,
)
from phosphor_eda.render.annotation_svg import DEFAULT_ANNOTATION_COLOR, _parse_rgb
from phosphor_eda.render.annotations import (
    ResolvedAnnotations,
    resolve_annotations,
    resolve_component_target,
    resolve_net_target,
    resolve_pad_target,
)

# ---------------------------------------------------------------------------
# Synthetic board fixture
# ---------------------------------------------------------------------------


def _make_test_board() -> Pcb:
    """Board with two footprints (U1, U2) and a shared net for target resolution tests."""
    front_cu = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT), number=0)
    front_fab = PcbLayer("F.Fab", (LayerRole.FABRICATION, LayerRole.FRONT), number=36)
    edge = PcbLayer("Edge.Cuts", (LayerRole.EDGE,), number=44)
    nets = {
        1: PcbNet(1, "VCC"),
        2: PcbNet(2, "SPI_CLK"),
        3: PcbNet(3, "SPI_MOSI"),
    }
    u1 = PcbFootprint(
        reference="U1",
        footprint_lib="Package_SO:SOIC-8",
        x=11.0,
        y=10.0,
        rotation=0.0,
        layer=front_cu,
        value="MCU",
        bbox=(9.0, 9.0, 13.0, 11.0),
    )
    u2 = PcbFootprint(
        reference="U2",
        footprint_lib="Package_SO:SOIC-16",
        x=31.0,
        y=10.0,
        rotation=0.0,
        layer=front_cu,
        value="ADC",
        bbox=(29.0, 9.0, 33.0, 11.0),
    )
    return Pcb(
        name="test",
        layers=[front_cu, front_fab, edge],
        nets=nets,
        footprints=[u1, u2],
        pads=[
            _pad(u1, "1", 10.0, 10.0, nets[1], front_cu),
            _pad(u1, "2", 12.0, 10.0, nets[2], front_cu),
            _pad(u2, "1", 30.0, 10.0, nets[2], front_cu),
            _pad(u2, "2", 32.0, 10.0, nets[3], front_cu),
        ],
        vias=[],
        drills=[],
        conductors=[],
        artwork=[
            *_box_lines(u1, 9.0, 9.0, 13.0, 11.0, front_fab),
            *_box_lines(u2, 29.0, 9.0, 33.0, 11.0, front_fab),
        ],
        pours=[],
        keepouts=[],
        board_profile=PcbBoardProfile(elements=tuple(_outline_lines(edge))),
    )


def _pad(
    footprint: PcbFootprint,
    number: str,
    x: float,
    y: float,
    net: PcbNet,
    layer: PcbLayer,
) -> PcbPad:
    return PcbPad(
        id=f"pad:{footprint.reference}:{number}",
        number=number,
        x=x,
        y=y,
        width=1.0,
        height=1.0,
        shape="rect",
        pad_type=PcbPadType.SMD,
        layers=(layer,),
        net=net,
        footprint=footprint,
    )


def _box_lines(
    footprint: PcbFootprint,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layer: PcbLayer,
) -> list[PcbArtwork]:
    return [
        _artwork_line(f"fab:{footprint.reference}:0", x1, y1, x2, y1, layer, footprint),
        _artwork_line(f"fab:{footprint.reference}:1", x2, y1, x2, y2, layer, footprint),
        _artwork_line(f"fab:{footprint.reference}:2", x2, y2, x1, y2, layer, footprint),
        _artwork_line(f"fab:{footprint.reference}:3", x1, y2, x1, y1, layer, footprint),
    ]


def _outline_lines(layer: PcbLayer) -> list[PcbBoardProfileElement]:
    return [
        PcbBoardProfileElement("edge:0", PcbArtworkKind.LINE, layer, PcbLine(0, 0, 40, 0, 0.1)),
        PcbBoardProfileElement("edge:1", PcbArtworkKind.LINE, layer, PcbLine(40, 0, 40, 20, 0.1)),
        PcbBoardProfileElement("edge:2", PcbArtworkKind.LINE, layer, PcbLine(40, 20, 0, 20, 0.1)),
        PcbBoardProfileElement("edge:3", PcbArtworkKind.LINE, layer, PcbLine(0, 20, 0, 0, 0.1)),
    ]


def _artwork_line(
    id_: str,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    layer: PcbLayer,
    footprint: PcbFootprint,
) -> PcbArtwork:
    return PcbArtwork(
        id=id_,
        kind=PcbArtworkKind.LINE,
        purpose=PcbArtworkPurpose.FABRICATION,
        layer=layer,
        data=PcbLine(start_x, start_y, end_x, end_y, 0.1),
        footprint=footprint,
    )


@pytest.fixture()
def board() -> Pcb:
    return _make_test_board()


# ---------------------------------------------------------------------------
# parse_annotations
# ---------------------------------------------------------------------------


class TestParseAnnotations:
    def test_valid_box(self) -> None:
        data = {"boxes": [{"targets": ["U1", "U2"], "label": "SPI bus"}]}
        spec = parse_annotations(data)
        assert len(spec.boxes) == 1
        assert spec.boxes[0].targets == ["U1", "U2"]
        assert spec.boxes[0].label == "SPI bus"

    def test_valid_pointer(self) -> None:
        data = {"pointers": [{"target": "U1.2", "label": "Clock pin"}]}
        spec = parse_annotations(data)
        assert len(spec.pointers) == 1
        assert spec.pointers[0].target == "U1.2"

    def test_valid_label(self) -> None:
        data = {"labels": [{"target": "U1", "content": "<b>Main MCU</b>"}]}
        spec = parse_annotations(data)
        assert len(spec.labels) == 1
        assert spec.labels[0].content == "<b>Main MCU</b>"

    def test_valid_legend(self) -> None:
        data = {
            "legend": {
                "title": "SPI Signals",
                "entries": [
                    {"color": "#ff0000", "label": "CLK"},
                    {"color": "#00ff00", "label": "MOSI"},
                ],
            }
        }
        spec = parse_annotations(data)
        assert spec.legend is not None
        assert spec.legend.title == "SPI Signals"
        assert len(spec.legend.entries) == 2

    def test_empty_spec(self) -> None:
        spec = parse_annotations({})
        assert spec.boxes == []
        assert spec.pointers == []
        assert spec.labels == []
        assert spec.legend is None

    def test_box_missing_targets_raises(self) -> None:
        with pytest.raises(ValueError, match="targets"):
            parse_annotations({"boxes": [{"label": "no targets"}]})

    def test_pointer_missing_target_raises(self) -> None:
        """Pointer must have either target or target_net+target_near."""
        with pytest.raises(ValueError, match="target"):
            parse_annotations({"pointers": [{"label": "orphan"}]})

    def test_pointer_null_target_raises(self) -> None:
        """JSON null targets are treated as missing, not as the string 'None'."""
        with pytest.raises(ValueError, match="target"):
            parse_annotations({"pointers": [{"target": None, "label": "orphan"}]})

    def test_label_null_fields_become_empty(self) -> None:
        """JSON null label fields become empty strings, not the string 'None'."""
        spec = parse_annotations({"labels": [{"target": None, "content": None, "position": None}]})
        assert spec.labels[0].target == ""
        assert spec.labels[0].content == ""
        assert spec.labels[0].position == ""

    def test_pointer_net_target(self) -> None:
        data = {"pointers": [{"target_net": "SPI_CLK", "target_near": "U2", "label": "CLK"}]}
        spec = parse_annotations(data)
        assert spec.pointers[0].target_net == "SPI_CLK"
        assert spec.pointers[0].target_near == "U2"

    def test_legend_entry_color_optional(self) -> None:
        """Legend entries without a color are text-only (descriptive)."""
        data = {
            "legend": {
                "title": "Notes",
                "entries": [
                    {"label": "All bypass caps within 5mm of VDD"},
                    {"color": "#ff0000", "label": "CLK"},
                ],
            }
        }
        spec = parse_annotations(data)
        assert spec.legend is not None
        assert spec.legend.entries[0].color == ""
        assert spec.legend.entries[0].label == "All bypass caps within 5mm of VDD"
        assert spec.legend.entries[1].color == "#ff0000"

    def test_legend_missing_entries_raises(self) -> None:
        with pytest.raises(ValueError, match="entries"):
            parse_annotations({"legend": {"title": "X"}})

    def test_position_hints_preserved(self) -> None:
        data = {
            "boxes": [
                {
                    "targets": ["U1"],
                    "label": "MCU",
                    "label_position": "below",
                    "color": "#ff6b35",
                }
            ]
        }
        spec = parse_annotations(data)
        assert spec.boxes[0].label_position == "below"
        assert spec.boxes[0].color == "#ff6b35"


# ---------------------------------------------------------------------------
# Color parsing
# ---------------------------------------------------------------------------


class TestParseRgb:
    def test_hex6(self) -> None:
        assert _parse_rgb("#ff6b35") == (255, 107, 53)

    def test_hex3(self) -> None:
        assert _parse_rgb("#f00") == (255, 0, 0)

    def test_rgb_functional(self) -> None:
        assert _parse_rgb("rgb(1, 2, 3)") == (1, 2, 3)

    def test_rgba_with_alpha(self) -> None:
        assert _parse_rgb("rgba(10, 20, 30, 0.5)") == (10, 20, 30)

    def test_hex6_with_trailing_garbage_falls_back(self) -> None:
        assert _parse_rgb("#FF0000garbage") == DEFAULT_ANNOTATION_COLOR

    def test_rgb_with_trailing_garbage_falls_back(self) -> None:
        assert _parse_rgb("rgb(255,0,0) extra") == DEFAULT_ANNOTATION_COLOR

    def test_unknown_color_falls_back(self) -> None:
        assert _parse_rgb("not-a-color") == DEFAULT_ANNOTATION_COLOR


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


class TestResolveTargets:
    def testresolve_component_target(self, board: Pcb) -> None:
        center, bbox = resolve_component_target("U1", board)
        assert center == pytest.approx((11.0, 10.0), abs=0.1)
        assert bbox == (9.0, 9.0, 13.0, 11.0)

    def test_resolve_component_unknown_raises(self, board: Pcb) -> None:
        with pytest.raises(ValueError, match="U99"):
            resolve_component_target("U99", board)

    def testresolve_pad_target(self, board: Pcb) -> None:
        x, y = resolve_pad_target("U1.2", board)
        assert x == pytest.approx(12.0)
        assert y == pytest.approx(10.0)

    def test_resolve_pad_unknown_ref_raises(self, board: Pcb) -> None:
        with pytest.raises(ValueError, match="U99"):
            resolve_pad_target("U99.1", board)

    def test_resolve_pad_unknown_number_raises(self, board: Pcb) -> None:
        with pytest.raises(ValueError, match="99"):
            resolve_pad_target("U1.99", board)

    def testresolve_net_target(self, board: Pcb) -> None:
        """SPI_CLK on U2 → pad 1 at (30, 10)."""
        x, y = resolve_net_target("SPI_CLK", "U2", board)
        assert x == pytest.approx(30.0)
        assert y == pytest.approx(10.0)

    def testresolve_net_target_not_on_component(self, board: Pcb) -> None:
        """SPI_MOSI is only on U2, not U1."""
        with pytest.raises(ValueError, match="SPI_MOSI.*U1"):
            resolve_net_target("SPI_MOSI", "U1", board)


# ---------------------------------------------------------------------------
# Margin assignment
# ---------------------------------------------------------------------------


class TestMarginAssignment:
    """auto_assign_margin uses nearest-edge: the label goes to whichever
    board edge the target is physically closest to."""

    def test_target_right_of_center(self) -> None:
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        margin = auto_assign_margin(35.0, 10.0, board_bbox)
        assert margin == "right"

    def test_target_left_of_center(self) -> None:
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        margin = auto_assign_margin(5.0, 10.0, board_bbox)
        assert margin == "left"

    def test_target_below_center(self) -> None:
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        margin = auto_assign_margin(20.0, 18.0, board_bbox)
        assert margin == "bottom"

    def test_target_above_center(self) -> None:
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        margin = auto_assign_margin(20.0, 2.0, board_bbox)
        assert margin == "top"

    def test_nearest_edge_beats_center_quadrant(self) -> None:
        """Target at (36, 3) is 4mm from right but only 3mm from top.
        Nearest-edge picks top even though the center-quadrant approach
        would pick right (larger normalized horizontal offset)."""
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        margin = auto_assign_margin(36.0, 3.0, board_bbox)
        assert margin == "top"

    def test_corner_target_picks_closest_edge(self) -> None:
        """Target near a corner goes to whichever edge is closer."""
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        # 2mm from right, 3mm from bottom
        assert auto_assign_margin(38.0, 17.0, board_bbox) == "right"
        # 1mm from bottom, 3mm from right
        assert auto_assign_margin(37.0, 19.0, board_bbox) == "bottom"

    def test_square_board_symmetry(self) -> None:
        """On a square board, nearest-edge is unambiguous for off-center."""
        board_bbox = (0.0, 0.0, 20.0, 20.0)
        assert auto_assign_margin(18.0, 10.0, board_bbox) == "right"
        assert auto_assign_margin(2.0, 10.0, board_bbox) == "left"
        assert auto_assign_margin(10.0, 18.0, board_bbox) == "bottom"
        assert auto_assign_margin(10.0, 2.0, board_bbox) == "top"

    def test_tie_prefers_horizontal_edge(self) -> None:
        """Equal horizontal/vertical distance should prefer horizontal (right/left)."""
        board_bbox = (0.0, 0.0, 20.0, 20.0)
        # (15, 5): right=5, top=5 — tie, prefer right
        assert auto_assign_margin(15.0, 5.0, board_bbox) == "right"
        # (5, 15): left=5, bottom=5 — tie, prefer left
        assert auto_assign_margin(5.0, 15.0, board_bbox) == "left"


# ---------------------------------------------------------------------------
# Connector paths
# ---------------------------------------------------------------------------


class TestConnectorPath:
    def test_right_margin_connector(self) -> None:
        """Right margin connector: label → horizontal → vertical → target."""
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        path = compute_connector(
            label_x=45.0,
            label_y=8.0,
            label_w=10.0,
            label_h=4.0,
            target_x=30.0,
            target_y=10.0,
            margin="right",
            board_bbox=board_bbox,
            margin_gap=5.0,
        )
        assert len(path) == 4
        # First point is at label left edge
        assert path[0][0] == pytest.approx(45.0)
        # Last point is at target
        assert path[-1] == pytest.approx((30.0, 10.0), abs=0.01)

    def test_left_margin_connector(self) -> None:
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        path = compute_connector(
            label_x=-15.0,
            label_y=8.0,
            label_w=10.0,
            label_h=4.0,
            target_x=5.0,
            target_y=10.0,
            margin="left",
            board_bbox=board_bbox,
            margin_gap=5.0,
        )
        assert len(path) == 4
        # First point is at label right edge
        assert path[0][0] == pytest.approx(-5.0)
        # Last point is at target
        assert path[-1] == pytest.approx((5.0, 10.0), abs=0.01)

    def test_connector_is_orthogonal(self) -> None:
        """All segments should be horizontal or vertical."""
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        path = compute_connector(
            label_x=45.0,
            label_y=5.0,
            label_w=10.0,
            label_h=3.0,
            target_x=35.0,
            target_y=15.0,
            margin="right",
            board_bbox=board_bbox,
            margin_gap=5.0,
        )
        for i in range(len(path) - 1):
            x1, y1 = path[i]
            x2, y2 = path[i + 1]
            # Each segment is either horizontal (same y) or vertical (same x)
            assert x1 == pytest.approx(x2) or y1 == pytest.approx(y2)


# ---------------------------------------------------------------------------
# Label measurement
# ---------------------------------------------------------------------------


class TestMeasureLabel:
    def test_empty_text(self) -> None:
        w, h = measure_label("", 1.0)
        assert w == 0.0
        assert h == 0.0

    def test_includes_padding(self) -> None:
        """Label pill should be larger than raw text measurement."""
        from phosphor_eda.geometry.text_metrics import measure_text

        text = "Hello"
        raw_w, raw_h = measure_text(text, 1.0)
        pill_w, pill_h = measure_label(text, 1.0)
        assert pill_w > raw_w
        assert pill_h > raw_h


# ---------------------------------------------------------------------------
# End-to-end resolve_annotations
# ---------------------------------------------------------------------------


class TestResolveAnnotations:
    def test_box_and_pointer(self, board: Pcb) -> None:
        spec = AnnotationSpec(
            boxes=[BoxSpec(targets=["U1", "U2"], label="SPI bus")],
            pointers=[PointerSpec(target="U1.2", label="Clock pin")],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        assert isinstance(resolved, ResolvedAnnotations)
        assert len(resolved.boxes) == 1
        assert len(resolved.pointers) == 1
        # Box should encompass both U1 and U2 (coords are in pixel space)
        box = resolved.boxes[0]
        scale = resolved.px_scale
        assert box.x * scale <= 9.0  # U1 left edge in board mm
        assert (box.x + box.width) * scale >= 33.0  # U2 right edge

    def test_content_bbox_encompasses_annotations(self, board: Pcb) -> None:
        spec = AnnotationSpec(
            boxes=[BoxSpec(targets=["U1"], label="MCU")],
            pointers=[],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        cx, cy, cx2, cy2 = resolved.content_bbox
        # Content bbox is in board mm; box coords are in pixels.
        # Convert box to board mm and check containment.
        box = resolved.boxes[0]
        s = resolved.px_scale
        assert cx <= box.x * s
        assert cy <= box.y * s
        assert cx2 >= (box.x + box.width) * s
        assert cy2 >= (box.y + box.height) * s

    def test_legend_resolved(self, board: Pcb) -> None:
        spec = AnnotationSpec(
            boxes=[],
            pointers=[],
            labels=[],
            legend=LegendSpec(
                title="Signals",
                entries=[LegendEntry(color="#f00", label="CLK")],
            ),
        )
        resolved = resolve_annotations(spec, board, "front")
        assert resolved.legend is not None
        assert resolved.legend.title == "Signals"

    def test_label_with_connector(self, board: Pcb) -> None:
        """Labels targeting a component should get a connector path."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[],
            labels=[LabelSpec(target="U1", content="Main MCU")],
        )
        resolved = resolve_annotations(spec, board, "front")
        assert len(resolved.labels) == 1
        callout = resolved.labels[0].callout
        assert callout is not None
        assert callout.text == "Main MCU"
        # On-board labels get a connector path to the target
        assert len(callout.connector_path) >= 2

    def test_label_without_target_no_connector(self, board: Pcb) -> None:
        """Labels without a target should have no connector."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[],
            labels=[LabelSpec(content="Board note")],
        )
        resolved = resolve_annotations(spec, board, "front")
        callout = resolved.labels[0].callout
        assert callout is not None
        assert callout.connector_path == []

    def test_net_pointer(self, board: Pcb) -> None:
        """Pointer via target_net + target_near resolves to the correct pad."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[
                PointerSpec(
                    target="",
                    target_net="SPI_CLK",
                    target_near="U2",
                    label="CLK",
                )
            ],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        ptr = resolved.pointers[0]
        s = resolved.px_scale
        # Target coords are in pixel space; convert back to board mm
        assert ptr.target_x * s == pytest.approx(30.0)
        assert ptr.target_y * s == pytest.approx(10.0)

    def test_back_side_auto_margin_uses_rendered_view(self, board: Pcb) -> None:
        """Back-side automatic placement uses the mirrored rendered target position."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[PointerSpec(target="U1.1", label="Pin 1")],
            labels=[],
        )
        front = resolve_annotations(spec, board, "front", width_px=800)
        back = resolve_annotations(spec, board, "back", width_px=800)

        front_ptr = front.pointers[0]
        back_ptr = back.pointers[0]
        assert front_ptr.callout is not None
        assert back_ptr.callout is not None
        assert front_ptr.callout.x < front_ptr.target_x
        assert back_ptr.callout.x > back_ptr.target_x

    def test_back_side_position_hint_is_rendered_view(self, board: Pcb) -> None:
        """Back-side explicit position hints are interpreted in rendered-view space."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[PointerSpec(target="U1.1", label="Pin 1", position="right")],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "back", width_px=800)
        ptr = resolved.pointers[0]

        assert ptr.callout is not None
        assert ptr.callout.x > ptr.target_x

    def test_back_side_pointer_target_uses_rendered_pad_location(self, board: Pcb) -> None:
        """Back-side connector endpoints point to the mirrored rendered pad location."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[PointerSpec(target="U1.1", label="Pin 1", position="right")],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "back", width_px=800)
        ptr = resolved.pointers[0]

        assert ptr.target_x * resolved.px_scale == pytest.approx(30.0)
        assert ptr.target_y * resolved.px_scale == pytest.approx(10.0)
        assert ptr.callout is not None
        assert ptr.callout.connector_path[-1] == pytest.approx((ptr.target_x, ptr.target_y))

    def test_empty_spec_returns_empty(self, board: Pcb) -> None:
        spec = AnnotationSpec(boxes=[], pointers=[], labels=[])
        resolved = resolve_annotations(spec, board, "front")
        assert resolved.boxes == []
        assert resolved.pointers == []
        assert resolved.labels == []
        assert resolved.legend is None

    def test_box_label_in_margin(self, board: Pcb) -> None:
        """Box labels should be placed in a margin outside the board."""
        spec = AnnotationSpec(
            boxes=[BoxSpec(targets=["U1"], label="MCU")],
            pointers=[],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        box = resolved.boxes[0]
        assert box.callout is not None
        s = resolved.px_scale
        board_bbox = board.bbox()
        # Convert label coords from pixels back to board mm for comparison
        lx = box.callout.x * s
        ly = box.callout.y * s
        lr = (box.callout.x + box.callout.width) * s
        lb = (box.callout.y + box.callout.height) * s
        outside = (
            lx > board_bbox[2]  # right of board
            or lr < board_bbox[0]  # left of board
            or ly > board_bbox[3]  # below board
            or lb < board_bbox[1]  # above board
        )
        assert outside, f"Label at ({lx}, {ly}) should be outside board"

    def test_box_label_has_connector(self, board: Pcb) -> None:
        """Box labels should have an orthogonal connector path."""
        spec = AnnotationSpec(
            boxes=[BoxSpec(targets=["U1"], label="MCU")],
            pointers=[],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        box = resolved.boxes[0]
        assert box.callout is not None
        assert len(box.callout.connector_path) >= 2

    def test_box_label_anchor_comes_from_margin(self, board: Pcb) -> None:
        """Left margin box labels should right-align their text inside the pill."""
        spec = AnnotationSpec(
            boxes=[BoxSpec(targets=["U1"], label="MCU", label_position="left")],
            pointers=[],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")

        callout = resolved.boxes[0].callout
        assert callout is not None
        assert callout.text_anchor == "end"

    def test_pointer_label_anchor_comes_from_margin(self, board: Pcb) -> None:
        """Right margin pointer labels should left-align their text inside the pill."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[PointerSpec(target="U1", label="MCU", position="right")],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")

        callout = resolved.pointers[0].callout
        assert callout is not None
        assert callout.text_anchor == "start"

    def test_label_anchor_comes_from_margin(self, board: Pcb) -> None:
        """Top/bottom margin labels should keep centered text."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[],
            labels=[LabelSpec(target="U1", content="Main MCU", position="top")],
        )
        resolved = resolve_annotations(spec, board, "front")

        callout = resolved.labels[0].callout
        assert callout is not None
        assert callout.text_anchor == "middle"

    def test_label_dimensions_populated(self, board: Pcb) -> None:
        """Resolved labels should have positive width and height."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[PointerSpec(target="U1", label="Main IC")],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        ptr = resolved.pointers[0]
        assert ptr.callout is not None
        assert ptr.callout.width > 0
        assert ptr.callout.height > 0

    def test_no_label_overlap(self, board: Pcb) -> None:
        """Multiple labels in the same margin should not overlap."""
        spec = AnnotationSpec(
            boxes=[],
            pointers=[],
            labels=[
                LabelSpec(target="U1", content="First"),
                LabelSpec(target="U2", content="Second"),
            ],
        )
        resolved = resolve_annotations(spec, board, "front")
        if len(resolved.labels) >= 2:
            a = resolved.labels[0].callout
            b = resolved.labels[1].callout
            assert a is not None
            assert b is not None
            # Check no vertical overlap (for labels in the same left/right margin)
            # or no horizontal overlap (for top/bottom margin)
            a_r = a.x + a.width
            b_r = b.x + b.width
            a_b = a.y + a.height
            b_b = b.y + b.height
            h_overlap = a.x < b_r and b.x < a_r
            v_overlap = a.y < b_b and b.y < a_b
            assert not (h_overlap and v_overlap), "Labels overlap"

    def test_font_size_stored(self, board: Pcb) -> None:
        """Resolved annotations should include the computed font size."""
        spec = AnnotationSpec(boxes=[], pointers=[], labels=[])
        resolved = resolve_annotations(spec, board, "front")
        assert resolved.font_size > 0
