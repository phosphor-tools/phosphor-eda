"""Parsers for the OrCAD Capture Hierarchy stream.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

from __future__ import annotations

import struct
from collections import Counter
from typing import TYPE_CHECKING

from phosphor_eda.formats.common.diagnostics import warn_optional
from phosphor_eda.formats.dsn.binary_reader import (
    BinaryReader,
    decode_orcad_text,
    skip_structure,
)
from phosphor_eda.formats.dsn.raw_models import (
    DsnHierarchy,
    DsnHierarchyEntry,
    DsnHierarchyGlobalNet,
    DsnHierarchyNamedConnection,
    DsnHierarchyNet,
    DsnHierarchyOccurrence,
    DsnHierarchyPinOccurrence,
    NetIdMapping,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.formats.common.diagnostics import ParseContext

# Structure type ids used inside the Hierarchy stream (OpenOrCadParser names).
_STH1 = 0x42  # SthInHierarchy1 — an occurrence entry (and each entry's region)
_STH_NET = 0x43  # occurrence-scoped net record
_STH_SUB = 0x44  # sub-entry: pin occurrence (id>=0) or named connection (id<0)

# Deepest plausible Capture sheet nesting; a malformed stream that nests past
# this fails structured parsing (and falls back to the byte scan) instead of
# recursing toward Python's stack limit.
MAX_ENTRY_DEPTH = 64


def parse_hierarchy(data: bytes, ctx: ParseContext | None = None) -> list[NetIdMapping]:
    """Parse the Hierarchy stream for net-to-ID mappings.

    A malformed or truncated stream degrades to the mappings recovered so far
    with a ``dsn_hierarchy_mappings`` diagnostic. This runs before the loader's
    byte-scan occurrence fallback (:func:`parse_hierarchy_stream`), so a raw
    ``struct.error`` here would escape the caller's error boundary and abort the
    whole project load instead of falling back.
    """
    r = BinaryReader(data, "Hierarchy")
    mappings: list[NetIdMapping] = []
    try:
        r.skip(9)  # unknown_0
        r.read_string_len_zero()  # schematic_name
        r.skip(7)  # unknown_1

        # SthInHierarchy2 list
        num_sth2 = r.read_uint16()
        for _ in range(num_sth2):
            skip_structure(r)
            r.skip(4)  # trailing uint32
            r.read_string_len_zero()  # someName

        # Net DB ID Mappings - this is what we want
        num_mappings = r.read_uint16()
        for _ in range(num_mappings):
            skip_structure(r)  # StructNetDbIdMapping (empty payload)
            mapping = NetIdMapping()
            mapping.db_id = r.read_uint32()
            mapping.name = r.read_string_len_zero()
            mappings.append(mapping)
    except (struct.error, IndexError, ValueError) as exc:
        warn_optional(
            ctx,
            "dsn_hierarchy_mappings",
            f"Hierarchy: net-id mapping parse failed ({exc}); "
            f"recovered {len(mappings)} mapping(s) before the error",
        )

    return mappings


def merge_net_id_mappings(*mapping_groups: Iterable[NetIdMapping]) -> list[NetIdMapping]:
    """Merge hierarchy net mappings in stream order, keeping the first DB ID."""
    merged: list[NetIdMapping] = []
    seen_db_ids: set[int] = set()
    for mappings in mapping_groups:
        for mapping in mappings:
            if mapping.db_id in seen_db_ids:
                continue
            seen_db_ids.add(mapping.db_id)
            merged.append(mapping)
    return merged


def parse_hierarchy_occurrences(
    data: bytes,
    placed_instance_db_ids: Iterable[int],
) -> list[DsnHierarchyOccurrence]:
    """Parse raw hierarchy occurrence links to placed instance DB IDs.

    Capture hierarchy streams contain occurrence/object IDs that are distinct
    from the placed-instance DB IDs exposed by page records. In fixture-backed
    streams, the hierarchy occurrence ID is immediately followed by the page
    placed-instance DB ID and then a `0x42` structure marker. Preserve these
    links so CIS group rows can later resolve to schematic objects.

    This whole-stream byte scan is the version-tolerant fallback: the
    structured parser (:func:`parse_hierarchy_stream`) supersedes it on modern
    streams but delegates here when it cannot decode an old/variant layout.
    """
    instance_ids = {db_id for db_id in placed_instance_db_ids if db_id > 0}
    if not instance_ids:
        return []

    occurrences: list[DsnHierarchyOccurrence] = []
    seen: set[tuple[int, int]] = set()
    for offset in range(0, max(0, len(data) - 8)):
        if data[offset + 8] != 0x42:
            continue
        occurrence_id = struct.unpack_from("<I", data, offset)[0]
        instance_db_id = struct.unpack_from("<I", data, offset + 4)[0]
        key = (occurrence_id, instance_db_id)
        if (
            occurrence_id <= 0
            or occurrence_id == instance_db_id
            or instance_db_id not in instance_ids
            or key in seen
        ):
            continue
        seen.add(key)
        occurrences.append(
            DsnHierarchyOccurrence(
                occurrence_id=occurrence_id,
                instance_db_id=instance_db_id,
            )
        )
    return occurrences


def parse_hierarchy_stream(
    data: bytes,
    placed_instance_db_ids: Iterable[int],
    *,
    stream_path: str,
    ctx: ParseContext | None = None,
) -> DsnHierarchy:
    """Parse a Hierarchy stream into the structured occurrence tree.

    Modern (Capture 16/17) streams parse fully: root schematic name,
    design-global net occurrences, one entry per instance occurrence with its
    block->child-schematic edge, per-occurrence refdes, pin occurrences,
    named connections, and occurrence-scoped net tables.

    Old or variant streams whose layout this parser cannot decode fall back to
    the whole-stream byte scan; the returned :class:`DsnHierarchy` has
    ``fallback_used=True`` and carries only ``(occurrence_id, instance_db_id)``
    entry stubs, with a ``dsn_hierarchy_fallback`` diagnostic on *ctx*.
    """
    try:
        return _parse_hierarchy_structured(data, stream_path)
    except (struct.error, IndexError, ValueError, RecursionError) as exc:
        warn_optional(
            ctx,
            "dsn_hierarchy_fallback",
            f"{stream_path}: structured hierarchy parse failed ({exc}); using byte-scan fallback",
        )
        occurrences = parse_hierarchy_occurrences(data, placed_instance_db_ids)
        return DsnHierarchy(
            stream_path=stream_path,
            fallback_used=True,
            entries=[
                DsnHierarchyEntry(
                    occurrence_id=occurrence.occurrence_id,
                    instance_db_id=occurrence.instance_db_id,
                )
                for occurrence in occurrences
            ],
        )


def _parse_hierarchy_structured(data: bytes, stream_path: str) -> DsnHierarchy:
    r = BinaryReader(data, stream_path)

    r.skip(9)  # top-level 0x42 prefix header
    schematic_name = r.read_string_len_zero()
    r.skip(7)

    global_nets: list[DsnHierarchyGlobalNet] = []
    for _ in range(r.read_uint16()):
        skip_structure(r)
        net_id = r.read_int32()
        name = r.read_string_len_zero()
        global_nets.append(DsnHierarchyGlobalNet(net_id=net_id, name=name))

    # Net DB id mappings (consumed by parse_hierarchy for net_id_mappings).
    for _ in range(r.read_uint16()):
        skip_structure(r)
        r.read_uint32()
        r.read_string_len_zero()

    trailer_h3: list[tuple[int, int, int]] = []
    for _ in range(r.read_uint16()):
        type_id = skip_structure(r)
        trailer_h3.append((type_id, r.read_uint32(), r.read_uint32()))

    trailer_t5b: list[tuple[int, int, int]] = []
    t5b_count = r.read_uint32()
    if t5b_count > len(data):
        msg = f"implausible 0x5b trailer count {t5b_count} for stream of {len(data)} bytes"
        raise ValueError(msg)
    for _ in range(t5b_count):
        type_id = skip_structure(r)
        trailer_t5b.append((type_id, r.read_uint32(), r.read_uint32()))

    if r.at_preamble():
        r.try_read_preamble()

    declared_entry_count = r.read_uint16()

    entries: list[DsnHierarchyEntry] = []
    while r.remaining() > 2:
        _read_entry(r, entries, parent_index=None, depth=0)

    return DsnHierarchy(
        stream_path=stream_path,
        schematic_name=schematic_name,
        declared_entry_count=declared_entry_count,
        fallback_used=False,
        global_nets=global_nets,
        entries=entries,
        trailer_h3=trailer_h3,
        trailer_t5b=trailer_t5b,
    )


def _read_entry(
    r: BinaryReader,
    entries: list[DsnHierarchyEntry],
    *,
    parent_index: int | None,
    depth: int,
) -> None:
    if depth > MAX_ENTRY_DEPTH:
        msg = f"hierarchy entry nesting exceeds {MAX_ENTRY_DEPTH} levels at offset {r.pos}"
        raise ValueError(msg)
    type_id, end_offset, _pairs = r.read_prefix_chain()
    if type_id != _STH1:
        msg = f"expected hierarchy entry 0x42, got 0x{type_id:02x} at offset {r.pos}"
        raise ValueError(msg)
    if end_offset > 0:
        # A corrupt end offset at or before the cursor would rewind the
        # entry loops and stall without ever raising into the fallback.
        if end_offset < r.pos:
            msg = f"hierarchy entry end offset {end_offset} rewinds before offset {r.pos}"
            raise ValueError(msg)
        r.pos = end_offset
    else:
        r.try_read_preamble()

    occurrence_id = r.read_uint32()
    instance_db_id = r.read_uint32()

    region_start = r.pos
    marker = r.read_uint8()
    if marker != _STH1:
        msg = f"expected 0x42 region marker, got 0x{marker:02x} at offset {region_start}"
        raise ValueError(msg)
    region_off = r.read_uint32()
    r.read_uint32()  # padding
    region_end = region_start + 9 + region_off

    child_schematic = r.read_string_len_zero()
    refdes = r.read_string_len_zero()
    r.skip(4)  # unknown_2

    index = len(entries)
    entry = DsnHierarchyEntry(
        occurrence_id=occurrence_id,
        instance_db_id=instance_db_id,
        child_schematic=child_schematic,
        refdes=refdes,
        depth=depth,
        parent_index=parent_index,
    )
    entries.append(entry)

    # Sub-entries (0x44): a non-negative id is a pin occurrence (id, pin_index);
    # a negative id is a named port/global connection (id, name).
    for _ in range(r.read_uint16()):
        sub_type = skip_structure(r)
        if sub_type != _STH_SUB:
            msg = f"expected 0x44 hierarchy sub-entry, got 0x{sub_type:02x} at offset {r.pos}"
            raise ValueError(msg)
        sub_id = r.read_int32()
        value = r.read_uint16()
        if sub_id < 0:
            name = decode_orcad_text(r.read_bytes(value))
            r.skip(1)  # null terminator
            entry.named_connections.append(
                DsnHierarchyNamedConnection(connection_id=sub_id, name=name)
            )
        else:
            entry.pin_occurrences.append(
                DsnHierarchyPinOccurrence(occurrence_id=sub_id, pin_index=value)
            )

    if r.pos < region_end:
        for _ in range(r.read_uint16()):
            net_type = skip_structure(r)
            if net_type != _STH_NET:
                msg = f"expected 0x43 occurrence net, got 0x{net_type:02x} at offset {r.pos}"
                raise ValueError(msg)
            net_id = r.read_uint32()
            net_name = r.read_string_len_zero()
            entry.occurrence_nets.append(DsnHierarchyNet(net_id=net_id, name=net_name))
        if r.pos < region_end:
            for _ in range(r.read_uint16()):
                skip_structure(r)
                entry.trailer_ids.append((r.read_uint32(), r.read_uint32()))
        while r.pos < region_end:
            if r.data[r.pos] == _STH1:
                _read_entry(r, entries, parent_index=index, depth=depth + 1)
                continue
            rest = r.data[r.pos : region_end]
            if rest.count(0) == len(rest):
                r.pos = region_end
                break
            msg = f"unexpected hierarchy region content at offset {r.pos}"
            raise ValueError(msg)

    r.read_uint16()  # entry trailer


def hierarchy_occurrences_from_entries(
    hierarchy: DsnHierarchy,
    placed_instance_db_ids: Iterable[int],
) -> list[DsnHierarchyOccurrence]:
    """Derive placed-instance occurrence links from a structured hierarchy.

    Applies the same filter as :func:`parse_hierarchy_occurrences` so the
    structured tree can supply the occurrence->instance links downstream.
    """
    instance_ids = {db_id for db_id in placed_instance_db_ids if db_id > 0}
    occurrences: list[DsnHierarchyOccurrence] = []
    seen: set[tuple[int, int]] = set()
    for entry in hierarchy.entries:
        occurrence_id = entry.occurrence_id
        instance_db_id = entry.instance_db_id
        key = (occurrence_id, instance_db_id)
        if (
            occurrence_id <= 0
            or occurrence_id == instance_db_id
            or instance_db_id not in instance_ids
            or key in seen
        ):
            continue
        seen.add(key)
        occurrences.append(
            DsnHierarchyOccurrence(
                occurrence_id=occurrence_id,
                instance_db_id=instance_db_id,
            )
        )
    return occurrences


def build_occurrence_to_instance(
    occurrences: Iterable[DsnHierarchyOccurrence],
    ctx: ParseContext | None = None,
) -> dict[int, int]:
    """Map occurrence ids to instance db ids, dropping conflicting occurrences.

    An occurrence id that links to more than one instance db id is ambiguous;
    rather than silently taking the last write, warn and treat that occurrence
    as unresolved (omit it) so downstream CIS rows do not resolve to the wrong
    instance.
    """
    result: dict[int, int] = {}
    conflicts: set[int] = set()
    for occurrence in occurrences:
        occurrence_id = occurrence.occurrence_id
        instance_db_id = occurrence.instance_db_id
        if occurrence_id in conflicts:
            continue
        previous = result.get(occurrence_id)
        if previous is not None and previous != instance_db_id:
            warn_optional(
                ctx,
                "dsn_occurrence_conflict",
                f"hierarchy occurrence {occurrence_id} maps to multiple instances "
                f"({previous} and {instance_db_id}); treating as unresolved",
            )
            del result[occurrence_id]
            conflicts.add(occurrence_id)
            continue
        result[occurrence_id] = instance_db_id
    return result


def warn_repeated_sheet_blocks(
    hierarchies: Iterable[DsnHierarchy],
    ctx: ParseContext | None = None,
) -> None:
    """Record repeated-sheet multiplicity as informational metadata.

    A child schematic placed by more than one block (RFSoC ``DAC_ADC_CHANNEL``
    x8) shares net and component names across occurrences. The resolver now
    gives each occurrence its own scope, refdes, and net identity (finding H2),
    so this is no longer a flattening hazard — it is emitted so the multiplicity
    stays observable in the diagnostic stream.
    """
    child_counts: Counter[str] = Counter()
    for hierarchy in hierarchies:
        for entry in hierarchy.entries:
            if entry.child_schematic:
                child_counts[entry.child_schematic] += 1

    repeated = {child: count for child, count in child_counts.items() if count > 1}
    if not repeated:
        return

    detail = ", ".join(f"{child} x{count}" for child, count in sorted(repeated.items()))
    warn_optional(
        ctx,
        "dsn_repeated_sheet",
        f"repeated-sheet hierarchy: {detail}; each block occurrence is resolved "
        "with occurrence-scoped page, refdes, and net identity",
    )
