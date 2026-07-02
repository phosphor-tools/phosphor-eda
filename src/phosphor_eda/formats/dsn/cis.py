"""Raw parser for OrCAD Capture CIS VariantStore streams."""

from __future__ import annotations

import struct
from collections import Counter
from typing import TYPE_CHECKING

from phosphor_eda.formats.common.diagnostics import warn_optional
from phosphor_eda.formats.dsn.binary_reader import BinaryReader
from phosphor_eda.formats.dsn.raw_models import (
    DsnCisBom,
    DsnCisBomEntry,
    DsnCisGroup,
    DsnCisGroupMember,
    DsnCisRawStream,
    DsnCisStringList,
    DsnCisUpdateStorageRow,
    DsnCisVariantName,
    DsnCisVariantStore,
    DsnResolutionKind,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.formats.common.diagnostics import ParseContext

_PLACEHOLDER_STREAM_SIZES = {
    "CIS/VariantStore/BOM/BOMDataStream": 5,
    "CIS/VariantStore/Groups/GroupsDataStream": 4,
    "CIS/VariantStore/VariantNames": 11,
}


def parse_cis_variant_store(
    stream_data_by_path: Mapping[str, bytes],
    storage_paths: set[str],
    occurrence_to_instance: Mapping[int, int],
    ctx: ParseContext | None = None,
) -> DsnCisVariantStore:
    """Parse raw OrCAD CIS VariantStore evidence.

    Placeholder stores are Capture-created empty containers. Preserve presence
    and placeholder semantics without reporting names, BOMs, groups, or public
    variants.
    """
    cis_streams = {
        path: data
        for path, data in stream_data_by_path.items()
        if path.startswith("CIS/VariantStore/")
    }
    present = "CIS/VariantStore" in storage_paths or bool(cis_streams)
    store = DsnCisVariantStore(present=present)
    if not present:
        return store

    sizes = {path: len(data) for path, data in cis_streams.items()}
    if sizes == _PLACEHOLDER_STREAM_SIZES:
        store.placeholder = True
        return store

    variant_names_data = cis_streams.get("CIS/VariantStore/VariantNames")
    if variant_names_data is not None:
        store.variant_names = _parse_variant_names(
            variant_names_data,
            "CIS/VariantStore/VariantNames",
            store,
            ctx,
        )
    store.boms = _parse_boms(cis_streams, occurrence_to_instance, store, ctx)
    store.groups = _parse_groups(cis_streams, occurrence_to_instance, store, ctx)
    _record_unknown_streams(cis_streams, store)
    return store


def _parse_groups(
    cis_streams: Mapping[str, bytes],
    occurrence_to_instance: Mapping[int, int],
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> list[DsnCisGroup]:
    groups_stream_path = "CIS/VariantStore/Groups/GroupsDataStream"
    data = cis_streams.get(groups_stream_path)
    if data is None:
        return []

    groups: list[DsnCisGroup] = []
    group_fields = _parse_groups_data_stream(data, groups_stream_path, store, ctx)
    for row_order, fields in enumerate(group_fields):
        if len(fields) < 2:
            continue
        name = fields[0]
        group = DsnCisGroup(
            name=name,
            stream_path=groups_stream_path,
            row_order=row_order,
            raw_fields=fields,
        )
        member_path = f"CIS/VariantStore/Groups/{name}/{name}"
        member_data = cis_streams.get(member_path)
        if member_data is not None:
            group.members = _parse_group_members(
                member_data,
                member_path,
                occurrence_to_instance,
                store,
                ctx,
            )
        else:
            _warn(store, ctx, f"{groups_stream_path}: group {name!r} has no member stream")
        update_storage_path = f"CIS/VariantStore/Groups/{name}/UpdateStorageGroupDataStream"
        update_storage_data = cis_streams.get(update_storage_path)
        if update_storage_data is not None:
            group.update_storage_rows = _parse_update_storage_rows(
                update_storage_data,
                update_storage_path,
                occurrence_to_instance,
                store,
                ctx,
            )
        groups.append(group)
    return groups


def _parse_groups_data_stream(
    data: bytes,
    stream_path: str,
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> list[list[str]]:
    payload = _payload_after_optional_size(data, stream_path, store, ctx)
    fields = [_decode_cis_text(field) for field in payload.split(b"\xb0")]
    fields = [field for field in fields if field]
    rows: list[list[str]] = []
    for index in range(0, len(fields), 2):
        row = fields[index : index + 2]
        if row:
            rows.append(row)
    return rows


def _parse_group_members(
    data: bytes,
    stream_path: str,
    occurrence_to_instance: Mapping[int, int],
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> list[DsnCisGroupMember]:
    payload = _payload_after_optional_size(data, stream_path, store, ctx)
    members: list[DsnCisGroupMember] = []
    for row_order, row in enumerate(payload.split(b"~")):
        fields = [_decode_cis_text(field) for field in row.split(b"\xb0")]
        fields = [field for field in fields if field]
        if len(fields) < 2:
            continue
        occurrence_id = _parse_decimal(fields[1])
        if occurrence_id is None:
            message = f"{stream_path}: group row {row_order} has non-numeric ID {fields[1]!r}"
            _warn(store, ctx, message)
            members.append(
                DsnCisGroupMember(
                    stream_path=stream_path,
                    row_order=row_order,
                    state=fields[0],
                    diagnostics=[message],
                )
            )
            continue
        instance_db_id = occurrence_to_instance.get(occurrence_id)
        diagnostics: list[str] = []
        resolution_kind: DsnResolutionKind = "hierarchy_occurrence"
        if instance_db_id is None:
            resolution_kind = "unresolved"
            diagnostics.append("group member ID did not resolve through hierarchy occurrences")
        members.append(
            DsnCisGroupMember(
                stream_path=stream_path,
                row_order=row_order,
                state=fields[0],
                occurrence_id=occurrence_id,
                resolved_instance_db_id=instance_db_id,
                resolution_kind=resolution_kind,
                diagnostics=diagnostics,
            )
        )
    return members


def _parse_update_storage_rows(
    data: bytes,
    stream_path: str,
    occurrence_to_instance: Mapping[int, int],
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> list[DsnCisUpdateStorageRow]:
    payload = _payload_after_required_size(data, stream_path, store, ctx)
    if payload is None:
        return []

    rows: list[DsnCisUpdateStorageRow] = []
    for row_order, row in enumerate(payload.split(b"~")):
        if not row:
            continue
        member_and_rest = row.split(b"\xb0", 1)
        if len(member_and_rest) != 2:
            message = f"{stream_path}: update-storage row {row_order} is missing columns"
            _warn(store, ctx, message)
            rows.append(
                DsnCisUpdateStorageRow(
                    stream_path=stream_path,
                    row_order=row_order,
                    diagnostics=[message],
                )
            )
            continue
        occurrence_value = _decode_cis_text(member_and_rest[0])
        occurrence_id = _parse_decimal(occurrence_value)
        columns_and_values = member_and_rest[1].split(b"\xc0", 1)
        columns = _split_caret_list(columns_and_values[0])
        values = _split_caret_list(columns_and_values[1]) if len(columns_and_values) == 2 else []
        diagnostics: list[str] = []
        instance_db_id: int | None = None
        resolution_kind: DsnResolutionKind = "unresolved"
        if occurrence_id is None:
            diagnostics.append(f"update-storage row has non-numeric ID {occurrence_value!r}")
        else:
            instance_db_id = occurrence_to_instance.get(occurrence_id)
            if instance_db_id is None:
                diagnostics.append(
                    "update-storage ID did not resolve through hierarchy occurrences"
                )
            else:
                resolution_kind = "hierarchy_occurrence"
        if len(columns) != len(values):
            diagnostics.append(
                f"update-storage row has {len(columns)} columns and {len(values)} values"
            )
        rows.append(
            DsnCisUpdateStorageRow(
                stream_path=stream_path,
                row_order=row_order,
                occurrence_id=occurrence_id,
                resolved_instance_db_id=instance_db_id,
                resolution_kind=resolution_kind,
                columns=columns,
                values=values,
                diagnostics=diagnostics,
            )
        )
    return rows


def _record_unknown_streams(
    cis_streams: Mapping[str, bytes],
    store: DsnCisVariantStore,
) -> None:
    known_paths = {
        "CIS/VariantStore/VariantNames",
        "CIS/VariantStore/BOM/BOMDataStream",
        "CIS/VariantStore/Groups/GroupsDataStream",
    }
    for bom in store.boms:
        child_prefix = f"CIS/VariantStore/BOM/{bom.name}/"
        known_paths.add(f"{child_prefix}{bom.name}")
        known_paths.add(f"{child_prefix}BOMPartData")
    for group in store.groups:
        known_paths.add(f"CIS/VariantStore/Groups/{group.name}/{group.name}")
        known_paths.add(f"CIS/VariantStore/Groups/{group.name}/UpdateStorageGroupDataStream")

    existing_unknown_paths = {stream.stream_path for stream in store.unknown_streams}
    for path, data in sorted(cis_streams.items()):
        if path in known_paths or path in existing_unknown_paths:
            continue
        store.unknown_streams.append(
            DsnCisRawStream(
                stream_path=path,
                size=len(data),
                reason="unsupported CIS VariantStore stream",
            )
        )


def _parse_boms(
    cis_streams: Mapping[str, bytes],
    occurrence_to_instance: Mapping[int, int],
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> list[DsnCisBom]:
    bom_stream_path = "CIS/VariantStore/BOM/BOMDataStream"
    data = cis_streams.get(bom_stream_path)
    if data is None:
        return []
    raw_fields = _parse_cis_string_list(data, bom_stream_path, store, ctx)
    if len(raw_fields) < 2:
        return []

    # BOMDataStream field list is stream-level evidence: record it once on the
    # store rather than copying the same list onto every BOM below.
    store.bom_raw_fields = raw_fields
    declared_count = _parse_decimal(raw_fields[0])
    bom_names = raw_fields[1:]
    if declared_count is not None and declared_count != len(bom_names):
        _warn(
            store,
            ctx,
            f"{bom_stream_path}: declared {declared_count} BOM names but parsed {len(bom_names)}",
        )

    boms: list[DsnCisBom] = []
    for bom_name in bom_names:
        bom = DsnCisBom(
            name=bom_name,
            stream_path=bom_stream_path,
        )

        child_prefix = f"CIS/VariantStore/BOM/{bom_name}/"
        child_paths = sorted(path for path in cis_streams if path.startswith(child_prefix))
        if not child_paths:
            _warn(store, ctx, f"{bom_stream_path}: BOM {bom_name!r} has no child streams")
        for path in child_paths:
            child_data = cis_streams[path]
            child_name = path.removeprefix(child_prefix)
            if child_name == bom_name:
                bom.child_string_lists.append(
                    DsnCisStringList(
                        stream_path=path,
                        values=_parse_cis_string_list(child_data, path, store, ctx),
                    )
                )
            elif child_name == "BOMPartData":
                bom.entries = _parse_bom_part_data(
                    child_data,
                    path,
                    occurrence_to_instance,
                    store,
                    ctx,
                )
            else:
                store.unknown_streams.append(
                    DsnCisRawStream(
                        stream_path=path,
                        size=len(child_data),
                        reason="unsupported BOM child",
                    )
                )
        boms.append(bom)
    return boms


def _parse_bom_part_data(
    data: bytes,
    stream_path: str,
    occurrence_to_instance: Mapping[int, int],
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> list[DsnCisBomEntry]:
    values = _parse_cis_string_list(data, stream_path, store, ctx)
    if not values:
        return []
    declared_count = _parse_decimal(values[0])
    raw_ids = values[1:]
    if declared_count is not None and declared_count != len(raw_ids):
        _warn(
            store,
            ctx,
            f"{stream_path}: declared {declared_count} BOMPartData IDs but parsed {len(raw_ids)}",
        )

    entries: list[DsnCisBomEntry] = []
    for row_order, raw_value in enumerate(raw_ids):
        raw_id = _parse_decimal(raw_value)
        if raw_id is None:
            message = f"{stream_path}: BOMPartData row {row_order} has non-numeric ID {raw_value!r}"
            entry = DsnCisBomEntry(
                stream_path=stream_path,
                row_order=row_order,
                diagnostics=[message],
            )
            _warn(store, ctx, message)
            entries.append(entry)
            continue
        instance_db_id = occurrence_to_instance.get(raw_id)
        diagnostics: list[str] = []
        resolution_kind: DsnResolutionKind = "hierarchy_occurrence"
        if instance_db_id is None:
            resolution_kind = "unresolved"
            diagnostics.append("BOMPartData ID did not resolve through hierarchy occurrences")
        entries.append(
            DsnCisBomEntry(
                stream_path=stream_path,
                row_order=row_order,
                raw_id=raw_id,
                resolved_instance_db_id=instance_db_id,
                resolution_kind=resolution_kind,
                diagnostics=diagnostics,
            )
        )
    return entries


def _parse_variant_names(
    data: bytes,
    stream_path: str,
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> list[DsnCisVariantName]:
    if len(data) < 8:
        _warn(store, ctx, f"{stream_path}: VariantNames stream is too short")
        return []

    r = BinaryReader(data, stream_path)
    _version = r.read_uint32()
    declared_count = r.read_uint32()
    names: list[DsnCisVariantName] = []
    seen: Counter[str] = Counter()
    while r.pos < len(data):
        if r.pos + 2 > len(data):
            _warn(store, ctx, f"{stream_path}: truncated VariantNames length at byte {r.pos}")
            break
        string_len = struct.unpack_from("<H", data, r.pos)[0]
        value_end = r.pos + 2 + string_len
        if value_end > len(data):
            _warn(store, ctx, f"{stream_path}: truncated VariantNames value at byte {r.pos + 2}")
            break
        name = r.read_string_len_zero(encoding="latin1")
        # read_string_len_zero consumes the null terminator when present; if it
        # did not advance past value_end there was no terminator to consume.
        if r.pos == value_end and r.pos < len(data):
            _warn(store, ctx, f"{stream_path}: missing null terminator after {name!r}")
        duplicate_index = seen[name]
        seen[name] += 1
        names.append(
            DsnCisVariantName(
                stream_path=stream_path,
                order=len(names),
                duplicate_index=duplicate_index,
                name=name,
            )
        )

    if declared_count != len(names):
        _warn(
            store,
            ctx,
            f"{stream_path}: declared {declared_count} variant names but parsed {len(names)}",
        )
    return names


def _parse_cis_string_list(
    data: bytes,
    stream_path: str,
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> list[str]:
    payload = _payload_after_required_size(data, stream_path, store, ctx)
    if payload is None:
        return []
    if not payload:
        return []
    values: list[str] = []
    for raw_value in payload.split(b"\xf9"):
        value = _decode_cis_text(raw_value)
        if value:
            values.append(value)
    return values


def _decode_cis_text(value: bytes) -> str:
    return value.strip(b"\x00").split(b"\x00", 1)[0].decode("latin1").strip()


def _split_caret_list(data: bytes) -> list[str]:
    return [_decode_cis_text(value) for value in data.split(b"^")]


def _payload_after_required_size(
    data: bytes,
    stream_path: str,
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> bytes | None:
    if len(data) < 4:
        _warn(store, ctx, f"{stream_path}: stream is too short for size prefix")
        return None
    return _payload_after_size_prefix(data, stream_path, store, ctx)


def _payload_after_optional_size(
    data: bytes,
    stream_path: str,
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> bytes:
    if len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == len(data) - 4:
        return _payload_after_size_prefix(data, stream_path, store, ctx)
    return data


def _payload_after_size_prefix(
    data: bytes,
    stream_path: str,
    store: DsnCisVariantStore,
    ctx: ParseContext | None,
) -> bytes:
    expected_len = struct.unpack_from("<I", data, 0)[0]
    payload = data[4:]
    if expected_len != len(payload):
        _warn(
            store,
            ctx,
            f"{stream_path}: declared {expected_len} payload bytes but found {len(payload)}",
        )
    return payload


def _parse_decimal(value: str) -> int | None:
    if not value.isdecimal():
        return None
    return int(value)


def _warn(store: DsnCisVariantStore, ctx: ParseContext | None, message: str) -> None:
    store.diagnostics.append(message)
    warn_optional(ctx, "dsn_cis", message)
