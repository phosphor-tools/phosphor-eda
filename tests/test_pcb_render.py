"""Tests for the PCB SVG renderer — structural and CSS assertions."""

import json
import math
import re
from pathlib import Path

import pytest

import phosphor_eda.pcb_render as pcb_render_module
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
    _cmp_class,  # pyright: ignore[reportPrivateUsage]
    _css_safe,  # pyright: ignore[reportPrivateUsage]
    _fmt_attrs,  # pyright: ignore[reportPrivateUsage]
    load_render_settings_file,
    load_render_settings_json,
    parse_render_settings,
    render_pcb_svg,
    render_settings_schema,
)
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometrySelector,
    build_geometry_store,
    geometry_matches_selector,
)

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"
ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"


@pytest.fixture(scope="module")
def board() -> Pcb:
    return parse_kicad_pcb(FIXTURE)


@pytest.fixture(scope="module")
def orangecrab_board() -> Pcb:
    return parse_kicad_pcb(ORANGECRAB_FIXTURE)


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


def test_has_base_style(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert '<style id="base">' in svg


def test_base_plan_svg_css_has_no_paint_defaults(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    base_css = svg[svg.index('<style id="base">') : svg.index("</style>")]
    assert "fill:" not in base_css
    assert "stroke:" not in base_css
    assert ".trace, .trace-arc" in base_css


def test_has_board_clip(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert "board-clip" in svg


def test_has_drill_clip(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert "drill-clip" in svg


def test_has_copper_layer_groups(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert 'data-layer="F.Cu"' in svg
    assert 'data-layer="B.Cu"' in svg


def test_layer_paint_order(board: Pcb) -> None:
    """B.Cu should appear before F.Cu in document order (painter's model)."""
    svg = render_pcb_svg(board)
    assert svg.index('data-layer="B.Cu"') < svg.index('data-layer="F.Cu"')


def test_silk_after_copper(board: Pcb) -> None:
    """Silkscreen layer group appears after copper layer groups."""
    svg = render_pcb_svg(board)
    fcu_pos = svg.index('data-layer="F.Cu"')
    silk_names = ["F.SilkS", "F.Silkscreen", "B.SilkS", "B.Silkscreen"]
    found = False
    for name in silk_names:
        marker = f'data-layer="{name}"'
        if marker in svg:
            assert svg.index(marker) > fcu_pos
            found = True
    assert found, "No silkscreen layer group found"


# ---------------------------------------------------------------------------
# Data attribute tests
# ---------------------------------------------------------------------------


def test_pad_attributes(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="pad"' in svg
    assert "data-component=" in svg
    assert "data-pad=" in svg
    assert "data-net=" in svg


def test_trace_attributes(board: Pcb) -> None:
    """All traces are always present (visibility controlled via CSS)."""
    svg = render_pcb_svg(board)
    assert 'data-type="trace"' in svg
    assert "data-net-number=" in svg


def test_via_attributes(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="via"' in svg
    assert re.search(r'class="via\b', svg)


def test_via_annular_ring_uses_size() -> None:
    """Annular ring radius should be via.size / 2, not drill / 2 + constant."""
    fp = PcbFootprint(
        reference="U1",
        footprint_lib="test",
        x=5.0,
        y=5.0,
        rotation=0.0,
        layer="F.Cu",
        pads=[
            PcbPad(
                number="1",
                x=5.0,
                y=5.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=1,
                net_name="SIG",
                footprint_ref="U1",
            )
        ],
        fab_lines=[PcbLine(4, 4, 6, 4, "F.Fab", 0.1)],
    )
    board = Pcb(
        name="via-size-test",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "SIG")},
        footprints=[fp],
        segments=[PcbSegment(5.0, 5.0, 10.0, 5.0, 0.25, "F.Cu", 1)],
        vias=[PcbVia(10.0, 5.0, size=0.8, drill=0.4, layers=["F.Cu", "B.Cu"], net_number=1)],
        outline_lines=[
            PcbLine(0, 0, 15, 0, "Edge.Cuts", 0.1),
            PcbLine(15, 0, 15, 10, "Edge.Cuts", 0.1),
            PcbLine(15, 10, 0, 10, "Edge.Cuts", 0.1),
            PcbLine(0, 10, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front"),
            PcbLayer("B.Cu", LayerFunction.COPPER, side="back"),
            PcbLayer("F.Fab", LayerFunction.FAB, side="front"),
        ],
    )
    svg = render_pcb_svg(board)
    # Annular ring radius should be size/2 = 0.4, not drill/2 + 0.05 = 0.25
    assert 'r="0.4000"' in svg
    assert 'r="0.2500"' not in svg  # old hardcoded formula


def test_via_drill_hole_is_in_drill_clip_without_mask_layers() -> None:
    """Physical via drills should clip the board even when via.layers are copper layers."""
    board = _make_board_with_inner_layers()
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    drill_start = svg.index('<clipPath id="drill-clip"')
    drill_clip = svg[drill_start : svg.index("</clipPath>", drill_start)]
    assert "M 9.8500 10.0000 A 0.1500 0.1500" in drill_clip


def test_via_annular_rings_are_emitted_on_spanned_copper_layers() -> None:
    board = _make_board_with_inner_layers()
    settings = load_render_settings_json('{"extends": "phosphor:design"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    def layer_svg(name: str) -> str:
        start = svg.index(f'data-layer="{name}"')
        next_layer = svg.find('<g data-layer="', start + 1)
        end = next_layer if next_layer != -1 else len(svg)
        return svg[start:end]

    front_layer = layer_svg("F.Cu")
    inner_layer = layer_svg("In1.Cu")
    back_layer = layer_svg("B.Cu")
    assert 'data-type="via"' in front_layer
    assert 'class="annular" style="fill: #c83434"' in front_layer
    assert 'data-type="via"' in inner_layer
    assert 'class="annular" style="fill: #7fc87f"' in inner_layer
    assert 'data-type="via"' not in back_layer
    assert 'data-layer="vias"' not in svg


def test_via_annular_rings_respect_selected_copper_layers() -> None:
    board = _make_board_with_inner_layers()
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'data-layer="F.Cu"' in svg
    assert 'data-type="via"' in svg[svg.index('data-layer="F.Cu"') :]
    assert 'data-layer="In1.Cu"' not in svg
    assert 'data-layer="B.Cu"' not in svg


def test_zone_attributes(board: Pcb) -> None:
    """swd_switch has zones on inner copper layers."""
    svg = render_pcb_svg(board)
    assert 'data-type="zone"' in svg


def test_component_body_attributes(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="body"' in svg


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
    assert 'data-type="pad"' in svg


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
    assert 'data-component="TP3"' in svg
    assert 'cx="118.5000" cy="64.5000"' in svg
    assert 'cx="93.5000" cy="64.5000"' not in svg


def test_simplified_high_contrast_svg_keeps_highlighted_trace_overlay(board: Pcb) -> None:
    settings = load_render_settings_json(
        '{"extends": "phosphor:simplified-high-contrast", '
        '"highlights": [{"net": "/SWDIO_TMS", "color": "#c00000"}]}'
    )
    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)
    assert 'class="highlight-overlay"' in svg
    assert 'data-net="/SWDIO_TMS"' in svg


def test_render_settings_plan_path_does_not_duplicate_settings_highlights(
    board: Pcb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_render_settings_json(
        '{"extends": "phosphor:simplified-high-contrast", '
        '"highlights": [{"net": "/SWDIO_TMS", "color": "#c00000"}]}'
    )
    captured_highlights: list[list[HighlightSpec]] = []
    original_build_render_plan = pcb_render_module.build_render_plan

    def capture_build_render_plan(
        board_arg: Pcb,
        *,
        settings: RenderSettings,
        side: str,
        width_px: int,
    ) -> pcb_render_module.PcbRenderPlan:
        captured_highlights.append(list(settings.highlights))
        return original_build_render_plan(
            board_arg,
            settings=settings,
            side=side,
            width_px=width_px,
        )

    monkeypatch.setattr(pcb_render_module, "build_render_plan", capture_build_render_plan)

    _ = render_pcb_svg(
        board,
        side="front",
        width_px=1200,
        render_settings=settings,
        highlight_specs=settings.highlights,
    )

    assert captured_highlights == [settings.highlights]


def test_structured_style_rule_emits_direct_attributes(board: Pcb) -> None:
    settings = load_render_settings_json(
        '{"extends": "phosphor:simplified-high-contrast", '
        '"style_rules": ['
        '{"match": {"pad": "TP3.1"}, '
        '"style": {"fill": "#123456", "pad_expansion_mm": 0.25}}'
        "]}",
    )

    svg = render_pcb_svg(board, side="back", width_px=1200, render_settings=settings)

    assert 'style="fill: #123456' in svg


def test_structured_preset_colors_use_inline_style_so_css_does_not_override(
    board: Pcb,
) -> None:
    settings = load_render_settings_json('{"extends": "phosphor:review"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    material_pos = svg.index('class="board-material"')
    mask_pos = svg.index('class="board-fill"')
    assert material_pos < mask_pos
    assert '<g clip-path="url(#drill-clip)">\n<path' in svg[:material_pos]
    assert 'class="board-material" style="fill: #1a5c2a; stroke: none"' in svg
    assert 'class="board-fill" style="fill: #1a5c2a; stroke: #1a5c2a"' in svg
    assert 'class="pad nn-1" style="fill: #b87333; opacity: 0.6000"' in svg
    assert 'class="pad nn-1" style="fill: #b87333; stroke:' not in svg
    assert 'class="trace nn-1" style="stroke: #145222; opacity: 0.6000"' in svg
    assert 'class="annular" style="fill: #b87333"' in svg
    assert 'class="annular" style="fill: #b87333; stroke:' not in svg


def test_clean_preset_enables_board_material(board: Pcb) -> None:
    settings = load_render_settings_json('{"extends": "phosphor:clean"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'class="board-material" style="fill: #1a5c2a; stroke: none"' in svg
    assert 'class="board-fill" style="fill: #1a5c2a; stroke: #1a5c2a"' in svg


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
    assert re.search(r'class="trace nn-1" style="stroke: #c83434; opacity: 0\.3500"', svg)
    assert re.search(r'class="pad nn-1" style="fill: #c83434; opacity: 0\.3500"', svg)
    assert re.search(r'class="zone nn-1" style="fill: #c83434; opacity: 0\.3500"', svg)
    assert "#ffffff" in svg
    assert 'data-type="body"' not in svg
    _assert_svg_contains_in_order(
        svg,
        [
            'data-layer="B.Cu"',
            'data-layer="In1.Cu"',
            'data-layer="F.Cu"',
        ],
    )


def test_design_preset_renders_board_outline_without_fill() -> None:
    board = _make_board_with_inner_layers()
    settings = load_render_settings_json('{"extends": "phosphor:design"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'class="board-material"' not in svg
    assert re.search(
        r'class="board-fill" style="fill: none; stroke: #d0d2cd; stroke-width: 0\.1500"',
        svg,
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

    in1_start = svg.index('data-layer="In1.Cu"')
    in2_start = svg.index('data-layer="In2.Cu"')
    front_start = svg.index('data-layer="F.Cu"')
    in1_svg = svg[in1_start:in2_start]
    in2_svg = svg[in2_start:front_start]
    assert 'class="trace nn-1" style="stroke: #7fc87f; opacity: 0.3500"' in in1_svg
    assert 'class="annular" style="fill: #7fc87f"' in in1_svg
    assert 'class="trace nn-1" style="stroke: #ce7d2c; opacity: 0.3500"' in in2_svg
    assert 'class="zone nn-1" style="fill: #ce7d2c; opacity: 0.3500"' in in2_svg


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

    front_layer_start = svg.index('data-layer="F.Cu"')
    next_layer_start = svg.find('<g data-layer="', front_layer_start + 1)
    front_layer_svg = svg[
        front_layer_start : next_layer_start if next_layer_start != -1 else len(svg)
    ]
    _assert_svg_contains_in_order(
        front_layer_svg,
        [
            'class="trace',
            'class="zone',
            'class="pad',
        ],
    )


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

    assert 'class="board-material" style="fill: #1a5c2a; stroke: none"' in svg
    assert 'class="pad nn-1' in svg
    assert "#b87333" in svg
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

    assert 'class="trace nn-1" style="stroke: #145222; opacity: 0.6000"' in svg
    assert 'class="pad nn-1" style="fill: #b87333; opacity: 0.6000"' in svg
    assert re.search(r'class="zone nn-1" style="fill: #145222; opacity: 0.6000"', svg)


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
        '{"extends": "phosphor:clean", '
        '"include": {"layers": ['
        '{"role": "copper", "side": "active", '
        '"objects": {"pads": "visible", "traces": "visible", "zones": "visible"}}'
        "]}}",
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'class="trace nn-1" style="stroke: #145222; opacity: 0.6000"' in svg
    assert 'class="pad nn-1" style="fill: #31443a; opacity: 0.6000"' in svg
    assert re.search(r'class="zone nn-1" style="fill: #145222; opacity: 0.6000"', svg)


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

    assert 'class="trace nn-1" style="stroke: #222"' in svg
    assert re.search(r'class="pad nn-1" style="fill: #111"', svg)
    assert re.search(r'class="zone nn-1" style="fill: #777"', svg)
    assert not re.search(r'class="pad nn-1" style="[^"]*opacity:', svg)
    assert not re.search(r'class="zone nn-1" style="[^"]*opacity:', svg)


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

    assert 'data-type="board_graphic_text"' in svg
    assert ">ON</text>" in svg


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

    assert 'class="user-text" style="fill: #ffffff"' in svg


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

    assert re.search(r'<line [^>]*class="silk" style="[^"]*stroke: #ffffff', svg)
    assert not re.search(r'<line [^>]*class="silk" style="[^"]*fill:', svg)
    assert re.search(r'<polygon [^>]*class="silk" style="fill: #ffffff"', svg)
    assert not re.search(r'<polygon [^>]*class="silk" style="[^"]*stroke:', svg)
    assert not re.search(r'<text [^>]*style="[^"]*stroke: #[^";]+', svg)


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

    assert 'class="user-text" style="fill: #ffffff"' in svg


def test_clean_preset_renders_silkscreen_white() -> None:
    board = _make_board_with_component()
    settings = load_render_settings_json('{"extends": "phosphor:clean"}')

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    assert 'class="silk" style="stroke: #ffffff"' in svg


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

    assert 'class="board-material" style="fill: #1a5c2a; stroke: none"' in svg
    assert 'data-type="pad"' not in svg
    assert 'class="silk' in svg
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
    assert 'class="silk' in svg
    assert 'data-type="body"' not in svg
    assert 'class="ref-text' not in svg


def test_structured_highlight_colors_use_inline_style_so_css_does_not_override(
    board: Pcb,
) -> None:
    settings = load_render_settings_json(
        '{"extends": "phosphor:review", '
        '"highlights": ['
        '{"net": "/SWDIO_TMS", "color": "#0057b8"}, '
        '{"component": "U1", "color": "#c00000"}'
        "]}"
    )

    svg = render_pcb_svg(board, side="front", width_px=1200, render_settings=settings)

    overlay = svg[svg.index('<g class="highlight-overlay">') :]
    assert 'class="trace nn-4 highlight" style="stroke: #0057b8' in overlay
    assert 'data-component="U1"' in overlay
    assert 'style="fill: #c00000' in overlay


# ---------------------------------------------------------------------------
# Highlight tests
# ---------------------------------------------------------------------------


def test_highlight_adds_style(board: Pcb) -> None:
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert '<style id="highlight">' in svg


def test_highlight_css_targets_net(board: Pcb) -> None:
    """VCC elements have data-net-number="1" attr (CSS uses .nn-1 class)."""
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert 'data-net-number="1"' in svg


def test_highlight_component_restores_by_ref(board: Pcb) -> None:
    """Component highlight restores elements by cmp-{ref} class."""
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    assert 'data-component="TP3"' in svg
    assert "Restore highlighted components" in svg


def test_highlight_component_does_not_highlight_nets(board: Pcb) -> None:
    """-c alone should not produce net-number restore rules."""
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    assert "Restore highlighted nets" not in svg


def test_no_highlight_without_args(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert '<style id="highlight">' not in svg


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


def test_highlight_overlay_renders_above_zones() -> None:
    """Highlighted traces are in an overlay group that paints after all layers."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, highlight_nets=["SIG"])
    # Overlay group exists and contains highlighted traces
    assert 'class="highlight-overlay"' in svg
    assert "nn-1" in svg  # net 1 = SIG
    # The overlay group element appears AFTER the fab layer groups
    overlay_pos = svg.index('<g class="highlight-overlay">')
    last_fab = svg.rindex('data-layer="F.Fab"')
    assert overlay_pos > last_fab


def test_highlight_overlay_contains_all_layers() -> None:
    """Overlay includes traces from every copper layer the highlighted net touches."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, highlight_nets=["SIG"])
    overlay_start = svg.index('class="highlight-overlay"')
    overlay = svg[overlay_start:]
    # SIG net has segments on F.Cu, In1.Cu, and B.Cu
    assert "data-layer" not in overlay or "trace" in overlay
    # Verify traces from all three layers appear in overlay
    assert overlay.count("nn-1") >= 3  # at least one trace per layer + via + pad


def test_highlight_overlay_includes_vias() -> None:
    """Highlighted vias appear in the overlay group."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, highlight_nets=["SIG"])
    overlay_start = svg.index('class="highlight-overlay"')
    overlay = svg[overlay_start:]
    assert "annular" in overlay


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


def test_component_highlight_overlay_renders_pads_on_top() -> None:
    """Component highlights re-render matching pads in the top overlay."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, highlight_components=["U1"])
    overlay_start = svg.index('class="highlight-overlay"')
    last_fab = svg.rindex('data-layer="F.Fab"')
    assert overlay_start > last_fab

    content_clip_end = svg.rindex("</g>", 0, svg.rindex("</svg>"))
    overlay = svg[overlay_start:content_clip_end]
    assert 'data-type="pad"' in overlay
    assert 'data-component="U1"' in overlay
    assert 'data-pad="1"' in overlay
    assert 'data-type="body"' not in overlay


def test_component_highlight_overlay_renders_after_ref_text() -> None:
    """Component highlight overlay paints after normal board ref text labels."""
    board = _make_board_with_inner_layers()
    board.footprints[0].texts.append(
        PcbText(
            text="U1",
            x=5.0,
            y=8.0,
            rotation=0.0,
            layer="F.Fab",
            font_size=0.5,
            kind="reference",
            footprint_ref="U1",
        )
    )

    svg = render_pcb_svg(board, highlight_components=["U1"])

    ref_text_start = svg.index('class="ref-text')
    overlay_start = svg.index('class="highlight-overlay"')
    assert overlay_start > ref_text_start


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


def test_back_mirror(board: Pcb) -> None:
    svg = render_pcb_svg(board, side="back")
    assert "scale(-1" in svg


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


def test_both_highlight_types(board: Pcb) -> None:
    svg = render_pcb_svg(board, highlight_nets=["GND"], highlight_components=["TP3"])
    assert svg.startswith("<svg")
    assert "TP3" in svg
    assert '<style id="highlight">' in svg


# ---------------------------------------------------------------------------
# OrangeCrab integration
# ---------------------------------------------------------------------------


def test_orangecrab_renders(orangecrab_board: Pcb) -> None:
    svg = render_pcb_svg(orangecrab_board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_orangecrab_has_zones(orangecrab_board: Pcb) -> None:
    svg = render_pcb_svg(orangecrab_board)
    assert 'data-type="zone"' in svg


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


def test_body_group_has_data_models() -> None:
    """Footprint with a cached model gets data-models on the body group."""
    model = PcbModel3D(
        source="test.step",
        offset=(1.0, 2.0, 3.0),
        rotation=(0.0, 0.0, 90.0),
        scale=(1.0, 1.0, 1.0),
        cache_key="abc123",
    )
    fab_line = PcbLine(9, 9, 11, 9, "F.Fab", 0.1)
    board = _make_board_with_models([model], fab_lines=[fab_line])
    svg = render_pcb_svg(board)
    assert "data-models=" in svg


def test_data_models_json_valid() -> None:
    """The data-models attribute contains valid JSON with the expected schema."""
    model = PcbModel3D(
        source="test.step",
        offset=(1.0, 2.0, 3.0),
        rotation=(0.0, 0.0, 90.0),
        scale=(1.0, 1.0, 1.0),
        cache_key="abc123",
    )
    fab_line = PcbLine(9, 9, 11, 9, "F.Fab", 0.1)
    board = _make_board_with_models([model], fab_lines=[fab_line])
    svg = render_pcb_svg(board)

    # Extract the data-models attribute value (XML-escaped JSON)
    match = re.search(r'data-models="([^"]*)"', svg)
    assert match is not None
    # The value is XML-escaped, but since we use compact JSON with no quotes
    # in values, the main escaping is &quot; for the JSON internal quotes.
    raw = match.group(1).replace("&quot;", '"').replace("&amp;", "&")
    parsed = json.loads(raw)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["key"] == "abc123"
    assert parsed[0]["offset"] == [1.0, 2.0, 3.0]
    assert parsed[0]["rotation"] == [0.0, 0.0, 90.0]
    assert parsed[0]["scale"] == [1.0, 1.0, 1.0]


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


def test_model_only_footprint_gets_body_group() -> None:
    """Footprint with 3D model but no fab geometry still gets a body group."""
    model = PcbModel3D(source="test.step", cache_key="def456")
    board = _make_board_with_models([model])
    svg = render_pcb_svg(board)
    assert "data-models=" in svg
    assert 'data-component="U1"' in svg


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


def test_pad_has_footprint_lib_and_value() -> None:
    """Pads carry data-footprint-lib and data-value from the footprint."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert 'data-footprint-lib="Package_SO:SOIC-8"' in svg
    assert 'data-value="SN74LVC2G66"' in svg


def test_silk_has_footprint_lib() -> None:
    """Silkscreen lines with a footprint_ref carry lib/value attributes."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    # Silk lines should have component attrs
    silk_pattern = re.compile(r'class="silk[^"]*"[^/]*data-footprint-lib="Package_SO:SOIC-8"')
    assert silk_pattern.search(svg)


def test_body_group_has_lib_and_value() -> None:
    """Body group <g> carries data-footprint-lib and data-value."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    body_pattern = re.compile(r'data-type="body"[^>]*data-footprint-lib="Package_SO:SOIC-8"')
    assert body_pattern.search(svg)


def test_ref_text_has_lib_and_value() -> None:
    """Ref text labels carry data-footprint-lib and data-value."""
    from phosphor_eda.pcb import PcbText

    board = _make_board_with_component()
    # Add a visible ref text so it renders
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
    svg = render_pcb_svg(board)
    ref_pattern = re.compile(r'class="ref-text[^"]*"[^>]*data-footprint-lib="Package_SO:SOIC-8"')
    assert ref_pattern.search(svg)


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


def test_custom_css_not_present_when_empty() -> None:
    """No custom style block when no custom CSS provided."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert '<style id="custom">' not in svg


# ---------------------------------------------------------------------------
# Real-fixture render integration (swd_switch)
# ---------------------------------------------------------------------------


def test_swd_switch_has_footprint_lib_attr(board: Pcb) -> None:
    """Rendered SVG should contain data-footprint-lib for real footprints."""
    svg = render_pcb_svg(board)
    assert "data-footprint-lib=" in svg


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


def test_swd_switch_has_data_value(board: Pcb) -> None:
    """At least some elements should carry data-value for components with values."""
    svg = render_pcb_svg(board)
    assert "data-value=" in svg


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


def test_back_side_annotations_not_mirrored() -> None:
    """Annotations should render outside the mirror group."""
    board = _make_board_with_component()
    annotations = _make_annotations(boxes=True)
    svg = render_pcb_svg(board, side="back", annotations=annotations)
    # The annotation group element should appear after the mirror group
    scale_pos = svg.index("scale(-1")
    # Look for the <g class="annotations"> group, not the CSS class name
    annotation_group_pos = svg.index('class="annotations"')
    assert annotation_group_pos > scale_pos


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
    assert "stroke-linejoin: round" in svg
    assert ".annotation-label-text" in svg
    assert "stroke: #fff" in svg
    assert "stroke-width: 4.0px" in svg
    assert "fill: #000" in svg
    assert ".annotation-pill { stroke: none; display: none; }" in svg
    assert ".annotation-connector" in svg
    assert "stroke: #333" in svg
    assert "stroke-width: 4.0px" in svg
    assert ".annotation-dot { display: none; }" in svg


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
        assert isinstance(schema["$schema"], str)
        assert schema["$schema"].startswith("https://json-schema.org/")
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["custom_css"]["type"] == "string"

    def test_render_settings_schema_is_v2_without_theme(self) -> None:
        schema = render_settings_schema()
        props = schema["properties"]
        assert "theme" not in props
        assert "font_size" not in props
        assert "font_size_px" in props
        assert "include" in props
        assert "highlight_behavior" in props
        assert "style_rules" in props
        assert "custom_css" in props
        assert "phosphor:simplified-high-contrast" in json.dumps(schema["examples"])
        example = schema["examples"][0]
        assert "include" in example
        assert "highlight_behavior" in example
        assert "style_rules" in example
        assert "custom_css" in example

    def test_empty_object(self) -> None:
        settings = parse_render_settings({})
        assert settings.side == ""
        assert settings.width == 0
        assert settings.font_size == 0.0
        assert settings.highlights == []
        assert settings.include.vias == "visible"
        assert settings.include.layers == []
        assert settings.highlight_behavior == {}
        assert settings.style_rules == []
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
                            "match": {"function": "copper", "side": "front"},
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
        assert settings.source.layers[0].match.side == "front"
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

    def test_render_settings_accepts_font_size_px(self) -> None:
        settings = parse_render_settings({"font_size_px": 72})
        assert settings.font_size == 72

    def test_render_settings_accepts_include_policy(self) -> None:
        settings = parse_render_settings(
            {
                "include": {
                    "board_outline": "visible",
                    "drills": "visible",
                    "vias": "when-highlighted",
                    "layers": [
                        {
                            "role": "copper",
                            "side": "active",
                            "objects": {
                                "pads": "visible",
                                "traces": "when-highlighted",
                                "zones": "hidden",
                            },
                        }
                    ],
                }
            }
        )
        assert settings.include.vias == "when-highlighted"
        assert settings.include.layers[0].objects["traces"] == "when-highlighted"

    def test_render_settings_rejects_unknown_include_state(self) -> None:
        with pytest.raises(ValueError, match=r"include\.layers\[0\]\.objects\.traces"):
            parse_render_settings(
                {
                    "include": {
                        "layers": [
                            {
                                "role": "copper",
                                "objects": {"traces": "sometimes"},
                            }
                        ]
                    }
                }
            )

    def test_render_settings_rejects_unqualified_style_size(self) -> None:
        with pytest.raises(ValueError, match="stroke_width"):
            parse_render_settings(
                {
                    "style_rules": [
                        {
                            "match": {"object": "trace"},
                            "style": {"stroke_width": 4},
                        }
                    ]
                }
            )

    def test_render_settings_rejects_mm_and_mil_for_same_style_field(self) -> None:
        with pytest.raises(ValueError, match="stroke_width"):
            parse_render_settings(
                {
                    "style_rules": [
                        {
                            "match": {"object": "trace"},
                            "style": {"stroke_width_mm": 0.2, "stroke_width_mil": 8},
                        }
                    ]
                }
            )

    def test_render_settings_rejects_px_and_physical_style_units(self) -> None:
        with pytest.raises(ValueError, match="text_halo_width"):
            parse_render_settings(
                {
                    "style_rules": [
                        {
                            "match": {"object": "annotation-label"},
                            "style": {"text_halo_width_px": 6, "text_halo_width_mm": 0.2},
                        }
                    ]
                }
            )

    def test_all_fields(self) -> None:
        data = {
            "side": "back",
            "width": 1200,
            "font_size_px": 24,
            "highlights": [
                {"net": "VBUS", "color": "#ff0000"},
                {"component": "U1"},
                {"pad": "CN11.30", "color": "#00ff00"},
            ],
            "include": {"vias": "hidden"},
            "highlight_behavior": {"overlay": True, "dim_unhighlighted": False},
            "style_rules": [
                {
                    "match": {"object": "pad"},
                    "style": {"stroke_width_mm": 0.03},
                }
            ],
            "annotations": {"boxes": [{"targets": ["U1"], "label": "MCU"}]},
            "custom_css": ".board-fill { fill: red; }",
        }
        settings = parse_render_settings(data)
        assert settings.side == "back"
        assert settings.width == 1200
        assert settings.font_size == 24.0
        assert len(settings.highlights) == 3
        assert settings.highlights[0].net == "VBUS"
        assert settings.highlights[0].color == "#ff0000"
        assert settings.highlights[1].component == "U1"
        assert settings.highlights[1].color == ""
        assert settings.highlights[2].pad == "CN11.30"
        assert settings.highlights[2].color == "#00ff00"
        assert settings.include.vias == "hidden"
        assert settings.highlight_behavior == {"overlay": True, "dim_unhighlighted": False}
        assert settings.style_rules[0].match == {"object": "pad"}
        assert settings.style_rules[0].style == {"stroke_width_mm": 0.03}
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

        with pytest.raises(ValueError, match="font_size"):
            parse_render_settings({"font_size_px": 0})

        with pytest.raises(ValueError, match="font_size"):
            parse_render_settings({"font_size_px": True})

        with pytest.raises(ValueError, match="font_size"):
            parse_render_settings({"font_size_px": 501})

    @pytest.mark.parametrize("font_size", [math.nan, math.inf, -math.inf])
    def test_invalid_font_size_must_be_finite(self, font_size: float) -> None:
        with pytest.raises(ValueError, match="font_size"):
            parse_render_settings({"font_size_px": font_size})

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
                "font_size_px": 48,
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
                "font_size_px": 72,
                "custom_css": ".annotation-connector { stroke-width: 6; }",
            }
        )
    )

    settings = load_render_settings_file(child)

    assert settings.font_size == 72
    assert settings.custom_css == ".annotation-connector { stroke-width: 6; }"
    assert settings.include.vias == "when-highlighted"
    assert settings.style_rules


def test_bundled_simplified_high_contrast_uses_v2_settings(tmp_path: Path) -> None:
    child = tmp_path / "child.json"
    child.write_text(json.dumps({"extends": "phosphor:simplified-high-contrast"}))

    settings = load_render_settings_file(child)

    assert settings.font_size == 40
    assert settings.include.layers
    assert settings.include.vias == "when-highlighted"
    assert settings.style_rules


@pytest.mark.parametrize(
    "name",
    [
        "review",
        "design",
        "clean",
        "high-contrast",
        "simplified-high-contrast",
        "print",  # compatibility alias for high-contrast
        "print-callout",  # compatibility alias for simplified-high-contrast
        "review-callout",  # compatibility alias for review
    ],
)
def test_bundled_render_settings_use_v2_settings(name: str) -> None:
    settings = load_render_settings_json(json.dumps({"extends": f"phosphor:{name}"}))

    assert settings.include.layers
    assert settings.style_rules


def test_simplified_high_contrast_extends_high_contrast_style_rules() -> None:
    settings = load_render_settings_json('{"extends": "phosphor:simplified-high-contrast"}')
    rules_by_match = {tuple(rule.match.items()): rule.style for rule in settings.style_rules}

    assert settings.font_size == 40
    assert rules_by_match[(("object", "board_outline"),)]["stroke"] == "#444"
    assert rules_by_match[(("annotation", "label"),)]["pill_visible"] is False
    assert rules_by_match[(("annotation", "connector"),)]["dot_visible"] is False


def test_render_settings_extends_merges_v2_policy(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    base.write_text(
        json.dumps(
            {
                "include": {
                    "board_outline": "visible",
                    "drills": "visible",
                    "vias": "visible",
                    "layers": [
                        {
                            "role": "copper",
                            "side": "active",
                            "objects": {
                                "pads": "visible",
                                "traces": "visible",
                                "zones": "visible",
                            },
                        },
                        {"role": "silkscreen", "side": "active", "objects": "visible"},
                    ],
                },
                "highlight_behavior": {"overlay": False, "palette": {"default": "#111"}},
                "style_rules": [
                    {"match": {"object": "board_outline"}, "style": {"stroke": "#444"}}
                ],
            }
        )
    )
    child = tmp_path / "child.json"
    child.write_text(
        json.dumps(
            {
                "extends": "./base.json",
                "include": {
                    "vias": "when-highlighted",
                    "layers": [
                        {
                            "role": "copper",
                            "side": "active",
                            "objects": {
                                "traces": "when-highlighted",
                                "zones": "hidden",
                            },
                        },
                        {"role": "fabrication", "side": "active", "objects": "visible"},
                    ],
                },
                "highlight_behavior": {"overlay": True, "palette": {"warning": "#c00"}},
                "style_rules": [{"match": {"annotation": "label"}, "style": {"fill": "#000"}}],
            }
        )
    )

    settings = load_render_settings_file(child)

    assert settings.include.board_outline == "visible"
    assert settings.include.vias == "when-highlighted"
    assert settings.include.layers[0].objects == {
        "pads": "visible",
        "traces": "when-highlighted",
        "zones": "hidden",
    }
    assert settings.include.layers[1].role == "silkscreen"
    assert settings.include.layers[2].role == "fabrication"
    assert settings.highlight_behavior == {
        "overlay": True,
        "palette": {"default": "#111", "warning": "#c00"},
    }
    assert [rule.match for rule in settings.style_rules] == [
        {"object": "board_outline"},
        {"annotation": "label"},
    ]


def test_render_settings_extends_merges_include_layer_object_scalar_defaults(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base.json"
    base.write_text(
        json.dumps(
            {
                "include": {
                    "layers": [
                        {
                            "role": "silkscreen",
                            "side": "active",
                            "objects": "visible",
                        }
                    ]
                }
            }
        )
    )
    child = tmp_path / "child.json"
    child.write_text(
        json.dumps(
            {
                "extends": "./base.json",
                "include": {
                    "layers": [
                        {
                            "role": "silkscreen",
                            "side": "active",
                            "objects": {"reference_text": "hidden"},
                        }
                    ]
                },
            }
        )
    )

    settings = load_render_settings_file(child)

    assert settings.include.layers[0].objects == {
        "*": "visible",
        "reference_text": "hidden",
    }


def test_load_render_settings_file_detects_extend_cycles(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"extends": "./second.json"}))
    second.write_text(json.dumps({"extends": "./first.json"}))

    with pytest.raises(ValueError, match="cycle"):
        load_render_settings_file(first)


def test_render_settings_schema_documents_extends() -> None:
    schema = render_settings_schema()

    assert schema["properties"]["extends"]["type"] == "string"
    assert "phosphor:simplified-high-contrast" in json.dumps(schema["examples"])


# ---------------------------------------------------------------------------
# Highlight colors
# ---------------------------------------------------------------------------


def test_highlight_net_with_color() -> None:
    """A highlight spec with a color applies that color to traces and pads."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        highlight_specs=[HighlightSpec(net="SIG", color="#d4a843")],
    )
    assert 'style id="highlight"' in svg
    # The custom color should appear in the CSS
    assert "#d4a843" in svg
    # Traces and pads with the net should get the custom color
    assert "stroke: #d4a843 !important" in svg
    assert "fill: #d4a843 !important" in svg


def test_highlight_net_without_color_uses_layer_defaults() -> None:
    """A highlight spec without color falls back to per-layer copper colors."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        highlight_specs=[HighlightSpec(net="SIG")],
    )
    assert 'style id="highlight"' in svg
    # Should have copper color rules, not a custom color
    assert "Restore vibrant copper colors" in svg


def test_highlight_mixed_colors_and_defaults() -> None:
    """Nets with colors get per-net rules; nets without get per-layer rules."""
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
                net_name="NET_A",
                footprint_ref="U1",
            ),
            PcbPad(
                number="2",
                x=7.0,
                y=10.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=2,
                net_name="NET_B",
                footprint_ref="U1",
            ),
        ],
        fab_lines=[
            PcbLine(4, 9, 8, 9, "F.Fab", 0.1),
            PcbLine(8, 9, 8, 11, "F.Fab", 0.1),
            PcbLine(8, 11, 4, 11, "F.Fab", 0.1),
            PcbLine(4, 11, 4, 9, "F.Fab", 0.1),
        ],
    )
    board = Pcb(
        name="mixed-test",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "NET_A"), 2: PcbNet(2, "NET_B")},
        footprints=[fp],
        segments=[
            PcbSegment(5.0, 10.0, 10.0, 10.0, 0.25, "F.Cu", 1),
            PcbSegment(7.0, 10.0, 12.0, 10.0, 0.25, "F.Cu", 2),
        ],
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
            PcbLayer("B.Cu", LayerFunction.COPPER, side="back"),
            PcbLayer("F.Fab", LayerFunction.FAB, side="front"),
        ],
    )
    svg = render_pcb_svg(
        board,
        highlight_specs=[
            HighlightSpec(net="NET_A", color="#ff0000"),
            HighlightSpec(net="NET_B"),
        ],
    )
    # NET_A gets per-net color
    assert "stroke: #ff0000 !important" in svg
    # NET_B gets per-layer copper color rules
    assert "Restore vibrant copper colors" in svg


def test_highlight_component_with_color() -> None:
    """Component highlight with color applies to pads and body."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        highlight_specs=[HighlightSpec(component="U1", color="#5b8abf")],
    )
    assert 'style id="highlight"' in svg
    assert "#5b8abf" in svg
    assert "fill: #5b8abf !important" in svg
    assert "stroke: #5b8abf !important" in svg


def test_highlight_pad_with_color_renders_top_overlay() -> None:
    """Pad highlight re-renders the target pad above normal board artwork."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        highlight_specs=[HighlightSpec(pad="U1.1", color="#e0115f")],
    )

    assert '<style id="highlight">' in svg
    assert "#e0115f" in svg
    overlay_start = svg.index('class="highlight-overlay"')
    content_clip_end = svg.rindex("</g>", 0, svg.rindex("</svg>"))
    assert overlay_start < content_clip_end
    overlay = svg[overlay_start:content_clip_end]
    assert 'data-component="U1"' in overlay
    assert 'data-pad="1"' in overlay
    assert "highlight-pad" in overlay


def test_highlight_specs_merge_with_flags() -> None:
    """highlight_specs merge with highlight_nets/highlight_components."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        highlight_nets=["SIG"],
        highlight_specs=[HighlightSpec(component="U1")],
    )
    # Both net and component should be highlighted
    assert "Restore highlighted nets" in svg
    assert "Restore highlighted components" in svg


# ---------------------------------------------------------------------------
# Class-based CSS selectors (no attribute selectors in <style>)
# ---------------------------------------------------------------------------


class TestClassBasedSelectors:
    """CSS uses class selectors (.nn-X, .cmp-X, .pfx-X, .lyr) instead of
    attribute selectors ([data-net-number="X"]) for O(1) rasterization."""

    def test_no_attribute_selectors_in_style(self, board: Pcb) -> None:
        """No attribute selectors should appear in any <style> block."""
        svg = render_pcb_svg(board, highlight_nets=["VCC"])
        # Extract all <style> blocks
        style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", svg, re.DOTALL)
        css = "\n".join(style_blocks)
        # No attribute selectors of the form [data-...]
        assert "[data-" not in css, f"Found attribute selector in CSS: {css}"

    def test_nn_class_on_traces(self) -> None:
        """Traces get nn-{number} class alongside data-net-number attr."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board)
        # Net 1 = SIG. Traces should have class="trace nn-1"
        assert re.search(r'class="trace nn-1"', svg)

    def test_nn_class_on_pads(self) -> None:
        """Pads get nn-{number} class."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board)
        assert re.search(r'class="pad[^"]*\bnn-1\b', svg)

    def test_nn_class_on_zones(self, board: Pcb) -> None:
        """Zone polygons get nn-{number} class."""
        svg = render_pcb_svg(board)
        assert re.search(r'class="zone nn-\d+"', svg)

    def test_nn_class_on_vias(self) -> None:
        """Via groups get nn-{number} class."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board)
        assert re.search(r'class="via nn-1"', svg)

    def test_nn_class_on_trace_arcs(self) -> None:
        """Trace arcs get nn-{number} class."""
        from phosphor_eda.pcb import PcbTraceArc

        board = _make_board_with_inner_layers()
        board.trace_arcs.append(PcbTraceArc(5.0, 10.0, 7.5, 8.0, 10.0, 10.0, 0.25, "F.Cu", 1))
        svg = render_pcb_svg(board)
        assert re.search(r'class="trace-arc nn-1"', svg)

    def test_cmp_class_on_pads(self) -> None:
        """Pads get cmp-{ref} class."""
        board = _make_board_with_component()
        svg = render_pcb_svg(board)
        assert re.search(r'class="pad[^"]*\bcmp-U1\b', svg)

    def test_cmp_class_on_body(self) -> None:
        """Body group gets cmp-{ref} class."""
        board = _make_board_with_component()
        svg = render_pcb_svg(board)
        assert re.search(r'class="[^"]*\bcmp-U1\b', svg)

    def test_cmp_class_on_ref_text(self) -> None:
        """Ref text gets cmp-{ref} class."""
        from phosphor_eda.pcb import PcbText

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
        svg = render_pcb_svg(board)
        assert re.search(r'class="ref-text[^"]*\bcmp-U1\b', svg)

    def test_cmp_class_on_silk(self) -> None:
        """Silk lines with a footprint ref get cmp-{ref} class."""
        board = _make_board_with_component()
        svg = render_pcb_svg(board)
        assert re.search(r'class="silk[^"]*\bcmp-U1\b', svg)

    def test_pfx_class_on_passive_components(self) -> None:
        """Components with R/C/L/TP prefix get pfx-{prefix} class."""
        board = _make_board_with_component(ref="R1", lib="Resistor_SMD:R_0402", value="10k")
        svg = render_pcb_svg(board)
        assert re.search(r'class="[^"]*\bpfx-R\b', svg)

    def test_pfx_class_not_on_ics(self) -> None:
        """IC components (U prefix) don't get a pfx- class."""
        board = _make_board_with_component(ref="U1")
        svg = render_pcb_svg(board)
        assert "pfx-U" not in svg

    def test_lyr_class_on_layer_groups(self) -> None:
        """Layer <g> groups get the lyr class."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board)
        assert re.search(r'class="layer-F-Cu lyr"', svg)

    def test_highlight_css_uses_nn_class(self) -> None:
        """Highlight CSS uses .nn-X selectors, not [data-net-number]."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board, highlight_nets=["SIG"])
        style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", svg, re.DOTALL)
        css = "\n".join(style_blocks)
        assert ".nn-1" in css
        assert "[data-net-number" not in css

    def test_highlight_css_uses_cmp_class(self) -> None:
        """Component highlight CSS uses .cmp-X selectors."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board, highlight_components=["U1"])
        style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", svg, re.DOTALL)
        css = "\n".join(style_blocks)
        assert ".cmp-U1" in css
        assert "[data-component" not in css

    def test_dim_css_uses_lyr_class(self) -> None:
        """Dim rules use g.lyr selector to match base style specificity."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board, highlight_nets=["SIG"])
        style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", svg, re.DOTALL)
        css = "\n".join(style_blocks)
        assert "g.lyr .trace" in css
        assert "g[data-layer]" not in css

    def test_dim_css_uses_paint_opacity(self) -> None:
        """Dim/restore rules use stroke-opacity/fill-opacity, not opacity."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board, highlight_nets=["SIG"])
        style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", svg, re.DOTALL)
        highlight_css = style_blocks[1] if len(style_blocks) > 1 else ""
        assert "stroke-opacity:" in highlight_css
        assert "fill-opacity:" in highlight_css
        # No bare "opacity:" — only stroke-opacity/fill-opacity
        bare = re.findall(r"(?<![-\w])opacity:", highlight_css)
        assert bare == [], f"bare opacity found: {bare}"

    def test_base_css_uses_paint_opacity(self) -> None:
        """Base CSS must use stroke-opacity/fill-opacity, not bare opacity."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board)
        style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", svg, re.DOTALL)
        base_css = style_blocks[0]
        bare = re.findall(r"(?<![-\w])opacity:", base_css)
        assert bare == [], f"bare opacity found in base CSS: {bare}"

    def test_passive_prefix_class_uses_pfx_class(self) -> None:
        """Passive metadata uses .pfx-R etc., not [data-component^=]."""
        board = _make_board_with_component(ref="R1", lib="R_0402", value="10k")
        svg = render_pcb_svg(board)
        assert "pfx-R" in svg
        assert '[data-component^="R"]' not in svg

    def test_data_attrs_still_present(self) -> None:
        """data-* attributes are preserved on elements for query/tooling use."""
        board = _make_board_with_inner_layers()
        svg = render_pcb_svg(board)
        assert 'data-net-number="1"' in svg
        assert 'data-component="U1"' in svg
        assert 'data-layer="F.Cu"' in svg

    def test_css_safe_identity_for_simple_refs(self) -> None:
        """Simple alphanumeric refs pass through unchanged."""
        assert _css_safe("R1") == "R1"
        assert _css_safe("U3A") == "U3A"
        assert _css_safe("C_10") == "C_10"

    def test_css_safe_encodes_special_chars(self) -> None:
        """Characters invalid in CSS class names are hex-escaped."""
        assert _css_safe("R?") == "R_3f"
        assert _css_safe("J1/SHIELD") == "J1_2fSHIELD"
        assert _css_safe("U1:A") == "U1_3aA"

    def test_cmp_class_uses_sanitized_ref(self) -> None:
        """_cmp_class produces valid CSS class tokens for special refs."""
        assert _cmp_class("R1") == "cmp-R1"
        assert _cmp_class("J1/SHIELD") == "cmp-J1_2fSHIELD"
