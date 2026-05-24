from pathlib import Path

from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
    PcbArc,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbNet,
    PcbPad,
    PcbPolygon,
    PcbSegment,
    PcbTraceArc,
    PcbVia,
    PcbZone,
)
from phosphor_eda.pcb_render import load_render_settings_json
from phosphor_eda.pcb_render_plan import (
    EmittedGeometry,
    GeometryKind,
    InclusionReason,
    PcbRenderPlan,
    ViewBox,
    build_render_plan,
    layer_matches_rule,
    layer_role,
)
from phosphor_eda.pcb_render_settings import HighlightSpec, LayerIncludeRule

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"


def test_render_plan_tracks_base_and_overlay_geometry() -> None:
    plan = PcbRenderPlan(
        side="front",
        width_px=1200,
        height_px=800,
        view_box=ViewBox(x=0.0, y=0.0, width=60.0, height=40.0),
        board_bbox=(0.0, 0.0, 60.0, 40.0),
    )
    plan.base.append(
        EmittedGeometry(
            kind=GeometryKind.PAD,
            layer="F.Cu",
            attrs={"data-component": "TP3", "data-pad": "1"},
            reason=InclusionReason.VISIBLE,
        )
    )
    plan.overlay.append(
        EmittedGeometry(
            kind=GeometryKind.TRACE,
            layer="In1.Cu",
            attrs={"data-net": "SWDIO"},
            reason=InclusionReason.HIGHLIGHT,
        )
    )

    assert plan.base[0].reason is InclusionReason.VISIBLE
    assert plan.overlay[0].reason is InclusionReason.HIGHLIGHT


def test_layer_role_maps_common_functions() -> None:
    assert layer_role(PcbLayer("F.Cu", LayerFunction.COPPER, "front")) == "copper"
    assert layer_role(PcbLayer("F.SilkS", LayerFunction.SILKSCREEN, "front")) == "silkscreen"
    assert layer_role(PcbLayer("F.Fab", LayerFunction.FAB, "front")) == "fabrication"
    assert layer_role(PcbLayer("F.Mask", LayerFunction.SOLDER_MASK, "front")) == "mask"
    assert layer_role(PcbLayer("F.Paste", LayerFunction.SOLDER_PASTE, "front")) == "paste"
    assert layer_role(PcbLayer("Dwgs.User", LayerFunction.MECHANICAL)) == "mechanical"
    assert layer_role(PcbLayer("F.CrtYd", LayerFunction.COURTYARD, "front")) == "unknown"


def test_layer_selector_matches_role_side_and_name() -> None:
    layer = PcbLayer("F.Cu", LayerFunction.COPPER, "front")
    inner_layer = PcbLayer("In1.Cu", LayerFunction.COPPER)

    assert layer_matches_rule(
        layer,
        LayerIncludeRule(role="copper", side="active"),
        active_side="front",
    )
    assert layer_matches_rule(layer, LayerIncludeRule(name="F.Cu"), active_side="back")
    assert layer_matches_rule(layer, LayerIncludeRule(side="any"), active_side="back")
    assert layer_matches_rule(layer, LayerIncludeRule(side=""), active_side="back")
    assert layer_matches_rule(layer, LayerIncludeRule(side="front"), active_side="back")
    assert layer_matches_rule(
        layer,
        LayerIncludeRule(name="F.Cu", role="copper"),
        active_side="back",
    )
    assert not layer_matches_rule(layer, LayerIncludeRule(side="back"), active_side="back")
    assert not layer_matches_rule(layer, LayerIncludeRule(name="B.Cu"), active_side="front")
    assert not layer_matches_rule(
        layer,
        LayerIncludeRule(role="silkscreen"),
        active_side="front",
    )
    assert not layer_matches_rule(
        layer,
        LayerIncludeRule(role="copper", side="opposite"),
        active_side="front",
    )
    assert not layer_matches_rule(
        inner_layer,
        LayerIncludeRule(role="copper", side="opposite"),
        active_side="front",
    )


