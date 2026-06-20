from __future__ import annotations

from pathlib import Path

from phosphor_eda.domain.pcb import LayerRole, PcbConductorKind, PcbPolygon, PcbPourFillMode
from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.graphics import extract_allegro_copper
from phosphor_eda.formats.allegro.layers import build_allegro_layers
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAUNCHXL_BOARD = (
    FIXTURES
    / "orcad"
    / "cp-smartgarden-launchxl-cc1310"
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
    ]
    assert len(fills) == 1
    fill = fills[0]
    assert fill.id == "allegro:129098328:fill"
    assert fill.pour in board.pours
    assert fill.pour is not None
    assert fill in fill.pour.fills
    assert fill.net is not None
    assert fill.pour.net is fill.net
    assert fill.pour.settings.fill_mode is PcbPourFillMode.SOLID
    assert fill.layer.has_role(LayerRole.COPPER)
    assert fill.pour.layers == (fill.layer,)
    assert isinstance(fill.data, PcbPolygon)
    assert len(fill.data.points) >= 3
    assert fill.metadata.properties["native_net_key"] == "126734664"
    assert fill.metadata.properties["native_assignment_key"] == "129151432"
    assert fill.metadata.properties["native_layer_name"] == fill.layer.name


def test_allegro_shape_voids_do_not_emit_uncut_positive_copper() -> None:
    """Proves native shape holes are not converted into unsafe positive copper.

    The synthetic 0x28/0x34 relationship proves parser behavior for the native
    void pointer. It cannot prove boolean-subtracted manufacturing geometry
    until a fixture with reliable void oracle output is promoted.
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
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=(*source.records, net, assignment, shape, *shape_segments, void),
        end_offset=void.end_offset,
    )

    board = build_allegro_board(record_set, name=LAUNCHXL_BOARD.stem)

    pour = board.pour_for("allegro:990000102:pour")
    assert pour is not None
    assert pour.fills == ()
    assert pour.settings.fill_mode is PcbPourFillMode.UNKNOWN
    assert all(conductor.id != "allegro:990000102:fill" for conductor in board.conductors)
    assert pour.metadata.properties["native_first_keepout_key"] == "990000200"
    assert int(board.metadata.properties["parse_diagnostic_count"]) > int(
        build_allegro_board(source, name=LAUNCHXL_BOARD.stem).metadata.properties[
            "parse_diagnostic_count"
        ]
    )


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
