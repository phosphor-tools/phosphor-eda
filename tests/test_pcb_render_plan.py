from phosphor_eda.pcb_render_plan import (
    EmittedGeometry,
    GeometryKind,
    InclusionReason,
    PcbRenderPlan,
    ViewBox,
)


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
