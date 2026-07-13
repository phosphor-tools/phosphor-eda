from __future__ import annotations

from pathlib import Path

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbClosedPath,
    PcbConductorKind,
    PcbPathSegmentKind,
    PcbPourFillMode,
)
from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.copper import extract_allegro_copper
from phosphor_eda.formats.allegro.layers import build_allegro_layers
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
LAUNCHXL_BOARD = (
    UPSTREAM_FIXTURES
    / "cp-smartgarden"
    / "Document/Hardware/mcu/swrc319/Cadence/Allegro"
    / "LAUNCHXL-CC1310.brd"
)


def test_allegro_board_assembly_links_net_owned_shape_fills_to_pours() -> None:
    """Proves net-owned Allegro 0x28 shapes preserve pour intent and fill geometry.

    The native net-assignment chain proves fill-to-net ownership for this
    fixture. It cannot prove Cadence dynamic shape rules beyond the preserved
    native metadata and diagnostics.
    """
    record_set = parse_allegro_records(LAUNCHXL_BOARD.read_bytes(), source_name=LAUNCHXL_BOARD.name)

    board = build_allegro_board(record_set, name=LAUNCHXL_BOARD.stem)

    fills = [
        conductor
        for conductor in board.conductors
        if conductor.kind is PcbConductorKind.POUR_FILL
        and conductor.metadata.native_type == "copper_shape_fill"
        and conductor.net is not None
    ]
    # Every board-level shape resolves its net (via the assignment chain or
    # its owner assignment); footprint-definition shapes are excluded.
    assert len(fills) == 54
    fill = next(fill for fill in fills if fill.id == "allegro:129098328:fill")
    assert fill.pour in board.pours
    assert fill.pour is not None
    assert fill in fill.pour.fills
    assert fill.net is not None
    assert fill.pour.net is fill.net
    assert fill.pour.settings.fill_mode is PcbPourFillMode.SOLID
    assert fill.layer.has_role(LayerRole.COPPER)
    assert fill.pour.layers == (fill.layer,)
    assert isinstance(fill.data, PcbClosedPath)
    assert len(fill.data.segments) >= 3
    assert fill.metadata.properties["native_net_key"] == "126734664"
    assert fill.metadata.properties["native_assignment_key"] == "129151432"
    assert fill.metadata.properties["native_layer_name"] == fill.layer.name


def test_allegro_shape_voids_emit_cut_positive_copper() -> None:
    """Proves native shape holes are preserved as cutouts in filled copper.

    The synthetic 0x28/0x34 relationship proves parser behavior for the native
    void pointer. It cannot prove Cadence dynamic shape rules beyond the
    preserved native void geometry.
    """
    source = parse_allegro_records(LAUNCHXL_BOARD.read_bytes(), source_name=LAUNCHXL_BOARD.name)
    net = AllegroRecord(
        tag=0x1B,
        offset=source.end_offset,
        end_offset=source.end_offset + 20,
        key=990_000_100,
        next_key=None,
        payload={"net_name_key": 0, "assignment_key": 990_000_101},
    )
    assignment = AllegroRecord(
        tag=0x04,
        offset=net.end_offset,
        end_offset=net.end_offset + 20,
        key=990_000_101,
        next_key=None,
        payload={"net_key": net.key or 0, "connected_item_key": 990_000_102},
    )
    shape = AllegroRecord(
        tag=0x28,
        offset=assignment.end_offset,
        end_offset=assignment.end_offset + 20,
        key=990_000_102,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_keepout_key": 990_000_200,
            "first_segment_key": 990_000_103,
        },
    )
    shape_segments = (
        _line_segment(990_000_103, shape.key or 0, 0, 0, 100_000, 0, 990_000_104),
        _line_segment(990_000_104, shape.key or 0, 100_000, 0, 100_000, 100_000, 990_000_105),
        _line_segment(990_000_105, shape.key or 0, 100_000, 100_000, 0, 100_000, 990_000_106),
        _line_segment(990_000_106, shape.key or 0, 0, 100_000, 0, 0, None),
    )
    void = AllegroRecord(
        tag=0x34,
        offset=shape_segments[-1].end_offset,
        end_offset=shape_segments[-1].end_offset + 20,
        key=990_000_200,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_segment_key": 990_000_201,
        },
    )
    void_segments = (
        _line_segment(990_000_201, void.key or 0, 25_000, 25_000, 75_000, 25_000, 990_000_202),
        _line_segment(990_000_202, void.key or 0, 75_000, 25_000, 75_000, 75_000, 990_000_203),
        _line_segment(990_000_203, void.key or 0, 75_000, 75_000, 25_000, 75_000, 990_000_204),
        _line_segment(990_000_204, void.key or 0, 25_000, 75_000, 25_000, 25_000, None),
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, net, assignment, shape, *shape_segments, void, *void_segments),
        end_offset=void_segments[-1].end_offset,
    )

    board = build_allegro_board(record_set, name=LAUNCHXL_BOARD.stem)

    pour = board.pour_for("allegro:990000102:pour")
    assert pour is not None
    assert len(pour.fills) == 1
    fill = pour.fills[0]
    assert fill.id == "allegro:990000102:fill"
    assert isinstance(fill.data, PcbClosedPath)
    assert len(fill.data.holes) == 1
    assert fill.data.holes[0].points == (
        (25.0, -25.0),
        (75.0, -25.0),
        (75.0, -75.0),
        (25.0, -75.0),
    )
    assert pour.settings.fill_mode is PcbPourFillMode.SOLID
    assert pour.metadata.properties["native_first_keepout_key"] == "990000200"


