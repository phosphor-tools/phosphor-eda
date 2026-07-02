"""Parsers for the OrCAD Capture Hierarchy stream.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from phosphor_eda.formats.dsn.binary_reader import BinaryReader, skip_structure
from phosphor_eda.formats.dsn.raw_models import DsnHierarchyOccurrence, NetIdMapping

if TYPE_CHECKING:
    from collections.abc import Iterable


def parse_hierarchy(data: bytes) -> list[NetIdMapping]:
    """Parse the Hierarchy stream for net-to-ID mappings."""
    r = BinaryReader(data, "Hierarchy")
    mappings: list[NetIdMapping] = []

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
