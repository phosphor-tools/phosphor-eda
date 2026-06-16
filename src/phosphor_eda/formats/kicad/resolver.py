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
from phosphor_eda.domain.schematic import NetName, NetNameKind
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
from phosphor_eda.formats.kicad.lib_symbols import strip_kicad_markup

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.domain.schematic import Schematic, ScopeId
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.kicad.source import (
        KiCadBusEntry,
        KiCadBusLabel,
        KiCadLocalNet,
        KiCadPinOccurrence,
        KiCadPowerSymbol,
        KiCadSourceDesign,
    )


@dataclass(slots=True)
class _NameCandidate:
    name: str
    kind: NetNameKind
    scope: ScopeId | None
    source: str
    priority: int
    path_length: int = 0
    source_index: int = 0
    sheet_pin_direction: str = ""


@dataclass(frozen=True, slots=True)
class _NameDecision:
    name: str
    names: tuple[NetName, ...]


_GLOBAL_LABEL_PRIORITY = 7
_GLOBAL_POWER_PIN_PRIORITY = 6
_LOCAL_POWER_PIN_PRIORITY = 5
_LOCAL_LABEL_PRIORITY = 4
_HIERARCHICAL_LABEL_PRIORITY = 3
_SHEET_PIN_PRIORITY = 2
_PIN_PRIORITY = 1


def resolve_kicad_source(source: KiCadSourceDesign, ctx: ParseContext | None = None) -> Schematic:
    """Resolve a KiCad source design into the public schematic graph.

    Non-fatal issues accumulated on *ctx* are surfaced as
    ``parse_issue_count`` in the resulting schematic metadata.
    """
    pin_occurrences = list(source.pin_occurrences)
    pins_by_local_net_id = _pins_by_local_net_id(pin_occurrences)
    sheet_names_by_scope = {
        instance.scope_id: instance.sheet_name for instance in source.sheet_instances
    }
    component_ids_by_source_id = _component_ids_by_source_id(pin_occurrences)
    component_source_ids_by_component_id = _component_source_ids_by_component_id(
        pin_occurrences,
        component_ids_by_source_id,
    )
    local_nets_by_id = {local_net.id: local_net for local_net in source.local_nets}
    bus_labels_by_id = {label.id: label for label in source.bus_labels}
    _validate_source_refs(source, local_nets_by_id)
    net_union = NetUnion(local_net.id for local_net in source.local_nets)

    _merge_repeated_logical_pins(net_union, pin_occurrences, component_ids_by_source_id)
    _merge_same_scope_names(net_union, source.local_nets)
    _merge_global_labels(net_union, source.local_nets)
    _merge_power_symbols(net_union, source.local_nets)
    _merge_bus_entry_members(source, net_union, sheet_names_by_scope, bus_labels_by_id)
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
            pins_by_local_net_id,
            sheet_names_by_scope,
            bus_labels_by_id,
            source.schematic_version,
            net_index,
            root_id,
            group_local_nets,
        ),
        include_net=_include_kicad_net,
        metadata=metadata,
    )
    design.buses = build_buses_from_definitions(
        design,
        _kicad_bus_definitions(source, sheet_names_by_scope),
    )
    return design


def _kicad_bus_definitions(
    source: KiCadSourceDesign,
    sheet_names_by_scope: dict[ScopeId, str],
) -> list[BusDefinition]:
    definitions: list[BusDefinition] = []
    seen: set[tuple[str, str, ScopeId]] = set()
    bus_index = 0
    for label in source.bus_labels:
        aliases = _kicad_bus_aliases_for_scope(source, label.scope_id)
        name = _clean_name(label.name)
        kind = bus_kind_for_name(name, aliases=aliases)
        member_names = tuple(
            _compose_name(member_name, label.scope_id, sheet_names_by_scope, prepend_path=True)
            if label.kind != "global_label"
            else _escape_net_name(member_name)
            for member_name in expand_bus_members(name, aliases=aliases) or ()
        )
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
    ids_by_name: dict[tuple[ScopeId | None, str], list[str]] = {}
    for local_net in local_nets:
        for symbol in local_net.power_symbols:
            name = _mergeable_name(symbol.name)
            if name is not None:
                scope_key = symbol.scope_id if _is_local_power_symbol(symbol) else None
                ids_by_name.setdefault((scope_key, name), []).append(local_net.id)

    for net_ids in ids_by_name.values():
        _merge_ids(net_union, net_ids)