def test_allegro_partial_void_resolution_marks_pour_fill_mode_unknown() -> None:
    """A shape with two voids where one has a broken segment chain is not fully
    resolved: the pour reports UNKNOWN fill and a void diagnostic is emitted.

    ``native_void_hole_count`` alone cannot prove a solid fill because it is set
    as soon as any single void parses; only equality with the void chain length
    does.
    """
    source = parse_allegro_records(LAUNCHXL_BOARD.read_bytes(), source_name=LAUNCHXL_BOARD.name)
    net = AllegroRecord(
        tag=0x1B,
        offset=source.end_offset,
        end_offset=source.end_offset + 20,
        key=990_000_100,
        next_key=None,
        payload={"net_name_key": 0, "assignment_key": 990_000_101},
    )
    assignment = AllegroRecord(
        tag=0x04,
        offset=net.end_offset,
        end_offset=net.end_offset + 20,
        key=990_000_101,
        next_key=None,
        payload={"net_key": net.key or 0, "connected_item_key": 990_000_102},
    )
    shape = AllegroRecord(
        tag=0x28,
        offset=assignment.end_offset,
        end_offset=assignment.end_offset + 20,
        key=990_000_102,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_keepout_key": 990_000_200,
            "first_segment_key": 990_000_103,
        },
    )
    shape_segments = (
        _line_segment(990_000_103, shape.key or 0, 0, 0, 100_000, 0, 990_000_104),
        _line_segment(990_000_104, shape.key or 0, 100_000, 0, 100_000, 100_000, 990_000_105),
        _line_segment(990_000_105, shape.key or 0, 100_000, 100_000, 0, 100_000, 990_000_106),
        _line_segment(990_000_106, shape.key or 0, 0, 100_000, 0, 0, None),
    )
    resolved_void = AllegroRecord(
        tag=0x34,
        offset=shape_segments[-1].end_offset,
        end_offset=shape_segments[-1].end_offset + 20,
        key=990_000_200,
        next_key=990_000_300,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_segment_key": 990_000_201,
        },
    )
    resolved_void_segments = (
        _line_segment(
            990_000_201, resolved_void.key or 0, 25_000, 25_000, 75_000, 25_000, 990_000_202
        ),
        _line_segment(
            990_000_202, resolved_void.key or 0, 75_000, 25_000, 75_000, 75_000, 990_000_203
        ),
        _line_segment(
            990_000_203, resolved_void.key or 0, 75_000, 75_000, 25_000, 75_000, 990_000_204
        ),
        _line_segment(990_000_204, resolved_void.key or 0, 25_000, 75_000, 25_000, 25_000, None),
    )
    # Second void rings back to the shape but carries no segment chain, so it
    # never resolves into a hole: two voids on the chain, one parsed.
    broken_void = AllegroRecord(
        tag=0x34,
        offset=resolved_void_segments[-1].end_offset,
        end_offset=resolved_void_segments[-1].end_offset + 20,
        key=990_000_300,
        next_key=shape.key,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_segment_key": 0,
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(
            *source.records,
            net,
            assignment,
            shape,
            *shape_segments,
            resolved_void,
            *resolved_void_segments,
            broken_void,
        ),
        end_offset=broken_void.end_offset,
    )

    board = build_allegro_board(record_set, name=LAUNCHXL_BOARD.stem)

    pour = board.pour_for("allegro:990000102:pour")
    assert pour is not None
    assert pour.settings.fill_mode is PcbPourFillMode.UNKNOWN
    assert pour.metadata.properties["native_void_total_count"] == "2"
    assert pour.metadata.properties["native_void_hole_count"] == "1"
    assert len(pour.fills) == 1
    assert isinstance(pour.fills[0].data, PcbClosedPath)
    assert len(pour.fills[0].data.holes) == 1
    assert "missing-shape-void-segment-chain" in board.metadata.properties["parse_diagnostic_codes"]


