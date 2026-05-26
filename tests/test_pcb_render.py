"""Tests for the PCB SVG renderer — structural and CSS assertions."""

import json
import math
import re
from pathlib import Path

import pytest
from shapely import GeometryCollection, MultiPolygon, Point, Polygon

import phosphor_eda.pcb_render as pcb_render_module
from phosphor_eda.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
    PcbFootprint,
    PcbGraphicText,
    PcbLayer,
    PcbLine,
    PcbModel3D,
    PcbNet,
    PcbPad,
    PcbPolygon,
    PcbSegment,
    PcbText,
    PcbVia,
)
from phosphor_eda.pcb_annotations import (
    LegendEntry,
    ResolvedAnnotations,
    ResolvedBox,
    ResolvedLabel,
    ResolvedLegend,
    ResolvedPointer,
)
from phosphor_eda.pcb_render import (
    HighlightSpec,
    RenderSettings,
    _fmt_attrs,  # pyright: ignore[reportPrivateUsage]
    load_render_settings_file,
    load_render_settings_json,
    parse_render_settings,
    render_pcb_svg,
    render_settings_schema,
)
from phosphor_eda.pcb_render_artwork import DerivedLayer
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometrySelector,
    build_geometry_store,
    geometry_matches_selector,
)
from phosphor_eda.pcb_render_modes import build_cad_layers
from phosphor_eda.pcb_render_plan import DerivedRenderPlan, ViewBox
from phosphor_eda.pcb_render_settings import is_json_dict, is_json_list
from phosphor_eda.pcb_render_tokens import ResolvedStyle, VisualRole

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"
ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"
PIMX8_FIXTURE = (
    Path(__file__).parent / "fixtures" / "altium" / "pi-mx8" / "PCB" / "PiMX8MP_r0.3.PcbDoc"
)
CANONICAL_DERIVED_PRESET_MODES = {
    "review": "realistic",
    "clean": "realistic",
    "design": "cad",
    "high-contrast": "cad",
    "simplified-high-contrast": "cad",
}
BUILT_IN_DERIVED_PRESETS = (
    *CANONICAL_DERIVED_PRESET_MODES,
    "print",
    "print-callout",
    "review-callout",
)
PIMX8_GALLERY_SMOKE_SETTINGS = (
    {"extends": "phosphor:high-contrast", "side": "front", "width": 400},
    {
        "extends": "phosphor:review",
        "side": "front",
        "width": 400,
        "highlights": [
            {"net": "VCC3.3_SYS", "color": "#0057b8"},
            {"net": "VCC1.8_SYS", "color": "#00875a"},
            {"net": "VCC_SOC", "color": "#8b2bb8"},
            {"net": "DRAM_VCC1.1", "color": "#c00000"},
        ],
    },
    {
        "extends": "phosphor:simplified-high-contrast",
        "side": "front",
        "width": 400,
        "highlights": [
            {"net": "ETH_TRX0_P", "color": "#0057b8"},
            {"net": "ETH_TRX0_N", "color": "#d67d00"},
            {"net": "ETH_TRX1_P", "color": "#00875a"},
            {"net": "ETH_TRX1_N", "color": "#8b2bb8"},
            {"net": "ETH_TRX2_P", "color": "#c00000"},
            {"net": "ETH_TRX2_N", "color": "#555555"},
        ],
    },
)


def _as_object_dict(value: object) -> dict[str, object]:
    assert is_json_dict(value)
    return value


def _as_object_list(value: object) -> list[object]:
    assert is_json_list(value)
    return value


@pytest.fixture(scope="module")
def board() -> Pcb:
    return parse_kicad_pcb(FIXTURE)


@pytest.fixture(scope="module")
def orangecrab_board() -> Pcb:
    return parse_kicad_pcb(ORANGECRAB_FIXTURE)


@pytest.fixture(scope="module")
def pimx8_board() -> Pcb:
    if not PIMX8_FIXTURE.exists():
        pytest.skip("Pi MX8 Altium fixture not available")
    return parse_altium_pcb(PIMX8_FIXTURE)


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


def _assert_svg_contains_in_order(svg: str, needles: list[str]) -> None:
    cursor = -1
    for needle in needles:
        next_index = svg.find(needle, cursor + 1)
        assert next_index >= 0, f"{needle!r} not found after index {cursor}"
        cursor = next_index


