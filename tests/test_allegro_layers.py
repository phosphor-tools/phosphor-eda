from __future__ import annotations

from pathlib import Path

import pytest

import phosphor_eda.formats.allegro.layers as allegro_layers
from phosphor_eda.domain.pcb import LayerRole
from phosphor_eda.formats.allegro.layers import AllegroLayerMap, build_allegro_layers
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

_CLASS_ETCH = 0x06

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"

COMMITTED_LAYER_EXPECTATIONS = (
    (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/breakout"
        / "board"
        / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd",
        186,
        ("TOP", "L2_GND", "L3_PLANE", "BOTTOM"),
    ),
    (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/sync"
        / "board"
        / "Fb_Connect1_SYNC_Life-3.brd",
        109,
        ("ETCH_1", "GND", "SIG1", "ETCH_4", "ETCH_5", "BOTTOM"),
    ),
    (
        UPSTREAM_FIXTURES
        / "cp-smartgarden"
        / "Document/Hardware/mcu/swrc319/Cadence/Allegro"
        / "LAUNCHXL-CC1310.brd",
        91,
        ("TOP", "ETCH_2", "ETCH_3", "BOTTOM"),
    ),
    (
        UPSTREAM_FIXTURES
        / "rohm-stepper-driver"
        / "Design Files for Rev 1.0"
        / "STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd",
        79,
        ("TOP", "ETCH_2", "PWR", "BOTTOM"),
    ),
)

LAYERED_RECORD_TAGS = {
    0x05,
    0x0A,
    0x0C,
    0x0E,
    0x14,
    0x23,
    0x24,
    0x28,
    0x30,
    0x32,
    0x33,
    0x34,
    0x3A,
}


def test_allegro_etch_layer_list_maps_to_concrete_copper_layers() -> None:
    path = (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/breakout"
        / "board"
        / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
    )
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)

    result = build_allegro_layers(record_set)

    copper_layers = result.layers_by_role(LayerRole.COPPER)
    assert [layer.name for layer in copper_layers] == ["TOP", "L2_GND", "L3_PLANE", "BOTTOM"]
    assert [layer.side for layer in copper_layers] == ["front", "inner", "inner", "back"]
    assert [layer.stack_index for layer in copper_layers] == [0, 1, 2, 3]
    assert all(layer.metadata.source_format == "allegro" for layer in copper_layers)
    assert {layer.metadata.properties["native_class_id"] for layer in copper_layers} == {"6"}
    assert [layer.metadata.properties["native_subclass_id"] for layer in copper_layers] == [
        "0",
        "1",
        "2",
        "3",
    ]
    assert {
        layer.metadata.properties["native_layer_unidentified_word"] for layer in copper_layers
    } == {"0"}
    assert not any(layer.name in {"All", "Multi-Layer", "*"} for layer in result.layers)


def test_allegro_fixed_class_subclass_roles_are_concrete_layers() -> None:
    path = (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/breakout"
        / "board"
        / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
    )
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)

    result = build_allegro_layers(record_set)

    _assert_roles(result, 0x01, 0xEA, LayerRole.EDGE, LayerRole.BOARD_SHAPE)
    _assert_roles(result, 0x09, 0xED, LayerRole.SOLDER_PASTE, LayerRole.FRONT)
    _assert_roles(result, 0x09, 0xF6, LayerRole.SILKSCREEN, LayerRole.BACK)
    _assert_roles(result, 0x09, 0xFB, LayerRole.COURTYARD, LayerRole.FRONT)
    _assert_roles(result, 0x09, 0xF8, LayerRole.FABRICATION, LayerRole.USER)
    _assert_roles(result, 0x0D, 0xFB, LayerRole.DESIGNATOR, LayerRole.SILKSCREEN, LayerRole.FRONT)
    _assert_roles(result, 0x03, 0xFB, LayerRole.FABRICATION, LayerRole.FRONT)
    _assert_lacks_roles(result, 0x03, 0xFB, LayerRole.SILKSCREEN)
    _assert_roles(result, 0x11, 0xFB, LayerRole.FABRICATION, LayerRole.FRONT)
    _assert_lacks_roles(result, 0x11, 0xFB, LayerRole.SILKSCREEN)
    _assert_roles(result, 0x02, 0xFC, LayerRole.VALUE, LayerRole.ASSEMBLY, LayerRole.BACK)
    _assert_roles(result, 0x0E, 0xFD, LayerRole.KEEPOUT)
    _assert_roles(result, 0x0F, 0xFC, LayerRole.KEEPOUT, LayerRole.FRONT)
    _assert_roles(result, 0x07, 0xF7, LayerRole.DRILL, LayerRole.DRILL_DRAWING)


def test_anti_etch_class_maps_to_keepout_not_copper() -> None:
    # Anti-etch is negative copper (a copper clearance/keepout), not a copper
    # conductor. Boundary (0x15) holds copper shape boundaries and stays copper.
    assert allegro_layers._class_roles(0x14) == (LayerRole.KEEPOUT,)
    assert LayerRole.COPPER not in allegro_layers._class_subclass_roles(0x14, 0, "ANTI ETCH TOP")
    assert allegro_layers._class_roles(0x15) == (LayerRole.COPPER,)


