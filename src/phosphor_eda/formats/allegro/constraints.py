"""Constraint Manager extraction for native Allegro board records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from phosphor_eda.domain.project import DesignRule, DiffPair, NetClass
from phosphor_eda.formats.allegro.constants import AllegroBoardUnits, AllegroVersion
from phosphor_eda.formats.allegro.records import (
    AllegroRecord,
    AllegroRecordDiagnostic,
    AllegroStringTable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.formats.allegro.records import AllegroRecordSet

_FIELD_LOGICAL_PATH = 0x37
_FIELD_PHYS_CONSTRAINT_SET = 0x1A0
_GROUP_SUBTYPE_DIFF_PAIR = 0x103
_PHYSICAL_TRACE_WIDTH_INDEX_PRE_V172 = 0
_PHYSICAL_CLEARANCE_INDEX_PRE_V172 = 1
_PHYSICAL_TRACE_WIDTH_INDEX = 1
_PHYSICAL_CLEARANCE_INDEX = 4
_PHYSICAL_DIFF_PAIR_GAP_INDEX = 7
_PHYSICAL_FIELD_COUNT = 14


@dataclass(frozen=True)
class AllegroConstraintExtraction:
    """Project-domain constraints decoded from Allegro source records."""

    net_classes: list[NetClass]
    design_rules: list[DesignRule]
    diff_pairs: list[DiffPair]
    diagnostics: tuple[AllegroRecordDiagnostic, ...]


@dataclass(frozen=True)
class _AllegroMatchGroup:
    name: str
    subtype: int
    members: tuple[str, ...]
    source_record_key: int | None


_PRE_V172 = {
    AllegroVersion.V_160,
    AllegroVersion.V_162,
    AllegroVersion.V_164,
    AllegroVersion.V_165,
    AllegroVersion.V_166,
}


def extract_allegro_constraints(record_set: AllegroRecordSet) -> AllegroConstraintExtraction:
    """Decode fixture-proven Allegro constraints into shared project domain objects."""
    header = record_set.header
    if header is None:
        return AllegroConstraintExtraction(
            net_classes=[],
            design_rules=[],
            diff_pairs=[],
            diagnostics=(),
        )

    diagnostics: list[AllegroRecordDiagnostic] = []
    net_classes: list[NetClass] = []
    design_rules: list[DesignRule] = []
    diff_pairs: list[DiffPair] = []
    class_by_raw_name_key: dict[int, NetClass] = {}
    class_by_name: dict[str, NetClass] = {}

    for index, record in enumerate(r for r in record_set.records if r.tag == 0x1D):
        fields = _first_data_b_fields(record)
        if fields is None:
            diagnostics.append(
                AllegroRecordDiagnostic(
                    code="allegro-constraint-set-empty",
                    message=f"constraint set record {record.key} has no DataB layer records",
                    offset=record.offset,
                    tag=record.tag,
                    key=record.key,
                )
            )
            continue

        name = _constraint_set_name(record, record_set.by_key, record_set.string_table, index)
        values = _physical_values_mm(
            fields,
            version=header.version,
            units=header.board_units,
            unit_divisor=header.unit_divisor,
        )
        if values is None:
            diagnostics.append(
                AllegroRecordDiagnostic(
                    code="allegro-constraint-set-short-row",
                    message=(
                        f"constraint set record {record.key} has {len(fields)} physical fields; "
                        f"expected at least {_PHYSICAL_FIELD_COUNT}"
                    ),
                    offset=record.offset,
                    tag=record.tag,
                    key=record.key,
                )
            )
            continue
        trace_width_mm, clearance_mm, diff_pair_gap_mm = values
        properties = {
            "source_format": "allegro",
            "source_record_tag": "0x1D",
            "allegro_constraint_kind": "physical_constraint_set",
        }
        if record.key is not None:
            properties["source_record_key"] = str(record.key)

        net_class = NetClass(
            name=name,
            trace_width_mm=trace_width_mm,
            clearance_mm=clearance_mm,
            diff_pair_width_mm=0.0,
            diff_pair_gap_mm=diff_pair_gap_mm,
            properties=dict(properties),
        )
        net_classes.append(net_class)
        class_by_name[name] = net_class
        raw_name_key = _payload_int(record, "name_string_key")
        if raw_name_key:
            class_by_raw_name_key[raw_name_key] = net_class
        design_rules.append(
            DesignRule(
                name=f"Allegro physical constraint set {name}",
                kind="allegro_physical_constraint_set",
                enabled=True,
                min_value_mm=clearance_mm,
                preferred_value_mm=trace_width_mm,
                properties=dict(properties),
            )
        )

    _assign_net_class_members(record_set, class_by_name, class_by_raw_name_key)
    _append_match_groups(record_set, net_classes, diff_pairs, diagnostics)

    return AllegroConstraintExtraction(
        net_classes=net_classes,
        design_rules=design_rules,
        diff_pairs=diff_pairs,
        diagnostics=tuple(diagnostics),
    )


def _assign_net_class_members(
    record_set: AllegroRecordSet,
    class_by_name: dict[str, NetClass],
    class_by_raw_name_key: dict[int, NetClass],
) -> None:
    default_class = _case_insensitive_lookup(class_by_name, "DEFAULT")
    for net_record in (record for record in record_set.records if record.tag == 0x1B):
        net_name = _net_name(net_record, record_set.by_key, record_set.string_table)
        if not net_name:
            continue
        assigned_class = _explicit_constraint_class(
            net_record,
            record_set.by_key,
            class_by_name,
            class_by_raw_name_key,
        )
        if assigned_class is None:
            assigned_class = default_class
        if assigned_class is not None and net_name not in assigned_class.members:
            assigned_class.members.append(net_name)


def _append_match_groups(
    record_set: AllegroRecordSet,
    net_classes: list[NetClass],
    diff_pairs: list[DiffPair],
    diagnostics: list[AllegroRecordDiagnostic],
) -> None:
    grouped_net_names: dict[int, list[str]] = {}
    for net_record in (record for record in record_set.records if record.tag == 0x1B):
        net_name = _net_name(net_record, record_set.by_key, record_set.string_table)
        if not net_name:
            continue
        group_key = _match_group_key(net_record, record_set.by_key)
        if group_key == 0:
            continue
        grouped_net_names.setdefault(group_key, []).append(net_name)

    for group_key, members in grouped_net_names.items():
        group = _match_group(
            group_key,
            members=tuple(members),
            records_by_key=record_set.by_key,
            string_table=record_set.string_table,
        )
        if group is None:
            continue
        if group.subtype == _GROUP_SUBTYPE_DIFF_PAIR:
            polarity = _diff_pair_order(group.members)
            if polarity is None:
                diagnostics.append(
                    AllegroRecordDiagnostic(
                        code="allegro-diff-pair-polarity-unknown",
                        message=(
                            f"diff-pair group {group.name} does not expose deterministic "
                            "positive/negative net polarity"
                        ),
                        key=group.source_record_key,
                    )
                )
            else:
                positive_net, negative_net = polarity
                diff_pairs.append(
                    DiffPair(
                        name=group.name,
                        positive_net=positive_net,
                        negative_net=negative_net,
                        properties={
                            "source_format": "allegro",
                            "allegro_constraint_kind": "diff_pair",
                            "allegro_group_name": group.name,
                        },
                    )
                )
                class_name = f"DP_{group.name}"
                kind = "diff_pair"
                net_classes.append(
                    NetClass(
                        name=class_name,
                        members=list(group.members),
                        properties={
                            "source_format": "allegro",
                            "allegro_constraint_kind": kind,
                            "allegro_group_name": group.name,
                        },
                    )
                )
                continue

        class_name = f"MG_{group.name}"
        net_classes.append(
            NetClass(
                name=class_name,
                members=list(group.members),
                properties={
                    "source_format": "allegro",
                    "allegro_constraint_kind": "match_group",
                    "allegro_group_name": group.name,
                },
            )
        )


def _match_group(
    group_key: int,
    *,
    members: tuple[str, ...],
    records_by_key: Mapping[int, AllegroRecord],
    string_table: AllegroStringTable | None,
) -> _AllegroMatchGroup | None:
    group_record = records_by_key.get(group_key)
    if group_record is None or group_record.tag != 0x2C:
        return None
    name = _constraint_table_name(group_record, string_table)
    if not name:
        return None
    return _AllegroMatchGroup(
        name=name,
        subtype=_payload_int(group_record, "subtype"),
        members=members,
        source_record_key=group_record.key,
    )


def _match_group_key(
    net_record: AllegroRecord,
    records_by_key: Mapping[int, AllegroRecord],
) -> int:
    current_key = _payload_int(net_record, "match_group_key")
    seen: set[int] = set()
    while current_key:
        if current_key in seen:
            return 0
        seen.add(current_key)
        record = records_by_key.get(current_key)
        if record is None:
            return 0
        if record.tag == 0x26:
            current_key = _payload_int(record, "group_key")
            continue
        if record.tag == 0x2C:
            return current_key
        return 0
    return 0


def _constraint_table_name(
    record: AllegroRecord,
    string_table: AllegroStringTable | None,
) -> str:
    string_key = _payload_int(record, "string_key")
    if string_key == 0 or string_table is None:
        return ""
    return string_table.by_id.get(string_key, "")


def _diff_pair_order(members: tuple[str, ...]) -> tuple[str, str] | None:
    if len(members) != 2:
        return None
    first, second = members
    first_polarity = _diff_pair_polarity(first)
    second_polarity = _diff_pair_polarity(second)
    if first_polarity == "positive" and second_polarity == "negative":
        return first, second
    if first_polarity == "negative" and second_polarity == "positive":
        return second, first
    return None


def _diff_pair_polarity(net_name: str) -> str:
    net_name_lower = net_name.lower()
    if net_name_lower.endswith(("_p", "_plus", "+", "-p")):
        return "positive"
    if net_name_lower.endswith(("_n", "_minus", "-", "-n")):
        return "negative"
    return ""


def _explicit_constraint_class(
    net_record: AllegroRecord,
    records_by_key: Mapping[int, AllegroRecord],
    class_by_name: dict[str, NetClass],
    class_by_raw_name_key: dict[int, NetClass],
) -> NetClass | None:
    field = _first_field(net_record, records_by_key, _FIELD_PHYS_CONSTRAINT_SET)
    if field is None:
        return None
    value = field.payload.get("value")
    if isinstance(value, int):
        return class_by_raw_name_key.get(value)
    if isinstance(value, str):
        return _case_insensitive_lookup(class_by_name, _constraint_name_from_field_value(value))
    return None


def _net_name(
    record: AllegroRecord,
    records_by_key: Mapping[int, AllegroRecord],
    string_table: AllegroStringTable | None,
) -> str:
    name_key = _payload_int(record, "net_name_key")
    if string_table is not None:
        resolved = string_table.by_id.get(name_key, "")
        if resolved:
            return resolved
    field = _first_field(record, records_by_key, _FIELD_LOGICAL_PATH)
    if field is None:
        return ""
    value = field.payload.get("value")
    if isinstance(value, str):
        return _constraint_name_from_field_value(value)
    return ""


def _first_field(
    owner_record: AllegroRecord,
    records_by_key: Mapping[int, AllegroRecord],
    field_key: int,
) -> AllegroRecord | None:
    current_key = _payload_int(owner_record, "fields_key")
    seen: set[int] = set()
    owner_key = owner_record.key
    while current_key and current_key != owner_key:
        if current_key in seen:
            return None
        seen.add(current_key)
        field = records_by_key.get(current_key)
        if field is None or field.tag != 0x03:
            return None
        if _payload_int(field, "field_key") == field_key:
            return field
        current_key = field.next_key or 0
    return None


def _case_insensitive_lookup(mapping: dict[str, NetClass], name: str) -> NetClass | None:
    for key, value in mapping.items():
        if key.lower() == name.lower():
            return value
    return None


def _constraint_set_name(
    record: AllegroRecord,
    records_by_key: Mapping[int, AllegroRecord],
    string_table: AllegroStringTable | None,
    index: int,
) -> str:
    name_key = _payload_int(record, "name_string_key")
    if string_table is not None:
        resolved = string_table.by_id.get(name_key, "")
        if resolved:
            return resolved

    field_key = _payload_int(record, "field_key")
    field = records_by_key.get(field_key)
    if field is not None:
        field_value = field.payload.get("value")
        if isinstance(field_value, str):
            resolved = _constraint_name_from_field_value(field_value)
            if resolved:
                return resolved

    return f"CS_{index}"


def _constraint_name_from_field_value(value: str) -> str:
    stripped = value.rstrip("\x00")
    marker = ":\\"
    if marker in stripped:
        stripped = stripped.rsplit(marker, 1)[1]
    elif ":" in stripped:
        stripped = stripped.rsplit(":", 1)[1]
    return stripped.strip("\\")


def _first_data_b_fields(record: AllegroRecord) -> tuple[int, ...] | None:
    value = record.payload.get("data_b_fields")
    if not _is_object_tuple(value) or not value:
        return None
    first_row = value[0]
    if not _is_object_tuple(first_row):
        return None
    fields: list[int] = []
    for item in first_row:
        if not isinstance(item, int):
            return None
        fields.append(item)
    return tuple(fields)


def _is_object_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    return isinstance(value, tuple)


def _physical_values_mm(
    fields: tuple[int, ...],
    *,
    version: AllegroVersion,
    units: AllegroBoardUnits,
    unit_divisor: int,
) -> tuple[float, float, float] | None:
    if len(fields) < _PHYSICAL_FIELD_COUNT:
        return None

    if version in _PRE_V172:
        trace_width_raw = fields[_PHYSICAL_TRACE_WIDTH_INDEX_PRE_V172]
        clearance_raw = fields[_PHYSICAL_CLEARANCE_INDEX_PRE_V172]
    else:
        trace_width_raw = fields[_PHYSICAL_TRACE_WIDTH_INDEX]
        clearance_raw = fields[_PHYSICAL_CLEARANCE_INDEX]
    diff_pair_gap_raw = fields[_PHYSICAL_DIFF_PAIR_GAP_INDEX]

    return (
        _coord_to_mm(trace_width_raw, units=units, unit_divisor=unit_divisor),
        _coord_to_mm(clearance_raw, units=units, unit_divisor=unit_divisor),
        _coord_to_mm(diff_pair_gap_raw, units=units, unit_divisor=unit_divisor),
    )


def _coord_to_mm(value: int, *, units: AllegroBoardUnits, unit_divisor: int) -> float:
    if unit_divisor <= 0:
        return 0.0
    if units is AllegroBoardUnits.MILS:
        return round(value / unit_divisor * 0.0254, 9)
    if units is AllegroBoardUnits.INCHES:
        return round(value / unit_divisor * 25.4, 9)
    if units is AllegroBoardUnits.MILLIMETERS:
        return round(value / unit_divisor, 9)
    if units is AllegroBoardUnits.CENTIMETERS:
        return round(value / unit_divisor * 10.0, 9)
    if units is AllegroBoardUnits.MICROMETERS:
        return round(value / unit_divisor / 1000.0, 9)
    raise ValueError(f"unsupported Allegro board unit {units}")


def _payload_int(record: AllegroRecord, key: str) -> int:
    value = record.payload.get(key)
    return value if isinstance(value, int) else 0
