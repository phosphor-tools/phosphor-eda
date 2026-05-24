from phosphor_eda.pcb import LayerFunction, PcbLayer
from phosphor_eda.pcb_render_plan import (
    EmittedGeometry,
    GeometryKind,
    InclusionReason,
    PcbRenderPlan,
    ViewBox,
    layer_matches_rule,
    layer_role,
)
from phosphor_eda.pcb_render_settings import LayerIncludeRule


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