def test_allegro_dynamic_shape_degradation_is_metadata_and_diagnostic() -> None:
    """Proves unsupported dynamic shape rules are visible after degradation.

    The synthetic V17.2-style dynamic flag proves preservation of the native
    discriminator. It cannot prove exact Allegro teardrop or thermal-rule
    reconstruction.
    """
    source = parse_allegro_records(LAUNCHXL_BOARD.read_bytes(), source_name=LAUNCHXL_BOARD.name)
    net = AllegroRecord(
        tag=0x1B,
        offset=source.end_offset,
        end_offset=source.end_offset + 20,
        key=990_001_100,
        next_key=None,
        payload={"net_name_key": 0, "assignment_key": 990_001_101},
    )
    assignment = AllegroRecord(
        tag=0x04,
        offset=net.end_offset,
        end_offset=net.end_offset + 20,
        key=990_001_101,
        next_key=None,
        payload={"net_key": net.key or 0, "connected_item_key": 990_001_102},
    )
    shape = AllegroRecord(
        tag=0x28,
        offset=assignment.end_offset,
        end_offset=assignment.end_offset + 20,
        key=990_001_102,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "dynamic_shape_flags": 0x1000,
            "first_segment_key": 990_001_103,
        },
    )
    shape_segments = (
        _line_segment(990_001_103, shape.key or 0, 0, 0, 100_000, 0, 990_001_104),
        _line_segment(990_001_104, shape.key or 0, 100_000, 0, 100_000, 100_000, 990_001_105),
        _line_segment(990_001_105, shape.key or 0, 100_000, 100_000, 0, 100_000, 990_001_106),
        _line_segment(990_001_106, shape.key or 0, 0, 100_000, 0, 0, None),
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, net, assignment, shape, *shape_segments),
        end_offset=shape_segments[-1].end_offset,
    )
    layer_map = build_allegro_layers(record_set)

    copper = extract_allegro_copper(record_set, layer_map)

    fill = next(
        conductor for conductor in copper.conductors if conductor.id == "allegro:990001102:fill"
    )
    pour = next(pour for pour in copper.pours if pour.id == "allegro:990001102:pour")
    assert fill.metadata.properties["native_dynamic_shape_flags"] == "4096"
    assert fill.metadata.properties["dynamic_shape_degraded"] == "true"
    assert pour.metadata.properties["dynamic_shape_degraded"] == "true"
    assert any(
        diagnostic.code == "unsupported-dynamic-shape-rules" and diagnostic.key == 990_001_102
        for diagnostic in copper.diagnostics
    )

    board = build_allegro_board(record_set, name=LAUNCHXL_BOARD.stem)
    domain_pour = board.pour_for("allegro:990001102:pour")
    assert domain_pour is not None
    assert domain_pour.settings.fill_mode is PcbPourFillMode.UNKNOWN


def test_allegro_shape_boundary_preserves_arc_segments() -> None:
    """Proves arc boundary segments survive into the pour path without chording.

    A shape whose east side is a semicircular bulge must keep that side as a
    native ARC path segment (with its on-arc midpoint) instead of collapsing to
    the segment vertices, and must not emit an approximation diagnostic.
    """
    source = parse_allegro_records(LAUNCHXL_BOARD.read_bytes(), source_name=LAUNCHXL_BOARD.name)
    net = AllegroRecord(
        tag=0x1B,
        offset=source.end_offset,
        end_offset=source.end_offset + 20,
        key=990_002_100,
        next_key=None,
        payload={"net_name_key": 0, "assignment_key": 990_002_101},
    )
    assignment = AllegroRecord(
        tag=0x04,
        offset=net.end_offset,
        end_offset=net.end_offset + 20,
        key=990_002_101,
        next_key=None,
        payload={"net_key": net.key or 0, "connected_item_key": 990_002_102},
    )
    shape = AllegroRecord(
        tag=0x28,
        offset=assignment.end_offset,
        end_offset=assignment.end_offset + 20,
        key=990_002_102,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_segment_key": 990_002_103,
        },
    )
    segments = (
        _line_segment(990_002_103, shape.key or 0, 0, 0, 100_000, 0, 990_002_104),
        _arc_segment(
            990_002_104,
            shape.key or 0,
            start=(100_000, 0),
            end=(100_000, 100_000),
            center=(100_000.0, 50_000.0),
            radius=50_000.0,
            next_key=990_002_105,
        ),
        _line_segment(990_002_105, shape.key or 0, 100_000, 100_000, 0, 100_000, 990_002_106),
        _line_segment(990_002_106, shape.key or 0, 0, 100_000, 0, 0, None),
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, net, assignment, shape, *segments),
        end_offset=segments[-1].end_offset,
    )
    layer_map = build_allegro_layers(record_set)

    copper = extract_allegro_copper(record_set, layer_map)

    pour = next(pour for pour in copper.pours if pour.id == "allegro:990002102:pour")
    arc_segments = [
        segment for segment in pour.boundary.segments if segment.kind is PcbPathSegmentKind.ARC
    ]
    assert len(arc_segments) == 1
    arc = arc_segments[0]
    assert (arc.start_x, arc.start_y) == (100.0, 0.0)
    assert (arc.end_x, arc.end_y) == (100.0, -100.0)
    assert (round(arc.mid_x, 6), round(arc.mid_y, 6)) == (150.0, -50.0)
    assert not any("approximated" in diagnostic.code for diagnostic in copper.diagnostics)


