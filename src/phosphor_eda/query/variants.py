"""Variant summary and detail formatters."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING

from phosphor_eda.domain.variants import VariantField
from phosphor_eda.query.format import tabulate

if TYPE_CHECKING:
    from phosphor_eda.domain.project import Project
    from phosphor_eda.domain.variants import Variant, VariantOverride, VariantValue


def format_variant_table(project: Project) -> str:
    """Format all project variants as a compact table."""
    if not project.variants:
        return "No variants found."
    return tabulate(
        (
            "NAME",
            "ACTIVE",
            "DESCRIPTION",
            "OVERRIDES",
            "NOT_FITTED",
            "ALT_PARTS",
            "PARAMS",
            "OTHER",
        ),
        [_variant_row(project, variant) for variant in project.variants],
    )


def format_variant_detail(project: Project, name: str) -> str:
    """Format one project variant and its overrides."""
    variant = _variant_or_error(project, name)
    lines = [
        f"Variant {variant.name}",
        f"  Active: {'yes' if project.active_variant is variant else 'no'}",
    ]
    if variant.description:
        lines.append(f"  Description: {variant.description}")
    if not variant.overrides:
        lines.append("  No overrides.")
        return "\n".join(lines)
    rows = [
        (
            _override_target_label(override),
            override.field.value,
            override.target.parameter_name,
            _value_label(override.value),
            _value_label(override.base_value),
            override.native_kind,
            override.source_id,
            "yes" if override.applied else "no",
        )
        for override in variant.overrides
    ]
    lines.extend(
        f"  {line}"
        for line in tabulate(
            ("TARGET", "FIELD", "PARAM", "VALUE", "BASE", "NATIVE", "SOURCE", "APPLIED"),
            rows,
        ).splitlines()
    )
    return "\n".join(lines)


def variant_counts(variant: Variant) -> tuple[int, int, int, int, int]:
    """Return total, not-fitted, alternate-part, parameter, and other counts."""
    not_fitted = 0
    alternate = 0
    parameters = 0
    for override in variant.overrides:
        if override.field is VariantField.FITTED and override.value is False:
            not_fitted += 1
        elif override.field is VariantField.ALTERNATE_PART:
            alternate += 1
        elif override.field is VariantField.PARAMETER:
            parameters += 1
    other = len(variant.overrides) - not_fitted - alternate - parameters
    return len(variant.overrides), not_fitted, alternate, parameters, other


def _variant_row(
    project: Project, variant: Variant
) -> tuple[str, str, str, str, str, str, str, str]:
    total, not_fitted, alternate, parameters, other = variant_counts(variant)
    return (
        variant.name,
        "yes" if project.active_variant is variant else "no",
        variant.description,
        str(total),
        str(not_fitted),
        str(alternate),
        str(parameters),
        str(other),
    )


def _variant_or_error(project: Project, name: str) -> Variant:
    for variant in project.variants:
        if variant.name == name:
            return variant
    valid = ", ".join(variant.name for variant in project.variants) or "none"
    raise ValueError(f"unknown variant '{name}'. Valid variants: {valid}")


def _override_target_label(override: VariantOverride) -> str:
    target = override.target
    for value in (target.reference, target.object_id, target.occurrence_id, target.source_id):
        if value:
            return value
    return target.kind.value


def _value_label(value: VariantValue) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    if is_dataclass(value):
        return json.dumps(asdict(value), separators=(",", ":"), sort_keys=True)
    return json.dumps(
        [asdict(item) if is_dataclass(item) else item for item in value],
        separators=(",", ":"),
        sort_keys=True,
    )
