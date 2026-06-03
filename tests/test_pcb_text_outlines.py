"""Tests for converting PCB-authored text to source-layer artwork."""

from __future__ import annotations

from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
    PcbFootprint,
    PcbGraphicText,
    PcbLayer,
    PcbLine,
    PcbText,
)
from phosphor_eda.pcb_render_artwork import (
    select_source_artwork,
)
from phosphor_eda.pcb_render_geometry import GeometryKind, build_geometry_store
from phosphor_eda.pcb_render_primitives import geometry_to_svg_primitive
from phosphor_eda.pcb_render_settings import LayerMatch, LayerSelectionRule
from phosphor_eda.text_outlines import text_outline_geometry


def test_text_outline_geometry_is_non_empty_and_scales_with_font_size() -> None:
    small = PcbText("PCB", 10.0, 20.0, 0.0, "F.SilkS", 1.0)
    large = PcbText("PCB", 10.0, 20.0, 0.0, "F.SilkS", 2.0)

    small_outline = text_outline_geometry(small)
    large_outline = text_outline_geometry(large)

    assert small_outline.is_valid
    assert not small_outline.is_empty
    assert large_outline.is_valid
    small_width = small_outline.bounds[2] - small_outline.bounds[0]
    large_width = large_outline.bounds[2] - large_outline.bounds[0]
    assert large_width > small_width * 1.9


def test_text_outline_rotation_changes_bounds() -> None:
    unrotated = text_outline_geometry(PcbText("PCB", 10.0, 20.0, 0.0, "F.SilkS", 1.0))
    rotated = text_outline_geometry(PcbText("PCB", 10.0, 20.0, 90.0, "F.SilkS", 1.0))

    unrotated_width = unrotated.bounds[2] - unrotated.bounds[0]
    rotated_width = rotated.bounds[2] - rotated.bounds[0]
    unrotated_height = unrotated.bounds[3] - unrotated.bounds[1]
    rotated_height = rotated.bounds[3] - rotated.bounds[1]

    assert rotated_width < unrotated_width * 0.75
    assert rotated_height > unrotated_height * 1.5


def test_footprint_user_text_on_silkscreen_becomes_silkscreen_artwork() -> None:
    board = _board_with_text(PcbText("U1", 10.0, 10.0, 0.0, "F.SilkS", 1.0, kind="user"))
    store = build_geometry_store(board, side="front")

    selected = select_source_artwork(
        store,
        (LayerSelectionRule(match=LayerMatch(function="silkscreen")),),
    )
    primitives = tuple(
        primitive
        for item in selected
        for primitive in (geometry_to_svg_primitive(item, target_layer_name=item.layer.name),)
        if primitive is not None
    )

    text_items = [item for item in store.items if item.kind is GeometryKind.USER_TEXT]
    assert len(text_items) == 1
    assert text_items[0].layer.role == "silkscreen"
    assert any(primitive.source_id == text_items[0].id for primitive in primitives)


def test_board_graphic_text_converts_to_svg_primitive() -> None:
    board = _empty_board()
    board.graphic_texts.append(PcbGraphicText("ON", 12.0, 8.0, 0.0, "F.SilkS", 0.8))
    store = build_geometry_store(board, side="front")
    [graphic_text] = [item for item in store.items if item.kind is GeometryKind.BOARD_GRAPHIC_TEXT]

    primitive = geometry_to_svg_primitive(graphic_text, target_layer_name=graphic_text.layer.name)

    assert primitive is not None
    assert primitive.source_layer == "F.SilkS"
    assert primitive.d.startswith("M ")
    assert primitive.d.endswith("Z")


def test_back_side_mirrored_text_still_produces_valid_svg_primitive() -> None:
    board = _board_with_text(PcbText("BOT", 4.0, 5.0, 15.0, "B.SilkS", 1.0, kind="user"))
    store = build_geometry_store(board, side="back")
    [text_item] = [item for item in store.items if item.kind is GeometryKind.USER_TEXT]

    primitive = geometry_to_svg_primitive(text_item, target_layer_name=text_item.layer.name)

    assert primitive is not None
    assert primitive.source_layer == "B.SilkS"
    assert primitive.d.startswith("M ")
    assert primitive.d.endswith("Z")


def _board_with_text(text: PcbText) -> Pcb:
    board = _empty_board()
    board.footprints.append(
        PcbFootprint(
            reference="U1",
            footprint_lib="Package",
            x=10.0,
            y=10.0,
            rotation=0.0,
            layer="F.Cu",
            texts=[text],
        )
    )
    return board


def _empty_board() -> Pcb:
    return Pcb(
        name="text-board",
        nets={},
        footprints=[],
        segments=[],
        vias=[],
        outline_lines=[
            PcbLine(0.0, 0.0, 20.0, 0.0, "Edge.Cuts", 0.1),
            PcbLine(20.0, 0.0, 20.0, 20.0, "Edge.Cuts", 0.1),
            PcbLine(20.0, 20.0, 0.0, 20.0, "Edge.Cuts", 0.1),
            PcbLine(0.0, 20.0, 0.0, 0.0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, "front", 0),
            PcbLayer("B.Cu", LayerFunction.COPPER, "back", 1),
            PcbLayer("F.SilkS", LayerFunction.SILKSCREEN, "front"),
            PcbLayer("B.SilkS", LayerFunction.SILKSCREEN, "back"),
            PcbLayer("Edge.Cuts", LayerFunction.EDGE),
        ],
    )
