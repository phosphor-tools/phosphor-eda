"""Map fixture-proven OrCAD CIS VariantStore evidence to public variants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import FootprintModel, Parameter, PartNumber
from phosphor_eda.domain.variants import (
    Variant,
    VariantField,
    VariantOverride,
    VariantTarget,
    VariantTargetKind,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from phosphor_eda.domain.schematic import Component, Schematic
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.dsn.raw_models import (
        DsnCisGroup,
        DsnCisGroupMember,
        DsnCisUpdateStorageRow,
        DsnCisVariantStore,
        ParsedDesign,
    )


_NOT_FITTED_TOKENS = (
    "DNP",
    "DNI",
    "DNM",
    "NI",
    "DO NOT PLACE",
    "NOT INSTALLED",
)


def _token_pattern(token: str) -> str:
    return re.escape(token).replace(r"\ ", r"\s+")


_TOKEN_PATTERNS = tuple(
    re.compile(rf"(?<![A-Z0-9_]){_token_pattern(token)}(?![A-Z0-9_])")
    for token in _NOT_FITTED_TOKENS
)
_RAW_FIELD_SEPARATOR = "\x1f"

# CIS update-storage sentinel for "no value recorded" — never a real override.
_UPDATE_STORAGE_UNDEFINED = "UNDEFINED"
# update-storage columns mapped to a public PARAMETER of the same name. The
# remaining columns are preserved verbatim in override metadata: Part Number and
# PCB Footprint get typed PART_NUMBERS/FOOTPRINTS change overrides when they
# differ from base; Source Library/Package/Graphic (LIB) and
# Name/Schematic Part/Implementation/Power Pins Visible stay metadata-only
# because their base identity is not cleanly comparable.
_UPDATE_STORAGE_PARAMETER_COLUMNS = ("Description", "Tolerance", "Power", "Part type")


@dataclass(frozen=True)
class _VariantEvidence:
    group_name: str
    group_stream_path: str
    group_raw_fields: str
    evidence: tuple[str, ...]


def map_orcad_cis_not_fitted_variants(
    raw: ParsedDesign, schematic: Schematic | None, ctx: ParseContext | None = None
) -> list[Variant]:
    """Create public OrCAD DNP-style variants from raw CIS groups.

    Group names with whole-token not-fitted vocabulary become DNP variants when
    the raw store has independent evidence (BOM child membership or per-component
    properties). Each not-fitted member's CIS update-storage row (a database
    property snapshot, not a substitution) is attached to the same variant as
    typed override evidence; snapshot values equal to recoverable base values
    are carried as metadata rather than change-claiming overrides, and OrCAD
    never emits an ``ALTERNATE_PART`` override (findings V1/V2, item G4).
    """
    if schematic is None:
        return []
    store = raw.cis_variant_store
    if not store.present or store.placeholder:
        return []

    components_by_instance_id = _components_by_instance_id(schematic.components, ctx)
    variants: list[Variant] = []
    for group in store.groups:
        if not _matches_not_fitted_token(group.name):
            continue
        group_level_evidence = _group_level_evidence(store, group)
        overrides = _group_not_fitted_overrides(
            group,
            components_by_instance_id,
            group_level_evidence=group_level_evidence,
            ctx=ctx,
        )
        if not overrides:
            if ctx is not None:
                ctx.warn(
                    "dsn_cis_variant",
                    f"not-fitted group {group.name!r} produced no overrides passing the "
                    "evidence gate; variant dropped",
                )
            continue
        overrides.extend(_group_update_storage_overrides(group, components_by_instance_id, ctx))
        evidence = _VariantEvidence(
            group_name=group.name,
            group_stream_path=group.stream_path,
            group_raw_fields=_raw_fields(group.raw_fields),
            evidence=_aggregate_override_evidence(overrides),
        )
        variants.append(
            Variant(
                name=group.name,
                order=len(variants) + 1,
                overrides=overrides,
                source_id=f"CIS/VariantStore/Groups/{group.name}",
                metadata=_variant_metadata(evidence),
            )
        )
    return variants


def _components_by_instance_id(
    components: Iterable[Component], ctx: ParseContext | None = None
) -> dict[int, Component]:
    """Index components by their typed placed-instance db id.

    Identity comes from the ``dsn_component_db_id`` metadata key, which
    ``to_schematic`` sets only for a real persistent db id (>0). Parsing the id
    back out of the ``dsn_component_source_ids`` display string is what let a
    ``db_id == 0`` instance (source id built from the instance index) collide
    with a real ``db_id == index`` component and silently win last-write (R5).

    A repeated hierarchical sheet places the same page-level instance db id once
    per occurrence, so one db id can map to several placed components that differ
    only by their per-occurrence refdes. That is genuinely ambiguous for
    db-id-keyed CIS matching: taking the last write would target the wrong
    occurrence. Flag the collision and drop the db id so no override silently
    resolves to the wrong component, mirroring ``build_occurrence_to_instance``.
    """
    result: dict[int, Component] = {}
    ambiguous: set[int] = set()
    for component in components:
        raw_db_id = component.metadata.get("dsn_component_db_id")
        if raw_db_id is None:
            continue
        try:
            instance_db_id = int(raw_db_id)
        except ValueError:
            continue
        if instance_db_id in result or instance_db_id in ambiguous:
            ambiguous.add(instance_db_id)
            result.pop(instance_db_id, None)
            continue
        result[instance_db_id] = component
    if ctx is not None:
        for instance_db_id in sorted(ambiguous):
            ctx.warn(
                "dsn_cis_variant",
                f"instance db id {instance_db_id} is placed by multiple occurrences "
                "(repeated sheet); its CIS overrides are ambiguous and were skipped",
            )
    return result


def _group_level_evidence(store: DsnCisVariantStore, group: DsnCisGroup) -> tuple[str, ...]:
    evidence = ["group_name"]
    if _group_is_in_bom_child_stream(store, group.name):
        evidence.append("bom_child_stream")
    return tuple(evidence)


def _group_is_in_bom_child_stream(store: DsnCisVariantStore, group_name: str) -> bool:
    for bom in store.boms:
        for child in bom.child_string_lists:
            if any(
                value == group_name and _matches_not_fitted_token(value) for value in child.values
            ):
                return True
    return False


def _group_not_fitted_overrides(
    group: DsnCisGroup,
    components_by_instance_id: dict[int, Component],
    *,
    group_level_evidence: tuple[str, ...],
    ctx: ParseContext | None = None,
) -> list[VariantOverride]:
    overrides: list[VariantOverride] = []
    for member in group.members:
        if member.resolved_instance_db_id is None:
            continue
        component = components_by_instance_id.get(member.resolved_instance_db_id)
        if component is None:
            if ctx is not None:
                ctx.warn(
                    "dsn_cis_variant",
                    f"group {group.name!r} member resolved to instance "
                    f"{member.resolved_instance_db_id} but no placed component matched it; "
                    "override skipped",
                )
            continue
        evidence = _override_evidence(component, group_level_evidence)
        if not _has_independent_evidence(evidence):
            continue
        overrides.append(
            VariantOverride(
                variant_name=group.name,
                target=VariantTarget(
                    kind=VariantTargetKind.COMPONENT,
                    object_id=component.id,
                    reference=component.reference,
                    occurrence_id=str(member.occurrence_id),
                    source_id=component.metadata.get("dsn_component_source_ids", ""),
                ),
                field=VariantField.DNP,
                value=True,
                source_id=f"{member.stream_path}:{member.row_order}",
                native_kind="orcad_cis_not_fitted_group_member",
                metadata=_override_metadata(group, member, evidence),
            )
        )
    return overrides


def _group_update_storage_overrides(
    group: DsnCisGroup,
    components_by_instance_id: dict[int, Component],
    ctx: ParseContext | None,
) -> list[VariantOverride]:
    """Attach CIS update-storage rows to the not-fitted variant as evidence.

    Update-storage rows are CIS-database property *snapshots* of a member
    (finding V1), never per-variant substitutions. Each resolved row becomes a
    ``orcad_cis_update_storage_row`` carrier override holding the full raw row
    so nothing is discarded, plus zero or more typed field overrides — but only
    where a column value is a provable change from the recoverable base. On
    every known fixture every value equals base (or has no base), so no
    change-claiming override is emitted; a row therefore coexists with, and
    never contradicts, the member's DNP override.
    """
    overrides: list[VariantOverride] = []
    for row in group.update_storage_rows:
        if row.resolved_instance_db_id is None:
            continue
        component = components_by_instance_id.get(row.resolved_instance_db_id)
        if component is None:
            if ctx is not None:
                ctx.warn(
                    "dsn_cis_variant",
                    f"group {group.name!r} update-storage row {row.row_order} resolved to "
                    f"instance {row.resolved_instance_db_id} but no placed component matched it; "
                    "row skipped",
                )
            continue
        columns = dict(zip(row.columns, row.values, strict=False))
        target = VariantTarget(
            kind=VariantTargetKind.COMPONENT,
            object_id=component.id,
            reference=component.reference,
            occurrence_id=str(row.occurrence_id),
            source_id=component.metadata.get("dsn_component_source_ids", ""),
        )
        source_id = f"{row.stream_path}:{row.row_order}"
        row_metadata = _update_storage_row_metadata(group, row, columns)
        overrides.append(
            VariantOverride(
                variant_name=group.name,
                target=target,
                field=VariantField.OTHER,
                value=None,
                source_id=source_id,
                native_kind="orcad_cis_update_storage_row",
                metadata=row_metadata,
            )
        )
        overrides.extend(
            _update_storage_change_overrides(
                group.name, target, source_id, component, columns, row_metadata
            )
        )
    return overrides


def _update_storage_row_metadata(
    group: DsnCisGroup,
    row: DsnCisUpdateStorageRow,
    columns: dict[str, str],
) -> dict[str, str]:
    metadata = {
        "source_format": "orcad_cis",
        "dsn_cis_group_name": group.name,
        "dsn_cis_update_storage_stream_path": row.stream_path,
        "dsn_cis_update_storage_row_order": str(row.row_order),
        "dsn_cis_occurrence_id": str(row.occurrence_id if row.occurrence_id is not None else ""),
        "dsn_cis_resolved_instance_db_id": str(row.resolved_instance_db_id or ""),
        "dsn_cis_resolution_kind": row.resolution_kind,
        "dsn_cis_update_storage": "snapshot",
    }
    for column, value in columns.items():
        metadata[f"dsn_cis_update_col:{column}"] = value
    return metadata


def _update_storage_change_overrides(
    variant_name: str,
    target: VariantTarget,
    source_id: str,
    component: Component,
    columns: dict[str, str],
    row_metadata: dict[str, str],
) -> list[VariantOverride]:
    overrides: list[VariantOverride] = []

    part_number = _clean_update_value(columns.get("Part Number"))
    if part_number is not None:
        base_pn = _base_part_number(component)
        if base_pn is not None and base_pn != part_number:
            overrides.append(
                VariantOverride(
                    variant_name=variant_name,
                    target=target,
                    field=VariantField.PART_NUMBERS,
                    value=(PartNumber(manufacturer="", number=part_number),),
                    base_value=(PartNumber(manufacturer="", number=base_pn),),
                    source_id=source_id,
                    native_kind="orcad_cis_update_storage_row",
                    metadata=row_metadata,
                )
            )

    footprint = _clean_update_value(columns.get("PCB Footprint"))
    if footprint is not None:
        base_fp = _base_footprint(component)
        if base_fp is not None and base_fp != footprint:
            overrides.append(
                VariantOverride(
                    variant_name=variant_name,
                    target=target,
                    field=VariantField.FOOTPRINTS,
                    value=(FootprintModel(name=footprint, is_current=True),),
                    base_value=(FootprintModel(name=base_fp, is_current=True),),
                    source_id=source_id,
                    native_kind="orcad_cis_update_storage_row",
                    metadata=row_metadata,
                )
            )

    for column in _UPDATE_STORAGE_PARAMETER_COLUMNS:
        value = _clean_update_value(columns.get(column))
        if value is None:
            continue
        base = _param_value(component, column)
        if base is not None and base != value:
            overrides.append(
                VariantOverride(
                    variant_name=variant_name,
                    target=VariantTarget(
                        kind=target.kind,
                        object_id=target.object_id,
                        reference=target.reference,
                        occurrence_id=target.occurrence_id,
                        source_id=target.source_id,
                        parameter_name=column,
                    ),
                    field=VariantField.PARAMETER,
                    value=Parameter(name=column, value=value, source="orcad_cis_update_storage"),
                    base_value=Parameter(name=column, value=base),
                    source_id=source_id,
                    native_kind="orcad_cis_update_storage_row",
                    metadata=row_metadata,
                )
            )
    return overrides


def _clean_update_value(value: str | None) -> str | None:
    """Return a comparable update-storage value, or ``None`` for empty/sentinel."""
    if value is None:
        return None
    value = value.strip()
    if not value or value == _UPDATE_STORAGE_UNDEFINED:
        return None
    return value


def _param_value(component: Component, name: str) -> str | None:
    for parameter in component.parameters:
        if parameter.name == name and parameter.value.strip():
            return parameter.value.strip()
    return None


def _base_part_number(component: Component) -> str | None:
    if component.part_numbers:
        return component.part_numbers[0].number
    return _param_value(component, "PART_NUMBER") or _param_value(component, "Part Number")


def _base_footprint(component: Component) -> str | None:
    for footprint in component.footprints:
        if footprint.is_current and footprint.name:
            return footprint.name
    if component.footprints and component.footprints[0].name:
        return component.footprints[0].name
    return _param_value(component, "PCB Footprint")


def _override_evidence(
    component: Component,
    group_level_evidence: tuple[str, ...],
) -> tuple[str, ...]:
    evidence = list(group_level_evidence)
    if _component_has_not_fitted_property(component):
        evidence.append("component_properties")
    return tuple(sorted(evidence, key=_evidence_order))


def _evidence_order(value: str) -> int:
    order = {
        "group_name": 0,
        "component_properties": 1,
        "bom_child_stream": 2,
    }
    return order.get(value, len(order))


def _has_independent_evidence(evidence: tuple[str, ...]) -> bool:
    return "group_name" in evidence and len(evidence) > 1


def _aggregate_override_evidence(overrides: Sequence[VariantOverride]) -> tuple[str, ...]:
    values: set[str] = set()
    for override in overrides:
        for value in override.metadata.get("not_fitted_evidence", "").split(","):
            if value:
                values.add(value)
    return tuple(sorted(values, key=_evidence_order))


def _component_has_not_fitted_property(component: Component) -> bool:
    if _matches_not_fitted_token(component.part):
        return True
    return any(_matches_not_fitted_token(parameter.value) for parameter in component.parameters)


def _matches_not_fitted_token(value: str) -> bool:
    normalized = " ".join(value.strip().upper().split())
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _TOKEN_PATTERNS)


def _variant_metadata(evidence: _VariantEvidence) -> dict[str, str]:
    return {
        "source_format": "orcad_cis",
        "dsn_cis_group_name": evidence.group_name,
        "dsn_cis_group_stream_path": evidence.group_stream_path,
        "dsn_cis_group_raw_fields": evidence.group_raw_fields,
        "not_fitted_evidence": ",".join(evidence.evidence),
    }


def _override_metadata(
    group: DsnCisGroup,
    member: DsnCisGroupMember,
    evidence: tuple[str, ...],
) -> dict[str, str]:
    return {
        "source_format": "orcad_cis",
        "dsn_cis_group_name": group.name,
        "dsn_cis_group_stream_path": group.stream_path,
        "dsn_cis_group_raw_fields": _raw_fields(group.raw_fields),
        "dsn_cis_member_stream_path": member.stream_path,
        "dsn_cis_member_row_order": str(member.row_order),
        "dsn_cis_member_state": member.state,
        "dsn_cis_occurrence_id": str(member.occurrence_id),
        "dsn_cis_resolved_instance_db_id": str(member.resolved_instance_db_id or ""),
        "dsn_cis_resolution_kind": member.resolution_kind,
        "not_fitted_evidence": ",".join(evidence),
    }


def _raw_fields(fields: Sequence[str]) -> str:
    return _RAW_FIELD_SEPARATOR.join(fields)
