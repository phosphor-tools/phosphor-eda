from __future__ import annotations

import math
import re

import pytest

from phosphor_eda.pcb import PcbPad, PcbPolygon, PcbSegment, PcbTraceArc, PcbVia, PcbZone
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometryLayer,
    GeometryTags,
    RenderableGeometry,
)
from phosphor_eda.pcb_render_skia import (
    SkiaPathData,
    geometry_to_skia_artwork,
    union_skia_artwork,
)


def test_skia_unions_rect_pad_and_trace_to_svg_path_data() -> None:
    items = (
        _renderable_pad(_pad(shape="rect", x=1.0, y=1.0), geometry_id="pad-1"),
        _renderable_trace(PcbSegment(1.0, 1.0, 3.0, 1.0, 0.25, "F.Cu", 1)),
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


def test_skia_converts_circular_pad_to_curved_path_data() -> None:
    path_data = _convert_one(_renderable_pad(_pad(shape="circle", width=1.2, height=1.2)))

    _assert_valid_path_data(path_data.d)
    assert path_data.curve_commands > 0


def test_skia_converts_oval_pad_to_curved_path_data() -> None:
    path_data = _convert_one(_renderable_pad(_pad(shape="oval", width=2.0, height=0.8)))

    _assert_valid_path_data(path_data.d)
    assert path_data.curve_commands > 0


def test_skia_converts_roundrect_pad_to_curved_path_data() -> None:
    path_data = _convert_one(
        _renderable_pad(_pad(shape="roundrect", width=2.0, height=0.8, roundrect_rratio=0.5))
    )

    _assert_valid_path_data(path_data.d)
    assert path_data.curve_commands > 0


def test_skia_converts_rotated_rect_pad_to_non_axis_aligned_path_data() -> None:
    path_data = _convert_one(
        _renderable_pad(_pad(shape="rect", width=2.0, height=1.0, rotation=45.0))
    )

    _assert_valid_path_data(path_data.d)
    assert any(
        not math.isclose(abs(x), 1.0, abs_tol=0.001)
        and not math.isclose(abs(y), 0.5, abs_tol=0.001)
        for x, y in _path_coordinate_pairs(path_data.d)
    )


def test_skia_converts_drilled_through_hole_pad_body_on_selected_copper_layer() -> None:
    pad = _pad(shape="circle", width=1.2, height=1.2, drill=0.5, layers=["*.Cu"])
    selected = geometry_to_skia_artwork(
        _renderable_pad(pad, layer_name="F.Cu"),
        target_layer_name="F.Cu",
    )
    nonmatching = geometry_to_skia_artwork(
        _renderable_pad(pad, layer_name="F.Cu"),
        target_layer_name="F.SilkS",
    )

    assert selected is not None
    path_data = union_skia_artwork((selected,))
    _assert_valid_path_data(path_data.d)
    assert path_data.curve_commands > 0
    assert path_data.move_commands == 1
    assert nonmatching is None


@pytest.mark.parametrize(
    "target_layer_name",
    ("F.Cu", "In1.Cu", "Top Layer", "MidLayer1", "Bottom Layer"),
)
def test_skia_matches_wildcard_pad_stack_to_copper_layer_names(
    target_layer_name: str,
) -> None:
    pad = _pad(shape="circle", width=1.2, height=1.2, drill=0.5, layers=["*.Cu"])

    selected = geometry_to_skia_artwork(
        _renderable_pad(pad, layer_name=target_layer_name),
        target_layer_name=target_layer_name,
    )

    assert selected is not None


def test_skia_converts_drilled_through_hole_pad_only_for_matching_explicit_layer() -> None:
    pad = _pad(shape="circle", width=1.2, height=1.2, drill=0.5, layers=["F.Cu", "B.Cu"])
    selected = geometry_to_skia_artwork(
        _renderable_pad(pad, layer_name="F.Cu"),
        target_layer_name="B.Cu",
    )
    unselected = geometry_to_skia_artwork(
        _renderable_pad(pad, layer_name="F.Cu"),
        target_layer_name="In1.Cu",
    )

    assert selected is not None
    assert unselected is None


def test_skia_converts_via_body_on_spanned_layer_only() -> None:
    via = PcbVia(0.0, 0.0, 1.0, 0.4, ["F.Cu", "B.Cu"], 1)
    selected = geometry_to_skia_artwork(_renderable_via(via), target_layer_name="B.Cu")
    unrelated = geometry_to_skia_artwork(_renderable_via(via), target_layer_name="In1.Cu")

    assert selected is not None
    path_data = union_skia_artwork((selected,))
    _assert_valid_path_data(path_data.d)
    assert path_data.curve_commands > 0
    assert path_data.move_commands == 1
    assert unrelated is None


def test_skia_converts_straight_trace_to_valid_path_data() -> None:
    path_data = _convert_one(_renderable_trace(PcbSegment(0.0, 0.0, 2.0, 0.0, 0.2, "F.Cu", 1)))

    _assert_valid_path_data(path_data.d)
    assert path_data.line_commands > 0


def test_skia_unions_connected_45_degree_traces_without_separate_contours() -> None:
    artwork = tuple(
        result
        for item in (
            _renderable_trace(
                PcbSegment(0.0, 0.0, 1.0, 1.0, 0.2, "F.Cu", 1),
                geometry_id="trace-a",
            ),
            _renderable_trace(
                PcbSegment(1.0, 1.0, 2.0, 0.0, 0.2, "F.Cu", 1),
                geometry_id="trace-b",
            ),
        )
        for result in (geometry_to_skia_artwork(item, target_layer_name="F.Cu"),)
        if result is not None
    )

    path_data = union_skia_artwork(artwork)

    _assert_valid_path_data(path_data.d)
    assert path_data.move_commands == 1
    assert path_data.source_ids == ("trace-a", "trace-b")


def test_skia_converts_trace_arc_to_valid_path_data() -> None:
    path_data = _convert_one(
        _renderable_trace_arc(PcbTraceArc(0.0, 0.0, 1.0, 1.0, 2.0, 0.0, 0.2, "F.Cu", 1))
    )

    _assert_valid_path_data(path_data.d)
    assert path_data.line_commands > 0


def test_skia_converts_zone_polygon_to_valid_path_data() -> None:
    path_data = _convert_one(
        _renderable_zone(
            PcbZone(1, "GND", "F.Cu", [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)])
        )
    )

    _assert_valid_path_data(path_data.d)
    assert path_data.line_commands > 0


