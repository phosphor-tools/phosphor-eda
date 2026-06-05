import json

from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
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
from phosphor_eda.pcb_annotations import ResolvedAnnotations
from phosphor_eda.pcb_render import load_render_settings_json
from phosphor_eda.pcb_render_plan import (
    DerivedRenderPlan,
    ViewBox,
    build_derived_render_plan,
)


def test_build_derived_render_plan_eda_uses_derived_layers() -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "eda",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {"eda.layer[F.Cu].fill": "#ff6600"},
            }
        )
    )
    plan = build_derived_render_plan(
        _make_plan_board(),
        settings=settings,
        side="front",
        width_px=1000,
        annotations=None,
    )

    assert isinstance(plan, DerivedRenderPlan)
    assert plan.width_px == 1000
    assert plan.height_px > 0
    assert plan.base_layers
    assert plan.base_layers[0].role.namespace == "eda"
    assert plan.base_layers[0].role.function == "copper"
    assert plan.base_layers[0].source_layers == ("F.Cu",)
    assert plan.highlight_groups == ()
    assert plan.warnings == ()


def test_build_derived_render_plan_realistic_uses_derived_layers() -> None:
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
                    "realistic.exposedSubstrate.fill": "#244426",
                    "realistic.exposedCopper.fill": "#d6a13d",
                    "realistic.silkscreen.fill": "#ffffff",
                    "realistic.boardOutline.fill": "none",
                    "realistic.boardOutline.stroke": "#111111",
                    "realistic.boardOutline.strokeWidthMm": 0.08,
                },
            }
        )
    )

    plan = build_derived_render_plan(
        _make_plan_board(),
        settings=settings,
        side="front",
        width_px=1000,
        annotations=None,
    )

    assert isinstance(plan, DerivedRenderPlan)
    assert {layer.role.namespace for layer in plan.base_layers} == {"realistic"}
    assert {layer.role.function for layer in plan.base_layers} >= {"substrate", "boardOutline"}


def test_build_derived_render_plan_uses_requested_side_over_settings_side() -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "realistic",
                "side": "back",
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
                    "realistic.exposedSubstrate.fill": "#244426",
                    "realistic.exposedCopper.fill": "#d6a13d",
                    "realistic.silkscreen.fill": "#ffffff",
                    "realistic.boardOutline.fill": "none",
                    "realistic.boardOutline.stroke": "#111111",
                    "realistic.boardOutline.strokeWidthMm": 0.08,
                },
            }
        )
    )

    plan = build_derived_render_plan(
        _make_plan_board(),
        settings=settings,
        side="front",
        width_px=1000,
        annotations=None,
    )

    covered_copper = next(
        layer for layer in plan.base_layers if layer.role.function == "coveredCopper"
    )
    assert covered_copper.source_layers == ("F.Cu",)


def test_build_derived_render_plan_expands_view_box_for_annotations() -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "renderMode": "eda",
                "source": {"layers": [{"match": {"name": "F.Cu"}}]},
                "tokens": {"eda.layer[F.Cu].fill": "#ff6600"},
            }
        )
    )

    plan = build_derived_render_plan(
        _make_plan_board(),
        settings=settings,
        side="front",
        width_px=1000,
        annotations=ResolvedAnnotations(content_bbox=(-10.0, -10.0, 30.0, 40.0)),
    )

    assert plan.view_box == ViewBox(x=-10.0, y=-10.0, width=40.0, height=50.0)
    assert plan.height_px == 1250


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
