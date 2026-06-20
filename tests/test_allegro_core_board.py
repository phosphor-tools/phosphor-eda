from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import phosphor_eda.formats.allegro.build as allegro_build
from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.oracle import parse_packaged_netlist_summary
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecordSet

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BREAKOUT_BOARD = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
BREAKOUT_NETLIST = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "orcad/OpenCellular/electronics/breakout/schematic/Netlist"
)


def test_allegro_board_assembly_emits_connectivity_padstacks_and_drills() -> None:
    """Proves native Allegro records assemble into strict board-domain objects.

    Packaged netlist sidecars prove component, net, and pin counts. They cannot
    prove physical padstack geometry, routed copper, board profile, or pours.
    """
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    oracle = parse_packaged_netlist_summary(BREAKOUT_NETLIST)

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert len(board.footprints) == oracle.unique_refdes_count
    assert (
        len({footprint.reference for footprint in board.footprints}) == oracle.unique_refdes_count
    )
    assert (
        len({footprint.reference for footprint in board.footprints} & oracle.component_refs) >= 40
    )
    assert len(board.nets) >= oracle.net_count - 1
    assert 0 not in board.nets

    footprint_pads = [pad for pad in board.pads if pad.footprint is not None]
    connected_footprint_pads = [pad for pad in footprint_pads if pad.net is not None]
    assert len(connected_footprint_pads) >= oracle.node_count - oracle.no_connect_node_count
    assert footprint_pads[0].number == "1"
    assert all(pad.drill is None or pad.drill.owner is pad for pad in board.pads)
    assert all(pad.footprint in board.footprints for pad in footprint_pads)

    assert board.vias
    assert board.drills
    assert all(via.drill.owner is via for via in board.vias)
    assert all(drill.layers for drill in board.drills)


def test_allegro_refdes_detection_preserves_lowercase_source_identifiers() -> None:
    assert allegro_build._looks_like_refdes("r1")
    assert allegro_build._looks_like_refdes(" R1 ")