def _make_plan_board() -> Pcb:
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
                net_name="/SWDIO_TMS",
                footprint_ref="U1",
            ),
            PcbPad(
                number="2",
                x=7.0,
                y=5.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=2,
                net_name="GND",
                footprint_ref="U1",
            ),
        ],
    )
    return Pcb(
        name="render-plan-test",
        nets={
            0: PcbNet(0, ""),
            1: PcbNet(1, "/SWDIO_TMS"),
            2: PcbNet(2, "GND"),
        },
        footprints=[fp],
        segments=[
            PcbSegment(5.0, 5.0, 10.0, 5.0, 0.2, "F.Cu", 1),
            PcbSegment(7.0, 5.0, 10.0, 8.0, 0.2, "F.Cu", 2),
        ],
        trace_arcs=[
            PcbTraceArc(10.0, 5.0, 12.0, 6.0, 14.0, 5.0, 0.2, "F.Cu", 1),
        ],
        vias=[
            PcbVia(10.0, 5.0, 0.6, 0.3, ["F.Cu", "B.Cu"], 1),
            PcbVia(10.0, 8.0, 0.6, 0.3, ["F.Cu", "B.Cu"], 2),
        ],
        polygons=[
            PcbPolygon(
                points=[(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)],
                layer="F.Cu",
                net_number=2,
                net_name="GND",
            ),
        ],
        zones=[
            PcbZone(
                net_number=1,
                net_name="/SWDIO_TMS",
                layer="F.Cu",
                boundary=[(11.0, 1.0), (13.0, 1.0), (13.0, 3.0), (11.0, 3.0)],
            ),
        ],
        outline_lines=[
            PcbLine(0.0, 0.0, 20.0, 0.0, "Edge.Cuts", 0.1),
            PcbLine(20.0, 0.0, 20.0, 12.0, "Edge.Cuts", 0.1),
            PcbLine(20.0, 12.0, 0.0, 12.0, "Edge.Cuts", 0.1),
            PcbLine(0.0, 12.0, 0.0, 0.0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front"),
            PcbLayer("B.Cu", LayerFunction.COPPER, side="back"),
            PcbLayer("Edge.Cuts", LayerFunction.EDGE),
        ],
    )


def _print_callout_settings():
    return load_render_settings_json(
        '{"extends": "phosphor:print-callout", "highlights": [{"net": "/SWDIO_TMS"}]}'
    )


def test_build_render_plan_print_callout_keeps_sparse_base_and_clip() -> None:
    plan = build_render_plan(
        _make_plan_board(),
        settings=_print_callout_settings(),
        side="front",
        width_px=1000,
    )

    base_kinds = [geometry.kind for geometry in plan.base]

    assert GeometryKind.PAD in base_kinds
    assert GeometryKind.BOARD_OUTLINE in base_kinds
    assert GeometryKind.TRACE not in base_kinds
    assert GeometryKind.TRACE_ARC not in base_kinds
    assert GeometryKind.ZONE not in base_kinds
    assert GeometryKind.VIA not in base_kinds
    assert plan.clip is not None
    assert plan.clip.board_path_d
    assert plan.height_px == 666


def test_print_callout_plan_omits_unhighlighted_traces_and_zones() -> None:
    board = parse_kicad_pcb(FIXTURE)
    settings = load_render_settings_json('{"extends": "phosphor:print-callout"}')

    plan = build_render_plan(board, settings=settings, side="front", width_px=1200)

    assert not any(item.kind is GeometryKind.TRACE for item in plan.base)
    assert not any(item.kind is GeometryKind.ZONE for item in plan.base)
    assert any(item.kind is GeometryKind.PAD for item in plan.base)
    assert any(item.kind is GeometryKind.BOARD_OUTLINE for item in plan.base)
    assert plan.clip is not None
    assert " A " in plan.clip.board_path_d


def test_print_callout_plan_emits_highlighted_trace_overlay() -> None:
    board = parse_kicad_pcb(FIXTURE)
    settings = load_render_settings_json('{"extends": "phosphor:print-callout"}')
    settings.highlights.append(HighlightSpec(net="/SWDIO_TMS", color="#c00000"))

    plan = build_render_plan(board, settings=settings, side="front", width_px=1200)

    assert any(item.kind is GeometryKind.TRACE for item in plan.overlay)
    assert all(item.reason is InclusionReason.HIGHLIGHT for item in plan.overlay)


def test_build_render_plan_print_callout_overlays_highlighted_net_geometry() -> None:
    plan = build_render_plan(
        _make_plan_board(),
        settings=_print_callout_settings(),
        side="front",
        width_px=1000,
    )

    overlay = [
        geometry
        for geometry in plan.overlay
        if geometry.attrs.get("data-net") == "/SWDIO_TMS"
        and geometry.reason is InclusionReason.HIGHLIGHT
    ]
    overlay_kinds = {geometry.kind for geometry in overlay}

    assert GeometryKind.TRACE in overlay_kinds
    assert GeometryKind.TRACE_ARC in overlay_kinds
    assert GeometryKind.VIA in overlay_kinds
    assert GeometryKind.PAD in overlay_kinds
    assert all(geometry.points for geometry in overlay)


def test_build_render_plan_ignores_malformed_pad_highlight() -> None:
    settings = load_render_settings_json('{"extends": "phosphor:print-callout"}')
    settings.highlights.append(HighlightSpec(pad="U1"))

    plan = build_render_plan(_make_plan_board(), settings=settings, side="front", width_px=1000)

    assert not any(
        geometry.reason is InclusionReason.HIGHLIGHT and geometry.kind is GeometryKind.PAD
        for geometry in plan.overlay
    )


def test_build_render_plan_print_includes_vias_and_classifies_zone_sources() -> None:
    settings = load_render_settings_json('{"extends": "phosphor:print"}')

    plan = build_render_plan(_make_plan_board(), settings=settings, side="front", width_px=1000)

    base_vias = [geometry for geometry in plan.base if geometry.kind is GeometryKind.VIA]
    base_zones = [geometry for geometry in plan.base if geometry.kind is GeometryKind.ZONE]

    assert base_vias
    assert {type(geometry.source) for geometry in base_zones} == {PcbPolygon, PcbZone}


def test_back_side_plan_keeps_transformed_zone_holes_on_source() -> None:
    board = _make_plan_board()
    board.polygons[0].holes = [[(1.5, 1.5), (2.0, 1.5), (2.0, 2.0)]]
    settings = load_render_settings_json(
        '{"include": {"layers": [{"role": "copper", "side": "any", '
        '"objects": {"zones": "visible"}}]}}'
    )

    plan = build_render_plan(board, settings=settings, side="back", width_px=1000)

    polygon_zone = next(
        geometry
        for geometry in plan.base
        if geometry.kind is GeometryKind.ZONE and isinstance(geometry.source, PcbPolygon)
    )
    assert polygon_zone.points[0].x == 19.0
    assert polygon_zone.source.holes == [[(18.5, 1.5), (18.0, 1.5), (18.0, 2.0)]]


def test_build_render_plan_includes_outline_arcs_in_clip_path() -> None:
    board = _make_plan_board()
    board.outline_lines = [
        PcbLine(0.0, 0.0, 10.0, 0.0, "Edge.Cuts", 0.1),
        PcbLine(10.0, 5.0, 0.0, 5.0, "Edge.Cuts", 0.1),
        PcbLine(0.0, 5.0, 0.0, 0.0, "Edge.Cuts", 0.1),
    ]
    board.outline_arcs = [
        PcbArc(10.0, 0.0, 12.0, 2.5, 10.0, 5.0, "Edge.Cuts", 0.1),
    ]
    settings = load_render_settings_json('{"extends": "phosphor:print"}')

    plan = build_render_plan(board, settings=settings, side="front", width_px=1000)

    assert plan.clip is not None
    assert " A " in plan.clip.board_path_d
    outline = next(
        geometry for geometry in plan.base if geometry.kind is GeometryKind.BOARD_OUTLINE
    )
    assert any(point.x == 12.0 and point.y == 2.5 for point in outline.points)


def test_back_side_plan_uses_rendered_view_x_coordinates() -> None:
    board = parse_kicad_pcb(FIXTURE)
    settings = load_render_settings_json(
        '{"include": {"layers": [{"role": "copper", "side": "any", '
        '"objects": {"pads": "when-highlighted"}}]}, '
        '"highlights": [{"pad": "TP3.1", "color": "#c00000"}]}'
    )

    front_plan = build_render_plan(board, settings=settings, side="front", width_px=1200)
    plan = build_render_plan(board, settings=settings, side="back", width_px=1200)

    front_pad = next(
        geometry
        for geometry in front_plan.overlay
        if geometry.kind is GeometryKind.PAD
        and geometry.attrs.get("data-component") == "TP3"
        and geometry.attrs.get("data-pad") == "1"
    )
    highlighted_pad = next(
        geometry
        for geometry in plan.overlay
        if geometry.kind is GeometryKind.PAD
        and geometry.attrs.get("data-component") == "TP3"
        and geometry.attrs.get("data-pad") == "1"
    )

    assert front_pad.points
    assert len(highlighted_pad.points) == 1
    assert front_pad.points[0].x == 93.5
    assert front_pad.points[0].x != highlighted_pad.points[0].x
    assert highlighted_pad.points[0].x == 118.5
    assert highlighted_pad.points[0].y == 64.5
