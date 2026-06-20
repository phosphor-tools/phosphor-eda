"""Source dataclasses for parsed Allegro binary container data."""

from __future__ import annotations

from dataclasses import dataclass
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
class AllegroHeader:
    magic: int
    version: AllegroVersion
    version_string: str
    object_count: int
    max_key: int
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
