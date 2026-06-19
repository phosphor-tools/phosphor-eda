"""Tests for OrCAD DSN binary stream parsing."""

import struct
from pathlib import Path

import pytest

import phosphor_eda.formats.dsn.parser as dsn_parser
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.raw_models import DsnView
from phosphor_eda.formats.dsn.binary_reader import PREAMBLE, BinaryReader
from phosphor_eda.formats.dsn.cis import parse_cis_variant_store
from phosphor_eda.formats.dsn.parser import (
    DsnSchematicPage,
    parse_dsn,
    parse_erc_symbol_stream,
    parse_net_bundle_map_data,
    parse_net_bundle_map_streams,
    parse_package_stream,
    parse_page_tail_objects,
)
from phosphor_eda.formats.dsn.views import parse_view_schematic, warn_repeated_sheet_identity


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
    data = _structure_with_end_offset(dsn_parser.STRUCT_ERC_SYMBOL, body, byte_offset=24)

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
        _short_prefix(dsn_parser.STRUCT_BUS_ENTRY)
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
        dsn_parser.STRUCT_ERC_OBJECT,
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
