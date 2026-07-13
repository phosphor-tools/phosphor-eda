from __future__ import annotations

import math
from pathlib import Path

import pytest

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbCircle,
    PcbClosedPath,
    PcbLayer,
    PcbPathSegmentKind,
    PcbPolygon,
    PcbText,
)
from phosphor_eda.formats.allegro.build import build_allegro_graphics_board
from phosphor_eda.formats.allegro.coords import BoardFrame, board_frame
from phosphor_eda.formats.allegro.graph import build_allegro_object_graph
from phosphor_eda.formats.allegro.graphics import extract_allegro_graphics, rectangle_primitive
from phosphor_eda.formats.allegro.layers import AllegroLayerMap, build_allegro_layers
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.primitives import AllegroPrimitiveRole
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet, payload_int

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
BREAKOUT_BOARD = (
    UPSTREAM_FIXTURES
    / "opencellular/electronics/breakout"
    / "board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
ROHM_BOARD = (
    UPSTREAM_FIXTURES
    / "rohm-stepper-driver"
    / "Design Files for Rev 1.0"
    / "STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd"
)


def _assert_close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)


def test_allegro_graphic_segment_records_preserve_native_geometry() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    graph = build_allegro_object_graph(record_set)

    graphic = graph.by_key[632_912_816]
    segment_key = graphic.payload["segment_key"]
    assert isinstance(segment_key, int)
    segment = graph.by_key[segment_key]

    assert graphic.tag == 0x14
    assert graphic.payload["layer_class_id"] == 0x01
    assert graphic.payload["layer_subclass_id"] == 0xF0
    assert segment.tag in {0x15, 0x16, 0x17}
    assert segment.payload["parent_key"] == graphic.key
    width = segment.payload["width"]
    start_x = segment.payload["start_x"]
    start_y = segment.payload["start_y"]
    end_x = segment.payload["end_x"]
    end_y = segment.payload["end_y"]
    assert isinstance(width, int)
    assert width >= 0
    assert isinstance(start_x, int)
    assert isinstance(start_y, int)
    assert isinstance(end_x, int)
    assert isinstance(end_y, int)
    assert (start_x, start_y) != (end_x, end_y)


def test_allegro_string_graphic_records_preserve_text_and_position() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    graph = build_allegro_object_graph(record_set)

    text = graph.by_key[632_913_056]

    assert text.tag == 0x31
    assert text.payload["text_wrapper_key"] == 632_913_096
    assert text.payload["string_layer_code"] == 0xF809
    assert text.payload["x"] == 154_776
    assert text.payload["y"] == 2_429_362
    assert text.payload["text"] == "C900H450"


def test_allegro_text_graphics_resolve_font_size_from_parameter_table() -> None:
    """Text font size resolves from the 0x36 code-0x08 table via 1-based font_key."""
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    text = next(primitive for primitive in graphics.artwork if primitive.id == "allegro:632913096")
    assert isinstance(text.data, PcbText)
    assert text.data.text == "C900H450"
    # font_key=60 -> item[59] char height 31.5 mil = 0.800100 mm.
    assert math.isclose(text.data.font_size, 0.800100, abs_tol=1e-6)
    assert not any(
        diagnostic.code == "unresolved-text-size" and diagnostic.key == 632_913_096
        for diagnostic in graphics.diagnostics
    )


def test_allegro_text_font_sizes_lock_research_worked_examples() -> None:
    """Locks the research worked examples: refdes, fab note, and drill-legend."""
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)
    by_id = {primitive.id: primitive for primitive in graphics.artwork}

    # U8 refdes font_key=1 -> 30 mil = 0.762 mm.
    u8 = by_id["allegro:633255704"]
    assert isinstance(u8.data, PcbText)
    assert u8.data.text == "U8"
    assert math.isclose(u8.data.font_size, 0.762, abs_tol=1e-6)

    # Fab note font_key=9 -> 125 mil = 3.175 mm.
    note = by_id["allegro:633029016"]
    assert isinstance(note.data, PcbText)
    assert "SURFACE FINISH" in note.data.text
    assert math.isclose(note.data.font_size, 3.175, abs_tol=1e-6)


def test_allegro_text_unresolved_size_diagnostics_are_eliminated() -> None:
    """The 0x36 text table resolves every fixture font key: no unresolved sizes."""
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    unresolved = [d for d in graphics.diagnostics if d.code == "unresolved-text-size"]
    assert unresolved == []


