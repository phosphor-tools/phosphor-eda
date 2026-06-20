from __future__ import annotations

from pathlib import Path

import pytest

from phosphor_eda.formats.allegro.errors import AllegroParseError
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.records import AllegroRecord

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BREAKOUT_BOARD = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
BREAKOUT_RECORD_0X27_END_OFFSET = 0x18C


def test_parse_allegro_records_starts_after_aligned_string_table_padding() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    first_record = record_set.records[0]
    assert first_record.tag == 0x06
    assert first_record.offset % 4 == 0
    assert first_record.offset > record_set.string_table.end_offset


def test_parse_allegro_records_preserves_native_key_next_and_raw_extent() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    first_component = record_set.records[0]
    assert isinstance(first_component, AllegroRecord)
    assert first_component.key == 632_553_504
    assert first_component.next_key == 632_553_544
    assert first_component.end_offset > first_component.offset
    assert first_component.payload["symbol_name_key"] == 405_643_889


def test_parse_allegro_records_rejects_unknown_implicit_length_record() -> None:
    data = bytearray(BREAKOUT_BOARD.read_bytes())
    data[0x54AC] = 0x7F

    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_records(bytes(data), source_name="unknown-record.brd")

    error = exc_info.value
    assert error.code == "unknown-record-tag"
    assert error.offset == 0x54AC


def test_parse_allegro_records_rejects_unaligned_0x27_reference_payload() -> None:
    data = bytearray(BREAKOUT_BOARD.read_bytes())
    original_end = int.from_bytes(
        data[BREAKOUT_RECORD_0X27_END_OFFSET : BREAKOUT_RECORD_0X27_END_OFFSET + 4],
        "little",
    )
    data[BREAKOUT_RECORD_0X27_END_OFFSET : BREAKOUT_RECORD_0X27_END_OFFSET + 4] = (
        original_end + 1
    ).to_bytes(4, "little")

    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_records(bytes(data), source_name="unaligned-0x27.brd")

    error = exc_info.value
    assert error.code == "record-length-invalid"
    assert error.source_name == "unaligned-0x27.brd"
    assert "0x27 reference payload" in str(error)


def test_parse_allegro_records_rejects_mismatched_field_substructure_size() -> None:
    data = bytearray(BREAKOUT_BOARD.read_bytes())
    field_record = next(
        (
            record
            for record in parse_allegro_records(
                BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name
            ).records
            if record.tag == 0x03 and record.payload["subtype"] == 0x6C
        ),
        None,
    )
    assert field_record is not None, "fixture must contain a 0x03 subtype 0x6C field record"
    size_offset = field_record.offset + 14
    data[size_offset : size_offset + 2] = (4).to_bytes(2, "little")

    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_records(bytes(data), source_name="bad-field-size.brd")

    error = exc_info.value
    assert error.code == "record-length-invalid"
    assert error.offset == field_record.offset
    assert error.source_name == "bad-field-size.brd"
    assert "0x03 subtype 0x6C consumed" in str(error)


def test_parse_allegro_records_rejects_mismatched_scalar_field_substructure_size() -> None:
    data = bytearray(BREAKOUT_BOARD.read_bytes())
    field_record = next(
        (
            record
            for record in parse_allegro_records(
                BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name
            ).records
            if record.tag == 0x03 and record.payload["subtype"] == 0x64
        ),
        None,
    )
    assert field_record is not None, "fixture must contain a 0x03 subtype 0x64 field record"
    size_offset = field_record.offset + 14
    data[size_offset : size_offset + 2] = (8).to_bytes(2, "little")

    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_records(bytes(data), source_name="bad-scalar-field-size.brd")

    error = exc_info.value
    assert error.code == "record-length-invalid"
    assert error.offset == field_record.offset
    assert error.source_name == "bad-scalar-field-size.brd"
    assert "0x03 subtype 0x64 consumed" in str(error)
