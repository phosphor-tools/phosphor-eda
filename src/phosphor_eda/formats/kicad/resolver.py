"""Resolve KiCad-native source connectivity into the public schematic model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.domain.buses import (
    BusDefinition,
    build_buses_from_definitions,
    bus_kind_for_name,
    expand_bus_members,
)
from phosphor_eda.formats.common.electrical import (
    KICAD_ELECTRICAL_MAP,
    set_pin_electrical,
)
from phosphor_eda.formats.common.net_union import NetUnion
from phosphor_eda.formats.common.resolved_graph import (
    ResolutionInputError,
    ResolvedComponentOccurrenceInput,
    ResolvedLocalNetInput,
    ResolvedNetInput,
    ResolvedPageInput,
    ResolvedPinInput,
    build_resolved_schematic,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.domain.schematic import Schematic, ScopeId
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.kicad.source import (
        KiCadLocalNet,
        KiCadPinOccurrence,
        KiCadSourceDesign,
    )


@dataclass(slots=True)
class _NameEvidence:
    global_labels: list[str]
    power_symbols: list[str]
    local_labels: list[str]
    hierarchical_labels: list[str]
    sheet_pins: list[str]
    generated: list[str]


def resolve_kicad_source(source: KiCadSourceDesign, ctx: ParseContext | None = None) -> Schematic:
    """Resolve a KiCad source design into the public schematic graph.

    Non-fatal issues accumulated on *ctx* are surfaced as
    ``parse_issue_count`` in the resulting schematic metadata.
    """
    pin_occurrences = list(source.pin_occurrences)
    component_ids_by_source_id = _component_ids_by_source_id(pin_occurrences)
    component_source_ids_by_component_id = _component_source_ids_by_component_id(
        pin_occurrences,
        component_ids_by_source_id,
    )
    local_nets_by_id = {local_net.id: local_net for local_net in source.local_nets}
    _validate_source_refs(source, local_nets_by_id)
    net_union = NetUnion(local_net.id for local_net in source.local_nets)

    _merge_repeated_logical_pins(net_union, pin_occurrences, component_ids_by_source_id)
    _merge_same_scope_names(net_union, source.local_nets)
    _merge_global_labels(net_union, source.local_nets)
    _merge_power_symbols(net_union, source.local_nets)
    _merge_hierarchical_sheet_pins(source, net_union)

    metadata = {"kicad_root_source_file": source.root_source_file}
    if ctx is not None and ctx.issues:
        metadata["parse_issue_count"] = str(len(ctx.issues))

    design = build_resolved_schematic(
        name=source.name,
        pages=_page_inputs(source),
        local_nets=_local_net_inputs(source.local_nets),
        pins=_pin_inputs(
            pin_occurrences,
            component_ids_by_source_id,
            component_source_ids_by_component_id,
        ),
        net_union=net_union,
        net_factory=lambda net_index, root_id, group_local_nets: _kicad_net_input_for_group(
            local_nets_by_id,
            net_index,
            root_id,
            group_local_nets,
        ),
        include_net=_include_kicad_net,
        metadata=metadata,
    )
    design.buses = build_buses_from_definitions(design, _kicad_bus_definitions(source))
    return design


def _kicad_bus_definitions(source: KiCadSourceDesign) -> list[BusDefinition]:
    definitions: list[BusDefinition] = []
    seen: set[tuple[str, str, ScopeId]] = set()
    bus_index = 0
    for label in source.bus_labels:
        aliases = _kicad_bus_aliases_for_scope(source, label.scope_id)
        name = _clean_name(label.name)
        kind = bus_kind_for_name(name, aliases=aliases)
        member_names = tuple(expand_bus_members(name, aliases=aliases) or ())
        if kind is None or not member_names or (kind.value, name, label.scope_id) in seen:
            continue
        seen.add((kind.value, name, label.scope_id))
        bus_index += 1
        definitions.append(
            BusDefinition(
                id=f"kicad:bus:{kind.value}:{bus_index:04d}",
                name=name,
                kind=kind,
                member_names=member_names,
                metadata={
                    "source_format": "kicad",
                    "source_id": label.id,
                    "source_kind": label.kind,
                    "source_scope": str(label.scope_id),
                },
            )
        )
    return definitions


def _kicad_bus_aliases_for_scope(
    source: KiCadSourceDesign,
    scope_id: ScopeId,
) -> dict[str, tuple[str, ...]]:
    return {alias.name: alias.members for alias in source.bus_aliases if alias.scope_id == scope_id}


def _merge_repeated_logical_pins(
    net_union: NetUnion,
    pin_occurrences: Iterable[KiCadPinOccurrence],
    component_ids_by_source_id: dict[str, str],
) -> None:
    net_ids_by_pin: dict[tuple[str, str], list[str]] = {}
    for pin_occurrence in pin_occurrences:
        key = (
            _component_identity(pin_occurrence, component_ids_by_source_id),
            pin_occurrence.pin_designator,
        )
        net_ids_by_pin.setdefault(key, []).append(pin_occurrence.local_net_id)

    for net_ids in net_ids_by_pin.values():
        _merge_ids(net_union, net_ids)


def _merge_same_scope_names(net_union: NetUnion, local_nets: Iterable[KiCadLocalNet]) -> None:
    label_ids: dict[tuple[ScopeId, str], list[str]] = {}

    for local_net in local_nets:
        for label in local_net.local_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                label_ids.setdefault((label.scope_id, name), []).append(local_net.id)
        for label in local_net.hierarchical_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                label_ids.setdefault((label.scope_id, name), []).append(local_net.id)

    for net_ids in label_ids.values():
        _merge_ids(net_union, net_ids)


def _merge_global_labels(net_union: NetUnion, local_nets: Iterable[KiCadLocalNet]) -> None:
    ids_by_name: dict[str, list[str]] = {}
    for local_net in local_nets:
        for label in local_net.global_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                ids_by_name.setdefault(name, []).append(local_net.id)

    for net_ids in ids_by_name.values():
        _merge_ids(net_union, net_ids)


def _merge_power_symbols(net_union: NetUnion, local_nets: Iterable[KiCadLocalNet]) -> None:
    ids_by_name: dict[str, list[str]] = {}
    for local_net in local_nets:
        for symbol in local_net.power_symbols:
            name = _mergeable_name(symbol.name)
            if name is not None:
                ids_by_name.setdefault(name, []).append(local_net.id)

    for net_ids in ids_by_name.values():
        _merge_ids(net_union, net_ids)


def _merge_hierarchical_sheet_pins(source: KiCadSourceDesign, net_union: NetUnion) -> None:
    child_hierarchical_net_ids: dict[tuple[ScopeId, str], list[str]] = {}
    for local_net in source.local_nets:
        for label in local_net.hierarchical_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                child_hierarchical_net_ids.setdefault((label.scope_id, name), []).append(
                    local_net.id
                )

    for sheet_pin in source.sheet_pins:
        name = _mergeable_name(sheet_pin.name)
        if name is None:
            continue
        child_key = (sheet_pin.child_scope_id, name)
        for child_net_id in child_hierarchical_net_ids.get(child_key, []):
            _ = net_union.union(sheet_pin.local_net_id, child_net_id)


def _merge_ids(net_union: NetUnion, net_ids: list[str]) -> None:
    if len(net_ids) < 2:
        return
    first_id = net_ids[0]
    for net_id in net_ids[1:]:
        _ = net_union.union(first_id, net_id)


def _validate_source_refs(
    source: KiCadSourceDesign,
    local_nets_by_id: dict[str, KiCadLocalNet],
) -> None:
    scopes = {instance.scope_id for instance in source.sheet_instances}
    attached_sheet_pin_local_net_ids: dict[str, str] = {}
    for local_net in source.local_nets:
        if local_net.scope_id not in scopes:
            msg = f"local net {local_net.id!r} references unknown scope {local_net.scope_id}"
            raise ResolutionInputError(msg)
        for label in local_net.local_labels:
            _validate_scoped_local_net_ref(
                kind="local label",
                id_=label.id,
                scope_id=label.scope_id,
                local_net_id=label.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for label in local_net.global_labels:
            _validate_scoped_local_net_ref(
                kind="global label",
                id_=label.id,
                scope_id=label.scope_id,
                local_net_id=label.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for label in local_net.hierarchical_labels:
            _validate_scoped_local_net_ref(
                kind="hierarchical label",
                id_=label.id,
                scope_id=label.scope_id,
                local_net_id=label.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for symbol in local_net.power_symbols:
            _validate_scoped_local_net_ref(
                kind="power symbol",
                id_=symbol.id,
                scope_id=symbol.scope_id,
                local_net_id=symbol.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for sheet_pin in local_net.sheet_pins:
            _validate_scoped_local_net_ref(
                kind="sheet pin",
                id_=sheet_pin.id,
                scope_id=sheet_pin.scope_id,
                local_net_id=sheet_pin.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
            if sheet_pin.child_scope_id not in scopes:
                msg = (
                    f"sheet pin {sheet_pin.id!r} references unknown child scope "
                    f"{sheet_pin.child_scope_id}"
                )
                raise ResolutionInputError(msg)
            attached_sheet_pin_local_net_ids[sheet_pin.id] = local_net.id
    for sheet_pin in source.sheet_pins:
        _validate_top_level_sheet_pin_ref(
            id_=sheet_pin.id,
            scope_id=sheet_pin.scope_id,
            child_scope_id=sheet_pin.child_scope_id,
            local_net_id=sheet_pin.local_net_id,
            scopes=scopes,
            local_nets_by_id=local_nets_by_id,
        )
        attached_local_net_id = attached_sheet_pin_local_net_ids.get(sheet_pin.id)
        if attached_local_net_id is None:
            msg = (
                f"sheet pin {sheet_pin.id!r} is not attached to local net "
                f"{sheet_pin.local_net_id!r}"
            )
            raise ResolutionInputError(msg)
        if attached_local_net_id != sheet_pin.local_net_id:
            msg = (
                f"sheet pin {sheet_pin.id!r} references local net "
                f"{sheet_pin.local_net_id!r} but is attached to local net "
                f"{attached_local_net_id!r}"
            )
            raise ResolutionInputError(msg)
    for pin in source.pin_occurrences:
        _validate_pin_ref(
            id_=pin.id,
            scope_id=pin.scope_id,
            local_net_id=pin.local_net_id,
            scopes=scopes,
            local_nets_by_id=local_nets_by_id,
        )


def _validate_top_level_sheet_pin_ref(
    *,
    id_: str,
    scope_id: ScopeId,
    child_scope_id: ScopeId,
    local_net_id: str,
    scopes: set[ScopeId],
    local_nets_by_id: dict[str, KiCadLocalNet],
) -> None:
    _validate_scoped_local_net_ref(
        kind="sheet pin",
        id_=id_,
        scope_id=scope_id,
        local_net_id=local_net_id,
        containing_local_net_id=local_net_id,
        scopes=scopes,
        local_nets_by_id=local_nets_by_id,
    )
    if child_scope_id not in scopes:
        msg = f"sheet pin {id_!r} references unknown child scope {child_scope_id}"
        raise ResolutionInputError(msg)


def _validate_pin_ref(
    *,
    id_: str,
    scope_id: ScopeId,
    local_net_id: str,
    scopes: set[ScopeId],
    local_nets_by_id: dict[str, KiCadLocalNet],
) -> None:
    if scope_id not in scopes:
        msg = f"pin {id_!r} references unknown scope {scope_id}"
        raise ResolutionInputError(msg)
    local_net = local_nets_by_id.get(local_net_id)
    if local_net is None:
        msg = f"pin {id_!r} references unknown local net {local_net_id!r}"
        raise ResolutionInputError(msg)
    if local_net.scope_id != scope_id:
        msg = (
            f"pin {id_!r} scope {scope_id} does not match "
            f"local net {local_net_id!r} scope {local_net.scope_id}"
        )
        raise ResolutionInputError(msg)


def _validate_scoped_local_net_ref(
    *,
    kind: str,
    id_: str,
    scope_id: ScopeId,
    local_net_id: str,
    containing_local_net_id: str,
    scopes: set[ScopeId],
    local_nets_by_id: dict[str, KiCadLocalNet],
) -> None:
    if scope_id not in scopes:
        msg = f"{kind} {id_!r} references unknown scope {scope_id}"
        raise ResolutionInputError(msg)
    local_net = local_nets_by_id.get(local_net_id)
    if local_net is None:
        msg = f"{kind} {id_!r} references unknown local net {local_net_id!r}"
        raise ResolutionInputError(msg)
    if local_net_id != containing_local_net_id:
        msg = (
            f"{kind} {id_!r} references local net {local_net_id!r} "
            f"but is attached to local net {containing_local_net_id!r}"
        )
        raise ResolutionInputError(msg)
    if local_net.scope_id != scope_id:
        msg = (
            f"{kind} {id_!r} scope {scope_id} does not match "
            f"local net {local_net_id!r} scope {local_net.scope_id}"
        )
        raise ResolutionInputError(msg)


def _page_inputs(source: KiCadSourceDesign) -> list[ResolvedPageInput]:
    return [
        ResolvedPageInput(
            id=instance.id,
            name=instance.sheet_name,
            source_file=instance.source_file,
            scope_id=instance.scope_id,
            title_block=instance.title_block,
            metadata={
                "kicad_sheet_symbol_id": instance.sheet_symbol_id,
            },
        )
        for instance in source.sheet_instances
    ]


def _local_net_inputs(local_nets: Iterable[KiCadLocalNet]) -> list[ResolvedLocalNetInput]:
    return [
        ResolvedLocalNetInput(
            id=local_net.id,
            scope_id=local_net.scope_id,
            source_names=frozenset(_source_names(local_net)),
        )
        for local_net in local_nets
    ]


def _kicad_net_input_for_group(
    local_nets_by_id: dict[str, KiCadLocalNet],
    net_index: int,
    root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
) -> ResolvedNetInput:
    kicad_local_nets = [local_nets_by_id[local_net.id] for local_net in group_local_nets]
    name = select_kicad_net_name(kicad_local_nets)
    aliases = _all_alias_names(kicad_local_nets)
    aliases.discard(name)
    return ResolvedNetInput(
        id=f"net:{net_index:04d}",
        name=name,
        aliases=frozenset(aliases),
        metadata={
            "kicad_root_local_net_id": root_id,
        },
    )


def _include_kicad_net(
    _root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
    pins: tuple[ResolvedPinInput, ...],
) -> bool:
    group_local_net_ids = {local_net.id for local_net in group_local_nets}
    return any(pin.local_net_id in group_local_net_ids for pin in pins)


def select_kicad_net_name(local_nets: Iterable[KiCadLocalNet]) -> str:
    """Select a public net name using KiCad source-name priority."""
    evidence = _combined_name_evidence(local_nets)
    for names in (
        evidence.global_labels,
        evidence.power_symbols,
        evidence.local_labels,
        evidence.hierarchical_labels,
        evidence.sheet_pins,
        evidence.generated,
    ):
        for name in names:
            if name:
                return name
    return "__auto_net"


def _combined_name_evidence(local_nets: Iterable[KiCadLocalNet]) -> _NameEvidence:
    global_labels: list[str] = []
    power_symbols: list[str] = []
    local_labels: list[str] = []
    hierarchical_labels: list[str] = []
    sheet_pins: list[str] = []
    generated: list[str] = []

    for local_net in local_nets:
        global_labels.extend(_global_label_names(local_net))
        power_symbols.extend(_power_symbol_names(local_net))
        local_labels.extend(_local_label_names(local_net))
        hierarchical_labels.extend(_hierarchical_label_names(local_net))
        sheet_pins.extend(_sheet_pin_names(local_net))
        generated_name = _clean_name(local_net.generated_name)
        if generated_name:
            generated.append(generated_name)

    return _NameEvidence(
        global_labels=_dedupe(global_labels),
        power_symbols=_dedupe(power_symbols),
        local_labels=_dedupe(local_labels),
        hierarchical_labels=_dedupe(hierarchical_labels),
        sheet_pins=_dedupe(sheet_pins),
        generated=_dedupe(generated),
    )


def _all_alias_names(local_nets: Iterable[KiCadLocalNet]) -> set[str]:
    evidence = _combined_name_evidence(local_nets)
    names: set[str] = set()
    names.update(evidence.global_labels)
    names.update(evidence.power_symbols)
    names.update(evidence.local_labels)
    names.update(evidence.hierarchical_labels)
    names.update(evidence.sheet_pins)
    names.update(evidence.generated)
    return names


def _source_names(local_net: KiCadLocalNet) -> set[str]:
    names: set[str] = set()
    names.update(_global_label_names(local_net))
    names.update(_power_symbol_names(local_net))
    names.update(_local_label_names(local_net))
    names.update(_hierarchical_label_names(local_net))
    names.update(_sheet_pin_names(local_net))
    return names


def _global_label_names(local_net: KiCadLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(label.name) or "" for label in local_net.global_labels)


def _power_symbol_names(local_net: KiCadLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(symbol.name) or "" for symbol in local_net.power_symbols)


def _local_label_names(local_net: KiCadLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(label.name) or "" for label in local_net.local_labels)


def _hierarchical_label_names(local_net: KiCadLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(label.name) or "" for label in local_net.hierarchical_labels)


def _sheet_pin_names(local_net: KiCadLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(sheet_pin.name) or "" for sheet_pin in local_net.sheet_pins)


def _mergeable_name(name: str) -> str | None:
    cleaned = _clean_name(name)
    if bus_kind_for_name(cleaned) is not None:
        return None
    return cleaned or None


def _clean_name(name: str) -> str:
    return name.replace("\\", "").strip()


def _dedupe(names: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _pin_inputs(
    pin_occurrences: Iterable[KiCadPinOccurrence],
    component_ids_by_source_id: dict[str, str],
    component_source_ids_by_component_id: dict[str, list[str]],
) -> list[ResolvedPinInput]:
    result: list[ResolvedPinInput] = []
    seen_pin_occurrences: set[tuple[str, str]] = set()
    for pin_occurrence in pin_occurrences:
        component_id = _component_identity(pin_occurrence, component_ids_by_source_id)
        pin_id = f"{component_id}:pin:{pin_occurrence.pin_designator}"
        pin_occurrence_key = (pin_id, pin_occurrence.id)
        if pin_occurrence_key in seen_pin_occurrences:
            continue
        seen_pin_occurrences.add(pin_occurrence_key)
        pin_metadata = {"kicad_pin_source_id": pin_occurrence.id}
        set_pin_electrical(pin_metadata, KICAD_ELECTRICAL_MAP.get(pin_occurrence.pin_type))
        result.append(
            ResolvedPinInput(
                id=pin_occurrence.id,
                scope_id=pin_occurrence.scope_id,
                local_net_id=pin_occurrence.local_net_id,
                component_id=component_id,
                component_reference=pin_occurrence.component_reference,
                component_part=pin_occurrence.component_value,
                component_description=pin_occurrence.component_description,
                pin_id=pin_id,
                pin_designator=pin_occurrence.pin_designator,
                pin_name=_clean_name(pin_occurrence.pin_name),
                no_connect=pin_occurrence.no_connect,
                component_occurrence=ResolvedComponentOccurrenceInput(
                    source_id=pin_occurrence.component_source_id,
                    part_id=pin_occurrence.component_value,
                    x=pin_occurrence.component_x,
                    y=pin_occurrence.component_y,
                    rotation=pin_occurrence.component_rotation,
                    mirror=pin_occurrence.component_mirror,
                ),
                pin_metadata=pin_metadata,
                pin_occurrence_metadata={
                    "kicad_component_source_id": pin_occurrence.component_source_id,
                },
                component_metadata=_component_metadata(
                    pin_occurrence,
                    component_source_ids_by_component_id.get(component_id, []),
                ),
                component_info=pin_occurrence.component_info,
            )
        )
    return result


def _component_source_ids_by_component_id(
    pin_occurrences: Iterable[KiCadPinOccurrence],
    component_ids_by_source_id: dict[str, str],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    seen_by_component_id: dict[str, set[str]] = {}
    for pin_occurrence in pin_occurrences:
        source_id = pin_occurrence.component_source_id
        if not source_id:
            continue
        component_id = _component_identity(pin_occurrence, component_ids_by_source_id)
        seen = seen_by_component_id.setdefault(component_id, set())
        if source_id in seen:
            continue
        seen.add(source_id)
        result.setdefault(component_id, []).append(source_id)
    return result


def _component_ids_by_source_id(
    pin_occurrences: Iterable[KiCadPinOccurrence],
) -> dict[str, str]:
    pins = list(pin_occurrences)
    multi_unit_source_ids = _multi_unit_component_source_ids(pins)
    result: dict[str, str] = {}
    for pin_occurrence in pins:
        if pin_occurrence.component_source_id in result:
            continue
        if pin_occurrence.component_source_id in multi_unit_source_ids:
            result[pin_occurrence.component_source_id] = (
                "kicad:component:"
                f"{pin_occurrence.component_identity_source_id}:"
                f"{pin_occurrence.component_reference}"
            )
        elif pin_occurrence.component_source_id:
            result[pin_occurrence.component_source_id] = (
                f"kicad:component:{pin_occurrence.component_source_id}"
            )
    return result


def _multi_unit_component_source_ids(
    pin_occurrences: Iterable[KiCadPinOccurrence],
) -> set[str]:
    groups: dict[tuple[ScopeId, str, str], set[tuple[str, int]]] = {}
    for pin_occurrence in pin_occurrences:
        if (
            not pin_occurrence.component_identity_source_id
            or pin_occurrence.component_identity_source_id == pin_occurrence.component_source_id
        ):
            continue
        key = (
            pin_occurrence.scope_id,
            pin_occurrence.component_identity_source_id,
            pin_occurrence.component_reference,
        )
        groups.setdefault(key, set()).add(
            (pin_occurrence.component_source_id, pin_occurrence.component_unit)
        )

    source_ids: set[str] = set()
    for source_units in groups.values():
        sources = {source_id for source_id, _unit in source_units}
        units = {unit for _source_id, unit in source_units}
        if len(sources) > 1 and len(units) > 1:
            source_ids.update(sources)
    return source_ids


def _component_metadata(
    pin_occurrence: KiCadPinOccurrence,
    component_source_ids: list[str],
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if pin_occurrence.component_value:
        metadata["Value"] = pin_occurrence.component_value
    if pin_occurrence.component_footprint:
        metadata["Footprint"] = pin_occurrence.component_footprint
    if pin_occurrence.component_datasheet:
        metadata["Datasheet"] = pin_occurrence.component_datasheet
    # Every symbol property lands in the convenience dict; first occurrence
    # of a name wins. Reference stays a typed identity field only.
    if pin_occurrence.component_info is not None:
        for parameter in pin_occurrence.component_info.parameters:
            if parameter.name == "Reference" or not parameter.value:
                continue
            _ = metadata.setdefault(parameter.name, parameter.value)
    metadata.update(pin_occurrence.component_attr_metadata)
    if component_source_ids:
        metadata["kicad_component_source_ids"] = ",".join(component_source_ids)
    return metadata


def _component_identity(
    pin_occurrence: KiCadPinOccurrence,
    component_ids_by_source_id: dict[str, str],
) -> str:
    component_id = component_ids_by_source_id.get(pin_occurrence.component_source_id)
    if component_id:
        return component_id
    scope_key = _scope_key(pin_occurrence.scope_id)
    return f"kicad:component:{scope_key}:{pin_occurrence.component_reference}"


def _scope_key(scope_id: ScopeId) -> str:
    return "root" if not scope_id.path else "/".join(scope_id.path)
