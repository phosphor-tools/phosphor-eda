from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from phosphor_eda.domain.pcb import PcbDrillPlating, PcbPadType
from phosphor_eda.formats.allegro.graph import build_allegro_object_graph
from phosphor_eda.formats.allegro.graphics import flash_symbol_keys
from phosphor_eda.formats.allegro.padstacks import (
    _first_copper_component,
    _pad_components,
    expand_allegro_padstack,
    place_custom_shapes,
)
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import (
    AllegroPadstackComponent,
    AllegroRecord,
    AllegroRecordDiagnostic,
    AllegroRecordSet,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
BREAKOUT_BOARD = (
    UPSTREAM_FIXTURES
    / "opencellular/electronics/breakout"
    / "board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)


def _record(tag: int, key: int, next_key: int, **payload: object) -> AllegroRecord:
    return AllegroRecord(
        tag=tag,
        offset=0,
        end_offset=0,
        key=key,
        next_key=next_key,
        payload=MappingProxyType(dict(payload)),
    )


def _line_segment(
    key: int, next_key: int, owner: int, box: tuple[int, int, int, int]
) -> AllegroRecord:
    start_x, start_y, end_x, end_y = box
    return _record(
        0x15,
        key,
        next_key,
        parent_key=owner,
        width=0,
        start_x=start_x,
        start_y=start_y,
        end_x=end_x,
        end_y=end_y,
    )


def _shape_symbol_record_set(*, shape_key: int, segments: list[AllegroRecord]) -> AllegroRecordSet:
    owner = _record(0x2B, 900, 0, footprint_name_key=0)
    shape = _record(
        0x28,
        shape_key,
        0,
        owner_key=owner.key,
        first_keepout_key=0,
        first_segment_key=segments[0].key,
    )
    return AllegroRecordSet(
        header=None,
        string_table=None,
        records=(owner, shape, *segments),
        end_offset=0,
    )


def _custom_padstack(string_key: int) -> AllegroRecord:
    component = AllegroPadstackComponent(
        index=0,
        component_type=22,
        width=1000,
        height=1000,
        offset_x=0,
        offset_y=0,
        string_key=string_key,
    )
    return _record(
        0x1C,
        1,
        0,
        components=(component,),
        drill_size=0,
        slot_x=0,
        slot_y=0,
        pad_type_code=0x20,
        layer_count=0,
        component_count=1,
        pad_name_key=0,
    )


def test_allegro_padstack_expansion_preserves_drill_and_copper_geometry() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    assert record_set.header is not None
    unit_to_mm = 0.0254 / record_set.header.unit_divisor
    padstack_record = next(
        record
        for record in record_set.records
        if record.tag == 0x1C
        and isinstance(record.payload.get("drill_size"), int)
        and record.payload["drill_size"] > 0
    )

    expanded = expand_allegro_padstack(
        padstack_record,
        name="fixture-padstack",
        unit_to_mm=unit_to_mm,
    )

    assert expanded.drill_diameter > 0.0
    assert expanded.stack.outer.size_x > 0.0
    assert expanded.stack.outer.size_y > 0.0
    assert expanded.plating in {
        PcbDrillPlating.PLATED,
        PcbDrillPlating.NON_PLATED,
        PcbDrillPlating.UNKNOWN,
    }
    assert expanded.metadata["native_padstack_key"] == str(padstack_record.key)
    assert expanded.metadata["native_component_count"] == str(
        padstack_record.payload["component_count"]
    )


def test_allegro_padstack_metadata_preserves_zero_native_key_and_slotted_holes() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    assert record_set.header is not None
    unit_to_mm = 0.0254 / record_set.header.unit_divisor
    source_record = next(record for record in record_set.records if record.tag == 0x1C)
    payload = dict(source_record.payload)
    payload["drill_size"] = 0
    payload["slot_x"] = 1000
    payload["slot_y"] = 2000
    payload["pad_type_code"] = 0x30
    slotted_record = replace(source_record, key=0, payload=MappingProxyType(payload))

    expanded = expand_allegro_padstack(
        slotted_record,
        name="fixture-slot",
        unit_to_mm=unit_to_mm,
    )

    assert expanded.metadata["native_padstack_key"] == "0"
    assert expanded.pad_type is PcbPadType.THROUGH_HOLE
    assert expanded.drill_diameter == 0.0
    assert expanded.drill_width > 0.0
    assert expanded.drill_height > 0.0


def test_allegro_shape_symbol_pad_resolves_flash_geometry_from_fixture() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    assert record_set.header is not None
    unit_to_mm = 0.0254 / record_set.header.unit_divisor
    graph = build_allegro_object_graph(record_set)

    # Pick a padstack whose selected copper component is a type-22 shape symbol.
    padstack_record = next(
        record
        for record in record_set.records
        if record.tag == 0x1C
        and record.key is not None
        and (component := _first_copper_component(record, _pad_components(record))) is not None
        and component.component_type == 22
    )

    expanded = expand_allegro_padstack(
        padstack_record,
        name="fixture-shape-symbol",
        unit_to_mm=unit_to_mm,
        graph=graph,
    )

    assert expanded.stack.outer.shape == "custom"
    assert len(expanded.custom_shapes) == 1
    polygon = expanded.custom_shapes[0]
    # A resolved flash is a real boundary (arcs linearized), not a 4-corner rect.
    assert len(polygon.points) > 4
    xs = [x for x, _ in polygon.points]
    ys = [y for _, y in polygon.points]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)
    # Pad-local flash bbox matches the component's reported pad dimensions.
    assert bbox_w == pytest.approx(expanded.stack.outer.size_x, rel=0.02)
    assert bbox_h == pytest.approx(expanded.stack.outer.size_y, rel=0.02)


def test_allegro_shape_symbol_triangle_chain_yields_three_point_polygon() -> None:
    segments = [
        _line_segment(101, 102, 100, (0, 0, 1000, 0)),
        _line_segment(102, 103, 100, (1000, 0, 1000, 1000)),
        _line_segment(103, 100, 100, (1000, 1000, 0, 0)),
    ]
    record_set = _shape_symbol_record_set(shape_key=100, segments=segments)
    graph = build_allegro_object_graph(record_set)
    diagnostics: list[AllegroRecordDiagnostic] = []

    expanded = expand_allegro_padstack(
        _custom_padstack(string_key=100),
        name="triangle",
        unit_to_mm=1.0,
        graph=graph,
        diagnostics=diagnostics,
    )

    assert expanded.stack.outer.shape == "custom"
    assert len(expanded.custom_shapes) == 1
    assert expanded.custom_shapes[0].points == [(0.0, -0.0), (1000.0, -0.0), (1000.0, -1000.0)]
    assert diagnostics == []


def test_allegro_shape_symbol_missing_reference_falls_back_with_diagnostic() -> None:
    record_set = _shape_symbol_record_set(
        shape_key=100, segments=[_line_segment(101, 100, 100, (0, 0, 1000, 0))]
    )
    graph = build_allegro_object_graph(record_set)
    diagnostics: list[AllegroRecordDiagnostic] = []

    expanded = expand_allegro_padstack(
        _custom_padstack(string_key=555),
        name="missing-flash",
        unit_to_mm=1.0,
        graph=graph,
        diagnostics=diagnostics,
    )

    assert expanded.custom_shapes == ()
    assert [d.code for d in diagnostics] == ["unresolved-pad-shape-symbol"]


def test_allegro_degenerate_padstack_emits_diagnostic() -> None:
    empty_padstack = _record(
        0x1C,
        7,
        0,
        components=(),
        drill_size=0,
        slot_x=0,
        slot_y=0,
        pad_type_code=0x20,
        component_count=0,
        pad_name_key=0,
    )
    diagnostics: list[AllegroRecordDiagnostic] = []

    expanded = expand_allegro_padstack(
        empty_padstack,
        name="degenerate",
        unit_to_mm=1.0,
        diagnostics=diagnostics,
    )

    assert expanded.stack.outer.size_x == pytest.approx(0.1)
    assert [d.code for d in diagnostics] == ["degenerate-pad-size"]


def test_place_custom_shapes_bakes_rotation_and_translation() -> None:
    segments = [
        _line_segment(101, 102, 100, (0, 0, 1000, 0)),
        _line_segment(102, 103, 100, (1000, 0, 1000, 1000)),
        _line_segment(103, 100, 100, (1000, 1000, 0, 0)),
    ]
    record_set = _shape_symbol_record_set(shape_key=100, segments=segments)
    graph = build_allegro_object_graph(record_set)
    expanded = expand_allegro_padstack(
        _custom_padstack(string_key=100),
        name="triangle",
        unit_to_mm=1.0,
        graph=graph,
    )

    placed = place_custom_shapes(expanded, x=10.0, y=20.0, rotation_deg=90.0)

    assert len(placed) == 1
    # rotation -90deg (y-down): place(lx, ly) = (x + ly, y - lx).
    # local (1000, -1000) -> (10 - 1000, 20 - 1000) = (-990, -980).
    placed_points = [(round(x, 3), round(y, 3)) for x, y in placed[0].points]
    assert placed_points == [(10.0, 20.0), (10.0, -980.0), (-990.0, -980.0)]


def _padstack_with_components(
    *components: AllegroPadstackComponent,
) -> AllegroRecordSet:
    padstack = _record(
        0x1C,
        1,
        0,
        components=components,
        pad_name_key=0,
    )
    return AllegroRecordSet(
        header=None,
        string_table=None,
        records=(padstack,),
        end_offset=0,
    )


def _pad_component(component_type: int, string_key: int) -> AllegroPadstackComponent:
    return AllegroPadstackComponent(
        index=0,
        component_type=component_type,
        width=1000,
        height=1000,
        offset_x=0,
        offset_y=0,
        string_key=string_key,
    )


def test_flash_symbol_keys_only_collects_custom_component_shape_keys() -> None:
    """Only custom pad components reference 0x28 flash shapes.

    A primitive-shape component (e.g. rect) carries string_key for other
    purposes, so its value must not be mistaken for a flash-symbol reference.
    """
    record_set = _padstack_with_components(
        _pad_component(component_type=0x16, string_key=700),  # custom
        _pad_component(component_type=0x05, string_key=800),  # rect primitive
        _pad_component(component_type=0x00, string_key=900),  # null pad slot
    )

    assert flash_symbol_keys(record_set) == frozenset({700})