def test_skia_converts_polygon_with_holes_to_valid_path_data() -> None:
    path_data = _convert_one(
        _renderable_polygon(
            PcbPolygon(
                points=[(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)],
                layer="F.Cu",
                holes=[[(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]],
            )
        )
    )

    _assert_valid_path_data(path_data.d)
    assert path_data.move_commands == 2


def test_skia_union_preserves_source_ids_and_source_layers() -> None:
    front = geometry_to_skia_artwork(
        _renderable_trace(
            PcbSegment(0.0, 0.0, 1.0, 0.0, 0.2, "F.Cu", 1),
            geometry_id="front-trace",
            layer_name="F.Cu",
        ),
        target_layer_name="F.Cu",
    )
    back = geometry_to_skia_artwork(
        _renderable_trace(
            PcbSegment(0.0, 1.0, 1.0, 1.0, 0.2, "B.Cu", 1),
            geometry_id="back-trace",
            layer_name="B.Cu",
        ),
        target_layer_name="B.Cu",
    )

    assert front is not None
    assert back is not None
    path_data = union_skia_artwork((front, back))

    assert path_data.source_ids == ("front-trace", "back-trace")
    assert path_data.source_layers == ("F.Cu", "B.Cu")


def _convert_one(item: RenderableGeometry, *, target_layer_name: str = "F.Cu") -> SkiaPathData:
    artwork = geometry_to_skia_artwork(item, target_layer_name=target_layer_name)
    assert artwork is not None
    return union_skia_artwork((artwork,))


def _renderable_pad(
    pad: PcbPad,
    *,
    geometry_id: str = "pad-1",
    layer_name: str = "F.Cu",
) -> RenderableGeometry:
    return _renderable(
        geometry_id,
        GeometryKind.PAD,
        layer_name,
        "copper",
        "front",
        geometry=pad,
        tags=GeometryTags(
            source_collection="pads",
            pad_number=pad.number,
            net_number=pad.net_number,
            net_name=pad.net_name,
        ),
    )


def _renderable_trace(
    segment: PcbSegment,
    *,
    geometry_id: str = "trace-1",
    layer_name: str = "F.Cu",
) -> RenderableGeometry:
    return _renderable(
        geometry_id,
        GeometryKind.TRACE,
        layer_name,
        "copper",
        "front",
        geometry=segment,
        tags=GeometryTags(source_collection="segments", net_number=segment.net_number),
    )


def _renderable_trace_arc(arc: PcbTraceArc) -> RenderableGeometry:
    return _renderable(
        "trace-arc-1",
        GeometryKind.TRACE_ARC,
        arc.layer,
        "copper",
        "front",
        geometry=arc,
        tags=GeometryTags(source_collection="trace_arcs", net_number=arc.net_number),
    )


def _renderable_via(via: PcbVia) -> RenderableGeometry:
    return _renderable(
        "via-1",
        GeometryKind.VIA,
        "vias",
        "via",
        "",
        geometry=via,
        tags=GeometryTags(source_collection="vias", net_number=via.net_number),
    )


def _renderable_zone(zone: PcbZone) -> RenderableGeometry:
    return _renderable(
        "zone-1",
        GeometryKind.ZONE,
        zone.layer,
        "copper",
        "front",
        geometry=zone,
        tags=GeometryTags(
            source_collection="zones",
            net_number=zone.net_number,
            net_name=zone.net_name,
        ),
    )


def _renderable_polygon(polygon: PcbPolygon) -> RenderableGeometry:
    return _renderable(
        "polygon-1",
        GeometryKind.MASK,
        polygon.layer,
        "mask",
        "front",
        geometry=polygon,
        tags=GeometryTags(source_collection="polygons"),
    )


def _renderable(
    geometry_id: str,
    kind: GeometryKind,
    layer_name: str,
    layer_role: str,
    side: str,
    *,
    geometry: object,
    tags: GeometryTags,
) -> RenderableGeometry:
    return RenderableGeometry(
        id=geometry_id,
        kind=kind,
        layer=GeometryLayer(name=layer_name, role=layer_role, side=side),
        tags=tags,
        payload=geometry,
        source=geometry,
    )


def _pad(
    *,
    shape: str,
    width: float = 1.0,
    height: float = 1.0,
    x: float = 0.0,
    y: float = 0.0,
    rotation: float = 0.0,
    drill: float = 0.0,
    roundrect_rratio: float = 0.0,
    layers: list[str] | None = None,
) -> PcbPad:
    return PcbPad(
        number="1",
        x=x,
        y=y,
        width=width,
        height=height,
        shape=shape,
        layers=["F.Cu"] if layers is None else layers,
        net_number=1,
        net_name="GND",
        footprint_ref="J1",
        rotation=rotation,
        drill=drill,
        roundrect_rratio=roundrect_rratio,
    )


def _assert_valid_path_data(path_data: str) -> None:
    assert path_data.startswith("M ")
    assert path_data.endswith("Z")
    assert len(path_data) > 0


def _path_coordinate_pairs(path_data: str) -> tuple[tuple[float, float], ...]:
    coordinates = tuple(float(match.group()) for match in re.finditer(r"-?\d+\.\d+", path_data))
    assert len(coordinates) % 2 == 0
    return tuple(zip(coordinates[::2], coordinates[1::2], strict=True))
