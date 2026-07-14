from __future__ import annotations

import os
from pathlib import Path

import pytest

from phosphor_eda.formats.allegro import parser as allegro_parser
from phosphor_eda.formats.allegro.binary import BoundedBinaryReader
from phosphor_eda.formats.allegro.constants import AllegroBoardUnits, allegro_unit_to_mm
from phosphor_eda.formats.allegro.errors import (
    AllegroParseError,
    AllegroUnsupportedVersionError,
)
from phosphor_eda.formats.allegro.parser import (
    parse_allegro_container,
    parse_allegro_header,
    parse_allegro_string_table,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
CORPUS_ROOT = Path(os.environ.get("PHOSPHOR_EDA_CORPUS_ROOT", "__external_corpus_missing__"))
EXTERNAL_KICAD_ALLEGRO_FIXTURES = CORPUS_ROOT / "kicad/qa/data/pcbnew/plugins/allegro"
PRE_V18_BOARD_UNITS_OFFSET = 0x180
PRE_V18_UNIT_DIVISOR_OFFSET = 0x26C

COMMITTED_BOARD_HEADERS = (
    (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/breakout"
        / "board"
        / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd",
        "V_166",
        66_448,
        1_181,
        "mils",
        1_000,
    ),
    (
        UPSTREAM_FIXTURES
        / "opencellular/electronics/sync"
        / "board"
        / "Fb_Connect1_SYNC_Life-3.brd",
        "V_166",
        102_341,
        1_382,
        "mils",
        1_000,
    ),
    (
        UPSTREAM_FIXTURES
        / "cp-smartgarden"
        / "Document/Hardware/mcu/swrc319/Cadence/Allegro"
        / "LAUNCHXL-CC1310.brd",
        "V_166",
        63_891,
        1_254,
        "millimeters",
        1_000,
    ),
    (
        UPSTREAM_FIXTURES
        / "rohm-stepper-driver"
        / "Design Files for Rev 1.0"
        / "STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd",
        "V_165",
        21_119,
        698,
        "mils",
        100,
    ),
)

COMMITTED_STRING_TABLE_EXPECTATIONS = (
    (
        COMMITTED_BOARD_HEADERS[0][0],
        1_181,
        {
            405637157: "TOP",
            405637162: "BOTTOM",
            405645878: "SMT",
        },
    ),
    (
        COMMITTED_BOARD_HEADERS[1][0],
        1_382,
        {
            476610835: "GND",
            476610857: "BOTTOM",
            476622816: "SOLDTOP.gbr",
        },
    ),
    (
        COMMITTED_BOARD_HEADERS[2][0],
        1_254,
        {
            247676498: "VDDC",
            247677226: "TOP",
            247677231: "BOTTOM",
        },
    ),
    (
        COMMITTED_BOARD_HEADERS[3][0],
        698,
        {
            144908865: "TOP",
            144908870: "BOTTOM",
            144912380: "GND_SIGNAL",
        },
    ),
)

OPTIONAL_KICAD_HEADER_EXPECTATIONS = (
    (
        "boards/BeagleBone-AI/BeagleBone-AI.brd",
        "V_172",
        22,
        4_542,
        "mils",
        1_000,
    ),
    (
        "boards/OpenBreath_encoder_v174/motor_encoder_brd.brd",
        "V_174",
        22,
        159,
        "millimeters",
        10_000,
    ),
    (
        "boards/CutiePi_V2_3_dbd18/header.bin",
        "V_180",
        28,
        1_423,
        "mils",
        1_000,
    ),
)


def _put_u32(data: bytearray, offset: int, value: int) -> None:
    data[offset : offset + 4] = value.to_bytes(4, "little")


def _synthetic_v18_container_bytes() -> bytes:
    data = bytearray(0x1200)
    _put_u32(data, 0x00, 0x00150000)
    _put_u32(data, 0x14, 42)
    _put_u32(data, 0x34, 2)
    _put_u32(data, 0x3C, 0x111)
    _put_u32(data, 0x40, 0x222)
    data[0x124 : 0x124 + len(b"dbd18-test")] = b"dbd18-test"
    _put_u32(data, 0x164, 0x333)
    data[0x18C] = 0x01
    _put_u32(data, 0x26C, 1_000)
    _put_u32(data, 0x428, 6)
    _put_u32(data, 0x42C, 0x444)
    data.extend((10).to_bytes(4, "little") + b"TOP\x00")
    data.extend((20).to_bytes(4, "little") + b"BOTTOM\x00")
    return bytes(data)


def test_bounded_binary_reader_reports_truncated_reads_with_offset() -> None:
    reader = BoundedBinaryReader(b"\x01\x02", source_name="tiny.brd")

    with pytest.raises(AllegroParseError) as exc_info:
        reader.read_uint32()

    error = exc_info.value
    assert error.code == "truncated-read"
    assert error.offset == 0
    assert "tiny.brd" in str(error)


def test_bounded_binary_reader_reports_invalid_seek_target() -> None:
    reader = BoundedBinaryReader(b"\x00", source_name="tiny.brd")

    with pytest.raises(AllegroParseError) as exc_info:
        reader.seek(2)

    error = exc_info.value
    assert error.code == "invalid-seek"
    assert error.offset == 2


def test_bounded_binary_reader_rejects_negative_read_size() -> None:
    reader = BoundedBinaryReader(b"\x00", source_name="tiny.brd")

    with pytest.raises(AllegroParseError) as exc_info:
        reader.read_bytes(-1)

    error = exc_info.value
    assert error.code == "negative-read"
    assert error.offset == 0


@pytest.mark.parametrize(
    ("units", "divisor", "expected"),
    (
        (AllegroBoardUnits.MILS, 1_000, 0.0000254),
        (AllegroBoardUnits.INCHES, 1_000, 0.0254),
        (AllegroBoardUnits.MILLIMETERS, 1_000, 0.001),
        (AllegroBoardUnits.CENTIMETERS, 1_000, 0.01),
        (AllegroBoardUnits.MICROMETERS, 1_000, 0.000001),
    ),
)
def test_allegro_unit_to_mm_is_the_single_board_unit_contract(
    units: AllegroBoardUnits,
    divisor: int,
    expected: float,
) -> None:
    assert allegro_unit_to_mm(units, divisor) == pytest.approx(expected)


@pytest.mark.parametrize("divisor", (0, -1))
def test_allegro_unit_to_mm_rejects_non_positive_divisor(divisor: int) -> None:
    with pytest.raises(ValueError, match="unit divisor must be positive"):
        _ = allegro_unit_to_mm(AllegroBoardUnits.MILLIMETERS, divisor)


@pytest.mark.parametrize(
    ("path", "version", "object_count", "string_count", "units", "unit_divisor"),
    COMMITTED_BOARD_HEADERS,
    ids=[path.name for path, *_ in COMMITTED_BOARD_HEADERS],
)
def test_parse_allegro_header_preserves_committed_fixture_metadata(
    path: Path,
    version: str,
    object_count: int,
    string_count: int,
    units: str,
    unit_divisor: int,
) -> None:
    header = parse_allegro_header(path.read_bytes(), source_name=path.name)

    assert header.version.value == version
    assert header.object_count == object_count
    assert header.string_count == string_count
    assert header.linked_list_count == 22
    assert header.board_units == units
    assert header.unit_divisor == unit_divisor
    assert len(header.layer_map) == 25
    assert sum(1 for entry in header.layer_map if entry.class_id or entry.layer_list_key) == 24


@pytest.mark.parametrize(
    ("path", "string_count", "known_strings"),
    COMMITTED_STRING_TABLE_EXPECTATIONS,
    ids=[path.name for path, *_ in COMMITTED_STRING_TABLE_EXPECTATIONS],
)
def test_parse_allegro_container_preserves_committed_fixture_string_table(
    path: Path,
    string_count: int,
    known_strings: dict[int, str],
) -> None:
    container = parse_allegro_container(path.read_bytes(), source_name=path.name)

    assert container.header.string_count == string_count
    assert len(container.string_table.entries) == string_count
    assert container.string_table.end_offset > 0x1200
    assert container.string_table.end_offset < path.stat().st_size
    for key, value in known_strings.items():
        assert container.string_table.by_id[key] == value


def test_parse_allegro_header_preserves_raw_linked_list_descriptor_words() -> None:
    path = COMMITTED_BOARD_HEADERS[0][0]

    header = parse_allegro_header(path.read_bytes(), source_name=path.name)

    assert header.linked_lists[0].index == 0
    assert header.linked_lists[0].head_key == 0
    assert header.linked_lists[0].tail_key == 588_664_652
    assert header.linked_lists[-1].index == 21
    assert header.linked_lists[-1].head_key == 634_518_752
    assert header.linked_lists[-1].tail_key == 588_664_828


def test_parse_allegro_container_covers_v18_header_layout_in_default_ci() -> None:
    container = parse_allegro_container(_synthetic_v18_container_bytes(), source_name="v18.brd")
    header = container.header

    assert header.version.value == "V_180"
    assert header.object_count == 42
    assert header.string_count == 2
    assert header.linked_list_count == 28
    assert header.linked_lists[0].head_key == 0x111
    assert header.linked_lists[0].tail_key == 0x222
    assert header.version_string == "dbd18-test"
    assert header.max_key == 0x333
    assert header.board_units == "mils"
    assert header.unit_divisor == 1_000
    assert header.layer_map[0].class_id == 6
    assert header.layer_map[0].layer_list_key == 0x444
    assert container.string_table.by_id == {10: "TOP", 20: "BOTTOM"}


def test_parse_allegro_header_rejects_pre_v16_magic_with_typed_error() -> None:
    data = (0x00120000).to_bytes(4, "little") + bytes(0x200)

    with pytest.raises(AllegroUnsupportedVersionError) as exc_info:
        parse_allegro_header(data, source_name="old.brd")

    error = exc_info.value
    assert error.code == "unsupported-version"
    assert error.offset == 0
    assert "pre-v16" in str(error)


def test_parse_allegro_header_rejects_unknown_board_units_with_typed_error() -> None:
    path = COMMITTED_BOARD_HEADERS[0][0]
    data = bytearray(path.read_bytes())
    data[PRE_V18_BOARD_UNITS_OFFSET] = 0xFE

    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_header(bytes(data), source_name="bad-units.brd")

    error = exc_info.value
    assert error.code == "unsupported-board-units"
    assert error.offset == PRE_V18_BOARD_UNITS_OFFSET


def test_parse_allegro_header_rejects_zero_unit_divisor_with_typed_error() -> None:
    path = COMMITTED_BOARD_HEADERS[0][0]
    data = bytearray(path.read_bytes())
    data[PRE_V18_UNIT_DIVISOR_OFFSET : PRE_V18_UNIT_DIVISOR_OFFSET + 4] = b"\x00\x00\x00\x00"

    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_header(bytes(data), source_name="zero-divisor.brd")

    error = exc_info.value
    assert error.code == "invalid-unit-divisor"
    assert error.offset == PRE_V18_UNIT_DIVISOR_OFFSET


def test_parse_allegro_header_reports_layout_waypoint_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = COMMITTED_BOARD_HEADERS[0][0]
    monkeypatch.setattr(allegro_parser, "UNIT_DIVISOR_OFFSET", PRE_V18_UNIT_DIVISOR_OFFSET + 1)

    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_header(path.read_bytes(), source_name="layout-drift.brd")

    error = exc_info.value
    assert error.code == "header-layout-mismatch"
    assert error.offset == PRE_V18_UNIT_DIVISOR_OFFSET


def test_parse_allegro_string_table_reports_unterminated_source_string() -> None:
    data = bytes(0x1200) + (123).to_bytes(4, "little") + b"TOP"

    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_string_table(data, string_count=1, source_name="broken.brd")

    error = exc_info.value
    assert error.code == "unterminated-string"
    assert error.offset == 0x1204
    assert "broken.brd" in str(error)


def test_parse_allegro_string_table_rejects_absurd_string_count_before_looping() -> None:
    with pytest.raises(AllegroParseError) as exc_info:
        parse_allegro_string_table(bytes(0x1200), string_count=1_000_001)

    error = exc_info.value
    assert error.code == "string-count-out-of-range"
    assert error.offset == 0x1200


def test_parse_allegro_string_table_preserves_duplicate_key_policy() -> None:
    data = (
        bytes(0x1200)
        + (123).to_bytes(4, "little")
        + b"TOP\x00"
        + (123).to_bytes(4, "little")
        + b"BOTTOM\x00"
    )

    string_table = parse_allegro_string_table(data, string_count=2, source_name="duplicate.brd")

    assert [entry.value for entry in string_table.entries] == ["TOP", "BOTTOM"]
    assert string_table.by_id[123] == "BOTTOM"
    assert string_table.duplicate_keys == (123,)
    with pytest.raises(TypeError):
        string_table.by_id[123] = "SIDE"


@pytest.mark.allegro_corpus
@pytest.mark.skipif(
    not EXTERNAL_KICAD_ALLEGRO_FIXTURES.exists(),
    reason="external KiCad Allegro importer fixtures not present",
)
@pytest.mark.parametrize(
    ("relative_path", "version", "linked_list_count", "string_count", "units", "unit_divisor"),
    OPTIONAL_KICAD_HEADER_EXPECTATIONS,
    ids=[Path(relative_path).name for relative_path, *_ in OPTIONAL_KICAD_HEADER_EXPECTATIONS],
)
def test_optional_kicad_allegro_headers_cover_newer_version_layouts(
    relative_path: str,
    version: str,
    linked_list_count: int,
    string_count: int,
    units: str,
    unit_divisor: int,
) -> None:
    path = EXTERNAL_KICAD_ALLEGRO_FIXTURES / relative_path

    header = parse_allegro_header(path.read_bytes(), source_name=path.name)

    assert header.version.value == version
    assert header.linked_list_count == linked_list_count
    assert header.string_count == string_count
    assert header.board_units == units
    assert header.unit_divisor == unit_divisor
    if version == "V_180":
        assert header.linked_lists[-1].index == 27
        assert header.linked_lists[-1].head_key == 0
        assert header.linked_lists[-1].tail_key == 124