def test_allegro_layer_name_roles_do_not_treat_solid_as_solder_mask() -> None:
    assert LayerRole.SOLDER_MASK not in allegro_layers._name_roles("solid_fill")
    assert LayerRole.SOLDER_MASK in allegro_layers._name_roles("solder_mask_top")
    assert LayerRole.SOLDER_MASK in allegro_layers._name_roles("smask_bottom")


def test_allegro_layer_info_records_preserve_native_class_and_subclass() -> None:
    path = (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/breakout"
        / "board"
        / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
    )
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)

    layered_records = [record for record in record_set.records if record.tag in LAYERED_RECORD_TAGS]

    assert layered_records
    assert all("layer_class_id" in record.payload for record in layered_records)
    assert all("layer_subclass_id" in record.payload for record in layered_records)
    assert {
        record.payload["layer_class_id"]
        for record in layered_records
        if record.payload["layer_class_id"] == 0x06
    } == {0x06}


@pytest.mark.parametrize(
    ("path", "expected_layer_count", "expected_stackup_layers"),
    COMMITTED_LAYER_EXPECTATIONS,
    ids=[path.name for path, *_ in COMMITTED_LAYER_EXPECTATIONS],
)
def test_committed_allegro_layer_counts_and_stackup_are_locked(
    path: Path,
    expected_layer_count: int,
    expected_stackup_layers: tuple[str, ...],
) -> None:
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)

    result = build_allegro_layers(record_set)

    assert len(result.layers) == expected_layer_count
    assert result.stackup is not None
    assert [layer.name for layer in result.stackup.layers] == list(expected_stackup_layers)
    assert [layer.name for layer in result.layers_by_role(LayerRole.COPPER)] == list(
        expected_stackup_layers
    )
    assert not any(layer.name in {"All", "Multi-Layer", "*"} for layer in result.layers)


def test_unresolved_layer_names_are_reported_as_diagnostics() -> None:
    path = (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/sync"
        / "board"
        / "Fb_Connect1_SYNC_Life-3.brd"
    )
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)

    result = build_allegro_layers(record_set)

    assert any(diagnostic.code == "unresolved-layer-name" for diagnostic in result.diagnostics)
    assert result.layer_for_class_subclass(0x06, 0).metadata.native_user_name == "ETCH_1"


def test_missing_etch_layer_list_degrades_with_diagnostic() -> None:
    path = (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/breakout"
        / "board"
        / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
    )
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)
    assert record_set.header is not None
    etch_key = record_set.header.layer_map[_CLASS_ETCH].layer_list_key
    filtered = tuple(
        record
        for record in record_set.records
        if not (record.tag == 0x2A and record.key == etch_key)
    )
    degraded = AllegroRecordSet(
        header=record_set.header,
        string_table=record_set.string_table,
        records=filtered,
        end_offset=record_set.end_offset,
    )

    result = build_allegro_layers(degraded)

    missing = next(
        diagnostic for diagnostic in result.diagnostics if diagnostic.code == "missing-layer-list"
    )
    assert missing.reference_key == etch_key
    assert result.stackup is None
    assert not result.layers_by_role(LayerRole.COPPER)


def test_malformed_etch_layer_entries_degrade_with_diagnostic() -> None:
    path = (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/breakout"
        / "board"
        / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
    )
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)
    assert record_set.header is not None
    etch_key = record_set.header.layer_map[_CLASS_ETCH].layer_list_key
    malformed = AllegroRecord(
        tag=0x2A,
        offset=0x100,
        end_offset=0x108,
        key=etch_key,
        next_key=None,
        payload={"layer_entries": (1, 2, 3)},
    )
    records = tuple(
        malformed if (record.tag == 0x2A and record.key == etch_key) else record
        for record in record_set.records
    )
    degraded = AllegroRecordSet(
        header=record_set.header,
        string_table=record_set.string_table,
        records=records,
        end_offset=record_set.end_offset,
    )

    result = build_allegro_layers(degraded)

    diagnostic = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "malformed-layer-entries"
    )
    assert diagnostic.key == etch_key
    assert not result.layers_by_role(LayerRole.COPPER)


def _assert_roles(
    result: AllegroLayerMap,
    class_id: int,
    subclass_id: int,
    *roles: LayerRole,
) -> None:
    layer = result.layer_for_class_subclass(class_id, subclass_id)
    assert layer is not None
    assert all(layer.has_role(role) for role in roles)
    assert layer.metadata.properties["native_class_id"] == str(class_id)
    assert layer.metadata.properties["native_subclass_id"] == str(subclass_id)


def _assert_lacks_roles(
    result: AllegroLayerMap,
    class_id: int,
    subclass_id: int,
    *roles: LayerRole,
) -> None:
    layer = result.layer_for_class_subclass(class_id, subclass_id)
    assert layer is not None
    assert not any(layer.has_role(role) for role in roles)