def _merge_bus_entry_members(
    source: KiCadSourceDesign,
    net_union: NetUnion,
    sheet_names_by_scope: dict[ScopeId, str],
    bus_labels_by_id: dict[str, KiCadBusLabel],
) -> None:
    ids_by_member: dict[str, list[str]] = {}
    for entry in source.bus_entries:
        member_name = _bus_entry_member_net_name(entry, bus_labels_by_id, sheet_names_by_scope)
        if member_name is not None:
            ids_by_member.setdefault(member_name, []).append(entry.local_net_id)

    for net_ids in ids_by_member.values():
        _merge_ids(net_union, net_ids)


def _is_local_power_symbol(symbol: KiCadPowerSymbol) -> bool:
    return symbol.power_kind == "local"


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
    attached_bus_entry_local_net_ids: dict[str, str] = {}
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
        for bus_entry in local_net.bus_entries:
            _validate_scoped_local_net_ref(
                kind="bus entry",
                id_=bus_entry.id,
                scope_id=bus_entry.scope_id,
                local_net_id=bus_entry.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
            attached_bus_entry_local_net_ids[bus_entry.id] = local_net.id
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
    for bus_entry in source.bus_entries:
        _validate_scoped_local_net_ref(
            kind="bus entry",
            id_=bus_entry.id,
            scope_id=bus_entry.scope_id,
            local_net_id=bus_entry.local_net_id,
            containing_local_net_id=bus_entry.local_net_id,
            scopes=scopes,
            local_nets_by_id=local_nets_by_id,
        )
        attached_local_net_id = attached_bus_entry_local_net_ids.get(bus_entry.id)
        if attached_local_net_id is None:
            msg = (
                f"bus entry {bus_entry.id!r} is not attached to local net "
                f"{bus_entry.local_net_id!r}"
            )
            raise ResolutionInputError(msg)
        if attached_local_net_id != bus_entry.local_net_id:
            msg = (
                f"bus entry {bus_entry.id!r} references local net "
                f"{bus_entry.local_net_id!r} but is attached to local net "
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
            directives=tuple(local_net.directives),
        )
        for local_net in local_nets
    ]


