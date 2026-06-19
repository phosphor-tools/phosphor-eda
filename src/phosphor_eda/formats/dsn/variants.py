"""Map fixture-proven OrCAD CIS VariantStore evidence to public variants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    from phosphor_eda.formats.common.raw_models import (
        DsnCisGroup,
        DsnCisGroupMember,
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


@dataclass(frozen=True)
class _VariantEvidence:
    group_name: str
    group_stream_path: str
    group_raw_fields: str
    evidence: tuple[str, ...]


def map_orcad_cis_not_fitted_variants(
    raw: ParsedDesign, schematic: Schematic | None
) -> list[Variant]:
    """Create public OrCAD DNP-style variants from raw CIS groups.

    This first public CIS slice is intentionally conservative: it maps only
    group names with whole-token not-fitted vocabulary, and only when the raw
    store has independent evidence such as BOM child membership or per-component
    properties. Alternate part/value/update-storage rows remain raw for the
    follow-up slice.
    """
    if schematic is None:
        return []
    store = raw.cis_variant_store
    if not store.present or store.placeholder:
        return []

    components_by_instance_id = _components_by_instance_id(schematic.components)
    variants: list[Variant] = []
    for group in store.groups:
        if not _matches_not_fitted_token(group.name):
            continue
        group_level_evidence = _group_level_evidence(store, group)
        overrides = _group_not_fitted_overrides(
            group,
            components_by_instance_id,
            group_level_evidence=group_level_evidence,
        )
        if not overrides:
            continue
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


def _components_by_instance_id(components: Iterable[Component]) -> dict[int, Component]:
    result: dict[int, Component] = {}
    for component in components:
        source_ids = component.metadata.get("dsn_component_source_ids", "")
        marker = ":component:"
        if marker not in source_ids:
            continue
        try:
            instance_db_id = int(source_ids.rsplit(marker, maxsplit=1)[1])
        except ValueError:
            continue
        result[instance_db_id] = component
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
) -> list[VariantOverride]:
    overrides: list[VariantOverride] = []
    for member in group.members:
        if member.resolved_instance_db_id is None:
            continue
        component = components_by_instance_id.get(member.resolved_instance_db_id)
        if component is None:
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
        "sidecar_bom_row": 3,
        "update_storage_row": 4,
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