def test_allegro_text_parameter_table_is_parsed() -> None:
    """The 0x36 code-0x08 record exposes native text-parameter item fields."""
    record_set = parse_allegro_records(ROHM_BOARD.read_bytes(), source_name=ROHM_BOARD.name)

    table = next(
        record
        for record in record_set.records
        if record.tag == 0x36 and record.payload.get("code") == 0x08
    )
    items = table.payload["text_parameter_items"]
    assert isinstance(items, tuple)
    # rohm divisor 100: text block 1 char height 2500 native units = 25 mil.
    assert items[0][2] == 2500


def test_allegro_text_justification_maps_alignment_codes() -> None:
    """Alignment codes map to renderer justify tokens (1=left, 2=right, 3=center)."""
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)
    by_id = {primitive.id: primitive for primitive in graphics.artwork}

    # align=1 (fab note) -> left; align=3 (U8) -> center ""; align=2 (back title) -> right.
    note = by_id["allegro:633029016"].data
    u8 = by_id["allegro:633255704"].data
    back_title = by_id["allegro:633110304"].data
    assert isinstance(note, PcbText) and note.justify == "left"
    assert isinstance(u8, PcbText) and u8.justify == ""
    assert isinstance(back_title, PcbText) and back_title.justify == "right"


def test_allegro_text_mirroring_from_reversal_code() -> None:
    """Nonzero text_reversal_code sets mirrored=True; zero sets False."""
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)
    by_id = {primitive.id: primitive for primitive in graphics.artwork}

    front = by_id["allegro:633255704"].data  # U8, reversal 0
    back = by_id["allegro:633270168"].data  # D10, reversal 1
    assert isinstance(front, PcbText) and front.mirrored is False
    assert isinstance(back, PcbText) and back.mirrored is True
    back_primitive = by_id["allegro:633270168"]
    assert back_primitive.metadata.properties["native_text_reversal_code"] == "1"


def test_allegro_graphics_extract_board_profile_primitives_with_native_provenance() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert graphics.board_profile
    assert any(
        primitive.has_role(AllegroPrimitiveRole.BOARD_PROFILE)
        and primitive.metadata.properties["native_class_id"] == "1"
        and primitive.metadata.properties["native_subclass_id"] in {"234", "253"}
        for primitive in graphics.board_profile
    )
    assert all(
        not primitive.has_role(AllegroPrimitiveRole.ARTWORK) for primitive in graphics.board_profile
    )


def test_allegro_graphics_arc_midpoint_is_on_arc_not_center() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    arc = next(primitive for primitive in graphics.artwork if primitive.id == "allegro:633220424")
    assert isinstance(arc.data, PcbArc)
    source = record_set.by_key[633_220_424]
    header = record_set.header
    assert header is not None
    center_x_raw = source.payload["center_x"]
    center_y_raw = source.payload["center_y"]
    radius_raw = source.payload["radius"]
    assert isinstance(center_x_raw, int | float)
    assert isinstance(center_y_raw, int | float)
    assert isinstance(radius_raw, int | float)
    center_x = float(center_x_raw) / header.unit_divisor * 0.0254
    center_y = -(float(center_y_raw) / header.unit_divisor * 0.0254)
    radius = float(radius_raw) / header.unit_divisor * 0.0254

    _assert_close(math.hypot(arc.data.mid_x - center_x, arc.data.mid_y - center_y), radius)
    assert math.hypot(arc.data.mid_x - center_x, arc.data.mid_y - center_y) > 0


def test_allegro_graphics_full_circle_arc_records_become_circles() -> None:
    """Proves full-circle Allegro arc segments are not emitted as zero-length arcs."""
    record_set = parse_allegro_records(ROHM_BOARD.read_bytes(), source_name=ROHM_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    circle = next(
        primitive for primitive in graphics.artwork if primitive.id == "allegro:109689296"
    )
    assert isinstance(circle.data, PcbCircle)
    assert circle.kind.value == "circle"
    _assert_close(circle.data.cx, 20.32)
    _assert_close(circle.data.cy, -31.75)
    _assert_close(circle.data.radius, 1.27)
    _assert_close(circle.data.width, 0.0254)


def test_allegro_graphics_board_assembles_geometry_backed_board_profile() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    board = build_allegro_graphics_board(record_set, name=BREAKOUT_BOARD.stem)

    assert board.board_profile is not None
    assert len(board.board_profile.elements) == 4
    bbox = board.bbox()
    assert bbox is not None
    for actual, expected in zip(bbox, (0.0, -76.2, 76.2, -0.0), strict=True):
        _assert_close(actual, expected)
    assert {
        element.metadata.properties["native_class_id"] for element in board.board_profile.elements
    } == {"1"}
    assert {
        element.metadata.properties["native_subclass_id"]
        for element in board.board_profile.elements
    } == {"253"}


def test_allegro_graphics_board_counts_layer_and_graphics_diagnostics() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)
    graphics = extract_allegro_graphics(record_set, layer_map)

    board = build_allegro_graphics_board(record_set, name=BREAKOUT_BOARD.stem)

    expected_count = len(layer_map.diagnostics) + len(graphics.diagnostics)
    assert len(layer_map.diagnostics) > 0
    assert board.metadata.properties["parse_diagnostic_count"] == str(expected_count)


