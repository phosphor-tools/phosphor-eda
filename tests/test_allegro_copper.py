from __future__ import annotations

from pathlib import Path

import pytest

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbClosedPath,
    PcbConductorKind,
    PcbLine,
    PcbPolygon,
)
from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
BREAKOUT_BOARD = (
    UPSTREAM_FIXTURES
    / "opencellular/electronics/breakout"
    / "board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
SYNC_BOARD = (
    UPSTREAM_FIXTURES / "opencellular/electronics/sync" / "board" / "Fb_Connect1_SYNC_Life-3.brd"
)


def test_allegro_board_assembly_emits_net_owned_trace_conductors() -> None:
    """Proves native routed track records become manufactured copper.

    Allegro net-assignment chains prove track-to-net ownership. They cannot
    prove pour intent, dynamic fill behavior, or shape voiding.
    """
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    traces = [
        conductor
        for conductor in board.conductors
        if conductor.kind is PcbConductorKind.TRACE
        and conductor.metadata.native_type == "track_segment"
        and conductor.net is not None
    ]
    assert len(traces) == 155
    board_net_object_ids = {id(net) for net in board.nets.values()}
    assert all(
        conductor.net is not None and id(conductor.net) in board_net_object_ids
        for conductor in traces
    )
    assert all(conductor.layer in board.layers for conductor in traces)
    assert all(conductor.layer.has_role(LayerRole.COPPER) for conductor in traces)
    assert all(isinstance(conductor.data, PcbLine) for conductor in traces)
    assert all(
        conductor.data.width > 0 for conductor in traces if isinstance(conductor.data, PcbLine)
    )
    assert {conductor.layer.name for conductor in traces} >= {"TOP", "BOTTOM"}

    sample_trace = next(trace for trace in traces if trace.id == "allegro:634413176")
    assert sample_trace.net is not None
    assert sample_trace.metadata.native_type == "track_segment"
    assert sample_trace.metadata.properties["native_track_key"]
    assert sample_trace.metadata.properties["native_class_id"] == "6"
    assert sample_trace.metadata.properties["native_layer_name"] == sample_trace.layer.name
    assert sample_trace.data.width == pytest.approx(0.14478)


def test_allegro_unassigned_track_becomes_conductor_with_no_net() -> None:
    """A 0x05 track with a segment chain but no 0x04 net assignment still renders
    as board copper, carrying net_key=None instead of being dropped."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    segment = AllegroRecord(
        tag=0x15,
        offset=source.end_offset,
        end_offset=source.end_offset + 20,
        key=990_000_101,
        next_key=None,
        payload={
            "parent_key": 990_000_100,
            "start_x": 1_000_000,
            "start_y": 1_000_000,
            "end_x": 2_000_000,
            "end_y": 1_000_000,
            "width": 10_000,
        },
    )
    track = AllegroRecord(
        tag=0x05,
        offset=segment.end_offset,
        end_offset=segment.end_offset + 20,
        key=990_000_100,
        next_key=None,
        payload={
            "layer_class_id": 6,
            "layer_subclass_id": 0,
            "first_segment_key": 990_000_101,
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, segment, track),
        end_offset=track.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    conductor = next(
        conductor
        for conductor in board.conductors
        if conductor.metadata.properties.get("native_track_key") == "990000100"
    )
    assert conductor.net is None
    assert conductor.kind is PcbConductorKind.TRACE
    assert conductor.metadata.native_type == "track_segment"
    assert conductor.metadata.properties["native_net_key"] == ""
    assert conductor.layer.has_role(LayerRole.COPPER)
    assert isinstance(conductor.data, PcbLine)


def test_allegro_board_assembly_preserves_trace_arcs_as_arc_conductors() -> None:
    """Proves routed Allegro arc segments stay arcs in the domain model.

    The fixture's net-owned track chain proves arc-to-net ownership. It cannot
    prove dynamic pour fill arc handling or manufacturing output parity.
    """
    record_set = parse_allegro_records(SYNC_BOARD.read_bytes(), source_name=SYNC_BOARD.name)

    board = build_allegro_board(record_set, name=SYNC_BOARD.stem)

    arcs = [
        conductor
        for conductor in board.conductors
        if conductor.kind is PcbConductorKind.TRACE_ARC
        and conductor.metadata.native_type == "track_arc"
        and conductor.net is not None
    ]
    assert len(arcs) == 1
    arc = arcs[0]
    assert isinstance(arc.data, PcbArc)
    assert arc.net is not None
    assert arc.layer.has_role(LayerRole.COPPER)
    assert arc.metadata.native_type == "track_arc"
    assert arc.metadata.properties["native_track_key"] == "647955992"
    assert arc.metadata.properties["native_net_key"] == "644791912"
    assert arc.data.width == pytest.approx(0.3556)
    assert (arc.data.mid_x, arc.data.mid_y) != (arc.data.start_x, arc.data.start_y)
    assert (arc.data.mid_x, arc.data.mid_y) != (arc.data.end_x, arc.data.end_y)


def test_allegro_board_assembly_emits_copper_graphics_as_unassigned_conductors() -> None:
    """Proves ETCH-class graphic segments are copper, not non-electrical artwork.

    The native class/subclass proves the graphics are on copper layers. It
    cannot prove a logical net when the record is not in a net-assignment chain.
    """
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    copper_graphics = [
        conductor
        for conductor in board.conductors
        if conductor.metadata.native_type == "copper_graphic_segment"
    ]
    # Footprint-definition-owned graphic chains are excluded from board copper.
    assert len(copper_graphics) == 48
    copper_graphic = next(
        conductor for conductor in copper_graphics if conductor.id == "allegro:634089136"
    )
    assert copper_graphic.kind is PcbConductorKind.TRACE
    assert copper_graphic.net is None
    assert copper_graphic.footprint is None
    assert copper_graphic.layer.name == "TOP"
    assert isinstance(copper_graphic.data, PcbLine)
    assert copper_graphic.data.width == pytest.approx(0.3048)
    assert copper_graphic.metadata.native_type == "copper_graphic_segment"
    assert copper_graphic.metadata.properties["native_parent_key"] == "632573000"
    assert copper_graphic.metadata.properties["native_footprint_key"] == "632573000"


def test_allegro_board_assembly_emits_copper_rectangles_as_regions() -> None:
    """Proves fixture-backed copper rectangle records become conductive regions.

    The native boundary/copper layer proves concrete copper geometry. It cannot
    prove dynamic pour intent, voiding, or thermal behavior owned by PR07.
    """
    record_set = parse_allegro_records(SYNC_BOARD.read_bytes(), source_name=SYNC_BOARD.name)

    board = build_allegro_board(record_set, name=SYNC_BOARD.stem)

    regions = [
        conductor
        for conductor in board.conductors
        if conductor.kind is PcbConductorKind.COPPER_REGION
        and conductor.metadata.native_type == "copper_rectangle_region"
    ]
    assert len(regions) == 9
    region = next(conductor for conductor in regions if conductor.id == "allegro:644982744")
    assert region.net is None
    assert region.layer.has_role(LayerRole.COPPER)
    assert isinstance(region.data, PcbPolygon)
    assert len(region.data.points) == 4
    assert region.metadata.properties["native_class_id"] == "15"
    assert region.metadata.properties["native_subclass_id"] == "0"
    assert region.metadata.properties["native_footprint_key"] == "644788264"


def test_allegro_board_assembly_emits_unassigned_voided_shape_pours() -> None:
    """Proves board-level Allegro shape planes are rendered even without net ownership.

    The Sync fixture carries its copper planes as unassigned 0x28 shapes with
    native void chains. The parser preserves the copper geometry and holes
    without inventing a net.
    """
    record_set = parse_allegro_records(SYNC_BOARD.read_bytes(), source_name=SYNC_BOARD.name)

    board = build_allegro_board(record_set, name=SYNC_BOARD.stem)

    fill = next(
        conductor for conductor in board.conductors if conductor.id == "allegro:649376864:fill"
    )
    assert fill.kind is PcbConductorKind.POUR_FILL
    assert fill.net is not None
    assert fill.net.name == "GND"
    assert fill.pour is not None
    assert fill.pour.net is fill.net
    assert fill.layer.name == "GND"
    assert isinstance(fill.data, PcbClosedPath)
    assert fill.data.holes
    assert len(fill.data.points) >= 3
    assert fill.metadata.native_type == "copper_shape_fill"
    assert fill.metadata.properties["native_first_keepout_key"] == "649377592"
    assert fill.metadata.properties["native_assignment_key"] == "648034920"
    assert fill.metadata.properties["native_net_key"] == "644801432"

    pour_layers = {pour.layers[0].name for pour in board.pours if pour.id.startswith("allegro:")}
    assert {"ETCH_1", "GND", "SIG1", "ETCH_4", "ETCH_5", "BOTTOM"} <= pour_layers


def test_allegro_board_assembly_counts_copper_diagnostics() -> None:
    """Proves copper extraction diagnostics survive board assembly."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    bad_net = AllegroRecord(
        tag=0x1B,
        offset=source.end_offset,
        end_offset=source.end_offset + 32,
        key=900_000_001,
        next_key=None,
        payload={
            "net_name_key": 0,
            "assignment_key": 900_000_002,
        },
    )
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, bad_net),
        end_offset=bad_net.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert int(board.metadata.properties["parse_diagnostic_count"]) == base_count + 1
