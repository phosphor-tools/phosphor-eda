"""Source dataclasses for parsed Allegro binary container data."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.formats.allegro.constants import AllegroBoardUnits, AllegroVersion


@dataclass(frozen=True)
class AllegroLinkedListDescriptor:
    index: int
    head_key: int
    tail_key: int


@dataclass(frozen=True)
class AllegroLayerMapEntry:
    class_id: int
    layer_list_key: int


@dataclass(frozen=True)
class AllegroLayerListEntry:
    """One 0x2A layer-list entry.

    Pre-V16.5 files store inline layer names. V16.5+ files store a string-table
    key plus two native words; the final word is retained because its semantics
    are not identified yet in the reverse-engineered format references.
    """

    index: int
    name: str = ""
    name_string_key: int | None = None
    properties: int | None = None
    unidentified_word: int | None = None


@dataclass(frozen=True)
class AllegroPadstackComponent:
    index: int
    component_type: int
    width: int
    height: int
    offset_x: int
    offset_y: int
    string_key: int
    z1: int | None = None
    z2: int | None = None


type AllegroPayloadValue = (
    bytes
    | int
    | str
    | tuple[int, ...]
    | tuple[tuple[int, ...], ...]
    | tuple[AllegroLayerListEntry, ...]
    | tuple[AllegroPadstackComponent, ...]
)


@dataclass(frozen=True)
class AllegroHeader:
    magic: int
    version: AllegroVersion
    version_string: str
    object_count: int
    max_key: int
    record_0x27_end: int
    string_count: int
    board_units: AllegroBoardUnits
    unit_divisor: int
    linked_lists: tuple[AllegroLinkedListDescriptor, ...]
    layer_map: tuple[AllegroLayerMapEntry, ...]

    @property
    def linked_list_count(self) -> int:
        return len(self.linked_lists)


@dataclass(frozen=True)
class AllegroStringEntry:
    key: int
    value: str


@dataclass(frozen=True)
class AllegroStringTable:
    entries: tuple[AllegroStringEntry, ...]
    by_id: Mapping[int, str]
    duplicate_keys: tuple[int, ...]
    end_offset: int


@dataclass(frozen=True)
class AllegroBinaryContainer:
    header: AllegroHeader
    string_table: AllegroStringTable


@dataclass(frozen=True)
class AllegroRecordDiagnostic:
    code: str
    message: str
    offset: int | None = None
    tag: int | None = None
    key: int | None = None
    reference_key: int | None = None


@dataclass(frozen=True)
class AllegroRecord:
    tag: int
    offset: int
    end_offset: int
    key: int | None
    next_key: int | None
    payload: Mapping[str, AllegroPayloadValue]

    @property
    def byte_length(self) -> int:
        return self.end_offset - self.offset


@dataclass(frozen=True)
class AllegroRecordSet:
    header: AllegroHeader | None
    string_table: AllegroStringTable | None
    records: tuple[AllegroRecord, ...]
    end_offset: int
    _by_key: Mapping[int, AllegroRecord] = field(init=False, repr=False)
    _tag_counts: Mapping[int, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        by_key = {record.key: record for record in self.records if record.key is not None}
        counts: dict[int, int] = {}
        for record in self.records:
            counts[record.tag] = counts.get(record.tag, 0) + 1
        object.__setattr__(self, "_by_key", MappingProxyType(by_key))
        object.__setattr__(self, "_tag_counts", MappingProxyType(counts))

    @property
    def by_key(self) -> Mapping[int, AllegroRecord]:
        return self._by_key

    @property
    def tag_counts(self) -> Mapping[int, int]:
        return self._tag_counts