def test_allegro_board_assembly_reports_unresolved_via_padstack_reference() -> None:
    """Proves degraded native connectivity records surface structured issues."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    via_record = next(record for record in source.records if record.tag == 0x33)
    missing_padstack_key = 999_999_999
    payload = dict(via_record.payload)
    payload["padstack_key"] = missing_padstack_key
    mutated_via = replace(via_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(mutated_via if record is via_record else record for record in source.records),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert f"via-{via_record.key}" not in {via.id for via in board.vias}
    assert int(board.metadata.properties["parse_diagnostic_count"]) == base_count + 1
    assert "unresolved-via-padstack" in board.metadata.properties["parse_diagnostic_codes"].split(
        ";"
    )
    assert str(via_record.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(missing_padstack_key) in board.metadata.properties[
        "parse_diagnostic_reference_keys"
    ].split(";")


def test_allegro_board_assembly_reports_unresolved_footprint_instance_chain() -> None:
    """Proves degraded package-instance chains preserve native diagnostics."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    definition_record = next(
        record
        for record in source.records
        if record.tag == 0x2B and record.payload.get("first_instance_key") == 0
    )
    missing_instance_key = 999_999_998
    payload = dict(definition_record.payload)
    payload["first_instance_key"] = missing_instance_key
    mutated_definition = replace(definition_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(
            mutated_definition if record is definition_record else record
            for record in source.records
        ),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert int(board.metadata.properties["parse_diagnostic_count"]) > base_count
    assert "unresolved-footprint-instance-chain" in board.metadata.properties[
        "parse_diagnostic_codes"
    ].split(";")
    assert str(definition_record.key) in board.metadata.properties["parse_diagnostic_keys"].split(
        ";"
    )
    assert str(missing_instance_key) in board.metadata.properties[
        "parse_diagnostic_reference_keys"
    ].split(";")


def test_allegro_board_assembly_reports_component_pad_chain_cycles() -> None:
    """Proves cyclic native pad ownership chains surface structured issues."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    component = next(
        record
        for record in source.records
        if record.tag == 0x07
        and isinstance(record.payload.get("first_pad_key"), int)
        and record.payload["first_pad_key"]
    )
    first_pad_key = component.payload["first_pad_key"]
    assert isinstance(first_pad_key, int)
    pad_record = source.by_key[first_pad_key]
    payload = dict(pad_record.payload)
    payload["next_in_component_key"] = first_pad_key
    mutated_pad = replace(pad_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(mutated_pad if record is pad_record else record for record in source.records),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert int(board.metadata.properties["parse_diagnostic_count"]) == base_count
    assert "component-pad-chain-cycle" in board.metadata.properties["parse_diagnostic_codes"].split(
        ";"
    )
    assert str(component.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(first_pad_key) in board.metadata.properties["parse_diagnostic_reference_keys"].split(
        ";"
    )


def test_allegro_board_assembly_reports_missing_footprint_instance_once() -> None:
    """Proves missing footprint instances do not create duplicate diagnostics."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    component = next(
        record
        for record in source.records
        if record.tag == 0x07 and isinstance(record.payload.get("footprint_instance_key"), int)
    )
    missing_instance_key = 999_999_997
    payload = dict(component.payload)
    payload["footprint_instance_key"] = missing_instance_key
    mutated_component = replace(component, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(
            mutated_component if record is component else record for record in source.records
        ),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)
    codes = board.metadata.properties["parse_diagnostic_codes"].split(";")

    assert "unresolved-footprint-instance" in codes
    assert "unresolved-component-footprint" not in codes
    assert str(component.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(missing_instance_key) in board.metadata.properties[
        "parse_diagnostic_reference_keys"
    ].split(";")


def test_allegro_board_assembly_reports_unresolved_pad_definition() -> None:
    """Proves degraded pad definition references surface structured issues."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    component = next(
        record
        for record in source.records
        if record.tag == 0x07
        and isinstance(record.payload.get("first_pad_key"), int)
        and record.payload["first_pad_key"]
    )
    first_pad_key = component.payload["first_pad_key"]
    assert isinstance(first_pad_key, int)
    pad_record = source.by_key[first_pad_key]
    missing_definition_key = 999_999_996
    payload = dict(pad_record.payload)
    payload["pad_definition_key"] = missing_definition_key
    mutated_pad = replace(pad_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(mutated_pad if record is pad_record else record for record in source.records),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert int(board.metadata.properties["parse_diagnostic_count"]) == base_count + 1
    assert "unresolved-pad-definition" in board.metadata.properties["parse_diagnostic_codes"].split(
        ";"
    )
    assert str(pad_record.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(missing_definition_key) in board.metadata.properties[
        "parse_diagnostic_reference_keys"
    ].split(";")


def test_allegro_board_assembly_reports_via_padstack_without_drill() -> None:
    """Proves unsupported via padstacks without drills surface diagnostics."""
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    base_board = build_allegro_board(source, name=BREAKOUT_BOARD.stem)
    base_count = int(base_board.metadata.properties.get("parse_diagnostic_count", "0"))
    via_record = next(record for record in source.records if record.tag == 0x33)
    padstack_key = via_record.payload["padstack_key"]
    assert isinstance(padstack_key, int)
    padstack_record = source.by_key[padstack_key]
    payload = dict(padstack_record.payload)
    payload["drill_size"] = 0
    payload["slot_x"] = 0
    payload["slot_y"] = 0
    mutated_padstack = replace(padstack_record, payload=MappingProxyType(payload))
    record_set = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(
            mutated_padstack if record is padstack_record else record for record in source.records
        ),
        end_offset=source.end_offset,
    )

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert f"via-{via_record.key}" not in {via.id for via in board.vias}
    assert int(board.metadata.properties["parse_diagnostic_count"]) > base_count
    assert "unsupported-via-without-drill" in board.metadata.properties[
        "parse_diagnostic_codes"
    ].split(";")
    assert str(via_record.key) in board.metadata.properties["parse_diagnostic_keys"].split(";")
    assert str(padstack_key) in board.metadata.properties["parse_diagnostic_reference_keys"].split(
        ";"
    )
