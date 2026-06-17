"""Apply a selected project variant to the public domain model."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING, TypeGuard, cast

from phosphor_eda.domain.schematic import (
    DnpSource,
    FootprintModel,
    LibraryLink,
    Parameter,
    PartNumber,
)
from phosphor_eda.domain.variants import VariantField, VariantOverride, VariantTargetKind
from phosphor_eda.formats.common.part_fields import resolve_part_fields

if TYPE_CHECKING:
    from phosphor_eda.domain.project import Project
    from phosphor_eda.domain.schematic import Component
    from phosphor_eda.domain.variants import Variant, VariantTarget


def materialize_project_variant(
    project: Project,
    *,
    variant_name: str | None = None,
    base_variant: bool = False,
) -> None:
    """Attach variant overrides and apply the selected variant in-place."""
    if variant_name and base_variant:
        raise ValueError("--variant and --base-variant are mutually exclusive.")

    if base_variant:
        selected = ""
    elif variant_name is not None:
        selected = variant_name
    else:
        selected = project.selected_variant_name

    if selected:
        _variant_by_name(project, selected)
    project.selected_variant_name = selected

    component_index = _ComponentIndex(project)
    for variant in project.variants:
        _attach_and_apply_variant(project, variant, component_index, selected)


def _variant_by_name(project: Project, name: str) -> Variant:
    for variant in project.variants:
        if variant.name == name:
            return variant
    valid = ", ".join(variant.name for variant in project.variants) or "none"
    raise ValueError(f"unknown variant '{name}'. Valid variants: {valid}")


def _attach_and_apply_variant(
    project: Project,
    variant: Variant,
    component_index: _ComponentIndex,
    selected: str,
) -> None:
    active = variant.name == selected
    updated_overrides: list[VariantOverride] = []
    for override in variant.overrides:
        component = component_index.resolve(override.target)
        if component is None:
            if active and override.target.kind is VariantTargetKind.COMPONENT:
                raise ValueError(
                    "could not resolve selected variant override "
                    f"{override.variant_name}:{override.field.value} "
                    f"target={_target_label(override.target)}"
                )
            updated_overrides.append(override)
            continue

        with_base = replace(
            override,
            base_value=_base_value(component, override),
            applied=active,
        )
        component.variant_overrides.append(with_base)
        if active:
            _apply_component_override(component, with_base)
        updated_overrides.append(with_base)
    variant.overrides = updated_overrides

    if project.schematic is not None and active:
        for component in project.schematic.components:
            _refresh_part_fields(component)


class _ComponentIndex:
    def __init__(self, project: Project) -> None:
        self._by_key: dict[tuple[str, str], list[Component]] = defaultdict(list)
        if project.schematic is None:
            return
        for component in project.schematic.components:
            self._add("object_id", component.id, component)
            self._add("reference", component.reference, component)
            self._add(
                "source_id", component.metadata.get("altium_component_unique_id", ""), component
            )
            for source_id in component.metadata.get("altium_component_source_ids", "").split(","):
                self._add("source_id", source_id, component)
            for occurrence in component.occurrences:
                self._add("occurrence_id", occurrence.id, component)
                self._add("source_id", occurrence.source_id, component)
                self._add(
                    "source_id",
                    occurrence.metadata.get("altium_component_occurrence_source_id", ""),
                    component,
                )
                self._add(
                    "source_id",
                    occurrence.metadata.get("altium_component_source_id", ""),
                    component,
                )

    def _add(self, kind: str, value: str, component: Component) -> None:
        value = value.strip()
        if value:
            bucket = self._by_key[(kind, value)]
            if component not in bucket:
                bucket.append(component)

    def resolve(self, target: VariantTarget) -> Component | None:
        if target.kind is not VariantTargetKind.COMPONENT:
            return None
        for kind, value in (
            ("object_id", target.object_id),
            ("occurrence_id", target.occurrence_id),
            ("source_id", target.source_id),
            ("reference", target.reference),
        ):
            if not value:
                continue
            matches = self._by_key.get((kind, value), [])
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ValueError(f"ambiguous variant target {kind}={value}")
        return None


def _target_label(target: VariantTarget) -> str:
    for value in (target.object_id, target.occurrence_id, target.source_id, target.reference):
        if value:
            return value
    return target.kind.value


def _base_value(component: Component, override: VariantOverride) -> object:
    if override.field is VariantField.FITTED:
        return not component.dnp
    if override.field is VariantField.DNP:
        return component.dnp
    if override.field is VariantField.EXCLUDE_FROM_BOM:
        return component.exclude_from_bom
    if override.field is VariantField.EXCLUDE_FROM_SIMULATION:
        return component.exclude_from_simulation
    if override.field is VariantField.PARAMETER:
        name = _parameter_name(override)
        for parameter in component.parameters:
            if parameter.name == name:
                return parameter
        return None
    if override.field is VariantField.LIB:
        return component.lib
    if override.field is VariantField.FOOTPRINTS:
        return tuple(component.footprints)
    if override.field is VariantField.PART_NUMBERS:
        return tuple(component.part_numbers)
    if override.field is VariantField.DATASHEET:
        return component.datasheet
    return None


def _apply_component_override(component: Component, override: VariantOverride) -> None:
    value = override.value
    if override.field is VariantField.FITTED:
        if value is False:
            component.dnp = True
            component.dnp_source = DnpSource.ACTIVE_VARIANT
        elif value is True:
            component.dnp = False
            component.dnp_source = None
    elif override.field is VariantField.DNP and isinstance(value, bool):
        component.dnp = value
        component.dnp_source = DnpSource.ACTIVE_VARIANT if value else None
    elif override.field is VariantField.EXCLUDE_FROM_BOM and isinstance(value, bool):
        component.exclude_from_bom = value
    elif override.field is VariantField.EXCLUDE_FROM_SIMULATION and isinstance(value, bool):
        component.exclude_from_simulation = value
    elif override.field is VariantField.PARAMETER and isinstance(value, Parameter):
        _replace_parameter(component, value)
    elif override.field is VariantField.LIB and isinstance(value, LibraryLink):
        component.lib = value
    elif override.field is VariantField.FOOTPRINTS and _all_footprints(value):
        component.footprints = list(value)
    elif override.field is VariantField.PART_NUMBERS and _all_part_numbers(value):
        component.part_numbers = list(value)
    elif override.field is VariantField.DATASHEET and isinstance(value, str):
        component.datasheet = value


def _parameter_name(override: VariantOverride) -> str:
    if isinstance(override.value, Parameter):
        return override.value.name
    return override.target.parameter_name


def _replace_parameter(component: Component, value: Parameter) -> None:
    for index, parameter in enumerate(component.parameters):
        if parameter.name == value.name:
            component.parameters[index] = value
            return
    component.parameters.append(value)


def _all_footprints(value: object) -> TypeGuard[tuple[FootprintModel, ...]]:
    if not isinstance(value, tuple):
        return False
    items = cast("tuple[object, ...]", value)
    return all(isinstance(item, FootprintModel) for item in items)


def _all_part_numbers(value: object) -> TypeGuard[tuple[PartNumber, ...]]:
    if not isinstance(value, tuple):
        return False
    items = cast("tuple[object, ...]", value)
    return all(isinstance(item, PartNumber) for item in items)


def _refresh_part_fields(component: Component) -> None:
    explicit_fields = {
        override.field for override in component.variant_overrides if override.applied
    }
    resolved = resolve_part_fields(component.parameters, part=component.part)
    if VariantField.PART_NUMBERS not in explicit_fields:
        component.part_numbers = resolved.part_numbers
    if resolved.datasheet and VariantField.DATASHEET not in explicit_fields:
        component.datasheet = resolved.datasheet
    if (
        resolved.dnp_convention
        and component.dnp_source is None
        and VariantField.DNP not in explicit_fields
        and VariantField.FITTED not in explicit_fields
    ):
        component.dnp = True
        component.dnp_source = DnpSource.CONVENTION
