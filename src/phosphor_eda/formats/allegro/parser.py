"""Header and string-table parsing for native Allegro binary containers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from phosphor_eda.formats.allegro.binary import BoundedBinaryReader
from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.constants import (
    BOARD_UNITS,
    LAYER_MAP_ENTRY_COUNT,
    LAYER_MAP_OFFSET,
    MAGIC_TO_VERSION,
    MAX_STRING_TABLE_ENTRIES,
    PRE_V18_VERSION_STRING_OFFSET,
    STRING_TABLE_OFFSET,
    UNIT_DIVISOR_OFFSET,
    V18_VERSION_STRING_OFFSET,
    VERSION_STRING_BYTES,
    AllegroVersion,
)
from phosphor_eda.formats.allegro.errors import (
    AllegroParseError,
    AllegroUnsupportedVersionError,
)
from phosphor_eda.formats.allegro.record_parser import parse_allegro_record_stream
from phosphor_eda.formats.allegro.records import (
    AllegroBinaryContainer,
    AllegroHeader,
    AllegroLayerMapEntry,
    AllegroLinkedListDescriptor,
    AllegroRecordSet,
    AllegroStringEntry,
    AllegroStringTable,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.formats.common.diagnostics import ParseContext

# Section sizes mirror KiCad's reverse-engineered FILE_HEADER read order. Keep
# these counts close to parser cursor checks so layout drift fails loudly.
_HEADER_ROLE_AND_WRITER_U32_COUNT = 4
_HEADER_MAGIC_AND_FLAGS_U32_COUNT = 2
_PRE_V18_UNKNOWN2_U32_COUNT = 7
_PRE_V18_SHARED_LINKED_LIST_COUNT = 18
_PRE_V18_FILE_REFERENCE_EXTENT_U32_COUNT = 2
_PRE_V18_TRAILING_LINKED_LIST_COUNT = 4
_PRE_V18_UNKNOWN3_U32_COUNT = 1
_V18_UNKNOWN2_AFTER_RECORD_EXTENT_U32_COUNT = 2
_V18_UNKNOWN2_AFTER_STRING_COUNT_U32_COUNT = 1
_V18_LINKED_LIST_COUNT = 28
_V18_FILE_REFERENCE_EXTENT_U32_COUNT = 2
_PRE_V18_UNKNOWN5_U32_COUNT = 17
_V18_UNKNOWN5_U32_COUNT = 9
_BOARD_UNITS_PADDING_BYTES = 3
_V18_STRING_COUNT_PREFIX_U32_COUNT = 2
_UNKNOWN9_U32_COUNT = 50
_UNKNOWN10_U32_COUNT = 3
_PRE_LAYER_MAP_PADDING_U32_COUNT = 110


@dataclass(frozen=True)
class _VersionLayout:
    version_string_offset: int
    unknown5_u32_count: int
    read_header_prefix: Callable[
        [BoundedBinaryReader],
        tuple[tuple[AllegroLinkedListDescriptor, ...], int | None, int | None],
    ]


def parse_allegro_header(data: bytes, *, source_name: str = "<bytes>") -> AllegroHeader:
    reader = BoundedBinaryReader(data, source_name=source_name)
    magic = reader.read_uint32()
    version = _version_from_magic(magic, source_name=source_name)
    layout = _layout_for_version(version)

    _skip_u32(reader, _HEADER_ROLE_AND_WRITER_U32_COUNT)
    object_count = reader.read_uint32()
    _skip_u32(reader, _HEADER_MAGIC_AND_FLAGS_U32_COUNT)

    linked_lists, prefix_string_count, prefix_record_0x27_end = layout.read_header_prefix(reader)
    _require_offset(reader, layout.version_string_offset, source_name=source_name)

    version_string = _decode_fixed_string(reader.read_bytes(VERSION_STRING_BYTES))
    _skip_u32(reader, 1)
    max_key = reader.read_uint32()
    _skip_u32(reader, layout.unknown5_u32_count)

    units_offset = reader.offset
    board_units_id = reader.read_uint8()
    board_units = BOARD_UNITS.get(board_units_id)
    if board_units is None:
        raise AllegroParseError(
            f"unsupported board unit id {board_units_id}",
            code="unsupported-board-units",
            offset=units_offset,
            source_name=source_name,
        )
    reader.skip(_BOARD_UNITS_PADDING_BYTES)

    if version is AllegroVersion.V_180:
        _skip_u32(reader, _V18_STRING_COUNT_PREFIX_U32_COUNT)
        string_count = prefix_string_count
        record_0x27_end = prefix_record_0x27_end
    else:
        _skip_u32(reader, 2)
        record_0x27_end = reader.read_uint32()
        _skip_u32(reader, 1)
        string_count = reader.read_uint32()
    if string_count is None or record_0x27_end is None:
        raise AllegroParseError(
            "header did not expose string count or 0x27 record extent",
            code="header-layout-mismatch",
            offset=reader.offset,
            source_name=source_name,
        )

    _skip_u32(reader, _UNKNOWN9_U32_COUNT)
    _skip_u32(reader, _UNKNOWN10_U32_COUNT)
    _require_offset(reader, UNIT_DIVISOR_OFFSET, source_name=source_name)
    unit_divisor_offset = reader.offset
    unit_divisor = reader.read_uint32()
    if unit_divisor == 0:
        raise AllegroParseError(
            "unit divisor must be nonzero",
            code="invalid-unit-divisor",
            offset=unit_divisor_offset,
            source_name=source_name,
        )

    _skip_u32(reader, _PRE_LAYER_MAP_PADDING_U32_COUNT)
    _require_offset(reader, LAYER_MAP_OFFSET, source_name=source_name)
    layer_map = tuple(
        AllegroLayerMapEntry(class_id=reader.read_uint32(), layer_list_key=reader.read_uint32())
        for _ in range(LAYER_MAP_ENTRY_COUNT)
    )

    return AllegroHeader(
        magic=magic,
        version=version,
        version_string=version_string,
        object_count=object_count,
        max_key=max_key,
        record_0x27_end=record_0x27_end,
        string_count=string_count,
        board_units=board_units,
        unit_divisor=unit_divisor,
        linked_lists=linked_lists,
        layer_map=layer_map,
    )


def parse_allegro_container(data: bytes, *, source_name: str = "<bytes>") -> AllegroBinaryContainer:
    header = parse_allegro_header(data, source_name=source_name)
    string_table = parse_allegro_string_table(
        data,
        string_count=header.string_count,
        source_name=source_name,
    )
    return AllegroBinaryContainer(header=header, string_table=string_table)


def parse_allegro_records(data: bytes, *, source_name: str = "<bytes>") -> AllegroRecordSet:
    container = parse_allegro_container(data, source_name=source_name)
    return parse_allegro_record_stream(
        data,
        header=container.header,
        string_table=container.string_table,
        source_name=source_name,
    )


def parse_allegro_pcb(path: Path, ctx: ParseContext | None = None) -> Board:
    """Parse a native Allegro/OrCAD ``.brd`` file into the PCB domain model."""
    # Accepted for loader parity with other PCB parsers. Allegro diagnostics are
    # currently preserved in board metadata by the source record/build layers.
    del ctx
    record_set = parse_allegro_records(path.read_bytes(), source_name=path.name)
    return build_allegro_board(
        record_set,
        name=path.stem,
        require_board_profile=True,
    )


def parse_allegro_string_table(
    data: bytes, *, string_count: int, source_name: str = "<bytes>"
) -> AllegroStringTable:
    reader = BoundedBinaryReader(data, source_name=source_name)
    if string_count < 0 or string_count > MAX_STRING_TABLE_ENTRIES:
        raise AllegroParseError(
            f"string count {string_count} is outside supported range 0..{MAX_STRING_TABLE_ENTRIES}",
            code="string-count-out-of-range",
            offset=STRING_TABLE_OFFSET,
            source_name=source_name,
        )
    reader.seek(STRING_TABLE_OFFSET)
    entries: list[AllegroStringEntry] = []
    by_id: dict[int, str] = {}
    duplicate_keys: list[int] = []
    for _ in range(string_count):
        key = reader.read_uint32()
        value = reader.read_c_string()
        if key in by_id:
            duplicate_keys.append(key)
        by_id[key] = value
        entries.append(AllegroStringEntry(key=key, value=value))
    return AllegroStringTable(
        entries=tuple(entries),
        by_id=MappingProxyType(by_id),
        duplicate_keys=tuple(duplicate_keys),
        end_offset=reader.offset,
    )


def _read_pre_v18_header_prefix(
    reader: BoundedBinaryReader,
) -> tuple[tuple[AllegroLinkedListDescriptor, ...], int | None, int | None]:
    _skip_u32(reader, _PRE_V18_UNKNOWN2_U32_COUNT)
    linked_lists = [
        _read_linked_list(reader, index=index, v18_word_order=False)
        for index in range(_PRE_V18_SHARED_LINKED_LIST_COUNT)
    ]
    _skip_u32(reader, _PRE_V18_FILE_REFERENCE_EXTENT_U32_COUNT)
    linked_lists.extend(
        _read_linked_list(reader, index=index, v18_word_order=False)
        for index in range(
            _PRE_V18_SHARED_LINKED_LIST_COUNT,
            _PRE_V18_SHARED_LINKED_LIST_COUNT + _PRE_V18_TRAILING_LINKED_LIST_COUNT,
        )
    )
    _skip_u32(reader, _PRE_V18_UNKNOWN3_U32_COUNT)
    return tuple(linked_lists), None, None


def _read_v18_header_prefix(
    reader: BoundedBinaryReader,
) -> tuple[tuple[AllegroLinkedListDescriptor, ...], int | None, int | None]:
    _skip_u32(reader, 2)
    record_0x27_end = reader.read_uint32()
    _skip_u32(reader, _V18_UNKNOWN2_AFTER_RECORD_EXTENT_U32_COUNT)
    string_count = reader.read_uint32()
    _skip_u32(reader, _V18_UNKNOWN2_AFTER_STRING_COUNT_U32_COUNT)

    linked_lists = [
        _read_linked_list(reader, index=index, v18_word_order=True)
        for index in range(_V18_LINKED_LIST_COUNT)
    ]
    _skip_u32(reader, _V18_FILE_REFERENCE_EXTENT_U32_COUNT)
    return tuple(linked_lists), string_count, record_0x27_end


_PRE_V18_LAYOUT = _VersionLayout(
    version_string_offset=PRE_V18_VERSION_STRING_OFFSET,
    unknown5_u32_count=_PRE_V18_UNKNOWN5_U32_COUNT,
    read_header_prefix=_read_pre_v18_header_prefix,
)

_V18_LAYOUT = _VersionLayout(
    version_string_offset=V18_VERSION_STRING_OFFSET,
    unknown5_u32_count=_V18_UNKNOWN5_U32_COUNT,
    read_header_prefix=_read_v18_header_prefix,
)


def _read_linked_list(
    reader: BoundedBinaryReader, *, index: int, v18_word_order: bool
) -> AllegroLinkedListDescriptor:
    first = reader.read_uint32()
    second = reader.read_uint32()
    if v18_word_order:
        head_key = first
        tail_key = second
    else:
        tail_key = first
        head_key = second
    return AllegroLinkedListDescriptor(index=index, head_key=head_key, tail_key=tail_key)


def _skip_u32(reader: BoundedBinaryReader, count: int) -> None:
    reader.skip(4 * count)


def _require_offset(reader: BoundedBinaryReader, expected_offset: int, *, source_name: str) -> None:
    if reader.offset != expected_offset:
        raise AllegroParseError(
            f"header field expected at 0x{expected_offset:X}, got 0x{reader.offset:X}",
            code="header-layout-mismatch",
            offset=reader.offset,
            source_name=source_name,
        )


def _layout_for_version(version: AllegroVersion) -> _VersionLayout:
    if version is AllegroVersion.V_180:
        return _V18_LAYOUT
    return _PRE_V18_LAYOUT


def _version_from_magic(magic: int, *, source_name: str) -> AllegroVersion:
    masked_magic = magic & 0xFFFFFF00
    version = MAGIC_TO_VERSION.get(masked_magic)
    if version is not None:
        return version

    major_version = (magic >> 16) & 0xFFFF
    if major_version <= 0x0012:
        message = f"pre-v16 Allegro files are unsupported (magic 0x{magic:08X})"
    else:
        message = f"unknown Allegro file version magic 0x{magic:08X}"
    raise AllegroUnsupportedVersionError(
        message,
        code="unsupported-version",
        offset=0,
        source_name=source_name,
    )


def _decode_fixed_string(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("latin1")