def _kicad_net_input_for_group(
    local_nets_by_id: dict[str, KiCadLocalNet],
    pins_by_local_net_id: dict[str, list[KiCadPinOccurrence]],
    sheet_names_by_scope: dict[ScopeId, str],
    bus_labels_by_id: dict[str, KiCadBusLabel],
    schematic_version: int,
    net_index: int,
    root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
) -> ResolvedNetInput:
    kicad_local_nets = [local_nets_by_id[local_net.id] for local_net in group_local_nets]
    decision = _select_kicad_net_names(
        kicad_local_nets,
        pins_by_local_net_id=pins_by_local_net_id,
        sheet_names_by_scope=sheet_names_by_scope,
        bus_labels_by_id=bus_labels_by_id,
        schematic_version=schematic_version,
        net_index=net_index,
    )
    return ResolvedNetInput(
        id=f"net:{net_index:04d}",
        name=decision.name,
        names=decision.names,
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
    return any(local_net.directives for local_net in group_local_nets) or any(
        pin.local_net_id in group_local_net_ids for pin in pins
    )


def select_kicad_net_name(local_nets: Iterable[KiCadLocalNet]) -> str:
    """Select a public net name using KiCad source-name priority."""
    decision = _select_kicad_net_names(
        tuple(local_nets),
        pins_by_local_net_id={},
        sheet_names_by_scope={},
        bus_labels_by_id={},
        schematic_version=20231120,
        net_index=0,
    )
    return decision.name


def _select_kicad_net_names(
    local_nets: Iterable[KiCadLocalNet],
    *,
    pins_by_local_net_id: dict[str, list[KiCadPinOccurrence]],
    sheet_names_by_scope: dict[ScopeId, str],
    bus_labels_by_id: dict[str, KiCadBusLabel],
    schematic_version: int,
    net_index: int,
) -> _NameDecision:
    nets = tuple(local_nets)
    label_candidates = _label_name_candidates(nets, sheet_names_by_scope, bus_labels_by_id)
    if label_candidates:
        candidates = _dedupe_candidates(label_candidates)
        canonical = min(candidates, key=_candidate_sort_key)
        return _NameDecision(
            name=canonical.name,
            names=tuple(_net_name(candidate) for candidate in candidates),
        )

    pin_candidates = _pin_name_candidates(
        nets,
        pins_by_local_net_id,
        schematic_version=schematic_version,
    )
    if pin_candidates:
        candidates = _dedupe_candidates(pin_candidates)
        canonical = min(candidates, key=_candidate_sort_key)
        return _NameDecision(
            name=canonical.name,
            names=tuple(_net_name(candidate) for candidate in candidates),
        )

    synthesized = _synthesized_name(nets, net_index)
    return _NameDecision(
        name=synthesized,
        names=(
            NetName(
                name=synthesized,
                kind=NetNameKind.SYNTHESIZED,
                scope=None,
                source="synthesized",
            ),
        ),
    )


def _label_name_candidates(
    local_nets: Iterable[KiCadLocalNet],
    sheet_names_by_scope: dict[ScopeId, str],
    bus_labels_by_id: dict[str, KiCadBusLabel],
) -> list[_NameCandidate]:
    candidates: list[_NameCandidate] = []
    for local_net in local_nets:
        for label in local_net.global_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                candidates.append(
                    _candidate(
                        name=_escape_net_name(name),
                        kind=NetNameKind.LABEL,
                        scope=label.scope_id,
                        source="global_label",
                        priority=_GLOBAL_LABEL_PRIORITY,
                        source_index=label.source_index,
                    )
                )
        for symbol in local_net.power_symbols:
            name = _mergeable_name(symbol.name)
            if name is not None:
                is_local_power = _is_local_power_symbol(symbol)
                candidates.append(
                    _candidate(
                        name=_compose_name(
                            name,
                            symbol.scope_id,
                            sheet_names_by_scope,
                            prepend_path=is_local_power,
                        ),
                        kind=NetNameKind.LABEL,
                        scope=symbol.scope_id,
                        source="power_symbol",
                        priority=(
                            _LOCAL_POWER_PIN_PRIORITY
                            if is_local_power
                            else _GLOBAL_POWER_PIN_PRIORITY
                        ),
                        source_index=symbol.source_index,
                    )
                )
        for label in local_net.local_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                candidates.append(
                    _candidate(
                        name=_compose_name(
                            name,
                            label.scope_id,
                            sheet_names_by_scope,
                            prepend_path=True,
                        ),
                        kind=NetNameKind.LABEL,
                        scope=label.scope_id,
                        source="local_label",
                        priority=_LOCAL_LABEL_PRIORITY,
                        source_index=label.source_index,
                    )
                )
        for label in local_net.hierarchical_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                candidates.append(
                    _candidate(
                        name=_compose_name(
                            name,
                            label.scope_id,
                            sheet_names_by_scope,
                            prepend_path=True,
                        ),
                        kind=NetNameKind.LABEL,
                        scope=label.scope_id,
                        source="hierarchical_label",
                        priority=_HIERARCHICAL_LABEL_PRIORITY,
                        source_index=label.source_index,
                    )
                )
        for sheet_pin in local_net.sheet_pins:
            name = _mergeable_name(sheet_pin.name)
            if name is not None:
                candidates.append(
                    _candidate(
                        name=_compose_name(
                            name,
                            sheet_pin.scope_id,
                            sheet_names_by_scope,
                            prepend_path=True,
                        ),
                        kind=NetNameKind.LABEL,
                        scope=sheet_pin.scope_id,
                        source="sheet_pin",
                        priority=_SHEET_PIN_PRIORITY,
                        source_index=sheet_pin.source_index,
                        sheet_pin_direction=sheet_pin.direction,
                    )
                )
        for bus_entry in local_net.bus_entries:
            name = _bus_entry_member_net_name(bus_entry, bus_labels_by_id, sheet_names_by_scope)
            if name is None:
                continue
            candidates.append(
                _candidate(
                    name=name,
                    kind=NetNameKind.LABEL,
                    scope=bus_entry.scope_id,
                    source="bus_entry",
                    priority=_SHEET_PIN_PRIORITY,
                    source_index=bus_entry.source_index,
                )
            )
    return candidates


def _bus_entry_member_net_name(
    entry: KiCadBusEntry,
    bus_labels_by_id: dict[str, KiCadBusLabel],
    sheet_names_by_scope: dict[ScopeId, str],
) -> str | None:
    if not entry.member_name:
        return None
    label = bus_labels_by_id.get(entry.member_label_id)
    if label is not None and label.kind == "global_label":
        return _escape_net_name(entry.member_name)
    return _compose_name(
        entry.member_name,
        entry.scope_id,
        sheet_names_by_scope,
        prepend_path=True,
    )


def _pin_name_candidates(
    local_nets: Iterable[KiCadLocalNet],
    pins_by_local_net_id: dict[str, list[KiCadPinOccurrence]],
    *,
    schematic_version: int,
) -> list[_NameCandidate]:
    pins = [pin for local_net in local_nets for pin in pins_by_local_net_id.get(local_net.id, [])]
    if not pins:
        return []

    force_unconnected = len(pins) == 1 or any(pin.no_connect for pin in pins)
    return [
        _candidate(
            name=_pin_default_net_name(
                pin,
                force_unconnected=force_unconnected or pin.no_connect,
                schematic_version=schematic_version,
            ),
            kind=NetNameKind.TOOL_AUTO,
            scope=None,
            source="pin",
            priority=_PIN_PRIORITY,
            source_index=pin.source_index,
        )
        for pin in pins
    ]


def _candidate(
    *,
    name: str,
    kind: NetNameKind,
    scope: ScopeId | None,
    source: str,
    priority: int,
    source_index: int = 0,
    sheet_pin_direction: str = "",
) -> _NameCandidate:
    return _NameCandidate(
        name=name,
        kind=kind,
        scope=scope,
        source=source,
        priority=priority,
        path_length=len(scope.path) if scope is not None else 0,
        source_index=source_index,
        sheet_pin_direction=sheet_pin_direction,
    )


def _candidate_sort_key(candidate: _NameCandidate) -> tuple[int, int, int, int, str, int]:
    sheet_pin_rank = 0 if candidate.sheet_pin_direction.lower() == "output" else 1
    pad_rank = 1 if "-Pad" in candidate.name else 0
    return (
        -candidate.priority,
        pad_rank,
        sheet_pin_rank,
        candidate.path_length,
        candidate.name,
        candidate.source_index,
    )


def _net_name(candidate: _NameCandidate) -> NetName:
    return NetName(
        name=candidate.name,
        kind=candidate.kind,
        scope=candidate.scope,
        source=candidate.source,
    )


def _dedupe_candidates(candidates: Iterable[_NameCandidate]) -> list[_NameCandidate]:
    result: list[_NameCandidate] = []
    seen: set[tuple[str, NetNameKind, ScopeId | None, str]] = set()
    for candidate in candidates:
        key = (candidate.name, candidate.kind, candidate.scope, candidate.source)
        if candidate.name and key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _compose_name(
    name: str,
    scope_id: ScopeId,
    sheet_names_by_scope: dict[ScopeId, str],
    *,
    prepend_path: bool,
) -> str:
    escaped_name = _escape_net_name(name)
    if not prepend_path:
        return escaped_name
    return f"{_sheet_path_prefix(scope_id, sheet_names_by_scope)}{escaped_name}"


def _sheet_path_prefix(scope_id: ScopeId, sheet_names_by_scope: dict[ScopeId, str]) -> str:
    if not scope_id.path:
        return "/"
    names: list[str] = []
    for index in range(1, len(scope_id.path) + 1):
        ancestor = type(scope_id)(path=scope_id.path[:index])
        sheet_name = sheet_names_by_scope.get(ancestor) or scope_id.path[index - 1]
        names.append(_escape_net_name(sheet_name))
    return "/" + "/".join(names) + "/"


def _escape_net_name(name: str) -> str:
    return _clean_name(name).replace("\r", "").replace("\n", "").replace("/", "{slash}")


def _pin_default_net_name(
    pin: KiCadPinOccurrence,
    *,
    force_unconnected: bool,
    schematic_version: int,
) -> str:
    prefix = "unconnected-(" if force_unconnected else "Net-("
    ref = _escape_net_name(pin.component_reference)
    pin_name = _escape_net_name(pin.pin_net_name)
    pad = _escape_net_name(pin.pin_designator)

    if force_unconnected:
        if (
            _unconnected_uses_pin_name_without_pad(schematic_version)
            and pin_name
            and pin_name != pad
        ):
            return f"{prefix}{ref}{_unit_suffix(pin)}-{pin_name})"
        if _unconnected_uses_pin_name_with_pad(schematic_version) and pin_name and pin_name != pad:
            return f"{prefix}{ref}{_unit_suffix(pin)}-{pin_name}-Pad{pad})"
        return f"{prefix}{ref}-Pad{pad})"

    if pin_name and pin_name != pad:
        return f"{prefix}{ref}{_unit_suffix(pin)}-{pin_name})"
    return f"{prefix}{ref}-Pad{pad})"


def _unit_suffix(pin: KiCadPinOccurrence) -> str:
    if not pin.component_has_multiple_units:
        return ""
    if pin.component_unit <= 0:
        return ""
    if pin.component_unit <= 26:
        return chr(ord("A") + pin.component_unit - 1)
    return str(pin.component_unit)


def _unconnected_uses_pin_name_without_pad(schematic_version: int) -> bool:
    return 20230121 <= schematic_version < 20231120


def _unconnected_uses_pin_name_with_pad(schematic_version: int) -> bool:
    return schematic_version >= 20231120


def _synthesized_name(local_nets: Iterable[KiCadLocalNet], net_index: int) -> str:
    for local_net in local_nets:
        name = _clean_name(local_net.generated_name)
        if name:
            return name
    return f"__kicad_net_{net_index:04d}"


def _source_names(local_net: KiCadLocalNet) -> set[str]:
    names: set[str] = set()
    names.update(_global_label_names(local_net))
    names.update(_power_symbol_names(local_net))
    names.update(_local_label_names(local_net))
    names.update(_hierarchical_label_names(local_net))
    names.update(_sheet_pin_names(local_net))
    names.update(_bus_entry_names(local_net))
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


def _bus_entry_names(local_net: KiCadLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(entry.member_name) or "" for entry in local_net.bus_entries)


def _mergeable_name(name: str) -> str | None:
    cleaned = _clean_name(name)
    if _is_bus_name(cleaned):
        return None
    return cleaned or None


def _is_bus_name(name: str) -> bool:
    if "${" in name:
        return False
    return bus_kind_for_name(strip_kicad_markup(name)) is not None


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


def _pins_by_local_net_id(
    pin_occurrences: Iterable[KiCadPinOccurrence],
) -> dict[str, list[KiCadPinOccurrence]]:
    result: dict[str, list[KiCadPinOccurrence]] = {}
    for pin_occurrence in pin_occurrences:
        result.setdefault(pin_occurrence.local_net_id, []).append(pin_occurrence)
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