def test_valid_svg(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_via_drill_hole_is_in_drill_clip_without_mask_layers() -> None:
    """Derived realistic presets subtract via drills without legacy clip paths."""
    board = _make_board_with_inner_layers()
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert '<clipPath id="drill-clip"' not in svg
    assert 'data-role="realistic.substrate"' in svg
    assert 'data-role="realistic.solderMask"' in svg


def test_via_annular_rings_are_emitted_on_spanned_copper_layers() -> None:
    board = _make_board_with_inner_layers()
    settings = load_render_settings_json('{"extends": "phosphor:design"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="cad.copper.front" data-source-layers="F.Cu"' in svg
    assert 'data-role="cad.copper.inner.1" data-source-layers="In1.Cu"' in svg
    assert 'data-role="cad.copper.back" data-source-layers="B.Cu"' in svg
    assert 'data-source-layers="vias"' not in svg


def test_via_annular_rings_respect_selected_copper_layers() -> None:
    board = _make_board_with_inner_layers()
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.coveredCopper"' in svg
    assert 'data-source-layers="F.Cu"' in svg
    assert 'data-role="cad.copper.inner' not in svg


def test_simplified_high_contrast_svg_omits_unhighlighted_traces(board: Pcb) -> None:
    settings = load_render_settings_json('{"extends": "phosphor:simplified-high-contrast"}')
    svg = render_pcb_svg(
        board,
        side="front",
        width_px=1200,
        render_settings=settings,
    )
    assert 'class="trace ' not in svg
    assert 'class="zone ' not in svg
    assert 'data-role="cad.copper.front"' in svg


def test_back_side_plan_svg_does_not_use_top_level_mirror_transform(board: Pcb) -> None:
    settings = load_render_settings_json(
        '{"extends": "phosphor:simplified-high-contrast", '
        '"highlights": [{"pad": "TP3.1", "color": "#c00000"}]}'
    )

    svg = render_pcb_svg(
        board,
        side="back",
        width_px=1200,
        render_settings=settings,
    )

    assert "scale(-1" not in svg
    assert 'data-role="cad.copper.back"' in svg
    assert 'data-role="highlight.copper.back"' in svg


def test_simplified_high_contrast_svg_keeps_highlighted_trace_overlay(board: Pcb) -> None:
    settings = load_render_settings_json(
        '{"extends": "phosphor:simplified-high-contrast", '
        '"highlights": [{"net": "/SWDIO_TMS", "color": "#c00000"}]}'
    )
    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)
    assert 'class="highlight-overlay"' in svg
    assert 'data-role="highlight.copper.front"' in svg
    assert 'data-highlight-target="net:/SWDIO_TMS"' in svg


def test_render_settings_plan_path_does_not_duplicate_settings_highlights(
    board: Pcb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_render_settings_json(
        '{"extends": "phosphor:simplified-high-contrast", '
        '"highlights": [{"net": "/SWDIO_TMS", "color": "#c00000"}]}'
    )
    captured_highlights: list[list[HighlightSpec]] = []
    original_build_derived_render_plan = pcb_render_module.build_derived_render_plan

    def capture_build_derived_render_plan(
        board_arg: Pcb,
        *,
        settings: RenderSettings,
        side: str,
        width_px: int,
        annotations: ResolvedAnnotations | None,
    ) -> DerivedRenderPlan:
        captured_highlights.append(list(settings.highlights))
        return original_build_derived_render_plan(
            board_arg,
            settings=settings,
            side=side,
            width_px=width_px,
            annotations=annotations,
        )

    monkeypatch.setattr(
        pcb_render_module,
        "build_derived_render_plan",
        capture_build_derived_render_plan,
    )

    _ = render_pcb_svg(
        board,
        side="front",
        width_px=1200,
        render_settings=settings,
        highlight_specs=settings.highlights,
    )

    assert captured_highlights == [settings.highlights]


def test_structured_token_override_emits_direct_attributes(board: Pcb) -> None:
    settings = load_render_settings_json('{"extends": "phosphor:simplified-high-contrast"}')

    svg = render_pcb_svg(board, side="back", width_px=1200, render_settings=settings)

    assert 'data-role="cad.copper.front"' in svg
    assert 'style="fill: #111111' in svg


def test_structured_preset_colors_use_inline_style_so_css_does_not_override(
    board: Pcb,
) -> None:
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    substrate_pos = svg.index('data-role="realistic.substrate"')
    mask_pos = svg.index('data-role="realistic.solderMask"')
    copper_pos = svg.index('data-role="realistic.coveredCopper"')
    assert substrate_pos < mask_pos < copper_pos
    assert 'style="fill: #1a5c2a"' in svg
    assert 'style="fill: #1f7a3a"' in svg
    assert 'style="fill: #145222; opacity: 0.6000"' in svg


def test_clean_preset_enables_board_material(board: Pcb) -> None:
    settings = load_render_settings_json('{"extends": "phosphor:clean"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.substrate"' in svg
    assert 'style="fill: #1a5c2a"' in svg


def test_renderable_geometry_store_preserves_component_pad_metadata() -> None:
    board = _make_board_with_component()

    store = build_geometry_store(board, side="front")
    pad = next(item for item in store.items if item.kind == GeometryKind.PAD)

    assert pad.id.startswith("pad:")
    assert pad.layer.name == "F.Cu"
    assert pad.layer.role == "copper"
    assert pad.tags.component_ref == "U1"
    assert pad.tags.component_prefix == "U"
    assert pad.tags.pad_number == "1"
    assert pad.tags.net_name == "VCC"
    assert pad.source is not None


def test_renderable_geometry_store_includes_non_copper_geometry() -> None:
    board = _make_board_with_component()
    board.footprints[0].texts.append(
        PcbText(
            text="U1",
            x=10.0,
            y=8.0,
            rotation=0.0,
            layer="F.SilkS",
            font_size=0.5,
            kind="reference",
            footprint_ref="U1",
        )
    )

    store = build_geometry_store(board, side="front")
    kinds = {item.kind for item in store.items}

    assert GeometryKind.BOARD_MATERIAL in kinds
    assert GeometryKind.BOARD_OUTLINE in kinds
    assert GeometryKind.PAD in kinds
    assert GeometryKind.SILK_LINE in kinds
    assert GeometryKind.FAB_LINE in kinds
    assert GeometryKind.REF_TEXT in kinds


def test_geometry_selector_matches_net_component_pad_and_prefix() -> None:
    board = _make_board_with_component(ref="R1", lib="Resistor_SMD:R_0402", value="10k")
    store = build_geometry_store(board, side="front")
    pad = next(item for item in store.items if item.kind == GeometryKind.PAD)

    assert geometry_matches_selector(
        pad,
        GeometrySelector(kinds=frozenset({GeometryKind.PAD}), net_name="VCC"),
        active_side="front",
    )
    assert geometry_matches_selector(
        pad,
        GeometrySelector(component_ref="R1", pad_number="1"),
        active_side="front",
    )
    assert geometry_matches_selector(
        pad,
        GeometrySelector(component_prefixes=("R",)),
        active_side="front",
    )
    assert not geometry_matches_selector(
        pad,
        GeometrySelector(component_prefixes=("U",)),
        active_side="front",
    )


def test_geometry_selector_matches_active_opposite_and_inner_layers() -> None:
    board = _make_board_with_inner_layers()
    store = build_geometry_store(board, side="front")
    front_trace = next(
        item
        for item in store.items
        if item.kind == GeometryKind.TRACE and item.layer.name == "F.Cu"
    )
    back_trace = next(
        item
        for item in store.items
        if item.kind == GeometryKind.TRACE and item.layer.name == "B.Cu"
    )
    inner_trace = next(
        item
        for item in store.items
        if item.kind == GeometryKind.TRACE and item.layer.name == "In1.Cu"
    )

    assert geometry_matches_selector(
        front_trace,
        GeometrySelector(role="copper", side="active"),
        active_side="front",
    )
    assert geometry_matches_selector(
        back_trace,
        GeometrySelector(role="copper", side="opposite"),
        active_side="front",
    )
    assert geometry_matches_selector(
        inner_trace,
        GeometrySelector(role="copper", side="inner"),
        active_side="front",
    )


def test_design_preset_matches_legacy_core_colors_and_order() -> None:
    board = _make_board_with_inner_layers()
    board.polygons.append(
        PcbPolygon(
            points=[(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0)],
            layer="F.Cu",
            net_number=1,
            net_name="SIG",
        )
    )
    board.footprints[0].silkscreen_lines.append(
        PcbLine(4.0, 8.5, 6.0, 8.5, "F.SilkS", 0.12, footprint_ref="U1")
    )
    settings = load_render_settings_json('{"extends": "phosphor:design"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert "#c83434" in svg
    assert "#4d7fc4" in svg
    assert "#7fc87f" in svg
    assert 'data-role="cad.copper.front"' in svg
    assert 'style="fill: #c83434; opacity: 0.3500"' in svg
    assert "#ffffff" in svg
    assert 'data-type="body"' not in svg
    _assert_svg_contains_in_order(
        svg,
        [
            'data-source-layers="B.Cu"',
            'data-source-layers="In1.Cu"',
            'data-source-layers="F.Cu"',
        ],
    )


def test_design_preset_renders_board_outline_without_fill() -> None:
    board = _make_board_with_inner_layers()
    settings = load_render_settings_json('{"extends": "phosphor:design"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'class="board-material"' not in svg
    assert re.search(
        r'data-role="cad.edge".*style="fill: none; stroke: #d0d2cd; stroke-width: 0\.1500"',
        svg,
        re.DOTALL,
    )


def test_design_preset_cycles_inner_copper_colors() -> None:
    board = _make_board_with_inner_layers()
    board.layers.insert(2, PcbLayer("In2.Cu", LayerFunction.COPPER, side="", number=2))
    board.segments.append(PcbSegment(3.0, 12.0, 7.0, 12.0, 0.25, "In2.Cu", 1))
    board.polygons.append(
        PcbPolygon(
            points=[(3.0, 13.0), (7.0, 13.0), (7.0, 15.0), (3.0, 15.0)],
            layer="In2.Cu",
            net_number=1,
            net_name="SIG",
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:design"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    in1_start = svg.index('data-source-layers="In1.Cu"')
    in2_start = svg.index('data-source-layers="In2.Cu"')
    front_start = svg.index('data-source-layers="F.Cu"')
    in1_svg = svg[in1_start:in2_start]
    in2_svg = svg[in2_start:front_start]
    assert 'style="fill: #7fc87f; opacity: 0.3500"' in in1_svg
    assert 'style="fill: #7fc87f; opacity: 0.3500"' in in2_svg


def test_copper_layer_paints_traces_zones_then_pads() -> None:
    board = _make_board_with_inner_layers()
    board.polygons.append(
        PcbPolygon(
            points=[(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0)],
            layer="F.Cu",
            net_number=1,
            net_name="SIG",
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:design"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    front_layer_start = svg.index('data-source-layers="F.Cu"')
    next_layer_start = svg.find("<g data-role=", front_layer_start + 1)
    front_layer_svg = svg[
        front_layer_start : next_layer_start if next_layer_start != -1 else len(svg)
    ]
    assert front_layer_svg.startswith('data-source-layers="F.Cu"')
    assert front_layer_svg.count("<path") == 1


def test_cad_trace_bends_are_buffered_as_joined_linework() -> None:
    board = Pcb(
        name="trace-bend",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "SIG")},
        footprints=[],
        segments=[
            PcbSegment(5.0, 5.0, 6.0, 5.0, 0.2, "F.Cu", 1),
            PcbSegment(6.0, 5.0, 6.0, 6.0, 0.2, "F.Cu", 1),
        ],
        vias=[],
        outline_lines=[
            PcbLine(0, 0, 10, 0, "Edge.Cuts", 0.1),
            PcbLine(10, 0, 10, 10, "Edge.Cuts", 0.1),
            PcbLine(10, 10, 0, 10, "Edge.Cuts", 0.1),
            PcbLine(0, 10, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front", number=1),
            PcbLayer("Edge.Cuts", LayerFunction.EDGE),
        ],
    )
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "cad",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {"cad.layer[F.Cu].fill": "#ff6600"},
            }
        )
    )

    layers = build_cad_layers(
        build_geometry_store(board, side="front"),
        settings,
        warn=lambda _msg: None,
    )

    assert len(layers) == 1
    copper = layers[0].geometry
    assert copper.contains(Point(6.07, 4.93))
    assert not copper.contains(Point(6.095, 4.905))


def test_review_preset_matches_legacy_mask_without_body_context() -> None:
    board = _make_board_with_component()
    board.footprints[0].texts.append(
        PcbText(
            text="U1",
            x=10.0,
            y=8.0,
            rotation=0.0,
            layer="F.Fab",
            font_size=0.5,
            kind="reference",
            footprint_ref="U1",
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.substrate"' in svg
    assert 'data-role="realistic.coveredCopper"' in svg
    assert "#145222" in svg
    assert "#ffffff" in svg
    assert 'data-type="body"' not in svg


def test_review_zone_opacity_matches_trace_opacity() -> None:
    board = _make_board_with_inner_layers()
    board.polygons.append(
        PcbPolygon(
            points=[(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0)],
            layer="F.Cu",
            net_number=1,
            net_name="SIG",
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.coveredCopper"' in svg
    assert 'style="fill: #145222; opacity: 0.6000"' in svg


def test_clean_copper_opacity_matches_when_copper_is_visible() -> None:
    board = _make_board_with_inner_layers()
    board.polygons.append(
        PcbPolygon(
            points=[(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0)],
            layer="F.Cu",
            net_number=1,
            net_name="SIG",
        )
    )
    settings = load_render_settings_json(
        '{"extends": "phosphor:clean"}',
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.coveredCopper"' in svg
    assert 'style="fill: #31443a; opacity: 0.5500"' in svg


def test_high_contrast_copper_opacity_matches_trace_opacity() -> None:
    board = _make_board_with_inner_layers()
    board.polygons.append(
        PcbPolygon(
            points=[(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0)],
            layer="F.Cu",
            net_number=1,
            net_name="SIG",
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:high-contrast"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="cad.copper.front"' in svg
    assert 'style="fill: #111111"' in svg
    assert "opacity:" not in svg


def test_plan_path_renders_board_level_graphic_text() -> None:
    board = _make_board_with_component()
    board.graphic_texts.append(
        PcbGraphicText(
            text="ON",
            x=12.0,
            y=12.0,
            rotation=0.0,
            layer="F.SilkS",
            font_size=0.75,
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.silkscreen"' in svg
    assert "#ffffff" in svg


def test_review_preset_renders_reference_and_board_text_white() -> None:
    board = _make_board_with_component()
    board.graphic_texts.append(
        PcbGraphicText(
            text="ON",
            x=12.0,
            y=12.0,
            rotation=0.0,
            layer="F.SilkS",
            font_size=0.75,
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.silkscreen"' in svg
    assert "#ffffff" in svg


def test_style_projection_matches_svg_primitive_semantics() -> None:
    board = _make_board_with_component()
    board.footprints[0].silkscreen_polygons.append(
        PcbPolygon(
            points=[(12.0, 9.0), (13.0, 9.0), (13.0, 10.0), (12.0, 10.0)],
            layer="F.SilkS",
            footprint_ref="U1",
        )
    )
    board.graphic_texts.append(
        PcbGraphicText(
            text="ON",
            x=12.0,
            y=12.0,
            rotation=0.0,
            layer="F.SilkS",
            font_size=0.75,
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.silkscreen"' in svg
    assert 'style="fill: #ffffff"' in svg


def test_design_preset_renders_board_text_white() -> None:
    board = _make_board_with_component()
    board.graphic_texts.append(
        PcbGraphicText(
            text="ON",
            x=12.0,
            y=12.0,
            rotation=0.0,
            layer="F.SilkS",
            font_size=0.75,
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:design"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="cad.silkscreen.front"' in svg
    assert "#ffffff" in svg


def test_clean_preset_renders_silkscreen_white() -> None:
    board = _make_board_with_component()
    settings = load_render_settings_json('{"extends": "phosphor:clean"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-role="realistic.silkscreen"' in svg
    assert "#ffffff" in svg


@pytest.mark.parametrize("ref", ["R1", "C1", "L1", "TP1"])
def test_clean_preset_omits_passive_component_body_context(ref: str) -> None:
    board = _make_board_with_component(ref=ref)
    board.footprints[0].texts.append(
        PcbText(
            text=ref,
            x=10.0,
            y=8.0,
            rotation=0.0,
            layer="F.Fab",
            font_size=0.5,
            kind="reference",
            footprint_ref=ref,
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:clean"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-type="pad"' not in svg
    assert 'data-role="realistic.silkscreen"' not in svg
    assert 'data-type="body"' not in svg
    assert 'class="ref-text' not in svg


def test_clean_preset_omits_non_passive_component_body_context() -> None:
    board = _make_board_with_component(ref="U1")
    board.footprints[0].texts.append(
        PcbText(
            text="U1",
            x=10.0,
            y=8.0,
            rotation=0.0,
            layer="F.Fab",
            font_size=0.5,
            kind="reference",
            footprint_ref="U1",
        )
    )
    settings = load_render_settings_json('{"extends": "phosphor:clean"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-type="pad"' not in svg
    assert 'data-role="realistic.silkscreen"' in svg
    assert 'data-type="body"' not in svg
    assert 'class="ref-text' not in svg


def test_structured_highlight_colors_use_inline_style_so_css_does_not_override(
    board: Pcb,
) -> None:
    settings = load_render_settings_json(
        '{"extends": "phosphor:simplified-high-contrast", '
        '"highlights": ['
        '{"net": "/SWDIO_TMS", "color": "#0057b8"}'
        "]}"
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    overlay = svg[svg.index('class="highlight-overlay"') :]
    assert 'data-role="highlight.copper.front"' in overlay
    assert 'data-highlight-target="net:/SWDIO_TMS"' in overlay
    assert 'style="fill: #0057b8' in overlay


# ---------------------------------------------------------------------------
# Highlight + inner-layer visibility
# ---------------------------------------------------------------------------


def _make_board_with_inner_layers() -> Pcb:
    """Board with front, inner, and back copper plus traces on all three."""
    fp = PcbFootprint(
        reference="U1",
        footprint_lib="test",
        x=5.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        pads=[
            PcbPad(
                number="1",
                x=5.0,
                y=10.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=1,
                net_name="SIG",
                footprint_ref="U1",
            ),
        ],
        fab_lines=[
            PcbLine(4, 9, 6, 9, "F.Fab", 0.1),
            PcbLine(6, 9, 6, 11, "F.Fab", 0.1),
            PcbLine(6, 11, 4, 11, "F.Fab", 0.1),
            PcbLine(4, 11, 4, 9, "F.Fab", 0.1),
        ],
    )
    return Pcb(
        name="inner-test",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "SIG")},
        footprints=[fp],
        segments=[
            PcbSegment(5.0, 10.0, 10.0, 10.0, 0.25, "F.Cu", 1),
            PcbSegment(10.0, 10.0, 15.0, 10.0, 0.25, "In1.Cu", 1),
            PcbSegment(15.0, 10.0, 15.0, 5.0, 0.25, "B.Cu", 1),
        ],
        vias=[
            PcbVia(10.0, 10.0, 0.6, 0.3, ["F.Cu", "In1.Cu"], 1),
        ],
        outline_lines=[
            PcbLine(0, 0, 20, 0, "Edge.Cuts", 0.1),
            PcbLine(20, 0, 20, 20, "Edge.Cuts", 0.1),
            PcbLine(20, 20, 0, 20, "Edge.Cuts", 0.1),
            PcbLine(0, 20, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front"),
            PcbLayer("In1.Cu", LayerFunction.COPPER, side="", number=1),
            PcbLayer("B.Cu", LayerFunction.COPPER, side="back"),
            PcbLayer("F.SilkS", LayerFunction.SILKSCREEN, side="front"),
            PcbLayer("F.Fab", LayerFunction.FAB, side="front"),
        ],
    )


def _make_altium_style_render_board() -> Pcb:
    """Representative Altium-style board with named copper, mask, silk, and mechanical layers."""
    return Pcb(
        name="altium-style",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "GND"), 2: PcbNet(2, "SPI_CLK")},
        footprints=[
            PcbFootprint(
                reference="U1",
                footprint_lib="Package_SO:SOIC-8",
                x=8.0,
                y=8.0,
                rotation=0.0,
                layer="Top Layer",
                pads=[
                    PcbPad(
                        number="1",
                        x=8.0,
                        y=8.0,
                        width=1.1,
                        height=1.1,
                        shape="rect",
                        layers=["Top Layer"],
                        net_number=2,
                        net_name="SPI_CLK",
                        footprint_ref="U1",
                        mask_expansion=0.08,
                    ),
                    PcbPad(
                        number="2",
                        x=10.0,
                        y=8.0,
                        width=1.1,
                        height=1.1,
                        shape="rect",
                        layers=["Bottom Layer"],
                        net_number=1,
                        net_name="GND",
                        footprint_ref="U1",
                        mask_expansion=0.08,
                    ),
                ],
                silkscreen_lines=[
                    PcbLine(7.0, 7.0, 11.0, 7.0, "Top Overlay", 0.15, footprint_ref="U1"),
                ],
                texts=[
                    PcbText(
                        text="U1",
                        x=9.0,
                        y=6.5,
                        rotation=0.0,
                        layer="Top Overlay",
                        font_size=1.0,
                        kind="reference",
                        footprint_ref="U1",
                    )
                ],
            )
        ],
        segments=[
            PcbSegment(8.0, 8.0, 13.0, 8.0, 0.25, "Top Layer", 2),
            PcbSegment(13.0, 8.0, 13.0, 12.0, 0.25, "MidLayer1", 2),
            PcbSegment(10.0, 8.0, 13.0, 10.0, 0.25, "Bottom Layer", 1),
        ],
        vias=[PcbVia(13.0, 8.0, 0.65, 0.3, ["Top Layer", "MidLayer1"], 2)],
        polygons=[
            PcbPolygon(
                [(7.3, 7.3), (8.7, 7.3), (8.7, 8.7), (7.3, 8.7)],
                "Top Solder",
            ),
            PcbPolygon(
                [(9.3, 7.3), (10.7, 7.3), (10.7, 8.7), (9.3, 8.7)],
                "Bottom Solder",
            ),
            PcbPolygon(
                [(2.0, 2.0), (5.0, 2.0), (5.0, 3.0), (2.0, 3.0)],
                "Mechanical 13",
            ),
        ],
        outline_lines=[
            PcbLine(0, 0, 20, 0, "Board Shape", 0.1),
            PcbLine(20, 0, 20, 16, "Board Shape", 0.1),
            PcbLine(20, 16, 0, 16, "Board Shape", 0.1),
            PcbLine(0, 16, 0, 0, "Board Shape", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("Top Layer", LayerFunction.COPPER, side="front", number=1),
            PcbLayer("MidLayer1", LayerFunction.COPPER, number=2),
            PcbLayer("Bottom Layer", LayerFunction.COPPER, side="back", number=32),
            PcbLayer("Top Solder", LayerFunction.SOLDER_MASK, side="front"),
            PcbLayer("Bottom Solder", LayerFunction.SOLDER_MASK, side="back"),
            PcbLayer("Top Overlay", LayerFunction.SILKSCREEN, side="front"),
            PcbLayer("Mechanical 13", LayerFunction.MECHANICAL, number=69),
            PcbLayer("Board Shape", LayerFunction.EDGE),
        ],
    )


def test_default_render_does_not_hide_inner_copper_with_css() -> None:
    """Layer omission is no longer driven by theme display rules."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board)
    assert "display: none" not in svg


def test_highlight_does_not_emit_visibility_restore_css() -> None:
    """Highlights no longer restore hidden groups through CSS."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, highlight_nets=["SIG"])
    assert "display: inline !important" not in svg


def test_highlight_overlay_includes_pads() -> None:
    """Highlighted pads appear in the overlay group."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, highlight_nets=["SIG"])
    overlay_start = svg.index('class="highlight-overlay"')
    overlay = svg[overlay_start:]
    assert "pad" in overlay


def test_highlight_overlay_includes_zones() -> None:
    """Highlighted zones appear in the overlay group."""
    board = _make_board_with_inner_layers()
    # Add a zone on the SIG net so there's something to find
    board.polygons.append(
        PcbPolygon(
            points=[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)],
            layer="F.Cu",
            net_number=1,
            net_name="SIG",
        )
    )
    svg = render_pcb_svg(board, highlight_nets=["SIG"])
    overlay_start = svg.index('class="highlight-overlay"')
    overlay = svg[overlay_start:]
    assert "zone" in overlay


def test_no_highlight_overlay_without_highlights() -> None:
    """No overlay group when no highlights are active."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board)
    assert 'class="highlight-overlay"' not in svg


def test_no_visibility_override_without_highlights() -> None:
    """Without highlights, no display overrides are emitted."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board)
    assert "display: inline !important" not in svg


# ---------------------------------------------------------------------------
# Side tests
# ---------------------------------------------------------------------------


def test_front_no_mirror(board: Pcb) -> None:
    svg = render_pcb_svg(board, side="front")
    assert "scale(-1" not in svg


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unknown_net_no_error(board: Pcb) -> None:
    svg = render_pcb_svg(board, highlight_nets=["NONEXISTENT_NET_XYZ"])
    assert svg.startswith("<svg")


def test_unknown_component_no_error(board: Pcb) -> None:
    svg = render_pcb_svg(board, highlight_components=["NONEXISTENT"])
    assert svg.startswith("<svg")


# ---------------------------------------------------------------------------
# OrangeCrab integration
# ---------------------------------------------------------------------------


def test_orangecrab_renders(orangecrab_board: Pcb) -> None:
    svg = render_pcb_svg(orangecrab_board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


# ---------------------------------------------------------------------------
# XML escaping (_fmt_attrs)
# ---------------------------------------------------------------------------


def test_fmt_attrs_escapes_special_chars() -> None:
    """Attribute values with quotes, ampersands, and angle brackets are escaped."""
    attrs = {"data-x": 'he said "hi" & <bye>'}
    result = _fmt_attrs(attrs)
    assert "&quot;" in result
    assert "&amp;" in result
    assert "&lt;" in result


def test_fmt_attrs_empty() -> None:
    assert _fmt_attrs(None) == ""
    assert _fmt_attrs({}) == ""


# ---------------------------------------------------------------------------
# 3D model metadata in SVG
# ---------------------------------------------------------------------------


def _make_board_with_models(
    models: list[PcbModel3D],
    *,
    fab_lines: list[PcbLine] | None = None,
) -> Pcb:
    """Create a minimal board with one footprint carrying the given models."""
    fp = PcbFootprint(
        reference="U1",
        footprint_lib="test",
        x=10.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        models_3d=models,
        fab_lines=fab_lines or [],
    )
    return Pcb(
        name="test",
        nets={0: PcbNet(0, "")},
        footprints=[fp],
        segments=[],
        vias=[],
        outline_lines=[
            PcbLine(0, 0, 20, 0, "Edge.Cuts", 0.1),
            PcbLine(20, 0, 20, 20, "Edge.Cuts", 0.1),
            PcbLine(20, 20, 0, 20, "Edge.Cuts", 0.1),
            PcbLine(0, 20, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
    )


def test_no_data_models_when_empty() -> None:
    """Footprint with no models → no data-models attribute."""
    fab_line = PcbLine(9, 9, 11, 9, "F.Fab", 0.1)
    board = _make_board_with_models([], fab_lines=[fab_line])
    svg = render_pcb_svg(board)
    assert "data-models" not in svg


def test_no_data_models_when_no_cache_key() -> None:
    """Models without cache_key are excluded from data-models."""
    model = PcbModel3D(source="test.step", cache_key="")
    fab_line = PcbLine(9, 9, 11, 9, "F.Fab", 0.1)
    board = _make_board_with_models([model], fab_lines=[fab_line])
    svg = render_pcb_svg(board)
    assert "data-models" not in svg


# ---------------------------------------------------------------------------
# Component metadata attributes (data-footprint-lib, data-value)
# ---------------------------------------------------------------------------


def _make_board_with_component(
    *,
    ref: str = "U1",
    lib: str = "Package_SO:SOIC-8",
    value: str = "SN74LVC2G66",
) -> Pcb:
    """Board with one footprint that has lib/value metadata and a pad + fab line."""
    fp = PcbFootprint(
        reference=ref,
        footprint_lib=lib,
        x=10.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        value=value,
        pads=[
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
                footprint_ref=ref,
            )
        ],
        silkscreen_lines=[
            PcbLine(9, 9, 11, 9, "F.SilkS", 0.12, footprint_ref=ref),
        ],
        fab_lines=[
            PcbLine(9, 9, 11, 9, "F.Fab", 0.1, footprint_ref=ref),
            PcbLine(11, 9, 11, 11, "F.Fab", 0.1, footprint_ref=ref),
            PcbLine(11, 11, 9, 11, "F.Fab", 0.1, footprint_ref=ref),
            PcbLine(9, 11, 9, 9, "F.Fab", 0.1, footprint_ref=ref),
        ],
        texts=[],
    )
    return Pcb(
        name="test",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "VCC")},
        footprints=[fp],
        segments=[],
        vias=[],
        outline_lines=[
            PcbLine(0, 0, 20, 0, "Edge.Cuts", 0.1),
            PcbLine(20, 0, 20, 20, "Edge.Cuts", 0.1),
            PcbLine(20, 20, 0, 20, "Edge.Cuts", 0.1),
            PcbLine(0, 20, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front"),
            PcbLayer("F.SilkS", LayerFunction.SILKSCREEN, side="front"),
            PcbLayer("F.Fab", LayerFunction.FAB, side="front"),
        ],
    )


def test_no_lib_attr_when_empty() -> None:
    """No data-footprint-lib if the footprint has no lib string."""
    board = _make_board_with_component(lib="", value="")
    svg = render_pcb_svg(board)
    assert "data-footprint-lib" not in svg
    assert "data-value" not in svg


# ---------------------------------------------------------------------------
# Component metadata JSON block
# ---------------------------------------------------------------------------


def test_pcb_metadata_json_block() -> None:
    """SVG contains a JSON metadata block with component lib/value info."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert '<script type="application/json" id="pcb-metadata">' in svg
    match = re.search(
        r'<script type="application/json" id="pcb-metadata">\n(.*?)\n</script>',
        svg,
        re.DOTALL,
    )
    assert match is not None
    parsed = json.loads(match.group(1))
    assert "U1" in parsed
    assert parsed["U1"]["lib"] == "Package_SO:SOIC-8"
    assert parsed["U1"]["value"] == "SN74LVC2G66"


def test_no_metadata_when_no_lib_or_value() -> None:
    """No metadata block if all footprints lack lib and value."""
    board = _make_board_with_component(lib="", value="")
    svg = render_pcb_svg(board)
    assert "pcb-metadata" not in svg


def test_metadata_json_escapes_script_terminators() -> None:
    board = _make_board_with_component(value='</script><script>alert("x")</script>')
    svg = render_pcb_svg(board)

    assert '</script><script>alert("x")</script>' not in svg
    assert "\\u003c/script\\u003e\\u003cscript\\u003e" in svg


# ---------------------------------------------------------------------------
# Custom CSS injection
# ---------------------------------------------------------------------------


def test_custom_css_injected() -> None:
    """Custom CSS appears in a dedicated <style id="custom"> block."""
    board = _make_board_with_component()
    css = ".board-fill { fill: purple; }"
    svg = render_pcb_svg(board, custom_css=css)
    assert '<style id="custom">' in svg
    assert "fill: purple;" in svg


def test_custom_css_escapes_style_terminators() -> None:
    board = _make_board_with_component()
    svg = render_pcb_svg(board, custom_css='.x { color: red; } </style><script>alert("x")</script>')

    assert '</style><script>alert("x")</script>' not in svg
    assert '<\\/style><script>alert("x")</script>' in svg


def test_custom_css_not_present_when_empty() -> None:
    """No custom style block when no custom CSS provided."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert '<style id="custom">' not in svg


# ---------------------------------------------------------------------------
# Real-fixture render integration (swd_switch)
# ---------------------------------------------------------------------------


def test_swd_switch_metadata_has_lib(board: Pcb) -> None:
    """The pcb-metadata JSON block should include entries with non-empty lib."""
    svg = render_pcb_svg(board)
    match = re.search(
        r'<script type="application/json" id="pcb-metadata">\n(.*?)\n</script>',
        svg,
        re.DOTALL,
    )
    assert match is not None
    parsed = json.loads(match.group(1))
    libs = [v["lib"] for v in parsed.values() if v.get("lib")]
    assert len(libs) >= 3


# ---------------------------------------------------------------------------
# Annotation rendering
# ---------------------------------------------------------------------------


def _make_resolved_box() -> ResolvedBox:
    return ResolvedBox(
        x=9.0,
        y=9.0,
        width=4.0,
        height=4.0,
        label_text="MCU",
        label_x=25.0,
        label_y=9.5,
        label_width=6.0,
        label_height=2.0,
        text_anchor="start",
        connector_path=[(25.0, 10.5), (22.0, 10.5), (22.0, 11.0), (11.0, 11.0)],
        color="rgba(255,107,53,0.9)",
    )


def _make_resolved_pointer() -> ResolvedPointer:
    return ResolvedPointer(
        target_x=10.0,
        target_y=10.0,
        label_text="Clock",
        label_x=25.0,
        label_y=13.0,
        label_width=7.0,
        label_height=2.0,
        text_anchor="end",
        connector_path=[(25.0, 14.0), (22.0, 14.0), (22.0, 10.0), (10.0, 10.0)],
        color="rgba(255,107,53,0.9)",
    )


def _make_resolved_legend() -> ResolvedLegend:
    return ResolvedLegend(
        title="SPI Signals",
        entries=[
            LegendEntry(color="#4488ff", label="SCLK"),
            LegendEntry(color="#e8922e", label="MOSI"),
        ],
        x=5.0,
        y=22.0,
        width=10.0,
        height=4.0,
    )


def _make_annotations(
    *,
    boxes: bool = False,
    pointers: bool = False,
    legend: bool = False,
    labels: bool = False,
) -> ResolvedAnnotations:
    return ResolvedAnnotations(
        boxes=[_make_resolved_box()] if boxes else [],
        pointers=[_make_resolved_pointer()] if pointers else [],
        labels=[
            ResolvedLabel(
                label_text="Main MCU",
                label_x=25.0,
                label_y=16.0,
                label_width=8.0,
                label_height=2.0,
                text_anchor="start",
                connector_path=[(25.0, 17.0), (22.0, 17.0), (22.0, 10.0), (10.0, 10.0)],
            )
        ]
        if labels
        else [],
        legend=_make_resolved_legend() if legend else None,
        font_size=1.0,
        content_bbox=(5.0, 5.0, 33.0, 26.0),
    )


def test_annotation_box_rendered() -> None:
    """SVG should contain annotation box rect and pill label."""
    board = _make_board_with_component()
    annotations = _make_annotations(boxes=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert 'class="annotation-box"' in svg
    assert "annotation-pill" in svg
    assert "MCU" in svg
    assert 'text-anchor="start"' in svg


def test_annotation_pointer_rendered() -> None:
    """SVG should contain connector path and pill label."""
    board = _make_board_with_component()
    annotations = _make_annotations(pointers=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert "annotation-connector" in svg
    assert 'text-anchor="end"' in svg
    assert "annotation-dot" in svg
    assert "Clock" in svg


def test_annotation_legend_rendered() -> None:
    """SVG should contain legend box with title and entries."""
    board = _make_board_with_component()
    annotations = _make_annotations(legend=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert "legend-bg" in svg
    assert "SPI Signals" in svg
    assert "SCLK" in svg
    assert "#4488ff" in svg


def test_annotation_legend_text_only_entry() -> None:
    """Legend entries without a color should render text without a swatch."""
    board = _make_board_with_component()
    legend = ResolvedLegend(
        title="Notes",
        entries=[
            LegendEntry(color="", label="Bypass caps within 5mm"),
            LegendEntry(color="#ff0000", label="CLK"),
        ],
        x=5.0,
        y=22.0,
        width=10.0,
        height=4.0,
    )
    annotations = ResolvedAnnotations(
        boxes=[],
        pointers=[],
        labels=[],
        legend=legend,
        font_size=1.0,
        px_scale=0.025,
        content_bbox=(0, 0, 20, 20),
    )
    svg = render_pcb_svg(board, annotations=annotations)
    assert "Bypass caps within 5mm" in svg
    assert "CLK" in svg
    assert "#ff0000" in svg
    # Only one swatch rect (for CLK), none for the text-only entry
    assert svg.count("fill: #ff0000") == 1


def test_annotation_label_with_connector() -> None:
    """Label annotation should have a connector and the label content."""
    board = _make_board_with_component()
    annotations = _make_annotations(labels=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert "annotation-connector" in svg
    assert 'text-anchor="start"' in svg
    assert "Main MCU" in svg


def test_no_annotations_no_group() -> None:
    """Without annotations, no annotation group or style block should appear."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert "annotations" not in svg or "pcb-metadata" in svg
    assert '<style id="annotations">' not in svg


def test_derived_cad_svg_groups_by_role_source_layers_and_style(board: Pcb) -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "cad",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {
                    "cad.layer[F.Cu].fill": "#ff6600",
                    "cad.copper.front.opacity": 0.75,
                },
            }
        )
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert '<g data-role="cad.copper.front" data-source-layers="F.Cu"' in svg
    assert 'style="fill: #ff6600; opacity: 0.7500"' in svg
    assert 'data-source-ids="' in svg
    assert "board-clip" not in svg
    assert "drill-clip" not in svg


def test_derived_realistic_svg_uses_derived_path(board: Pcb) -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "realistic",
                "side": "front",
                "source": {
                    "layers": [
                        {"match": {"function": "copper", "side": "front"}},
                        {"match": {"name": "Edge.Cuts"}},
                    ]
                },
                "tokens": {
                    "realistic.substrate.fill": "#244426",
                    "realistic.solderMask.fill": "#0f5f32",
                    "realistic.coveredCopper.fill": "#9a6924",
                    "realistic.exposedCopper.fill": "#d6a13d",
                    "realistic.silkscreen.fill": "#ffffff",
                    "realistic.boardOutline.fill": "none",
                    "realistic.boardOutline.stroke": "#111111",
                    "realistic.boardOutline.strokeWidthMm": 0.08,
                },
            }
        )
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert '<g data-role="realistic.substrate"' in svg
    assert '<g data-role="realistic.boardOutline"' in svg
    assert "board-clip" not in svg
    assert "drill-clip" not in svg


def test_derived_serializer_does_not_use_raw_source_kind_branches(
    board: Pcb,
) -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "cad",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {"cad.layer[F.Cu].fill": "#ff6600"},
            }
        )
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert not hasattr(pcb_render_module, "_render_plan_item")
    assert '<g data-role="cad.copper.front"' in svg


def test_derived_serializer_renders_annotations(board: Pcb) -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "cad",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {"cad.layer[F.Cu].fill": "#ff6600"},
            }
        )
    )

    svg = render_pcb_svg(
        board,
        side="front",
        width_px=1200,
        annotations=_make_annotations(labels=True),
        render_settings=settings,
    )

    assert '<style id="annotations">' in svg
    assert "annotation-connector" in svg
    assert "Main MCU" in svg


def test_derived_renderer_preserves_component_metadata_block() -> None:
    board = _make_board_with_component()
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "cad",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {"cad.layer[F.Cu].fill": "#ff6600"},
            }
        )
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)
    match = re.search(
        r'<script type="application/json" id="pcb-metadata">\n(.*?)\n</script>',
        svg,
        re.DOTALL,
    )

    assert match is not None
    assert '"U1":{"lib":"Package_SO:SOIC-8","value":"SN74LVC2G66"}' in match.group(1)


def test_derived_renderer_escapes_metadata_script_terminators() -> None:
    board = _make_board_with_component(value='</script><script>alert("x")</script>')
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "cad",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {"cad.layer[F.Cu].fill": "#ff6600"},
            }
        )
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert '</script><script>alert("x")</script>' not in svg
    assert "\\u003c/script\\u003e\\u003cscript\\u003e" in svg


def test_derived_renderer_escapes_custom_css_style_terminators() -> None:
    board = _make_board_with_component()
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "cad",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {"cad.layer[F.Cu].fill": "#ff6600"},
                "custom_css": '.x { color: red; } </STYLE ><script>alert("x")</script>',
            }
        )
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert '</STYLE ><script>alert("x")</script>' not in svg
    assert '<\\/style><script>alert("x")</script>' in svg


def test_derived_renderer_escapes_annotation_css_style_terminators() -> None:
    plan = DerivedRenderPlan(
        view_box=ViewBox(0.0, 0.0, 10.0, 10.0),
        width_px=100,
        height_px=100,
        base_layers=(),
        highlight_groups=(),
        annotations=_make_annotations(labels=True),
        annotation_style={"label": {"fill": '</style><script>alert("x")</script>'}},
        warnings=(),
    )

    svg = pcb_render_module.render_pcb_svg_from_derived_plan(plan)
    style_block = re.search(r'<style id="annotations">(.*?)</style>', svg, re.DOTALL)

    assert style_block is not None
    assert '</style><script>alert("x")</script>' not in style_block.group(1)
    assert '<\\/style><script>alert("x")</script>' in style_block.group(1)


def test_derived_serializer_converts_polygons_multipolygons_holes_and_empty_geometry() -> None:
    plan = DerivedRenderPlan(
        view_box=ViewBox(0.0, 0.0, 10.0, 10.0),
        width_px=100,
        height_px=100,
        base_layers=(
            DerivedLayer(
                id="cad:copper:front",
                role=VisualRole(namespace="cad", function="copper", side="front"),
                geometry=Polygon(
                    shell=[(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)],
                    holes=[[(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]],
                ),
                source_layers=("F.Cu",),
                source_ids=("pad-1",),
                style=ResolvedStyle(fill="#ff6600"),
            ),
            DerivedLayer(
                id="cad:mask:front",
                role=VisualRole(namespace="cad", function="mask", side="front"),
                geometry=MultiPolygon(
                    [
                        Polygon([(5.0, 0.0), (6.0, 0.0), (6.0, 1.0), (5.0, 1.0)]),
                        Polygon([(7.0, 0.0), (8.0, 0.0), (8.0, 1.0), (7.0, 1.0)]),
                    ]
                ),
                source_layers=("F.Mask",),
                source_ids=("mask-1", "mask-2"),
                style=ResolvedStyle(fill="#008800"),
            ),
            DerivedLayer(
                id="cad:empty:front",
                role=VisualRole(namespace="cad", function="empty", side="front"),
                geometry=GeometryCollection(),
                source_layers=("Empty",),
                source_ids=(),
                style=ResolvedStyle(fill="#000000"),
            ),
        ),
        highlight_groups=(),
        annotations=None,
        warnings=(),
    )

    svg = pcb_render_module.render_pcb_svg_from_derived_plan(plan)

    assert 'data-role="cad.copper.front"' in svg
    assert 'fill-rule="evenodd"' in svg
    assert "M 0.0000 0.0000 L 4.0000 0.0000" in svg
    assert "M 1.0000 1.0000 L 2.0000 1.0000" in svg
    assert 'data-role="cad.mask.front"' in svg
    assert "M 5.0000 0.0000 L 6.0000 0.0000" in svg
    assert "M 7.0000 0.0000 L 8.0000 0.0000" in svg
    assert 'data-role="cad.empty.front"' not in svg


def test_annotation_css_present() -> None:
    """Annotation CSS block appears when annotations are provided."""
    board = _make_board_with_component()
    annotations = _make_annotations(boxes=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert '<style id="annotations">' in svg
    assert ".annotation-box" in svg


def test_viewbox_expands_for_annotations() -> None:
    """ViewBox should expand to include off-board annotation content."""
    board = _make_board_with_component()
    # Default viewBox is around (0,0)-(20,20) with 2mm padding
    svg_default = render_pcb_svg(board)
    # Annotation with content below the board
    annotations = ResolvedAnnotations(
        content_bbox=(-10.0, -10.0, 30.0, 40.0),
    )
    svg_annotated = render_pcb_svg(board, annotations=annotations)
    # Extract viewBox values
    vb_default = re.search(r'viewBox="([^"]+)"', svg_default)
    vb_annotated = re.search(r'viewBox="([^"]+)"', svg_annotated)
    assert vb_default is not None and vb_annotated is not None
    # Annotated viewBox should be larger
    def_vals = [float(x) for x in vb_default.group(1).split()]
    ann_vals = [float(x) for x in vb_annotated.group(1).split()]
    # Width and height should be larger
    assert ann_vals[2] > def_vals[2] or ann_vals[3] > def_vals[3]


def test_no_foreign_object() -> None:
    """Pure SVG rendering should not use foreignObject."""
    board = _make_board_with_component()
    annotations = _make_annotations(boxes=True, pointers=True, legend=True, labels=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert "<foreignObject" not in svg


# ---------------------------------------------------------------------------
# End-to-end: parse + resolve + render on real fixture
# ---------------------------------------------------------------------------


def test_swd_switch_annotation_end_to_end(board: Pcb) -> None:
    """Full annotation pipeline on a real board: parse → resolve → render."""
    from phosphor_eda.pcb_annotations import parse_annotations, resolve_annotations

    data = {
        "boxes": [{"targets": ["D1"], "label": "Status LED"}],
        "pointers": [{"target": "TP3", "label": "SWD Enable"}],
    }
    spec = parse_annotations(data)
    resolved = resolve_annotations(spec, board, "front")
    svg = render_pcb_svg(board, annotations=resolved)
    assert 'class="annotation-box"' in svg
    assert "Status LED" in svg
    assert "annotation-connector" in svg
    assert "SWD Enable" in svg


def test_annotation_label_style_rule_emits_halo_css_or_attrs(board: Pcb) -> None:
    from phosphor_eda.pcb_annotations import parse_annotations, resolve_annotations

    settings = load_render_settings_json(
        json.dumps(
            {
                "extends": "phosphor:simplified-high-contrast",
                "annotations": {"pointers": [{"target": "TP3.1", "label": "SWD"}]},
            }
        )
    )
    spec = parse_annotations(settings.annotations)
    annotations = resolve_annotations(
        spec,
        board,
        "front",
        width_px=800,
        font_size=settings.font_size,
    )

    svg = render_pcb_svg(board, annotations=annotations, render_settings=settings)

    assert "font-size: 40.0px" in svg
    assert ".annotation-label-text" in svg
    assert ".annotation-connector" in svg
    assert settings.tokens["annotation.label.fill"] == "#000"
    assert settings.tokens["annotation.label.pillVisible"] is False
    assert settings.tokens["annotation.connector.dotVisible"] is False


def test_resolve_annotations_uses_requested_font_size(board: Pcb) -> None:
    """Annotation resolution should size label layout from requested display px."""
    from phosphor_eda.pcb_annotations import parse_annotations, resolve_annotations

    spec = parse_annotations({"pointers": [{"target": "TP3", "label": "SWD"}]})
    default_annotations = resolve_annotations(spec, board, "front")
    large_annotations = resolve_annotations(spec, board, "front", font_size=24)

    assert default_annotations.font_size == 10.0
    assert large_annotations.font_size == 24.0
    assert large_annotations.pointers[0].label_width > default_annotations.pointers[0].label_width


# ---------------------------------------------------------------------------
# parse_render_settings
# ---------------------------------------------------------------------------


class TestParseRenderSettings:
    def test_render_settings_schema_is_json_schema_object(self) -> None:
        schema = render_settings_schema()
        properties = _as_object_dict(schema["properties"])
        custom_css = _as_object_dict(properties["custom_css"])
        assert isinstance(schema["$schema"], str)
        assert schema["$schema"].startswith("https://json-schema.org/")
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert custom_css["type"] == "string"

    def test_render_settings_schema_is_v2_without_theme(self) -> None:
        schema = render_settings_schema()
        props = _as_object_dict(schema["properties"])
        assert "theme" not in props
        assert "font_size" not in props
        assert "font_size_px" not in props
        assert "include" not in props
        assert "highlight_behavior" not in props
        assert "style_rules" not in props
        assert "fontSizePx" in props
        assert "source" in props
        assert "tokens" in props
        assert "custom_css" in props
        assert "phosphor:simplified-high-contrast" in json.dumps(schema["examples"])
        example = _as_object_dict(_as_object_list(schema["examples"])[0])
        assert "source" in example
        assert "tokens" in example
        assert "dimming" in example
        assert "font_size_px" not in example
        assert "include" not in example
        assert "highlight_behavior" not in example
        assert "style_rules" not in example
        assert "custom_css" in example

    def test_empty_object(self) -> None:
        settings = parse_render_settings({})
        assert settings.side == ""
        assert settings.width == 0
        assert settings.font_size == 0.0
        assert settings.highlights == []
        assert settings.source.layers == []
        assert settings.tokens == {}
        assert settings.dimming.enabled is False
        assert settings.annotations == {}
        assert settings.custom_css == ""

    def test_render_settings_rejects_theme(self) -> None:
        with pytest.raises(ValueError, match="theme"):
            parse_render_settings({"theme": "print"})

    @pytest.mark.parametrize("render_mode", ["cad", "realistic"])
    def test_render_settings_accepts_render_mode(self, render_mode: str) -> None:
        settings = parse_render_settings({"renderMode": render_mode})
        assert settings.render_mode == render_mode

    def test_render_settings_rejects_unknown_render_mode(self) -> None:
        with pytest.raises(ValueError, match="renderMode"):
            parse_render_settings({"renderMode": "xray"})

    def test_render_settings_accepts_source_layer_rules(self) -> None:
        settings = parse_render_settings(
            {
                "source": {
                    "layers": [
                        {
                            "match": {"function": "copper", "side": "active"},
                            "visible": True,
                            "objects": ["pads", "traces"],
                        },
                        {
                            "match": {"name": "Mechanical 13"},
                            "visible": False,
                        },
                    ],
                    "excludeComponents": ["R", "C"],
                }
            }
        )

        assert settings.source.layers[0].match.function == "copper"
        assert settings.source.layers[0].match.side == "active"
        assert settings.source.layers[0].visible is True
        assert settings.source.layers[0].objects == ("pads", "traces")
        assert settings.source.layers[1].match.name == "Mechanical 13"
        assert settings.source.layers[1].visible is False
        assert settings.source.layers[1].objects == ()
        assert settings.source.exclude_components == ("R", "C")

    def test_render_settings_accepts_dot_and_native_layer_tokens(self) -> None:
        settings = parse_render_settings(
            {
                "tokens": {
                    "cad.copper.front.fill": "#d17a22",
                    "cad.layer[F.Cu].fill": "#ff0000",
                    "highlight.copper.front.opacity": 0.85,
                }
            }
        )

        assert settings.tokens["cad.copper.front.fill"] == "#d17a22"
        assert settings.tokens["cad.layer[F.Cu].fill"] == "#ff0000"
        assert settings.tokens["highlight.copper.front.opacity"] == 0.85

    def test_render_settings_accepts_font_size_px_camel_case(self) -> None:
        settings = parse_render_settings({"fontSizePx": 72})
        assert settings.font_size == 72

    def test_render_settings_accepts_annotations_highlights_and_dimming(self) -> None:
        settings = parse_render_settings(
            {
                "annotations": {"pointers": [{"target": "J1", "label": "USB interface"}]},
                "highlights": [{"net": "SPI_CLK", "color": "#ff3b30"}],
                "dimming": {"enabled": True},
            }
        )

        assert settings.annotations == {"pointers": [{"target": "J1", "label": "USB interface"}]}
        assert settings.highlights == [HighlightSpec(net="SPI_CLK", color="#ff3b30")]
        assert settings.dimming.enabled is True

    @pytest.mark.parametrize(
        "legacy_key",
        ["font_size_px", "include", "highlight_behavior", "style_rules"],
    )
    def test_render_settings_rejects_legacy_settings_keys(self, legacy_key: str) -> None:
        with pytest.raises(ValueError, match=legacy_key):
            parse_render_settings({legacy_key: {}})

    def test_all_fields(self) -> None:
        data: dict[str, object] = {
            "side": "back",
            "width": 1200,
            "fontSizePx": 24,
            "renderMode": "cad",
            "source": {
                "layers": [{"match": {"function": "copper"}, "objects": ["pads"]}],
                "excludeComponents": ["R", "C"],
            },
            "tokens": {
                "cad.copper.front.fill": "#d17a22",
                "highlight.copper.front.fill": "#ff8a00",
            },
            "dimming": {"enabled": True},
            "highlights": [
                {"net": "VBUS", "color": "#ff0000"},
                {"component": "U1"},
                {"pad": "CN11.30", "color": "#00ff00"},
            ],
            "annotations": {"boxes": [{"targets": ["U1"], "label": "MCU"}]},
            "custom_css": ".board-fill { fill: red; }",
        }
        settings = parse_render_settings(data)
        assert settings.side == "back"
        assert settings.width == 1200
        assert settings.font_size == 24.0
        assert settings.render_mode == "cad"
        assert settings.source.layers[0].match.function == "copper"
        assert settings.source.layers[0].objects == ("pads",)
        assert settings.source.exclude_components == ("R", "C")
        assert settings.tokens["cad.copper.front.fill"] == "#d17a22"
        assert settings.dimming.enabled is True
        assert len(settings.highlights) == 3
        assert settings.highlights[0].net == "VBUS"
        assert settings.highlights[0].color == "#ff0000"
        assert settings.highlights[1].component == "U1"
        assert settings.highlights[1].color == ""
        assert settings.highlights[2].pad == "CN11.30"
        assert settings.highlights[2].color == "#00ff00"
        assert settings.annotations == data["annotations"]
        assert settings.custom_css == ".board-fill { fill: red; }"

    def test_invalid_side(self) -> None:
        with pytest.raises(ValueError, match="side"):
            parse_render_settings({"side": "top"})

    def test_invalid_width(self) -> None:
        with pytest.raises(ValueError, match="width"):
            parse_render_settings({"width": -10})

    def test_invalid_font_size(self) -> None:
        with pytest.raises(ValueError, match="font_size"):
            parse_render_settings({"font_size": 24})

        with pytest.raises(ValueError, match="fontSizePx"):
            parse_render_settings({"fontSizePx": 0})

        with pytest.raises(ValueError, match="fontSizePx"):
            parse_render_settings({"fontSizePx": True})

        with pytest.raises(ValueError, match="fontSizePx"):
            parse_render_settings({"fontSizePx": 501})

    @pytest.mark.parametrize("font_size", [math.nan, math.inf, -math.inf])
    def test_invalid_font_size_must_be_finite(self, font_size: float) -> None:
        with pytest.raises(ValueError, match="fontSizePx"):
            parse_render_settings({"fontSizePx": font_size})

    def test_highlight_missing_net_and_component(self) -> None:
        with pytest.raises(ValueError, match="exactly one of 'net', 'component', or 'pad'"):
            parse_render_settings({"highlights": [{"color": "#ff0000"}]})

    def test_highlight_both_net_and_component(self) -> None:
        with pytest.raises(ValueError, match="exactly one of 'net', 'component', or 'pad'"):
            parse_render_settings({"highlights": [{"net": "GND", "component": "U1"}]})

    def test_highlight_rejects_multiple_targets_including_pad(self) -> None:
        with pytest.raises(ValueError, match="exactly one of 'net', 'component', or 'pad'"):
            parse_render_settings({"highlights": [{"component": "U1", "pad": "U1.1"}]})

    def test_highlight_rejects_invalid_pad_target(self) -> None:
        with pytest.raises(ValueError, match=r"highlights\[0\]\.pad must be '<component>\.<pad>'"):
            parse_render_settings({"highlights": [{"pad": "U1"}]})

    def test_highlights_not_array(self) -> None:
        with pytest.raises(ValueError, match="highlights must be an array"):
            parse_render_settings({"highlights": "GND"})

    def test_annotations_not_object(self) -> None:
        with pytest.raises(ValueError, match="annotations must be an object"):
            parse_render_settings({"annotations": "bad"})

    def test_custom_css_not_string(self) -> None:
        with pytest.raises(ValueError, match="custom_css must be a string"):
            parse_render_settings({"custom_css": 42})

    def test_extends_not_string(self) -> None:
        with pytest.raises(ValueError, match="extends must be a string"):
            parse_render_settings({"extends": 42})

    def test_width_rejects_bool(self) -> None:
        with pytest.raises(ValueError, match="width must be a positive integer"):
            parse_render_settings({"width": True})

    def test_highlight_field_rejects_non_string(self) -> None:
        with pytest.raises(ValueError, match=r"highlights\[0\]\.net must be a string"):
            parse_render_settings({"highlights": [{"net": 42}]})

        with pytest.raises(ValueError, match=r"highlights\[0\]\.pad must be a string"):
            parse_render_settings({"highlights": [{"pad": 42}]})


# ---------------------------------------------------------------------------
# render settings extends
# ---------------------------------------------------------------------------


def test_load_render_settings_file_extends_local_file(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    base.write_text(
        json.dumps(
            {
                "width": 1200,
                "fontSizePx": 48,
                "custom_css": ".base { color: black; }",
                "annotations": {"legend": {"title": "Base", "entries": []}},
            }
        )
    )
    child = tmp_path / "child.json"
    child.write_text(
        json.dumps(
            {
                "extends": "./base.json",
                "width": 2400,
                "custom_css": ".child { color: red; }",
                "highlights": [{"pad": "TP3.1", "color": "#c00000"}],
                "annotations": {"pointers": [{"target": "TP3.1", "label": "SWD"}]},
            }
        )
    )

    settings = load_render_settings_file(child)

    assert settings.width == 2400
    assert settings.font_size == 48
    assert settings.custom_css == ".base { color: black; }\n.child { color: red; }"
    assert settings.highlights == [HighlightSpec(pad="TP3.1", color="#c00000")]
    assert settings.annotations == {
        "legend": {"title": "Base", "entries": []},
        "pointers": [{"target": "TP3.1", "label": "SWD"}],
    }


def test_load_render_settings_file_extends_packaged_settings(tmp_path: Path) -> None:
    child = tmp_path / "child.json"
    child.write_text(
        json.dumps(
            {
                "extends": "phosphor:simplified-high-contrast",
                "fontSizePx": 72,
                "custom_css": ".annotation-connector { stroke-width: 6; }",
            }
        )
    )

    settings = load_render_settings_file(child)

    assert settings.font_size == 72
    assert settings.custom_css == ".annotation-connector { stroke-width: 6; }"
    assert settings.source.layers
    assert settings.tokens


def test_bundled_simplified_high_contrast_uses_v2_settings(tmp_path: Path) -> None:
    child = tmp_path / "child.json"
    child.write_text(json.dumps({"extends": "phosphor:simplified-high-contrast"}))

    settings = load_render_settings_file(child)

    assert settings.font_size == 40
    assert settings.source.layers
    assert settings.tokens


@pytest.mark.parametrize(
    "name",
    BUILT_IN_DERIVED_PRESETS,
)
def test_bundled_render_settings_use_v2_settings(name: str) -> None:
    settings = load_render_settings_json(json.dumps({"extends": f"phosphor:{name}"}))

    assert settings.source.layers
    assert settings.tokens


@pytest.mark.parametrize("name", CANONICAL_DERIVED_PRESET_MODES)
def test_canonical_bundled_render_settings_do_not_use_legacy_policy_keys(name: str) -> None:
    preset_path = (
        Path(__file__).parents[1] / "src" / "phosphor_eda" / "render_settings" / f"{name}.json"
    )
    raw_settings = _as_object_dict(json.loads(preset_path.read_text()))

    assert "renderMode" in raw_settings
    assert "source" in raw_settings
    assert "tokens" in raw_settings
    assert "include" not in raw_settings
    assert "style_rules" not in raw_settings


@pytest.mark.parametrize(("name", "render_mode"), CANONICAL_DERIVED_PRESET_MODES.items())
def test_bundled_render_settings_use_expected_render_modes(name: str, render_mode: str) -> None:
    settings = load_render_settings_json(json.dumps({"extends": f"phosphor:{name}"}))

    assert settings.render_mode == render_mode


@pytest.mark.parametrize("name", ["high-contrast", "simplified-high-contrast"])
def test_high_contrast_presets_limit_surface_layers_to_active_side(name: str) -> None:
    settings = load_render_settings_json(json.dumps({"extends": f"phosphor:{name}"}))
    rules_by_function = {
        rule.match.function: rule
        for rule in settings.source.layers
        if rule.match.function in {"silkscreen", "fab", "mechanical"}
    }

    assert rules_by_function["silkscreen"].match.side == "active"
    assert rules_by_function["silkscreen"].objects == ("silk", "board_graphic_text")
    assert rules_by_function["fab"].match.side == "active"
    assert rules_by_function["mechanical"].objects == ("mechanical",)


@pytest.mark.parametrize("name", BUILT_IN_DERIVED_PRESETS)
def test_bundled_render_settings_resolve_required_tokens_for_representative_boards(
    name: str,
    orangecrab_board: Pcb,
) -> None:
    altium_style_board = _make_altium_style_render_board()
    settings = load_render_settings_json(
        json.dumps(
            {
                "extends": f"phosphor:{name}",
                "highlights": [{"net": "SPI_CLK", "color": "#c00000"}],
            }
        )
    )

    orangecrab_svg = render_pcb_svg(
        orangecrab_board,
        side="front",
        width_px=1200,
        render_settings=settings,
    )
    altium_svg = render_pcb_svg(
        altium_style_board,
        side="front",
        width_px=1200,
        render_settings=settings,
    )

    assert 'data-role="' in orangecrab_svg
    assert 'data-role="' in altium_svg
    assert "highlight.copper" in orangecrab_svg or "highlight.copper" in altium_svg


@pytest.mark.parametrize("settings_json", PIMX8_GALLERY_SMOKE_SETTINGS)
def test_pimx8_gallery_render_cases_resolve_bundled_preset_tokens(
    settings_json: dict[str, object],
    pimx8_board: Pcb,
) -> None:
    settings = load_render_settings_json(json.dumps(settings_json))

    svg = render_pcb_svg(
        pimx8_board,
        side="front",
        width_px=400,
        render_settings=settings,
    )

    assert 'data-role="' in svg


@pytest.mark.parametrize("name", BUILT_IN_DERIVED_PRESETS)
def test_bundled_render_settings_do_not_enable_highlight_halo_by_default(name: str) -> None:
    settings = load_render_settings_json(json.dumps({"extends": f"phosphor:{name}"}))
    highlight_stroke_tokens = {
        key: value
        for key, value in settings.tokens.items()
        if key.startswith("highlight.") and key.endswith(".stroke")
    }
    highlight_stroke_width_tokens = {
        key: value
        for key, value in settings.tokens.items()
        if key.startswith("highlight.") and key.endswith(".strokeWidthMm")
    }

    assert highlight_stroke_tokens
    assert all(value == "none" for value in highlight_stroke_tokens.values())
    assert highlight_stroke_width_tokens
    assert all(value == 0 for value in highlight_stroke_width_tokens.values())


def test_simplified_high_contrast_extends_high_contrast_tokens() -> None:
    settings = load_render_settings_json('{"extends": "phosphor:simplified-high-contrast"}')

    assert settings.font_size == 40
    assert settings.render_mode == "cad"
    assert settings.tokens["cad.edge.fill"] == "none"
    assert settings.tokens["annotation.label.pillVisible"] is False
    assert settings.tokens["annotation.connector.dotVisible"] is False


def test_render_settings_extends_merges_v2_policy(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    base.write_text(
        json.dumps(
            {
                "source": {
                    "layers": [
                        {"match": {"function": "copper"}, "objects": ["pads"]},
                        {"match": {"function": "silkscreen", "side": "front"}},
                    ],
                    "excludeComponents": ["R"],
                },
                "tokens": {
                    "cad.copper.front.fill": "#111111",
                    "annotation.label.fill": "#000000",
                },
                "dimming": {"enabled": False},
            }
        )
    )
    child = tmp_path / "child.json"
    child.write_text(
        json.dumps(
            {
                "extends": "./base.json",
                "source": {
                    "layers": [{"match": {"name": "Mechanical 13"}}],
                    "excludeComponents": ["C"],
                },
                "tokens": {
                    "cad.copper.front.fill": "#222222",
                    "highlight.copper.front.fill": "#ff8a00",
                },
                "dimming": {"enabled": True},
            }
        )
    )

    settings = load_render_settings_file(child)

    assert [rule.match.name for rule in settings.source.layers] == ["Mechanical 13"]
    assert settings.source.exclude_components == ("C",)
    assert settings.tokens == {
        "cad.copper.front.fill": "#222222",
        "annotation.label.fill": "#000000",
        "highlight.copper.front.fill": "#ff8a00",
    }
    assert settings.dimming.enabled is True


def test_load_render_settings_file_detects_extend_cycles(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"extends": "./second.json"}))
    second.write_text(json.dumps({"extends": "./first.json"}))

    with pytest.raises(ValueError, match="cycle"):
        load_render_settings_file(first)


def test_render_settings_schema_documents_extends() -> None:
    schema = render_settings_schema()
    properties = _as_object_dict(schema["properties"])
    extends = _as_object_dict(properties["extends"])

    assert extends["type"] == "string"
    assert "phosphor:simplified-high-contrast" in json.dumps(schema["examples"])
