"""Tests for OrCAD DSN binary stream parsing."""

import struct
from pathlib import Path

import olefile
import pytest

import phosphor_eda.formats.dsn.parser as dsn_parser
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.binary_reader import (
    PREAMBLE,
    STRUCT_BUS_ENTRY,
    STRUCT_ERC_OBJECT,
    STRUCT_ERC_SYMBOL,
    BinaryReader,
)
from phosphor_eda.formats.dsn.cis import parse_cis_variant_store
from phosphor_eda.formats.dsn.erc import parse_erc_symbol_stream
from phosphor_eda.formats.dsn.packages import parse_package_stream
from phosphor_eda.formats.dsn.parser import (
    DsnSchematicPage,
    parse_dsn,
    parse_net_bundle_map_data,
    parse_net_bundle_map_streams,
    parse_page_tail_objects,
)
from phosphor_eda.formats.dsn.raw_models import DsnView
from phosphor_eda.formats.dsn.views import parse_view_schematic, warn_repeated_sheet_identity

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNC_DSN = (
    FIXTURES
    / "orcad/opencellular-sync/orcad/OpenCellular/electronics/sync/schematics/dsn"
    / "FB_CONNECT1_SYNC_LIFE-3_V1P1.DSN"
)
SYNC_ERC_STREAM = "Symbols/ERC"
SYNC_VIEW_SCHEMATIC_STREAM = "Views/opencellular_coonect1_sync/Schematic"


def _read_dsn_stream(dsn_path: Path, stream_path: str) -> bytes:
    with olefile.OleFileIO(str(dsn_path)) as ole:
        return ole.openstream(stream_path).read()


def _dsn_string(value: str) -> bytes:
    encoded = value.encode("ascii")
    return struct.pack("<H", len(encoded)) + encoded + b"\x00"


