from __future__ import annotations

from phosphor_eda.pcb import PcbPad, PcbSegment
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometryLayer,
    GeometryTags,
    RenderableGeometry,
)
from phosphor_eda.pcb_render_skia import geometry_to_skia_artwork, union_skia_artwork


def test_skia_unions_rect_pad_and_trace_to_svg_path_data() -> None:
    layer = GeometryLayer(name="F.Cu", role="copper", side="front")
    items = (
        RenderableGeometry(
            id="pad-1",
            kind=GeometryKind.PAD,
            layer=layer,
            tags=GeometryTags(source_collection="pads"),
            payload=PcbPad(
                number="1",
                x=1.0,
                y=1.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=1,
                net_name="GND",
                footprint_ref="J1",
            ),
            source=None,
        ),
        RenderableGeometry(
            id="trace-1",
            kind=GeometryKind.TRACE,
            layer=layer,
            tags=GeometryTags(source_collection="segments"),
            payload=PcbSegment(1.0, 1.0, 3.0, 1.0, 0.25, "F.Cu", 1),
            source=None,
        ),
    )

    artwork = tuple(
        result
        for item in items
        for result in (geometry_to_skia_artwork(item, target_layer_name="F.Cu"),)
        if result is not None
    )
    path_data = union_skia_artwork(artwork)

    assert path_data.d.startswith("M ")
    assert path_data.path_characters > 0
    assert path_data.line_commands > 0
    assert path_data.source_ids == ("pad-1", "trace-1")
