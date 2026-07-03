from __future__ import annotations

import math
from pathlib import Path

from phosphor_eda.domain.pcb import LayerRole, PcbArc, PcbCircle, PcbLayer, PcbPolygon, PcbText
from phosphor_eda.formats.allegro.build import build_allegro_graphics_board
from phosphor_eda.formats.allegro.coords import BoardFrame
from phosphor_eda.formats.allegro.graph import build_allegro_object_graph
from phosphor_eda.formats.allegro.graphics import extract_allegro_graphics, rectangle_primitive
from phosphor_eda.formats.allegro.layers import AllegroLayerMap, build_allegro_layers
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.primitives import AllegroPrimitiveRole
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BREAKOUT_BOARD = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
ROHM_BOARD = (
    FIXTURES
    / "orcad"
    / "rohm-stepper-driver-ctrl"
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


def test_allegro_text_graphics_use_renderable_fallback_font_size() -> None:
    """Proves unresolved native Allegro text sizes still render visibly."""
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    text = next(primitive for primitive in graphics.artwork if primitive.id == "allegro:632913096")
    assert isinstance(text.data, PcbText)
    assert text.data.text == "C900H450"
    assert text.data.font_size > 0
    assert any(
        diagnostic.code == "unresolved-text-size" and diagnostic.key == 632_913_096
        for diagnostic in graphics.diagnostics
    )


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
    assert any(
        diagnostic.code == "unresolved-text-size"
        and diagnostic.key == 632_913_096
        and diagnostic.reference_key == 632_913_056
        for diagnostic in graphics.diagnostics
    )


def test_allegro_graphics_extracts_keepouts_with_native_layer_provenance() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    layer_map = build_allegro_layers(record_set)

    graphics = extract_allegro_graphics(record_set, layer_map)

    keepout = next(
        primitive for primitive in graphics.keepouts if primitive.id == "allegro:634443400"
    )
    assert keepout.has_role(AllegroPrimitiveRole.KEEPOUT)
    assert isinstance(keepout.data, PcbPolygon)
    assert len(keepout.data.points) >= 8
    assert keepout.metadata.properties["native_class_id"] == "21"
    assert keepout.metadata.properties["native_subclass_id"] == "0"

    board = build_allegro_graphics_board(record_set, name=BREAKOUT_BOARD.stem)
    domain_keepout = board.keepout_for("allegro:634443400")
    assert domain_keepout is not None
    assert len(domain_keepout.boundary.segments) == len(keepout.data.points)
    assert domain_keepout.metadata.properties["native_class_id"] == "21"


def test_allegro_graphics_reports_keepout_arc_approximation() -> None:
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
            "parent_key": 10,
            "start_x": 0,
            "start_y": 0,
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
            "start_x": 1000,
            "start_y": 0,
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
            "start_x": 1000,
            "start_y": 1000,
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
    assert any(
        diagnostic.code == "approximated-keepout-arc"
        and diagnostic.key == 10
        and diagnostic.reference_key == 20
        for diagnostic in graphics.diagnostics
    )


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
    assert isinstance(graphics.keepouts[0].data, PcbPolygon)
    assert len(graphics.keepouts[0].data.points) == 3
    assert not any(
        diagnostic.code == "segment-owner-mismatch" for diagnostic in graphics.diagnostics
    )
