"""Object-key graph helpers for Allegro source records."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from phosphor_eda.formats.allegro.records import AllegroRecordDiagnostic

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from phosphor_eda.formats.allegro.records import (
        AllegroLinkedListDescriptor,
        AllegroRecord,
        AllegroRecordSet,
    )


@dataclass(frozen=True)
class AllegroLinkedListWalk:
    records: tuple[AllegroRecord, ...]
    diagnostics: tuple[AllegroRecordDiagnostic, ...]


@dataclass(frozen=True)
class AllegroObjectGraph:
    records: Sequence[AllegroRecord]
    by_key: Mapping[int, AllegroRecord]
    diagnostics: tuple[AllegroRecordDiagnostic, ...]

    def walk_linked_list(self, descriptor: AllegroLinkedListDescriptor) -> AllegroLinkedListWalk:
        return self.walk_key_chain(head_key=descriptor.head_key, tail_key=descriptor.tail_key)

    def walk_key_chain(
        self, *, head_key: int, tail_key: int | None = None
    ) -> AllegroLinkedListWalk:
        if head_key == 0:
            return AllegroLinkedListWalk(records=(), diagnostics=())

        records: list[AllegroRecord] = []
        diagnostics: list[AllegroRecordDiagnostic] = []
        seen: set[int] = set()
        current_key = head_key

        while current_key != 0:
            if current_key in seen:
                diagnostics.append(
                    AllegroRecordDiagnostic(
                        code="linked-list-cycle",
                        message=f"linked list repeats object key {current_key}",
                        reference_key=current_key,
                    )
                )
                break
            seen.add(current_key)

            record = self.by_key.get(current_key)
            if record is None:
                if tail_key is not None and current_key == tail_key:
                    break
                diagnostics.append(
                    AllegroRecordDiagnostic(
                        code="unresolved-reference",
                        message=f"linked list references missing object key {current_key}",
                        reference_key=current_key,
                    )
                )
                break

            records.append(record)
            if tail_key is not None and current_key == tail_key:
                break
            current_key = record.next_key or 0

        return AllegroLinkedListWalk(records=tuple(records), diagnostics=tuple(diagnostics))


def build_allegro_object_graph(record_set: AllegroRecordSet) -> AllegroObjectGraph:
    by_key: dict[int, AllegroRecord] = {}
    diagnostics: list[AllegroRecordDiagnostic] = []

    for record in record_set.records:
        if record.key is None:
            continue
        if record.key in by_key:
            diagnostics.append(
                AllegroRecordDiagnostic(
                    code="duplicate-object-key",
                    message=f"duplicate Allegro object key {record.key}",
                    offset=record.offset,
                    tag=record.tag,
                    key=record.key,
                )
            )
            continue
        by_key[record.key] = record

    return AllegroObjectGraph(
        records=record_set.records,
        by_key=MappingProxyType(by_key),
        diagnostics=tuple(diagnostics),
    )