def test_allegro_graphics_extracts_text_artwork_with_layer_provenance() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    matching = [
        primitive
        for primitive in graphics.artwork
        if primitive.kind.value == "text"
        and isinstance(primitive.data, PcbText)
        and primitive.data.text == "C900H450"
    ]
    assert matching
    text = matching[0]
    assert text.has_role(AllegroPrimitiveRole.TEXT)
    assert text.metadata.properties["native_class_id"] == "9"
    assert text.metadata.properties["native_subclass_id"] == "248"
    assert text.metadata.properties["native_text_key"] == "632913056"
    assert text.metadata.properties["native_font_key"] == "60"
    assert not any(
        diagnostic.code == "unresolved-text-size" and diagnostic.key == 632_913_096
        for diagnostic in graphics.diagnostics
    )


def test_allegro_graphics_excludes_shape_owned_voids_from_keepouts() -> None:
    """Shape voids are cut into the copper fill as holes; they must not also be
    emitted as standalone keepouts, which would double-render the clearance."""
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)
    graph = build_allegro_object_graph(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    shape_owned_void_keys: set[int] = set()
    for shape in (record for record in record_set.records if record.tag == 0x28):
        walk = graph.walk_key_chain(
            head_key=payload_int(shape, "first_keepout_key"),
            owner_key=shape.key,
            expected_tags=frozenset({0x34}),
        )
        shape_owned_void_keys.update(void.key for void in walk.records if void.key is not None)

    assert shape_owned_void_keys  # fixture actually carries shape voids
    keepout_keys = {int(primitive.id.split(":")[1]) for primitive in graphics.keepouts}
    assert keepout_keys.isdisjoint(shape_owned_void_keys)
    # 634443400 was previously double-rendered as a keepout; it is a shape void.
    assert 634_443_400 in shape_owned_void_keys
    assert not any(primitive.id == "allegro:634443400" for primitive in graphics.keepouts)


def test_allegro_graphics_preserves_keepout_arc_segments() -> None:
    """Proves a keepout boundary keeps native arc curvature as ARC path segments."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(source)
    keepout = AllegroRecord(
        tag=0x34,
        offset=100,
        end_offset=120,
        key=10,
        next_key=None,
        payload={
            "layer_class_id": 21,
            "layer_subclass_id": 0,
            "first_segment_key": 20,
        },
    )
    arc = AllegroRecord(
        tag=0x01,
        offset=120,
        end_offset=160,
        key=20,
        next_key=21,
        payload={
            "subtype": 0,
            "parent_key": 10,
            "width": 0,
            "start_x": 1000,
            "start_y": 0,
            "end_x": 0,
            "end_y": 1000,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1000.0,
        },
    )
    line_1 = AllegroRecord(
        tag=0x15,
        offset=160,
        end_offset=180,
        key=21,
        next_key=22,
        payload={
            "parent_key": 10,
            "start_x": 0,
            "start_y": 1000,
            "end_x": 0,
            "end_y": 0,
        },
    )
    line_2 = AllegroRecord(
        tag=0x15,
        offset=180,
        end_offset=200,
        key=22,
        next_key=None,
        payload={
            "parent_key": 10,
            "start_x": 0,
            "start_y": 0,
            "end_x": 1000,
            "end_y": 0,
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(keepout, arc, line_1, line_2),
        end_offset=line_2.end_offset,
    )

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert len(graphics.keepouts) == 1
    data = graphics.keepouts[0].data
    assert isinstance(data, PcbClosedPath)
    arc_segments = [segment for segment in data.segments if segment.kind is PcbPathSegmentKind.ARC]
    assert len(arc_segments) == 1
    assert not any("approximated" in diagnostic.code for diagnostic in graphics.diagnostics)


def test_allegro_graphics_extracts_rectangle_artwork() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    rectangle = next(
        primitive for primitive in graphics.artwork if primitive.id == "allegro:633092192"
    )
    assert rectangle.kind.value == "rectangle"
    assert isinstance(rectangle.data, PcbPolygon)
    assert len(rectangle.data.points) == 4
    assert not rectangle.data.fill
    assert rectangle.data.width > 0.0
    assert rectangle.metadata.properties["native_class_id"] == "4"
    assert rectangle.metadata.properties["native_subclass_id"] == "13"


def test_allegro_rectangle_fill_follows_physical_aperture_layers() -> None:
    keepout_layer = PcbLayer(name="ROUTE KEEPOUT", roles=(LayerRole.KEEPOUT,))
    copper_layer = PcbLayer(name="TOP", roles=(LayerRole.COPPER,))
    layer_map = AllegroLayerMap(
        layers=(keepout_layer, copper_layer),
        stackup=None,
        by_class_subclass={(0x0F, 0xFD): keepout_layer, (0x05, 0x00): copper_layer},
    )
    frame = BoardFrame(unit_to_mm=1e-5)

    def _rectangle(class_id: int, subclass_id: int) -> PcbPolygon:
        record = AllegroRecord(
            tag=0x0E,
            offset=0,
            end_offset=20,
            key=1,
            next_key=None,
            payload={
                "layer_class_id": class_id,
                "layer_subclass_id": subclass_id,
                "coords": (0, 0, 1000, 2000),
            },
        )
        primitive = rectangle_primitive(record, frame=frame, layer_map=layer_map, diagnostics=[])
        assert primitive is not None
        assert isinstance(primitive.data, PcbPolygon)
        return primitive.data

    # Documentation graphics (keepout here) render as stroked outlines, not
    # filled slabs — fill only physical apertures.
    keepout_rect = _rectangle(0x0F, 0xFD)
    assert keepout_rect.fill is False
    assert keepout_rect.width > 0.0

    # Copper apertures stay filled.
    copper_rect = _rectangle(0x05, 0x00)
    assert copper_rect.fill is True
    assert copper_rect.width == 0.0


def _rotated_rectangle(rotation_mdeg: int) -> PcbPolygon:
    copper_layer = PcbLayer(name="TOP", roles=(LayerRole.COPPER,))
    layer_map = AllegroLayerMap(
        layers=(copper_layer,),
        stackup=None,
        by_class_subclass={(0x05, 0x00): copper_layer},
    )
    frame = BoardFrame(unit_to_mm=1e-5)
    record = AllegroRecord(
        tag=0x0E,
        offset=0,
        end_offset=20,
        key=1,
        next_key=None,
        payload={
            "layer_class_id": 0x05,
            "layer_subclass_id": 0x00,
            "coords": (0, 0, 1000, 2000),
            "rotation_mdeg": rotation_mdeg,
        },
    )
    primitive = rectangle_primitive(record, frame=frame, layer_map=layer_map, diagnostics=[])
    assert primitive is not None
    assert isinstance(primitive.data, PcbPolygon)
    return primitive.data


def test_allegro_rectangle_rotation_preserves_center_area_and_radius() -> None:
    upright = _rotated_rectangle(0)
    rotated = _rotated_rectangle(30_000)

    from shapely import Polygon

    up_poly = Polygon(upright.points)
    rot_poly = Polygon(rotated.points)

    # Center and area (a rigid rotation) are preserved; the shape is no longer
    # axis-aligned.
    assert rot_poly.area == pytest.approx(up_poly.area, rel=1e-9)
    assert rot_poly.centroid.x == pytest.approx(up_poly.centroid.x, abs=1e-12)
    assert rot_poly.centroid.y == pytest.approx(up_poly.centroid.y, abs=1e-12)

    cx = sum(x for x, _ in upright.points) / 4.0
    cy = sum(y for _, y in upright.points) / 4.0
    for (ux, uy), (rx, ry) in zip(upright.points, rotated.points, strict=True):
        # Each corner keeps its distance from the center and moves by 30 degrees.
        assert math.hypot(rx - cx, ry - cy) == pytest.approx(math.hypot(ux - cx, uy - cy))
    # At least one corner has visibly moved (not still axis-aligned).
    assert any(
        abs(rx - ux) > 1e-6 for (ux, _), (rx, _) in zip(upright.points, rotated.points, strict=True)
    )


def test_allegro_rectangle_right_angle_rotation_swaps_extents() -> None:
    upright = _rotated_rectangle(0)
    rotated = _rotated_rectangle(90_000)

    ux = [x for x, _ in upright.points]
    uy = [y for _, y in upright.points]
    rx = [x for x, _ in rotated.points]
    ry = [y for _, y in rotated.points]

    # 90-degree rotation about the center swaps the bbox width and height.
    assert (max(rx) - min(rx)) == pytest.approx(max(uy) - min(uy))
    assert (max(ry) - min(ry)) == pytest.approx(max(ux) - min(ux))
    assert sum(rx) / 4.0 == pytest.approx(sum(ux) / 4.0)
    assert sum(ry) / 4.0 == pytest.approx(sum(uy) / 4.0)


def test_allegro_rotated_fixture_rectangles_swap_extents_about_center() -> None:
    # Every rotated rectangle in the fixtures uses a right-angle rotation; a
    # 90/270 rotation about the center swaps the coords bbox width and height
    # while holding the center, and 180 leaves it unchanged. A corner anchor
    # (the wrong one) would move the center instead.
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)
    frame = board_frame(record_set.header)
    assert frame is not None

    checked = 0
    for record in record_set.records:
        if record.tag not in {0x0E, 0x24}:
            continue
        rotation_mdeg = payload_int(record, "rotation_mdeg")
        coords = record.payload.get("coords")
        if not rotation_mdeg or not isinstance(coords, tuple):
            continue
        primitive = rectangle_primitive(record, frame=frame, layer_map=layer_map, diagnostics=[])
        if primitive is None or not isinstance(primitive.data, PcbPolygon):
            continue
        checked += 1
        x0, y0, x1, y1 = coords
        raw_w = abs(x1 - x0) * frame.unit_to_mm
        raw_h = abs(y1 - y0) * frame.unit_to_mm
        raw_cx = (frame.x(x0) + frame.x(x1)) / 2.0
        raw_cy = (frame.y(y0) + frame.y(y1)) / 2.0
        xs = [x for x, _ in primitive.data.points]
        ys = [y for _, y in primitive.data.points]
        out_w = max(xs) - min(xs)
        out_h = max(ys) - min(ys)
        assert (sum(xs) / 4.0) == pytest.approx(raw_cx, abs=1e-9)
        assert (sum(ys) / 4.0) == pytest.approx(raw_cy, abs=1e-9)
        if (rotation_mdeg // 1000) % 180 == 90:
            assert out_w == pytest.approx(raw_h, abs=1e-9)
            assert out_h == pytest.approx(raw_w, abs=1e-9)
        else:
            assert out_w == pytest.approx(raw_w, abs=1e-9)
            assert out_h == pytest.approx(raw_h, abs=1e-9)
    assert checked > 0


def test_allegro_graphics_extracts_outline_rectangles_as_board_profile() -> None:
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(source)
    record = AllegroRecord(
        tag=0x24,
        offset=100,
        end_offset=120,
        key=50,
        next_key=None,
        payload={
            "layer_class_id": 1,
            "layer_subclass_id": 253,
            "coords": (0, 0, 1000, 2000),
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(record,),
        end_offset=record.end_offset,
    )

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert graphics.artwork == ()
    assert len(graphics.board_profile) == 1
    rectangle = graphics.board_profile[0]
    assert rectangle.has_role(AllegroPrimitiveRole.BOARD_PROFILE)
    assert not rectangle.has_role(AllegroPrimitiveRole.ARTWORK)
    assert isinstance(rectangle.data, PcbPolygon)
    assert rectangle.metadata.properties["native_subclass_id"] == "253"


def _shape_record_set(
    source: AllegroRecordSet,
    *,
    layer_class_id: int,
    layer_subclass_id: int,
    owner_key: int = 0,
    owner: AllegroRecord | None = None,
) -> AllegroRecordSet:
    """A 0x28 shape (triangle boundary + one triangular void) plus its chains."""
    shape = AllegroRecord(
        tag=0x28,
        offset=100,
        end_offset=120,
        key=10,
        next_key=None,
        payload={
            "layer_class_id": layer_class_id,
            "layer_subclass_id": layer_subclass_id,
            "owner_key": owner_key,
            "first_keepout_key": 30,
            "first_segment_key": 20,
        },
    )
    boundary = [
        AllegroRecord(
            tag=0x15,
            offset=120 + 20 * i,
            end_offset=140 + 20 * i,
            key=20 + i,
            next_key=(20 + i + 1) if i < 2 else 10,
            payload={"parent_key": 10, "start_x": sx, "start_y": sy, "end_x": ex, "end_y": ey},
        )
        for i, (sx, sy, ex, ey) in enumerate(
            [(0, 0, 4000, 0), (4000, 0, 4000, 4000), (4000, 4000, 0, 0)]
        )
    ]
    void = AllegroRecord(
        tag=0x34,
        offset=200,
        end_offset=220,
        key=30,
        next_key=10,
        payload={
            "layer_class_id": layer_class_id,
            "layer_subclass_id": layer_subclass_id,
            "first_segment_key": 40,
        },
    )
    void_segments = [
        AllegroRecord(
            tag=0x15,
            offset=220 + 20 * i,
            end_offset=240 + 20 * i,
            key=40 + i,
            next_key=(40 + i + 1) if i < 2 else 30,
            payload={"parent_key": 30, "start_x": sx, "start_y": sy, "end_x": ex, "end_y": ey},
        )
        for i, (sx, sy, ex, ey) in enumerate(
            [(1000, 1000, 2000, 1000), (2000, 1000, 2000, 2000), (2000, 2000, 1000, 1000)]
        )
    ]
    records = (shape, *boundary, void, *void_segments)
    if owner is not None:
        records = (owner, *records)
    return AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=records,
        end_offset=void_segments[-1].end_offset,
    )


def test_allegro_graphics_extracts_non_etch_shape_as_artwork_polygon() -> None:
    """A non-etch 0x28 shape becomes a closed-path artwork polygon with voids."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    silk_layer = PcbLayer(name="SILKSCREEN TOP", roles=(LayerRole.SILKSCREEN,))
    layer_map = AllegroLayerMap(
        layers=(silk_layer,),
        stackup=None,
        by_class_subclass={(0x09, 0x00): silk_layer},
    )
    record_set = _shape_record_set(source, layer_class_id=0x09, layer_subclass_id=0x00)

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert len(graphics.artwork) == 1
    assert graphics.board_profile == ()
    shape = graphics.artwork[0]
    assert shape.id == "allegro:10"
    assert shape.kind.value == "polygon"
    assert shape.has_role(AllegroPrimitiveRole.ARTWORK)
    assert isinstance(shape.data, PcbClosedPath)
    assert len(shape.data.segments) == 3
    assert len(shape.data.holes) == 1


def test_allegro_graphics_does_not_emit_etch_shapes() -> None:
    """Etch-class 0x28 shapes belong to copper extraction, not graphics."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    copper_layer = PcbLayer(name="TOP", roles=(LayerRole.COPPER,))
    layer_map = AllegroLayerMap(
        layers=(copper_layer,),
        stackup=None,
        by_class_subclass={(0x06, 0x00): copper_layer},
    )
    record_set = _shape_record_set(source, layer_class_id=0x06, layer_subclass_id=0x00)

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert graphics.artwork == ()
    assert graphics.board_profile == ()


def test_allegro_graphics_skips_footprint_definition_owned_shapes() -> None:
    """Shapes owned by a footprint definition (0x2B) are local symbol geometry."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    silk_layer = PcbLayer(name="SILKSCREEN TOP", roles=(LayerRole.SILKSCREEN,))
    layer_map = AllegroLayerMap(
        layers=(silk_layer,),
        stackup=None,
        by_class_subclass={(0x09, 0x00): silk_layer},
    )
    footprint_def = AllegroRecord(
        tag=0x2B, offset=0, end_offset=20, key=99, next_key=None, payload={}
    )
    record_set = _shape_record_set(
        source, layer_class_id=0x09, layer_subclass_id=0x00, owner_key=99, owner=footprint_def
    )

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert graphics.artwork == ()
    assert graphics.board_profile == ()


def test_allegro_graphics_routes_board_outline_shape_to_board_profile() -> None:
    """A 0x28 shape on a board-outline subclass lands in board_profile."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    outline_layer = PcbLayer(
        name="BOARD GEOMETRY OUTLINE",
        roles=(LayerRole.BOARD_SHAPE, LayerRole.EDGE),
    )
    layer_map = AllegroLayerMap(
        layers=(outline_layer,),
        stackup=None,
        by_class_subclass={(0x01, 0xFD): outline_layer},
    )
    record_set = _shape_record_set(source, layer_class_id=0x01, layer_subclass_id=0xFD)

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert graphics.artwork == ()
    assert len(graphics.board_profile) == 1
    outline = graphics.board_profile[0]
    assert outline.has_role(AllegroPrimitiveRole.BOARD_PROFILE)
    assert isinstance(outline.data, PcbClosedPath)


def test_allegro_graphics_preserves_drc_markers_as_diagnostics() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert any(
        diagnostic.code == "drc-marker" and diagnostic.key == 634_518_400 and diagnostic.tag == 0x0A
        for diagnostic in graphics.diagnostics
    )


def test_allegro_graphics_reports_unresolved_artwork_layers() -> None:
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    record = AllegroRecord(
        tag=0x24,
        offset=100,
        end_offset=120,
        key=1,
        next_key=None,
        payload={
            "layer_class_id": 4,
            "layer_subclass_id": 13,
            "coords": (0, 0, 100, 100),
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(record,),
        end_offset=record.end_offset,
    )
    layer_map = AllegroLayerMap(layers=(), stackup=None, by_class_subclass={})

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert graphics.artwork == ()
    assert any(
        diagnostic.code == "unresolved-graphic-layer"
        and diagnostic.key == 1
        and diagnostic.tag == 0x24
        for diagnostic in graphics.diagnostics
    )


def test_allegro_graphics_reports_segment_chain_truncation_diagnostics() -> None:
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(source)
    missing_segment_graphic = AllegroRecord(
        tag=0x14,
        offset=100,
        end_offset=120,
        key=100,
        next_key=None,
        payload={
            "layer_class_id": 1,
            "layer_subclass_id": 240,
            "segment_key": 999,
        },
    )
    mismatched_keepout = AllegroRecord(
        tag=0x34,
        offset=120,
        end_offset=140,
        key=200,
        next_key=None,
        payload={
            "layer_class_id": 21,
            "layer_subclass_id": 0,
            "first_segment_key": 201,
        },
    )
    mismatched_segment = AllegroRecord(
        tag=0x15,
        offset=140,
        end_offset=160,
        key=201,
        next_key=None,
        payload={
            "parent_key": 999,
            "start_x": 0,
            "start_y": 0,
        },
    )
    cyclic_graphic = AllegroRecord(
        tag=0x14,
        offset=160,
        end_offset=180,
        key=300,
        next_key=None,
        payload={
            "layer_class_id": 1,
            "layer_subclass_id": 240,
            "segment_key": 301,
        },
    )
    cyclic_segment = AllegroRecord(
        tag=0x15,
        offset=180,
        end_offset=200,
        key=301,
        next_key=301,
        payload={
            "parent_key": 300,
            "start_x": 0,
            "start_y": 0,
            "end_x": 1000,
            "end_y": 0,
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(
            missing_segment_graphic,
            mismatched_keepout,
            mismatched_segment,
            cyclic_graphic,
            cyclic_segment,
        ),
        end_offset=cyclic_segment.end_offset,
    )

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert {
        (diagnostic.code, diagnostic.key, diagnostic.reference_key)
        for diagnostic in graphics.diagnostics
        if diagnostic.code
        in {
            "unresolved-segment-record",
            "segment-owner-mismatch",
            "segment-chain-cycle",
        }
    } == {
        ("unresolved-segment-record", 100, 999),
        ("segment-owner-mismatch", 200, 201),
        ("segment-chain-cycle", 300, 301),
    }


def test_allegro_graphics_terminates_segment_chain_at_owner_ring() -> None:
    """Proves a segment chain that rings back to its owner terminates cleanly.

    Allegro segment chains are circular: the final segment's ``next_key`` points
    back at the owning shape record. That owner record is not a segment, so it
    must terminate the walk without a ``segment-owner-mismatch`` diagnostic while
    still yielding every segment before the ring closes.
    """
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(source)
    keepout = AllegroRecord(
        tag=0x34,
        offset=100,
        end_offset=120,
        key=10,
        next_key=None,
        payload={
            "layer_class_id": 21,
            "layer_subclass_id": 0,
            "first_segment_key": 20,
        },
    )
    line_1 = AllegroRecord(
        tag=0x15,
        offset=120,
        end_offset=140,
        key=20,
        next_key=21,
        payload={"parent_key": 10, "start_x": 0, "start_y": 0},
    )
    line_2 = AllegroRecord(
        tag=0x15,
        offset=140,
        end_offset=160,
        key=21,
        next_key=22,
        payload={"parent_key": 10, "start_x": 1000, "start_y": 0},
    )
    line_3 = AllegroRecord(
        tag=0x15,
        offset=160,
        end_offset=180,
        key=22,
        next_key=10,
        payload={"parent_key": 10, "start_x": 1000, "start_y": 1000},
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(keepout, line_1, line_2, line_3),
        end_offset=line_3.end_offset,
    )

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert len(graphics.keepouts) == 1
    assert isinstance(graphics.keepouts[0].data, PcbClosedPath)
    assert len(graphics.keepouts[0].data.points) == 3
    assert not any(
        diagnostic.code == "segment-owner-mismatch" for diagnostic in graphics.diagnostics
    )


def test_allegro_graphics_skips_footprint_definition_owned_graphic_chains() -> None:
    """0x14 chains parented by a footprint definition are unplaced symbol masters.

    Their placed copies carry 0x2D instance parents; rendering the masters too
    piles their local-coordinate line art onto the board origin.
    """
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    silk_layer = PcbLayer(name="SILKSCREEN TOP", roles=(LayerRole.SILKSCREEN,))
    layer_map = AllegroLayerMap(
        layers=(silk_layer,),
        stackup=None,
        by_class_subclass={(0x09, 0x00): silk_layer},
    )
    footprint_def = AllegroRecord(
        tag=0x2B, offset=0, end_offset=20, key=990_010_050, next_key=None, payload={}
    )
    graphic = AllegroRecord(
        tag=0x14,
        offset=20,
        end_offset=40,
        key=990_010_100,
        next_key=None,
        payload={
            "layer_class_id": 0x09,
            "layer_subclass_id": 0x00,
            "parent_key": footprint_def.key or 0,
            "segment_key": 990_010_101,
        },
    )
    segment = AllegroRecord(
        tag=0x15,
        offset=40,
        end_offset=60,
        key=990_010_101,
        next_key=None,
        payload={
            "parent_key": graphic.key or 0,
            "width": 100,
            "start_x": 0,
            "start_y": 0,
            "end_x": 1000,
            "end_y": 0,
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(footprint_def, graphic, segment),
        end_offset=segment.end_offset,
    )

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert graphics.artwork == ()


def test_allegro_graphics_skips_footprint_definition_owned_rectangles() -> None:
    """0x0E rectangles whose footprint_key targets a 0x2B definition are masters."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    silk_layer = PcbLayer(name="SILKSCREEN TOP", roles=(LayerRole.SILKSCREEN,))
    layer_map = AllegroLayerMap(
        layers=(silk_layer,),
        stackup=None,
        by_class_subclass={(0x09, 0x00): silk_layer},
    )
    footprint_def = AllegroRecord(
        tag=0x2B, offset=0, end_offset=20, key=990_011_050, next_key=None, payload={}
    )
    rectangle = AllegroRecord(
        tag=0x0E,
        offset=20,
        end_offset=60,
        key=990_011_100,
        next_key=None,
        payload={
            "layer_class_id": 0x09,
            "layer_subclass_id": 0x00,
            "footprint_key": footprint_def.key or 0,
            "coords": (0, 0, 1000, 1000),
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(footprint_def, rectangle),
        end_offset=rectangle.end_offset,
    )

    graphics = extract_allegro_graphics(record_set, layer_map)

    assert graphics.artwork == ()


def test_allegro_graphics_skips_footprint_definition_owned_texts() -> None:
    """Text chains ring back to their owner; masters (0x2B terminator) are
    unplaced symbol text and must not render at local coordinates."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    silk_layer = PcbLayer(name="SILKSCREEN TOP", roles=(LayerRole.SILKSCREEN,))
    layer_map = AllegroLayerMap(
        layers=(silk_layer,),
        stackup=None,
        by_class_subclass={(0x09, 0x00): silk_layer},
    )
    footprint_def = AllegroRecord(
        tag=0x2B, offset=0, end_offset=20, key=990_012_050, next_key=None, payload={}
    )
    instance = AllegroRecord(
        tag=0x2D, offset=20, end_offset=40, key=990_012_060, next_key=None, payload={}
    )
    string_master = AllegroRecord(
        tag=0x31,
        offset=40,
        end_offset=60,
        key=990_012_101,
        next_key=None,
        payload={"text": "24"},
    )
    master_text = AllegroRecord(
        tag=0x30,
        offset=60,
        end_offset=80,
        key=990_012_100,
        next_key=footprint_def.key,
        payload={
            "layer_class_id": 0x09,
            "layer_subclass_id": 0x00,
            "string_graphic_key": string_master.key or 0,
            "x": 0,
            "y": 0,
            "rotation_mdeg": 0,
            "font_key": 0,
            "text_alignment_code": 1,
            "text_reversal_code": 0,
        },
    )
    string_placed = AllegroRecord(
        tag=0x31,
        offset=80,
        end_offset=100,
        key=990_012_201,
        next_key=None,
        payload={"text": "R1"},
    )
    placed_text = AllegroRecord(
        tag=0x30,
        offset=100,
        end_offset=120,
        key=990_012_200,
        next_key=instance.key,
        payload={
            "layer_class_id": 0x09,
            "layer_subclass_id": 0x00,
            "string_graphic_key": string_placed.key or 0,
            "x": 1000,
            "y": 1000,
            "rotation_mdeg": 0,
            "font_key": 0,
            "text_alignment_code": 1,
            "text_reversal_code": 0,
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(footprint_def, instance, string_master, master_text, string_placed, placed_text),
        end_offset=placed_text.end_offset,
    )

    graphics = extract_allegro_graphics(record_set, layer_map)

    texts = [primitive for primitive in graphics.artwork if isinstance(primitive.data, PcbText)]
    assert [primitive.id for primitive in texts] == ["allegro:990012200"]