def test_allegro_circular_void_from_full_circle_arc_is_preserved() -> None:
    """Proves a circular void built from one full-circle arc becomes a hole.

    Previously the circle collapsed to a single vertex and the whole void was
    dropped, leaving solid copper over the clearance.
    """
    source = parse_allegro_records(LAUNCHXL_BOARD.read_bytes(), source_name=LAUNCHXL_BOARD.name)
    net = AllegroRecord(
        tag=0x1B,
        offset=source.end_offset,
        end_offset=source.end_offset + 20,
        key=990_003_100,
        next_key=None,
        payload={"net_name_key": 0, "assignment_key": 990_003_101},
    )
    assignment = AllegroRecord(
        tag=0x04,
        offset=net.end_offset,
        end_offset=net.end_offset + 20,
        key=990_003_101,
        next_key=None,
        payload={"net_key": net.key or 0, "connected_item_key": 990_003_102},
    )
    shape = AllegroRecord(
        tag=0x28,
        offset=assignment.end_offset,
        end_offset=assignment.end_offset + 20,
        key=990_003_102,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_keepout_key": 990_003_200,
            "first_segment_key": 990_003_103,
        },
    )
    shape_segments = (
        _line_segment(990_003_103, shape.key or 0, 0, 0, 100_000, 0, 990_003_104),
        _line_segment(990_003_104, shape.key or 0, 100_000, 0, 100_000, 100_000, 990_003_105),
        _line_segment(990_003_105, shape.key or 0, 100_000, 100_000, 0, 100_000, 990_003_106),
        _line_segment(990_003_106, shape.key or 0, 0, 100_000, 0, 0, None),
    )
    void = AllegroRecord(
        tag=0x34,
        offset=shape_segments[-1].end_offset,
        end_offset=shape_segments[-1].end_offset + 20,
        key=990_003_200,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_segment_key": 990_003_201,
        },
    )
    circle = _arc_segment(
        990_003_201,
        void.key or 0,
        start=(50_000, 25_000),
        end=(50_000, 25_000),
        center=(50_000.0, 50_000.0),
        radius=25_000.0,
        next_key=None,
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, net, assignment, shape, *shape_segments, void, circle),
        end_offset=circle.end_offset,
    )
    layer_map = build_allegro_layers(record_set)

    copper = extract_allegro_copper(record_set, layer_map)

    fill = next(
        conductor for conductor in copper.conductors if conductor.id == "allegro:990003102:fill"
    )
    assert isinstance(fill.data, PcbClosedPath)
    assert len(fill.data.holes) == 1
    hole = fill.data.holes[0]
    assert len(hole.segments) == 2
    assert all(segment.kind is PcbPathSegmentKind.ARC for segment in hole.segments)
    xs = [x for segment in hole.segments for x in (segment.start_x, segment.mid_x)]
    ys = [y for segment in hole.segments for y in (segment.start_y, segment.mid_y)]
    assert (round(min(xs), 6), round(max(xs), 6)) == (25.0, 75.0)
    assert (round(min(ys), 6), round(max(ys), 6)) == (-75.0, -25.0)
    assert not any(d.code == "invalid-shape-void-boundary" for d in copper.diagnostics)


