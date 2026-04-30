"""Tests for PCB annotation data model, parsing, and placement."""

import pytest

from phosphor_eda.pcb import (
    PcbBoard,
    PcbFootprint,
    PcbLine,
    PcbNet,
    PcbPad,
)
from phosphor_eda.pcb_annotations import (
    AnnotationSpec,
    BoxSpec,
    LabelSpec,
    LegendEntry,
    LegendSpec,
    PointerSpec,
    ResolvedAnnotations,
    _auto_place_box_label,  # pyright: ignore[reportPrivateUsage]
    _auto_place_legend,  # pyright: ignore[reportPrivateUsage]
    _auto_place_pointer,  # pyright: ignore[reportPrivateUsage]
    _compute_annotation_font_size,  # pyright: ignore[reportPrivateUsage]
    _estimate_label_size,  # pyright: ignore[reportPrivateUsage]
    _resolve_component_target,  # pyright: ignore[reportPrivateUsage]
    _resolve_net_target,  # pyright: ignore[reportPrivateUsage]
    _resolve_pad_target,  # pyright: ignore[reportPrivateUsage]
    parse_annotations,
    resolve_annotations,
)

# ---------------------------------------------------------------------------
# Synthetic board fixture
# ---------------------------------------------------------------------------


def _make_test_board() -> PcbBoard:
    """Board with two footprints (U1, U2) and a shared net for target resolution tests."""
    u1_pads = [
        PcbPad(
            number="1",
            x=10.0,
            y=10.0,
            width=1.0,
            height=1.0,
            shape="rect",
            layers=["F.Cu"],
            net_number=1,
            net_name="VCC",
            footprint_ref="U1",
        ),
        PcbPad(
            number="2",
            x=12.0,
            y=10.0,
            width=1.0,
            height=1.0,
            shape="rect",
            layers=["F.Cu"],
            net_number=2,
            net_name="SPI_CLK",
            footprint_ref="U1",
        ),
    ]
    u2_pads = [
        PcbPad(
            number="1",
            x=30.0,
            y=10.0,
            width=1.0,
            height=1.0,
            shape="rect",
            layers=["F.Cu"],
            net_number=2,
            net_name="SPI_CLK",
            footprint_ref="U2",
        ),
        PcbPad(
            number="2",
            x=32.0,
            y=10.0,
            width=1.0,
            height=1.0,
            shape="rect",
            layers=["F.Cu"],
            net_number=3,
            net_name="SPI_MOSI",
            footprint_ref="U2",
        ),
    ]
    u1 = PcbFootprint(
        reference="U1",
        footprint_lib="Package_SO:SOIC-8",
        x=11.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        value="MCU",
        pads=u1_pads,
        fab_lines=[
            PcbLine(9, 9, 13, 9, "F.Fab", 0.1, footprint_ref="U1"),
            PcbLine(13, 9, 13, 11, "F.Fab", 0.1, footprint_ref="U1"),
            PcbLine(13, 11, 9, 11, "F.Fab", 0.1, footprint_ref="U1"),
            PcbLine(9, 11, 9, 9, "F.Fab", 0.1, footprint_ref="U1"),
        ],
        bbox=(9.0, 9.0, 13.0, 11.0),
    )
    u2 = PcbFootprint(
        reference="U2",
        footprint_lib="Package_SO:SOIC-16",
        x=31.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        value="ADC",
        pads=u2_pads,
        fab_lines=[
            PcbLine(29, 9, 33, 9, "F.Fab", 0.1, footprint_ref="U2"),
            PcbLine(33, 9, 33, 11, "F.Fab", 0.1, footprint_ref="U2"),
            PcbLine(33, 11, 29, 11, "F.Fab", 0.1, footprint_ref="U2"),
            PcbLine(29, 11, 29, 9, "F.Fab", 0.1, footprint_ref="U2"),
        ],
        bbox=(29.0, 9.0, 33.0, 11.0),
    )
    return PcbBoard(
        name="test",
        nets={
            0: PcbNet(0, ""),
            1: PcbNet(1, "VCC"),
            2: PcbNet(2, "SPI_CLK"),
            3: PcbNet(3, "SPI_MOSI"),
        },
        footprints=[u1, u2],
        segments=[],
        vias=[],
        outline_lines=[
            PcbLine(0, 0, 40, 0, "Edge.Cuts", 0.1),
            PcbLine(40, 0, 40, 20, "Edge.Cuts", 0.1),
            PcbLine(40, 20, 0, 20, "Edge.Cuts", 0.1),
            PcbLine(0, 20, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
    )


@pytest.fixture()
def board() -> PcbBoard:
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

    def test_pointer_net_target(self) -> None:
        data = {"pointers": [{"target_net": "SPI_CLK", "target_near": "U2", "label": "CLK"}]}
        spec = parse_annotations(data)
        assert spec.pointers[0].target_net == "SPI_CLK"
        assert spec.pointers[0].target_near == "U2"

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
# Target resolution
# ---------------------------------------------------------------------------


class TestResolveTargets:
    def test_resolve_component_target(self, board: PcbBoard) -> None:
        center, bbox = _resolve_component_target("U1", board)
        assert center == pytest.approx((11.0, 10.0), abs=0.1)
        assert bbox == (9.0, 9.0, 13.0, 11.0)

    def test_resolve_component_unknown_raises(self, board: PcbBoard) -> None:
        with pytest.raises(ValueError, match="U99"):
            _resolve_component_target("U99", board)

    def test_resolve_pad_target(self, board: PcbBoard) -> None:
        x, y = _resolve_pad_target("U1.2", board)
        assert x == pytest.approx(12.0)
        assert y == pytest.approx(10.0)

    def test_resolve_pad_unknown_ref_raises(self, board: PcbBoard) -> None:
        with pytest.raises(ValueError, match="U99"):
            _resolve_pad_target("U99.1", board)

    def test_resolve_pad_unknown_number_raises(self, board: PcbBoard) -> None:
        with pytest.raises(ValueError, match="99"):
            _resolve_pad_target("U1.99", board)

    def test_resolve_net_target(self, board: PcbBoard) -> None:
        """SPI_CLK on U2 → pad 1 at (30, 10)."""
        x, y = _resolve_net_target("SPI_CLK", "U2", board)
        assert x == pytest.approx(30.0)
        assert y == pytest.approx(10.0)

    def test_resolve_net_target_not_on_component(self, board: PcbBoard) -> None:
        """SPI_MOSI is only on U2, not U1."""
        with pytest.raises(ValueError, match="SPI_MOSI.*U1"):
            _resolve_net_target("SPI_MOSI", "U1", board)


# ---------------------------------------------------------------------------
# Placement heuristics
# ---------------------------------------------------------------------------


class TestPlacement:
    def test_auto_place_pointer_top_right(self) -> None:
        """Target in the top-right quadrant → label placed right or above."""
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        pos = _auto_place_pointer(35.0, 5.0, 5.0, 2.0, board_bbox)
        assert pos in ("right", "above")

    def test_auto_place_pointer_bottom_left(self) -> None:
        """Target in the bottom-left quadrant → label placed left or below."""
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        pos = _auto_place_pointer(5.0, 15.0, 5.0, 2.0, board_bbox)
        assert pos in ("left", "below")

    def test_auto_place_box_label_near_top(self) -> None:
        """Box near top edge → label placed below."""
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        box_bbox = (15.0, 1.0, 25.0, 5.0)
        pos, _x, _y = _auto_place_box_label(box_bbox, 5.0, 2.0, board_bbox)
        assert pos == "below"

    def test_auto_place_box_label_near_bottom(self) -> None:
        """Box near bottom edge → label placed above."""
        board_bbox = (0.0, 0.0, 40.0, 20.0)
        box_bbox = (15.0, 15.0, 25.0, 19.0)
        pos, _x, _y = _auto_place_box_label(box_bbox, 5.0, 2.0, board_bbox)
        assert pos == "above"

    def test_auto_place_legend_wide_board(self) -> None:
        """Wide board → legend at bottom."""
        board_bbox = (0.0, 0.0, 100.0, 30.0)
        pos, _x, _y = _auto_place_legend(board_bbox, 20.0, 5.0)
        assert pos == "board-bottom"

    def test_auto_place_legend_tall_board(self) -> None:
        """Tall board → legend on the right."""
        board_bbox = (0.0, 0.0, 30.0, 100.0)
        pos, _x, _y = _auto_place_legend(board_bbox, 10.0, 20.0)
        assert pos == "board-right"


# ---------------------------------------------------------------------------
# Label size estimation
# ---------------------------------------------------------------------------


class TestEstimateSize:
    def test_simple_text(self) -> None:
        w, h = _estimate_label_size("Hello", 1.0)
        assert w > 0
        assert h > 0

    def test_multiline_taller(self) -> None:
        _, h1 = _estimate_label_size("One line", 1.0)
        _, h2 = _estimate_label_size("Line 1<br>Line 2", 1.0)
        assert h2 > h1

    def test_html_tags_stripped_for_width(self) -> None:
        """Tags like <b> shouldn't inflate the width estimate."""
        w_plain, _ = _estimate_label_size("bold text", 1.0)
        w_tagged, _ = _estimate_label_size("<b>bold text</b>", 1.0)
        assert w_plain == pytest.approx(w_tagged)


# ---------------------------------------------------------------------------
# Font size from board diagonal
# ---------------------------------------------------------------------------


def test_font_size_scales_with_diagonal() -> None:
    small_bbox = (0.0, 0.0, 20.0, 20.0)
    large_bbox = (0.0, 0.0, 200.0, 200.0)
    small_font = _compute_annotation_font_size(small_bbox)
    large_font = _compute_annotation_font_size(large_bbox)
    assert large_font > small_font
    # Font should scale roughly proportionally to diagonal
    ratio = large_font / small_font
    assert ratio == pytest.approx(10.0, rel=0.5)


# ---------------------------------------------------------------------------
# End-to-end resolve_annotations
# ---------------------------------------------------------------------------


class TestResolveAnnotations:
    def test_box_and_pointer(self, board: PcbBoard) -> None:
        spec = AnnotationSpec(
            boxes=[BoxSpec(targets=["U1", "U2"], label="SPI bus")],
            pointers=[PointerSpec(target="U1.2", label="Clock pin")],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        assert isinstance(resolved, ResolvedAnnotations)
        assert len(resolved.boxes) == 1
        assert len(resolved.pointers) == 1
        # Box should encompass both U1 and U2
        box = resolved.boxes[0]
        assert box.x <= 9.0  # U1 left edge
        assert box.x + box.width >= 33.0  # U2 right edge

    def test_content_bbox_encompasses_annotations(self, board: PcbBoard) -> None:
        spec = AnnotationSpec(
            boxes=[BoxSpec(targets=["U1"], label="MCU")],
            pointers=[],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        cx, cy, cx2, cy2 = resolved.content_bbox
        # Content bbox should encompass box + label
        box = resolved.boxes[0]
        assert cx <= box.x
        assert cy <= box.y
        assert cx2 >= box.x + box.width
        assert cy2 >= box.y + box.height

    def test_legend_resolved(self, board: PcbBoard) -> None:
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

    def test_label_with_leader(self, board: PcbBoard) -> None:
        spec = AnnotationSpec(
            boxes=[],
            pointers=[],
            labels=[LabelSpec(target="U1", content="Main MCU")],
        )
        resolved = resolve_annotations(spec, board, "front")
        assert len(resolved.labels) == 1
        label = resolved.labels[0]
        assert label.label_html == "Main MCU"
        # On-board labels get a leader line to the target
        assert label.leader_target is not None

    def test_net_pointer(self, board: PcbBoard) -> None:
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
        assert ptr.target_x == pytest.approx(30.0)
        assert ptr.target_y == pytest.approx(10.0)

    def test_empty_spec_returns_empty(self, board: PcbBoard) -> None:
        spec = AnnotationSpec(boxes=[], pointers=[], labels=[])
        resolved = resolve_annotations(spec, board, "front")
        assert resolved.boxes == []
        assert resolved.pointers == []
        assert resolved.labels == []
        assert resolved.legend is None

    def test_box_with_explicit_position(self, board: PcbBoard) -> None:
        spec = AnnotationSpec(
            boxes=[
                BoxSpec(
                    targets=["U1"],
                    label="MCU",
                    label_position="above",
                )
            ],
            pointers=[],
            labels=[],
        )
        resolved = resolve_annotations(spec, board, "front")
        box = resolved.boxes[0]
        assert box.label_position == "above"
        # Label should be above the box
        assert box.label_y < box.y
