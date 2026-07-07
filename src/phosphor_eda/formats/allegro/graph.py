"""Object-key graph helpers for Allegro source records."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from phosphor_eda.formats.allegro.records import AllegroRecordDiagnostic

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

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
        self,
        *,
        head_key: int,
        tail_key: int | None = None,
        owner_key: int | None = None,
        expected_tags: frozenset[int] | None = None,
        next_key_of: Callable[[AllegroRecord], int] | None = None,
        guard: Callable[[AllegroRecord], str | None] | None = None,
    ) -> AllegroLinkedListWalk:
        """Walk a linked chain of records starting at ``head_key``.

        Allegro chains are circular: the last record links back to the owning
        record, so reaching ``owner_key`` is clean termination. Anomalies stop
        the walk with a diagnostic: a repeated key (``linked-list-cycle``), a
        key with no record (``unresolved-reference``), a record whose tag is
        not in ``expected_tags`` (``unexpected-record-tag``), or a record the
        caller's ``guard`` rejects (``chain-guard-rejected``). ``next_key_of``
        overrides how the successor key is read (payload-field chains);
        ``tail_key`` marks an explicit final record for non-ring chains.
        """
        if head_key == 0:
            return AllegroLinkedListWalk(records=(), diagnostics=())

        records: list[AllegroRecord] = []
        diagnostics: list[AllegroRecordDiagnostic] = []
        seen: set[int] = set()
        current_key = head_key

        while current_key != 0:
            if owner_key is not None and current_key == owner_key:
                break
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
            if expected_tags is not None and record.tag not in expected_tags:
                diagnostics.append(
                    AllegroRecordDiagnostic(
                        code="unexpected-record-tag",
                        message=(f"linked list reached 0x{record.tag:02X} record {current_key}"),
                        reference_key=current_key,
                    )
                )
                break
            if guard is not None and (reason := guard(record)) is not None:
                diagnostics.append(
                    AllegroRecordDiagnostic(
                        code="chain-guard-rejected",
                        message=f"linked list stopped at record {current_key}: {reason}",
                        reference_key=current_key,
                    )
                )
                break

            records.append(record)
            if tail_key is not None and current_key == tail_key:
                break
            current_key = next_key_of(record) if next_key_of is not None else record.next_key or 0

        return AllegroLinkedListWalk(records=tuple(records), diagnostics=tuple(diagnostics))


def build_allegro_object_graph(record_set: AllegroRecordSet) -> AllegroObjectGraph:
    diagnostics: list[AllegroRecordDiagnostic] = []
    seen: set[int] = set()

    for record in record_set.records:
        if record.key is None:
            continue
        if record.key in seen:
            diagnostics.append(
                AllegroRecordDiagnostic(
                    code="duplicate-object-key",
                    message=f"duplicate Allegro object key {record.key}",
                    offset=record.offset,
                    tag=record.tag,
                    key=record.key,
                )
            )
        else:
            seen.add(record.key)

    return AllegroObjectGraph(
        records=record_set.records,
        by_key=MappingProxyType(dict(record_set.by_key)),
        diagnostics=tuple(diagnostics),
    )