def _line_segment(
    key: int,
    parent_key: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    next_key: int | None,
) -> AllegroRecord:
    return AllegroRecord(
        tag=0x15,
        offset=key,
        end_offset=key + 20,
        key=key,
        next_key=next_key,
        payload={
            "parent_key": parent_key,
            "width": 0,
            "start_x": start_x,
            "start_y": start_y,
            "end_x": end_x,
            "end_y": end_y,
        },
    )


def _arc_segment(
    key: int,
    parent_key: int,
    *,
    start: tuple[int, int],
    end: tuple[int, int],
    center: tuple[float, float],
    radius: float,
    next_key: int | None,
    subtype: int = 0,
) -> AllegroRecord:
    return AllegroRecord(
        tag=0x01,
        offset=key,
        end_offset=key + 20,
        key=key,
        next_key=next_key,
        payload={
            "subtype": subtype,
            "parent_key": parent_key,
            "width": 0,
            "start_x": start[0],
            "start_y": start[1],
            "end_x": end[0],
            "end_y": end[1],
            "center_x": center[0],
            "center_y": center[1],
            "radius": radius,
        },
    )


def test_allegro_footprint_owned_shapes_are_excluded_from_board_copper() -> None:
    """Proves package-symbol-local 0x28 shapes stay out of board copper.

    A shape whose owner_key resolves to a 0x2B footprint definition is
    package-symbol geometry in local coordinates; including it would render
    copper blobs near the board origin.
    """
    source = parse_allegro_records(LAUNCHXL_BOARD.read_bytes(), source_name=LAUNCHXL_BOARD.name)
    definition = AllegroRecord(
        tag=0x2B,
        offset=source.end_offset,
        end_offset=source.end_offset + 20,
        key=990_004_050,
        next_key=None,
        payload={},
    )
    shape = AllegroRecord(
        tag=0x28,
        offset=definition.end_offset,
        end_offset=definition.end_offset + 20,
        key=990_004_102,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "owner_key": definition.key or 0,
            "first_segment_key": 990_004_103,
        },
    )
    segments = (
        _line_segment(990_004_103, shape.key or 0, 0, 0, 100_000, 0, 990_004_104),
        _line_segment(990_004_104, shape.key or 0, 100_000, 0, 100_000, 100_000, 990_004_105),
        _line_segment(990_004_105, shape.key or 0, 100_000, 100_000, 0, 0, None),
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, definition, shape, *segments),
        end_offset=segments[-1].end_offset,
    )
    layer_map = build_allegro_layers(record_set)

    copper = extract_allegro_copper(record_set, layer_map)

    assert not any(pour.id == "allegro:990004102:pour" for pour in copper.pours)
    assert not any(conductor.id == "allegro:990004102:fill" for conductor in copper.conductors)


def test_allegro_board_shape_inherits_net_from_owner_assignment() -> None:
    """Proves a board-level shape resolves its net via its owner 0x04 record.

    Board-level shapes reference a net assignment directly through their
    owner pointer even when no net's assignment chain reaches them.
    """
    source = parse_allegro_records(LAUNCHXL_BOARD.read_bytes(), source_name=LAUNCHXL_BOARD.name)
    net = AllegroRecord(
        tag=0x1B,
        offset=source.end_offset,
        end_offset=source.end_offset + 20,
        key=990_005_100,
        next_key=None,
        payload={"net_name_key": 0},
    )
    assignment = AllegroRecord(
        tag=0x04,
        offset=net.end_offset,
        end_offset=net.end_offset + 20,
        key=990_005_101,
        next_key=None,
        payload={"net_key": net.key or 0, "connected_item_key": 0},
    )
    shape = AllegroRecord(
        tag=0x28,
        offset=assignment.end_offset,
        end_offset=assignment.end_offset + 20,
        key=990_005_102,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "owner_key": assignment.key or 0,
            "first_segment_key": 990_005_103,
        },
    )
    segments = (
        _line_segment(990_005_103, shape.key or 0, 0, 0, 100_000, 0, 990_005_104),
        _line_segment(990_005_104, shape.key or 0, 100_000, 0, 100_000, 100_000, 990_005_105),
        _line_segment(990_005_105, shape.key or 0, 100_000, 100_000, 0, 0, None),
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, net, assignment, shape, *segments),
        end_offset=segments[-1].end_offset,
    )
    layer_map = build_allegro_layers(record_set)

    copper = extract_allegro_copper(record_set, layer_map)

    fill = next(
        conductor for conductor in copper.conductors if conductor.id == "allegro:990005102:fill"
    )
    assert fill.net_key == net.key
    assert fill.metadata.properties["native_assignment_key"] == str(assignment.key)