def _cis_size_prefixed(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + payload


def _cis_string_list(values: list[str]) -> bytes:
    return _cis_size_prefixed(b"\xf9".join(value.encode("latin1") for value in values))


def _short_prefix(type_id: int, size: int = 0) -> bytes:
    return bytes([type_id]) + struct.pack("<h", size)


def _structure(type_id: int, body: bytes) -> bytes:
    return (
        bytes([type_id])
        + struct.pack("<I", len(body) + 3)
        + (b"\x00" * 4)
        + _short_prefix(type_id, -1)
        + body
    )


def _structure_with_end_offset(type_id: int, body: bytes, byte_offset: int) -> bytes:
    return (
        bytes([type_id])
        + struct.pack("<I", byte_offset)
        + (b"\x00" * 4)
        + _short_prefix(type_id, -1)
        + body
    )


def _cis_variant_names(names: list[tuple[str, bool]]) -> bytes:
    payload = bytearray(struct.pack("<II", 900, len(names)))
    for name, has_null in names:
        encoded = name.encode("latin1")
        payload.extend(struct.pack("<H", len(encoded)))
        payload.extend(encoded)
        if has_null:
            payload.append(0)
    return bytes(payload)


def _net_bundle_map_stream(name: str, members: list[str]) -> bytes:
    member_data = b"".join(_dsn_string(member) + struct.pack("<H", 1) for member in members)
    return (
        b"\x00\x00"
        + struct.pack("<H", 1)
        + _dsn_string(name)
        + _short_prefix(103)
        + (b"\x00" * 6)
        + struct.pack("<H", len(members))
        + member_data
    )


def _erc_object(
    *,
    symbol_name: str = "ERC",
    message: str = "ERROR(ORCAP-1620): Port has a type which is inconsistent",
    subject: str = "NET_A ",
    detail: str = "SCHEMATIC1, PAGE1  (30.48, 78.74) ",
) -> bytes:
    return (
        _short_prefix(77)
        + PREAMBLE
        + struct.pack("<I", 0)
        + (b"\x00" * 8)
        + _dsn_string(symbol_name)
        + struct.pack("<I", 12345)
        + struct.pack("<h", 305)
        + struct.pack("<h", 1195)
        + struct.pack("<h", 315)
        + struct.pack("<h", 1205)
        + struct.pack("<h", 1200)
        + struct.pack("<h", 310)
        + struct.pack("<B", 48)
        + (b"\x00" * 3)
        + struct.pack("<H", 0)
        + struct.pack("<B", 0x4B)
        + _dsn_string(message)
        + _dsn_string(subject)
        + _dsn_string(detail)
    )


def test_parse_net_bundle_map_data_stream() -> None:
    data = _net_bundle_map_stream("I2C", ["SDA", "SCL"])

    bundles = parse_net_bundle_map_data(data)

    assert len(bundles) == 1
    assert bundles[0].name == "I2C"
    assert [(member.name, member.wire_type) for member in bundles[0].members] == [
        ("SDA", 1),
        ("SCL", 1),
    ]


def test_parse_net_bundle_map_streams_accumulates_multiple_streams() -> None:
    bundles = parse_net_bundle_map_streams(
        [
            _net_bundle_map_stream("I2C", ["SDA", "SCL"]),
            _net_bundle_map_stream("SPI", ["MOSI", "MISO"]),
        ]
    )

    assert [bundle.name for bundle in bundles] == ["I2C", "SPI"]


def test_malformed_net_bundle_map_data_warns_and_returns_no_bundles() -> None:
    ctx = ParseContext()

    bundles = parse_net_bundle_map_data(b"\x00\x00\x01", ctx)

    assert bundles == []
    assert any(issue.category == "dsn_net_bundle_map" for issue in ctx.issues)


def test_parse_package_stream_preserves_uncommon_raw_fields() -> None:
    stream_path = "Packages/SYNTH"
    part_cell = _structure(
        6,
        _dsn_string("SYNTH_CELL")
        + _dsn_string("unused")
        + struct.pack("<H", 2)
        + _dsn_string("SYNTH_CELL.Normal")
        + _dsn_string("SYNTH_CELL.Convert"),
    )
    library_part = _structure(
        24,
        _dsn_string("SYNTH_CELL.Normal") + _dsn_string("synthetic.olb"),
    )
    device = _structure(
        32,
        _dsn_string("A")
        + _dsn_string("SYNTH")
        + struct.pack("<H", 3)
        + struct.pack("<h", -1)
        + _dsn_string("A1")
        + bytes([0x80 | 5])
        + _dsn_string("B2")
        + bytes([0x7F]),
    )
    package = _structure(
        31,
        _dsn_string("SYNTH")
        + _dsn_string("synthetic.olb")
        + _dsn_string("U")
        + _dsn_string("")
        + _dsn_string("SYNTH_FOOTPRINT")
        + struct.pack("<H", 1)
        + device,
    )
    data = struct.pack("<H", 1) + part_cell + struct.pack("<H", 1) + library_part + package

    parsed = parse_package_stream(data, ParseContext(), stream_path)

    assert parsed is not None
    assert parsed.name == "SYNTH"
    assert parsed.source_library == "synthetic.olb"
    assert parsed.pcb_footprint == "SYNTH_FOOTPRINT"
    assert len(parsed.part_cells) == 1
    assert parsed.part_cells[0].convert_name == "SYNTH_CELL.Convert"
    assert [part.name for part in parsed.part_cells[0].library_parts] == ["SYNTH_CELL.Normal"]
    assert [part.name for part in parsed.library_parts] == ["SYNTH_CELL.Normal"]
    assert len(parsed.devices) == 1
    assert [
        (pin.order, pin.package_pin, pin.ignored, pin.group) for pin in parsed.devices[0].pins
    ] == [
        (0, "", False, ""),
        (1, "A1", True, "5"),
        (2, "B2", False, ""),
    ]


def test_package_stream_diagnostic_has_one_authoritative_offset() -> None:
    ctx = ParseContext()
    stream_path = "Packages/BAD"

    parsed = parse_package_stream(struct.pack("<H", 65535), ctx, stream_path)

    assert parsed is None
    assert len(ctx.issues) == 1
    assert ctx.issues[0].message == (
        "Packages/BAD at byte offset 0: implausible part-cell count 65535"
    )


def test_package_library_part_overrun_is_diagnostic() -> None:
    stream_path = "Packages/BAD_LIB"
    part_cell = _structure(
        6,
        _dsn_string("SYNTH_CELL")
        + _dsn_string("unused")
        + struct.pack("<H", 1)
        + _dsn_string("SYNTH_CELL.Normal"),
    )
    library_part = _structure_with_end_offset(
        24,
        _dsn_string("SYNTH_CELL.Normal") + _dsn_string("synthetic.olb"),
        byte_offset=6,
    )
    data = struct.pack("<H", 1) + part_cell + struct.pack("<H", 1) + library_part
    ctx = ParseContext()

    parsed = parse_package_stream(data, ctx, stream_path)

    assert parsed is None
    assert len(ctx.issues) == 1
    assert ctx.issues[0].category == "dsn_package_stream"
    assert "library part parsed to byte" in ctx.issues[0].message


def test_parse_dsn_closes_ole_file_when_parse_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class _BrokenOle:
        def __init__(self, _path: str) -> None:
            pass

        def openstream(self, _path: str) -> object:
            raise RuntimeError("boom")

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(dsn_parser.olefile, "OleFileIO", _BrokenOle)

    with pytest.raises(RuntimeError, match="boom"):
        parse_dsn(Path("broken.dsn"))

    assert closed == [True]


def test_cis_variant_store_preserves_multiple_boms_and_unknown_streams() -> None:
    store = parse_cis_variant_store(
        {
            "CIS/VariantStore/BOM/BOMDataStream": _cis_string_list(["2", "BomA", "BomB"]),
            "CIS/VariantStore/BOM/BomA/BomA": _cis_string_list(["1", "Common"]),
            "CIS/VariantStore/BOM/BomA/BOMPartData": _cis_string_list(["1", "101"]),
            "CIS/VariantStore/BOM/BomB/BomB": _cis_string_list(["0"]),
            "CIS/VariantStore/UnexpectedStream": b"raw",
        },
        {"CIS/VariantStore"},
        {101: 201},
    )

    assert [bom.name for bom in store.boms] == ["BomA", "BomB"]
    assert store.boms[0].entries[0].raw_id == 101
    assert store.boms[0].entries[0].resolved_instance_db_id == 201
    assert store.boms[1].entries == []
    assert [(stream.stream_path, stream.size) for stream in store.unknown_streams] == [
        ("CIS/VariantStore/UnexpectedStream", 3)
    ]


def test_cis_variant_names_missing_null_does_not_skip_next_length_prefix() -> None:
    ctx = ParseContext()

    store = parse_cis_variant_store(
        {"CIS/VariantStore/VariantNames": _cis_variant_names([("DNI", False), ("Common", True)])},
        {"CIS/VariantStore"},
        {},
        ctx,
    )

    assert [name.name for name in store.variant_names] == ["DNI", "Common"]
    assert any("missing null terminator after 'DNI'" in issue.message for issue in ctx.issues)


def test_cis_update_storage_rows_preserve_mismatches_without_external_corpus() -> None:
    update_payload = (
        b"101\xb0Part Number^Value\xc0PN-1^10k~102\xb0Part Number^Value^Description\xc0PN-2^20k"
    )
    store = parse_cis_variant_store(
        {
            "CIS/VariantStore/Groups/GroupsDataStream": _cis_size_prefixed(b"DNI\xb00\xb0\xb0"),
            "CIS/VariantStore/Groups/DNI/DNI": _cis_size_prefixed(b"0\xb0101"),
            "CIS/VariantStore/Groups/DNI/UpdateStorageGroupDataStream": (
                _cis_size_prefixed(update_payload)
            ),
        },
        {"CIS/VariantStore"},
        {101: 201},
    )

    assert len(store.groups) == 1
    rows = store.groups[0].update_storage_rows
    assert len(rows) == 2
    assert rows[0].occurrence_id == 101
    assert rows[0].resolved_instance_db_id == 201
    assert rows[0].columns == ["Part Number", "Value"]
    assert rows[0].values == ["PN-1", "10k"]
    assert rows[0].diagnostics == []
    assert rows[1].occurrence_id == 102
    assert rows[1].resolved_instance_db_id is None
    assert rows[1].columns == ["Part Number", "Value", "Description"]
    assert rows[1].values == ["PN-2", "20k"]
    assert rows[1].diagnostics == [
        "update-storage ID did not resolve through hierarchy occurrences",
        "update-storage row has 3 columns and 2 values",
    ]


# H3/T3: the CIS parser has ~14 ``_warn`` branches; most of the truncation and
# malformed-stream paths were unexercised. Each case below slices a synthetic
# builder mid-field (or supplies a deliberately malformed size prefix / count)
# to drive exactly one branch, asserting the diagnostic content rather than just
# "an error happened".
_CIS_WARN_CASES: list[tuple[str, dict[str, bytes], str]] = [
    (
        "empty_group_name",
        {"CIS/VariantStore/Groups/GroupsDataStream": b"\xb00\xb0\xb0"},
        "group row 0 has an empty name",
    ),
    (
        "group_has_no_member_stream",
        {"CIS/VariantStore/Groups/GroupsDataStream": _cis_size_prefixed(b"DNI\xb00")},
        "group 'DNI' has no member stream",
    ),
    (
        "group_member_non_numeric_id",
        {
            "CIS/VariantStore/Groups/GroupsDataStream": _cis_size_prefixed(b"DNI\xb00"),
            "CIS/VariantStore/Groups/DNI/DNI": _cis_size_prefixed(b"0\xb0ABC"),
        },
        "group row 0 has non-numeric ID 'ABC'",
    ),
    (
        "update_storage_missing_columns",
        {
            "CIS/VariantStore/Groups/GroupsDataStream": _cis_size_prefixed(b"DNI\xb00"),
            "CIS/VariantStore/Groups/DNI/DNI": _cis_size_prefixed(b"0\xb0101"),
            "CIS/VariantStore/Groups/DNI/UpdateStorageGroupDataStream": _cis_size_prefixed(b"101"),
        },
        "update-storage row 0 is missing columns",
    ),
    (
        "bom_declared_count_mismatch",
        {
            "CIS/VariantStore/BOM/BOMDataStream": _cis_string_list(["2", "BomA"]),
            "CIS/VariantStore/BOM/BomA/BomA": _cis_string_list(["0"]),
        },
        "declared 2 BOM names but parsed 1",
    ),
    (
        "bom_has_no_child_streams",
        {"CIS/VariantStore/BOM/BOMDataStream": _cis_string_list(["1", "BomA"])},
        "BOM 'BomA' has no child streams",
    ),
    (
        "bom_part_data_declared_count_mismatch",
        {
            "CIS/VariantStore/BOM/BOMDataStream": _cis_string_list(["1", "BomA"]),
            "CIS/VariantStore/BOM/BomA/BomA": _cis_string_list(["0"]),
            "CIS/VariantStore/BOM/BomA/BOMPartData": _cis_string_list(["2", "101"]),
        },
        "declared 2 BOMPartData IDs but parsed 1",
    ),
    (
        "bom_part_data_non_numeric_id",
        {
            "CIS/VariantStore/BOM/BOMDataStream": _cis_string_list(["1", "BomA"]),
            "CIS/VariantStore/BOM/BomA/BomA": _cis_string_list(["0"]),
            "CIS/VariantStore/BOM/BomA/BOMPartData": _cis_string_list(["1", "XYZ"]),
        },
        "BOMPartData row 0 has non-numeric ID 'XYZ'",
    ),
    (
        "variant_names_stream_too_short",
        {"CIS/VariantStore/VariantNames": b"\x00\x00\x00"},
        "VariantNames stream is too short",
    ),
    (
        "variant_names_truncated_length",
        {"CIS/VariantStore/VariantNames": struct.pack("<II", 900, 1) + b"\x01"},
        "truncated VariantNames length at byte 8",
    ),
    (
        "variant_names_truncated_value",
        {"CIS/VariantStore/VariantNames": struct.pack("<II", 900, 1) + struct.pack("<H", 50)},
        "truncated VariantNames value at byte 10",
    ),
    (
        "stream_too_short_for_size_prefix",
        {"CIS/VariantStore/BOM/BOMDataStream": b"\x00"},
        "stream is too short for size prefix",
    ),
    (
        "size_prefix_mismatch",
        {"CIS/VariantStore/BOM/BOMDataStream": struct.pack("<I", 99) + b"1\xf9BomA"},
        "declared 99 payload bytes but found 6",
    ),
]


@pytest.mark.parametrize(
    ("streams", "expected_substring"),
    [(streams, substring) for _, streams, substring in _CIS_WARN_CASES],
    ids=[case_id for case_id, _, _ in _CIS_WARN_CASES],
)
def test_cis_malformed_streams_emit_targeted_warning(
    streams: dict[str, bytes], expected_substring: str
) -> None:
    ctx = ParseContext()

    _ = parse_cis_variant_store(streams, {"CIS/VariantStore"}, {}, ctx)

    messages = [issue.message for issue in ctx.issues if issue.category == "dsn_cis"]
    assert any(expected_substring in message for message in messages), messages


def test_unsupported_erc_symbol_stream_warns() -> None:
    ctx = ParseContext()

    symbol = parse_erc_symbol_stream(_short_prefix(0x4E), "Symbols/ERC", ctx)

    assert symbol is None
    assert any(issue.category == "dsn_erc_symbol" for issue in ctx.issues)


def test_malformed_erc_symbol_stream_warns() -> None:
    ctx = ParseContext()

    symbol = parse_erc_symbol_stream(b"", "Symbols/ERC", ctx)

    assert symbol is None
    assert any(issue.category == "dsn_erc_symbol" for issue in ctx.issues)


def test_overrun_erc_symbol_stream_warns() -> None:
    ctx = ParseContext()
    body = (
        PREAMBLE
        + struct.pack("<I", 0)
        + _dsn_string("E")
        + _dsn_string("L")
        + struct.pack("<I", 48)
        + struct.pack("<H", 0)
    )
    data = _structure_with_end_offset(STRUCT_ERC_SYMBOL, body, byte_offset=24)

    symbol = parse_erc_symbol_stream(data, "Symbols/ERC", ctx)

    assert symbol is None
    assert any(
        issue.category == "dsn_erc_symbol" and "ERC symbol parsed to byte" in issue.message
        for issue in ctx.issues
    )


def test_unsupported_page_tail_erc_object_warns() -> None:
    ctx = ParseContext()
    page = DsnSchematicPage(name="PAGE1")
    data = struct.pack("<H", 1) + _short_prefix(0x4E) + struct.pack("<H", 0)

    parse_page_tail_objects(BinaryReader(data, "page-tail"), page, ctx)

    assert page.erc_objects == []
    assert any(issue.category == "dsn_erc_object" for issue in ctx.issues)


def test_malformed_page_tail_erc_object_does_not_parse_misaligned_bus_entries() -> None:
    ctx = ParseContext()
    page = DsnSchematicPage(name="PAGE1")
    data = struct.pack("<H", 1) + _short_prefix(77) + struct.pack("<H", 1)

    parse_page_tail_objects(BinaryReader(data, "page-tail"), page, ctx)

    assert page.erc_objects == []
    assert page.bus_entries == []
    assert any(issue.category == "dsn_page_tail" for issue in ctx.issues)


def test_page_tail_erc_object_overrun_warns_without_rewinding() -> None:
    ctx = ParseContext()
    page = DsnSchematicPage(name="PAGE1")
    body_before_bus_count = (
        PREAMBLE
        + struct.pack("<I", 0)
        + (b"\x00" * 8)
        + _dsn_string("ERC")
        + struct.pack("<I", 12345)
        + struct.pack("<h", 305)
        + struct.pack("<h", 1195)
        + struct.pack("<h", 315)
        + struct.pack("<h", 1205)
        + struct.pack("<h", 1200)
        + struct.pack("<h", 310)
        + struct.pack("<B", 48)
        + (b"\x00" * 3)
        + struct.pack("<H", 0)
        + struct.pack("<B", 0x4B)
        + _dsn_string("ERROR")
        + _dsn_string("NET_A")
    )
    bus_entry = (
        _short_prefix(STRUCT_BUS_ENTRY)
        + PREAMBLE
        + struct.pack("<I", 0)
        + struct.pack("<I", 0xAABBCCDD)
        + struct.pack("<i", 11)
        + struct.pack("<i", 22)
        + struct.pack("<i", 33)
        + struct.pack("<i", 44)
        + b"TRAILING!"
    )
    malformed_object = _structure_with_end_offset(
        STRUCT_ERC_OBJECT,
        body_before_bus_count + struct.pack("<H", 1) + bus_entry,
        byte_offset=3 + len(body_before_bus_count),
    )
    data = struct.pack("<H", 1) + malformed_object

    parse_page_tail_objects(BinaryReader(data, "page-tail"), page, ctx)

    assert page.erc_objects == []
    assert page.bus_entries == []
    assert any(
        issue.category == "dsn_page_tail" and "ERC object parsed to byte" in issue.message
        for issue in ctx.issues
    )


def test_page_tail_erc_object_decodes_raw_fields() -> None:
    ctx = ParseContext()
    page = DsnSchematicPage(name="PAGE1")
    data = struct.pack("<H", 1) + _erc_object() + struct.pack("<H", 0)

    parse_page_tail_objects(BinaryReader(data, "page-tail"), page, ctx)

    assert len(page.erc_objects) == 1
    erc_object = page.erc_objects[0]
    assert erc_object.page_name == "PAGE1"
    assert erc_object.symbol_name == "ERC"
    assert erc_object.db_id == 12345
    assert erc_object.loc_x == 1195
    assert erc_object.loc_y == 305
    assert erc_object.bbox_x1 == 1200
    assert erc_object.bbox_y1 == 310
    assert erc_object.bbox_x2 == 1205
    assert erc_object.bbox_y2 == 315
    assert erc_object.color == 48
    assert erc_object.unknown_flag == 0x4B
    assert erc_object.message == "ERROR(ORCAP-1620): Port has a type which is inconsistent"
    assert erc_object.subject == "NET_A "
    assert erc_object.detail == "SCHEMATIC1, PAGE1  (30.48, 78.74) "
    assert not ctx.issues


def test_malformed_view_schematic_warns_and_returns_no_view() -> None:
    ctx = ParseContext()

    view = parse_view_schematic(
        b"\x00",
        stream_path="Views/Broken/Schematic",
        hierarchy_stream_paths=[],
        ctx=ctx,
    )

    assert view is None
    assert any(issue.category == "dsn_view" for issue in ctx.issues)


# H4/T4: the trivial ``b""`` / ``b"\x00"`` malformed tests above cover the empty
# edge; these augment them with the established realistic pattern -- slice a real
# fixture stream at several cut points and assert the parser degrades to a
# diagnostic (never a crash, never a partially-built object).
@pytest.mark.parametrize("cut_fraction", [0.25, 0.5, 0.75])
def test_truncated_real_erc_symbol_stream_diagnoses_without_partial_result(
    cut_fraction: float,
) -> None:
    data = _read_dsn_stream(SYNC_DSN, SYNC_ERC_STREAM)
    truncated = data[: int(len(data) * cut_fraction)]
    ctx = ParseContext()

    symbol = parse_erc_symbol_stream(truncated, SYNC_ERC_STREAM, ctx)

    assert symbol is None
    assert any(issue.category == "dsn_erc_symbol" for issue in ctx.issues)


def test_full_real_erc_symbol_stream_parses_cleanly() -> None:
    data = _read_dsn_stream(SYNC_DSN, SYNC_ERC_STREAM)
    ctx = ParseContext()

    symbol = parse_erc_symbol_stream(data, SYNC_ERC_STREAM, ctx)

    assert symbol is not None
    assert not ctx.issues


@pytest.mark.parametrize("cut_fraction", [0.25, 0.5, 0.75])
def test_truncated_real_view_schematic_stream_diagnoses_without_partial_result(
    cut_fraction: float,
) -> None:
    data = _read_dsn_stream(SYNC_DSN, SYNC_VIEW_SCHEMATIC_STREAM)
    truncated = data[: int(len(data) * cut_fraction)]
    ctx = ParseContext()

    view = parse_view_schematic(
        truncated,
        stream_path=SYNC_VIEW_SCHEMATIC_STREAM,
        hierarchy_stream_paths=[],
        ctx=ctx,
    )

    assert view is None
    assert any(issue.category == "dsn_view" for issue in ctx.issues)


def test_full_real_view_schematic_stream_parses_all_pages() -> None:
    data = _read_dsn_stream(SYNC_DSN, SYNC_VIEW_SCHEMATIC_STREAM)
    ctx = ParseContext()

    view = parse_view_schematic(
        data,
        stream_path=SYNC_VIEW_SCHEMATIC_STREAM,
        hierarchy_stream_paths=[],
        ctx=ctx,
    )

    assert view is not None
    assert len(view.page_names) == 14
    assert not ctx.issues


def test_repeated_sheet_identity_warning_uses_reused_page_names() -> None:
    ctx = ParseContext()

    warn_repeated_sheet_identity(
        [
            DsnView(name="A", page_names=["Shared"]),
            DsnView(name="B", page_names=["Shared"]),
        ],
        ctx,
    )

    assert [issue.category for issue in ctx.issues] == ["dsn_repeated_sheet_identity"]


def test_unique_view_page_names_do_not_warn_about_repeated_sheet_identity() -> None:
    ctx = ParseContext()

    warn_repeated_sheet_identity(
        [
            DsnView(name="A", page_names=["A1"]),
            DsnView(name="B", page_names=["B1"]),
        ],
        ctx,
    )

    assert ctx.issues == []


# --- A3: binary reader hardening ---


def test_read_string_len_zero_raises_on_missing_terminator() -> None:
    # Length says 4 bytes but the byte after them is not the 0x00 terminator.
    reader = BinaryReader(struct.pack("<H", 4) + b"ABCD" + b"X", "s")

    with pytest.raises(struct.error, match="not null-terminated"):
        reader.read_string_len_zero()


def test_read_string_len_zero_raises_on_eof_overshoot() -> None:
    # Length claims 8 bytes but only 4 remain — the old reader sliced silently.
    reader = BinaryReader(struct.pack("<H", 8) + b"ABCD", "s")

    with pytest.raises(struct.error, match="overshoots stream end"):
        reader.read_string_len_zero()


def test_read_string_len_zero_allow_missing_terminator_returns_string() -> None:
    reader = BinaryReader(struct.pack("<H", 3) + b"DNI" + b"\x03", "s")

    assert reader.read_string_len_zero(allow_missing_terminator=True) == "DNI"
    # A non-null trailing byte is left unconsumed for the caller to interpret.
    assert reader.pos == 5


def test_read_string_len_zero_decodes_cp1252() -> None:
    reader = BinaryReader(struct.pack("<H", 5) + b"0.1\xb5F" + b"\x00", "s")

    assert reader.read_string_len_zero() == "0.1µF"


def test_can_read_string_len_zero_respects_scan_limit() -> None:
    from phosphor_eda.formats.dsn.cache import _can_read_string_len_zero

    data = struct.pack("<H", 4) + b"ABCD" + b"\x00"

    # The terminator sits at offset 6; a limit that stops before it must reject.
    assert _can_read_string_len_zero(data, 0) is True
    assert _can_read_string_len_zero(data, 0, limit=6) is False


# --- B1: unknown-structure diagnostics per page section ---


def _page_prologue() -> bytes:
    return (
        _short_prefix(0, 0)
        + _dsn_string("PAGE")
        + _dsn_string("A4")
        + (b"\x00" * 156)
        + struct.pack("<H", 0)  # title blocks
        + struct.pack("<H", 0)  # T0x34
        + struct.pack("<H", 0)  # T0x35
    )


def _unknown_structure(type_id: int = 0x99) -> bytes:
    body = b"\x00" * 40
    return _structure_with_end_offset(type_id, body, byte_offset=3 + len(body))


_PAGE_SECTIONS = ("wire", "instance", "port", "global", "off-page connector")


def _page_with_unknown(section: str) -> bytes:
    unknown = _unknown_structure()

    def count(name: str) -> bytes:
        return struct.pack("<H", 1 if section == name else 0)

    def record(name: str, trailing: bytes = b"") -> bytes:
        return unknown + trailing if section == name else b""

    return (
        _page_prologue()
        + struct.pack("<H", 0)  # nets
        + count("wire")
        + record("wire")
        + count("instance")
        + record("instance")
        + count("port")
        + record("port")
        + count("global")
        + record("global", b"\x00" * 5)
        + count("off-page connector")
        + record("off-page connector", b"\x00" * 5)
    )


@pytest.mark.parametrize("section", _PAGE_SECTIONS)
def test_unknown_structure_in_page_section_warns_without_crashing(section: str) -> None:
    from phosphor_eda.formats.dsn.parser import parse_page

    ctx = ParseContext()

    page = parse_page(_page_with_unknown(section), [], ctx)

    unknown_issues = [issue for issue in ctx.issues if issue.category == "dsn_unknown_structure"]
    assert len(unknown_issues) == 1
    assert f"page {section} section" in unknown_issues[0].message
    assert "0x99" in unknown_issues[0].message
    # Offset-anchored recovery leaves the section's parsed list empty, not
    # populated with garbage.
    assert page.wires == []
    assert page.instances == []
    assert page.ports == []
    assert page.globals == []
    assert page.off_page_connectors == []


def test_unknown_bus_entry_structure_warns() -> None:
    ctx = ParseContext()
    page = DsnSchematicPage(name="PAGE1")
    data = (
        struct.pack("<H", 0)  # ERC object count
        + struct.pack("<H", 1)  # bus entry count
        + _unknown_structure()
    )

    parse_page_tail_objects(BinaryReader(data, "page-tail"), page, ctx)

    assert page.bus_entries == []
    assert [issue.category for issue in ctx.issues] == ["dsn_unknown_structure"]
    assert "page bus entry section" in ctx.issues[0].message


# --- A9/A10: boundary sweep and old-format robustness ---


def test_truncated_library_stream_raises_typed_dsn_format_error() -> None:
    from phosphor_eda.formats.dsn.errors import DsnFormatError
    from phosphor_eda.formats.dsn.library import parse_library

    # An old-format Library header cut short (unpack past buffer) must surface as
    # a typed DsnFormatError instead of an unhandled struct.error crash (A10).
    with pytest.raises(DsnFormatError, match="Library stream is truncated"):
        parse_library(b"\x00" * 40)


def test_truncated_page_stream_raises_typed_dsn_format_error() -> None:
    from phosphor_eda.formats.dsn.errors import DsnFormatError
    from phosphor_eda.formats.dsn.parser import parse_page

    # The header parses but the net list is cut short: the early structural
    # reads run outside the per-section guards, so a raw struct.error would
    # otherwise escape parse_page. It must be converted to a typed
    # DsnFormatError so load_orcad_project records a parse_error instead of
    # aborting the whole project load.
    data = _page_prologue() + struct.pack("<H", 3)  # 3 nets declared, none present
    with pytest.raises(DsnFormatError, match="Page stream is truncated"):
        parse_page(data, [], ParseContext())


def test_library_string_count_uint16_fallback_emits_version_crosscheck() -> None:
    from phosphor_eda.formats.dsn.library import parse_library

    header = (
        b"\x00" * 32  # intro pad
        + struct.pack("<HH", 3, 2)  # version major/minor
        + b"\x00" * 8  # timestamps
        + b"\x00" * 4  # zero padding
        + struct.pack("<H", 1)  # text_font_len -> no LOGFONTA entries
        + struct.pack("<H", 0)  # some_len
        + b"\x00" * 8  # unknown_2_0/1
    )
    header += b"".join(_dsn_string("f") for _ in range(8))  # 8 part fields
    header += b"\x00" * 156  # page settings
    header += struct.pack("<I", 131072)  # str_lst_len > 100000 -> uint16 fallback
    ctx = ParseContext()

    _header, string_list, _fields = parse_library(header, ctx)

    assert string_list == []
    assert any(
        issue.category == "dsn_library_string_width" and "version_major=3" in issue.message
        for issue in ctx.issues
    )


def test_malformed_cache_stream_warns_and_continues() -> None:
    from phosphor_eda.formats.dsn.cache import parse_cache_symbols

    # Header + a symbol-name length prefix that overshoots the stream. The read
    # raises struct.error; containment keeps parse_dsn alive (A9b).
    data = b"\x00" * 4 + b"\x05\x00" + b"\xff\x7f" + b"\x00\x00\x00\x00"
    ctx = ParseContext()

    result = parse_cache_symbols(data, ctx)

    assert result.pin_names == {}
    assert any(issue.category == "dsn_cache" for issue in ctx.issues)


def test_wire_alias_count_pre_check_rejects_truncated_wire_body() -> None:
    # The 1 unknown byte + uint16 count must fit inside the wire body before it
    # is read; a body that ends first fails the pre-check instead of reading a
    # bogus count out of the next structure (A9c).
    reader = BinaryReader(b"\x00\x00\x00\x00", "wire")
    reader.pos = 2

    with pytest.raises(ValueError, match="wire body ends before its alias count"):
        dsn_parser._parse_wire_aliases(reader, wire_end_offset=3, ctx=None)
