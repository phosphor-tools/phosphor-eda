"""Resolve Altium-native source connectivity into the public schematic model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.formats.altium._helpers import parse_bus_notation
from phosphor_eda.formats.altium.project import AltiumHierarchyMode
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

    from phosphor_eda.formats.altium.source import (
        AltiumLocalNet,
        AltiumPinOccurrence,
        AltiumSheetEntry,
        AltiumSheetSource,
        AltiumSheetSymbol,
        AltiumSourceDesign,
    )
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.domain.schematic import Schematic, ScopeId


@dataclass(slots=True)
class _LocalNetRef:
    sheet: AltiumSheetSource
    local_net: AltiumLocalNet


@dataclass(slots=True)
class _NameEvidence:
    labels: list[str]
    powers: list[str]
    ports: list[str]
    sheet_entries: list[str]
    harness_members: list[str]
    generated: str


def resolve_altium_source(source: AltiumSourceDesign, ctx: ParseContext | None = None) -> Schematic:
    """Resolve an Altium source design into the public schematic graph.

    Non-fatal issues accumulated on *ctx* are surfaced as
    ``parse_issue_count`` in the resulting schematic metadata.
    """
    local_refs = _collect_local_refs(source)
    net_union = NetUnion(ref.local_net.id for ref in local_refs)
    local_net_by_id = {ref.local_net.id: ref for ref in local_refs}
    pin_occurrences = _collect_pin_occurrences(source)
    effective_mode = _effective_hierarchy_mode(source)
    _validate_source_refs(source, local_net_by_id)

    _merge_repeated_logical_pins(net_union, pin_occurrences)
    _merge_source_names(source, local_refs, net_union, effective_mode)
    _merge_hierarchy(source, local_refs, local_net_by_id, net_union, effective_mode)

    component_source_ids_by_component_id = _component_source_ids_by_component_id(pin_occurrences)

    metadata = {
        "altium_hierarchy_mode": source.project.hierarchy_mode.name,
        "altium_effective_hierarchy_mode": effective_mode.name,
    }
    if ctx is not None and ctx.issues:
        metadata["parse_issue_count"] = str(len(ctx.issues))

    return build_resolved_schematic(
        name=source.name,
        pages=_page_inputs(source),
        local_nets=_local_net_inputs(local_refs),
        pins=_pin_inputs(pin_occurrences, component_source_ids_by_component_id),
        net_union=net_union,
        net_factory=lambda net_index, root_id, group_local_nets: _altium_net_input_for_group(
            source,
            local_net_by_id,
            net_index,
            root_id,
            group_local_nets,
        ),
        include_net=_include_altium_net,
        metadata=metadata,
    )


def _collect_local_refs(source: AltiumSourceDesign) -> list[_LocalNetRef]:
    refs: list[_LocalNetRef] = []
    for sheet in source.sheets.values():
        for local_net in sheet.local_nets:
            refs.append(_LocalNetRef(sheet=sheet, local_net=local_net))
    return refs


def _collect_pin_occurrences(source: AltiumSourceDesign) -> list[AltiumPinOccurrence]:
    occurrences: list[AltiumPinOccurrence] = []
    for sheet in source.sheets.values():
        occurrences.extend(sheet.pin_occurrences)
    return occurrences


def _effective_hierarchy_mode(source: AltiumSourceDesign) -> AltiumHierarchyMode:
    mode = source.project.hierarchy_mode
    if mode is not AltiumHierarchyMode.SMART:
        return mode

    root_sheet = source.sheets.get(source.root_sheet_name)
    if root_sheet is not None and root_sheet.sheet_entries:
        return AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL

    for sheet in source.sheets.values():
        for local_net in sheet.local_nets:
            if local_net.ports:
                return AltiumHierarchyMode.FLAT

    return AltiumHierarchyMode.GLOBAL


def _merge_repeated_logical_pins(
    net_union: NetUnion,
    pin_occurrences: Iterable[AltiumPinOccurrence],
) -> None:
    net_ids_by_pin: dict[tuple[str, str], list[str]] = {}
    for pin_occurrence in pin_occurrences:
        if not pin_occurrence.local_net_id:
            continue
        key = (
            _source_component_identity(pin_occurrence),
            pin_occurrence.pin_designator,
        )
        net_ids_by_pin.setdefault(key, []).append(pin_occurrence.local_net_id)

    for net_ids in net_ids_by_pin.values():
        first_net_id = net_ids[0]
        for net_id in net_ids[1:]:
            _ = net_union.union(first_net_id, net_id)


def _merge_source_names(
    source: AltiumSourceDesign,
    local_refs: Iterable[_LocalNetRef],
    net_union: NetUnion,
    effective_mode: AltiumHierarchyMode,
) -> None:
    if effective_mode in (
        AltiumHierarchyMode.FLAT,
        AltiumHierarchyMode.GLOBAL,
        AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
    ):
        _merge_by_source_name(net_union, _power_name_ids(local_refs))

    if effective_mode in (AltiumHierarchyMode.FLAT, AltiumHierarchyMode.GLOBAL):
        _merge_by_source_name(net_union, _port_name_ids(local_refs))

    if effective_mode is AltiumHierarchyMode.GLOBAL:
        _merge_by_source_name(net_union, _label_name_ids(local_refs))

    _ = source


def _merge_hierarchy(
    source: AltiumSourceDesign,
    local_refs: Iterable[_LocalNetRef],
    local_net_by_id: dict[str, _LocalNetRef],
    net_union: NetUnion,
    effective_mode: AltiumHierarchyMode,
) -> None:
    if effective_mode not in (
        AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        AltiumHierarchyMode.HIERARCHICAL_POWER_LOCAL,
    ):
        return

    child_sheets_by_file = _child_sheets_by_source_file(source)
    repeated_child_files = _repeated_child_source_files(source)
    child_port_nets = _child_port_net_ids(source)
    for ref in local_refs:
        for entry in ref.local_net.sheet_entries:
            entry_name = _mergeable_name(entry.name)
            if entry_name is None:
                continue
            sheet_symbol = next(
                (
                    symbol
                    for symbol in ref.sheet.sheet_symbols
                    if symbol.id == entry.sheet_symbol_id
                ),
                None,
            )
            if sheet_symbol is None:
                continue
            child_sheets = _child_sheets_for_symbol(
                child_sheets_by_file,
                repeated_child_files,
                sheet_symbol,
            )
            for child_sheet in child_sheets:
                for child_net_id in child_port_nets.get((child_sheet.id, entry_name), []):
                    if child_net_id in local_net_by_id:
                        _ = net_union.union(ref.local_net.id, child_net_id)


def _child_sheets_by_source_file(
    source: AltiumSourceDesign,
) -> dict[str, list[AltiumSheetSource]]:
    result: dict[str, list[AltiumSheetSource]] = {}
    for sheet in source.sheets.values():
        result.setdefault(_source_file_key(sheet.source_file), []).append(sheet)
        result.setdefault(sheet.name, []).append(sheet)
    return result


def _repeated_child_source_files(source: AltiumSourceDesign) -> set[str]:
    symbol_counts: dict[str, int] = {}
    for sheet in source.sheets.values():
        for symbol in sheet.sheet_symbols:
            if symbol.child_source_file:
                child_source_file = _source_file_key(symbol.child_source_file)
                symbol_counts[child_source_file] = symbol_counts.get(child_source_file, 0) + 1
    return {child_source_file for child_source_file, count in symbol_counts.items() if count > 1}


def _child_sheets_for_symbol(
    child_sheets_by_file: dict[str, list[AltiumSheetSource]],
    repeated_child_files: set[str],
    sheet_symbol: AltiumSheetSymbol,
) -> list[AltiumSheetSource]:
    child_sheets = child_sheets_by_file.get(_source_file_key(sheet_symbol.child_source_file), [])
    instance_scoped = [
        sheet
        for sheet in child_sheets
        if _scope_matches_sheet_symbol(sheet.scope_id.path, sheet_symbol)
    ]
    if instance_scoped:
        return instance_scoped
    if _source_file_key(sheet_symbol.child_source_file) in repeated_child_files:
        return []
    return child_sheets


def _scope_matches_sheet_symbol(
    scope_path: tuple[str, ...],
    sheet_symbol: AltiumSheetSymbol,
) -> bool:
    return sheet_symbol.id in scope_path or (
        bool(sheet_symbol.name) and sheet_symbol.name in scope_path
    )


def _child_port_net_ids(source: AltiumSourceDesign) -> dict[tuple[str, str], list[str]]:
    result: dict[tuple[str, str], list[str]] = {}
    for sheet in source.sheets.values():
        for local_net in sheet.local_nets:
            for name in _port_names(local_net):
                result.setdefault((sheet.id, name), []).append(local_net.id)
    return result


def _merge_by_source_name(net_union: NetUnion, ids_by_name: dict[str, list[str]]) -> None:
    for ids in ids_by_name.values():
        if len(ids) < 2:
            continue
        first_id = ids[0]
        for other_id in ids[1:]:
            _ = net_union.union(first_id, other_id)


def _label_name_ids(local_refs: Iterable[_LocalNetRef]) -> dict[str, list[str]]:
    ids_by_name: dict[str, list[str]] = {}
    for ref in local_refs:
        for name in _label_names(ref.local_net):
            ids_by_name.setdefault(name, []).append(ref.local_net.id)
    return ids_by_name


def _power_name_ids(local_refs: Iterable[_LocalNetRef]) -> dict[str, list[str]]:
    ids_by_name: dict[str, list[str]] = {}
    for ref in local_refs:
        for name in _power_names(ref.local_net):
            ids_by_name.setdefault(name, []).append(ref.local_net.id)
    return ids_by_name


def _port_name_ids(local_refs: Iterable[_LocalNetRef]) -> dict[str, list[str]]:
    ids_by_name: dict[str, list[str]] = {}
    for ref in local_refs:
        for name in _port_names(ref.local_net):
            ids_by_name.setdefault(name, []).append(ref.local_net.id)
    return ids_by_name


def _validate_source_refs(
    source: AltiumSourceDesign,
    local_net_by_id: dict[str, _LocalNetRef],
) -> None:
    scopes = {sheet.scope_id for sheet in source.sheets.values()}
    for ref in local_net_by_id.values():
        if ref.local_net.scope_id not in scopes:
            msg = (
                f"local net {ref.local_net.id!r} references unknown scope {ref.local_net.scope_id}"
            )
            raise ResolutionInputError(msg)
        _validate_local_evidence(ref, scopes)

    for sheet in source.sheets.values():
        sheet_symbol_ids = {symbol.id for symbol in sheet.sheet_symbols}
        for entry in sheet.sheet_entries:
            _validate_sheet_entry_ref(
                entry=entry,
                sheet=sheet,
                scopes=scopes,
                sheet_symbol_ids=sheet_symbol_ids,
            )

    for pin in _collect_pin_occurrences(source):
        _validate_pin_ref(pin, scopes, local_net_by_id)


def _validate_local_evidence(ref: _LocalNetRef, scopes: set[ScopeId]) -> None:
    sheet_symbol_ids = {symbol.id for symbol in ref.sheet.sheet_symbols}
    for label in ref.local_net.net_labels:
        _validate_scoped_source_ref("net label", label.id, label.scope_id, ref, scopes)
    for power_port in ref.local_net.power_ports:
        _validate_scoped_source_ref("power port", power_port.id, power_port.scope_id, ref, scopes)
    for port in ref.local_net.ports:
        _validate_scoped_source_ref("port", port.id, port.scope_id, ref, scopes)
    for entry in ref.local_net.sheet_entries:
        _validate_scoped_source_ref("sheet entry", entry.id, entry.scope_id, ref, scopes)
        if entry.sheet_symbol_id and entry.sheet_symbol_id not in sheet_symbol_ids:
            msg = (
                f"sheet entry {entry.id!r} references unknown sheet symbol "
                f"{entry.sheet_symbol_id!r}"
            )
            raise ResolutionInputError(msg)
    for member in ref.local_net.harness_members:
        _validate_scoped_source_ref("harness member", member.id, member.scope_id, ref, scopes)


def _validate_scoped_source_ref(
    kind: str,
    id_: str,
    scope_id: ScopeId,
    ref: _LocalNetRef,
    scopes: set[ScopeId],
) -> None:
    if scope_id not in scopes:
        msg = f"{kind} {id_!r} references unknown scope {scope_id}"
        raise ResolutionInputError(msg)
    if scope_id != ref.local_net.scope_id:
        msg = (
            f"{kind} {id_!r} scope {scope_id} does not match "
            f"local net {ref.local_net.id!r} scope {ref.local_net.scope_id}"
        )
        raise ResolutionInputError(msg)


def _validate_sheet_entry_ref(
    *,
    entry: AltiumSheetEntry,
    sheet: AltiumSheetSource,
    scopes: set[ScopeId],
    sheet_symbol_ids: set[str],
) -> None:
    if entry.scope_id not in scopes:
        msg = f"sheet entry {entry.id!r} references unknown scope {entry.scope_id}"
        raise ResolutionInputError(msg)
    if entry.scope_id != sheet.scope_id:
        msg = (
            f"sheet entry {entry.id!r} scope {entry.scope_id} does not match "
            f"sheet {sheet.id!r} scope {sheet.scope_id}"
        )
        raise ResolutionInputError(msg)
    if entry.sheet_symbol_id and entry.sheet_symbol_id not in sheet_symbol_ids:
        msg = f"sheet entry {entry.id!r} references unknown sheet symbol {entry.sheet_symbol_id!r}"
        raise ResolutionInputError(msg)


def _validate_pin_ref(
    pin: AltiumPinOccurrence,
    scopes: set[ScopeId],
    local_net_by_id: dict[str, _LocalNetRef],
) -> None:
    if pin.scope_id not in scopes:
        msg = f"pin {pin.id!r} references unknown scope {pin.scope_id}"
        raise ResolutionInputError(msg)
    ref = local_net_by_id.get(pin.local_net_id)
    if ref is None:
        msg = f"pin {pin.id!r} references unknown local net {pin.local_net_id!r}"
        raise ResolutionInputError(msg)
    if ref.local_net.scope_id != pin.scope_id:
        msg = (
            f"pin {pin.id!r} scope {pin.scope_id} does not match "
            f"local net {pin.local_net_id!r} scope {ref.local_net.scope_id}"
        )
        raise ResolutionInputError(msg)


def _page_inputs(source: AltiumSourceDesign) -> list[ResolvedPageInput]:
    return [
        ResolvedPageInput(
            id=sheet.id,
            name=sheet.name,
            source_file=sheet.source_file,
            scope_id=sheet.scope_id,
        )
        for sheet in source.sheets.values()
    ]


def _local_net_inputs(local_refs: Iterable[_LocalNetRef]) -> list[ResolvedLocalNetInput]:
    return [
        ResolvedLocalNetInput(
            id=ref.local_net.id,
            scope_id=ref.local_net.scope_id,
            source_names=frozenset(_all_source_names([ref])),
        )
        for ref in local_refs
    ]


def _altium_net_input_for_group(
    source: AltiumSourceDesign,
    local_net_by_id: dict[str, _LocalNetRef],
    net_index: int,
    root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
) -> ResolvedNetInput:
    refs = [local_net_by_id[local_net.id] for local_net in group_local_nets]
    name = _select_net_name(source, refs)
    aliases = _all_source_names(refs)
    aliases.discard(name)
    return ResolvedNetInput(
        id=f"net:{net_index:04d}",
        name=name,
        aliases=frozenset(aliases),
        metadata={
            "altium_root_local_net_id": root_id,
        },
    )


def _include_altium_net(
    _root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
    pins: tuple[ResolvedPinInput, ...],
) -> bool:
    group_local_net_ids = {local_net.id for local_net in group_local_nets}
    return any(pin.local_net_id in group_local_net_ids for pin in pins)


def _pin_inputs(
    pin_occurrences: Iterable[AltiumPinOccurrence],
    component_source_ids_by_component_id: dict[str, list[str]],
) -> list[ResolvedPinInput]:
    result: list[ResolvedPinInput] = []
    seen_pin_occurrences: set[tuple[str, str]] = set()
    for pin_occurrence in pin_occurrences:
        component_id = _component_identity(pin_occurrence)
        pin_id = f"{component_id}:pin:{pin_occurrence.pin_designator}"
        pin_occurrence_key = (pin_id, pin_occurrence.id)
        if pin_occurrence_key in seen_pin_occurrences:
            continue
        seen_pin_occurrences.add(pin_occurrence_key)
        result.append(
            ResolvedPinInput(
                id=pin_occurrence.id,
                scope_id=pin_occurrence.scope_id,
                local_net_id=pin_occurrence.local_net_id,
                component_id=component_id,
                component_reference=pin_occurrence.component_reference,
                component_part=pin_occurrence.component_part,
                component_description=pin_occurrence.component_description,
                pin_id=pin_id,
                pin_designator=pin_occurrence.pin_designator,
                pin_name=_clean_name(pin_occurrence.pin_name),
                no_connect=pin_occurrence.no_connect,
                component_occurrence=ResolvedComponentOccurrenceInput(
                    source_id=_component_occurrence_source_id(pin_occurrence),
                    part_id=pin_occurrence.component_part_id,
                    metadata=_component_occurrence_metadata(pin_occurrence),
                ),
                pin_metadata={
                    "altium_pin_source_id": pin_occurrence.id,
                },
                pin_occurrence_metadata=_pin_occurrence_metadata(pin_occurrence),
                component_metadata=_component_metadata_for_pin(
                    pin_occurrence,
                    component_source_ids_by_component_id.get(component_id, []),
                ),
            )
        )
    return result


def _source_file_key(source_file: str) -> str:
    return source_file.replace("\\", "/")


def _select_net_name(source: AltiumSourceDesign, refs: Iterable[_LocalNetRef]) -> str:
    evidence = _combined_name_evidence(refs)
    priority_groups: list[list[str]]
    if source.project.power_port_names_take_priority:
        priority_groups = [
            evidence.powers,
            evidence.labels,
            evidence.ports if source.project.allow_port_net_names else [],
            evidence.sheet_entries if source.project.allow_sheet_entry_net_names else [],
            evidence.harness_members,
        ]
    else:
        priority_groups = [
            evidence.labels,
            evidence.powers,
            evidence.ports if source.project.allow_port_net_names else [],
            evidence.sheet_entries if source.project.allow_sheet_entry_net_names else [],
            evidence.harness_members,
        ]

    for names in priority_groups:
        for name in names:
            if name:
                return name
    if evidence.generated:
        return evidence.generated
    return "__auto_net"


def _combined_name_evidence(refs: Iterable[_LocalNetRef]) -> _NameEvidence:
    labels: list[str] = []
    powers: list[str] = []
    ports: list[str] = []
    sheet_entries: list[str] = []
    harness_members: list[str] = []
    generated = ""

    for ref in refs:
        labels.extend(_label_names(ref.local_net))
        powers.extend(_power_names(ref.local_net))
        ports.extend(_port_names(ref.local_net))
        sheet_entries.extend(_sheet_entry_names(ref.local_net))
        harness_members.extend(_harness_member_names(ref.local_net))
        if not generated:
            generated = _clean_name(ref.local_net.generated_name)

    return _NameEvidence(
        labels=_dedupe(labels),
        powers=_dedupe(powers),
        ports=_dedupe(ports),
        sheet_entries=_dedupe(sheet_entries),
        harness_members=_dedupe(harness_members),
        generated=generated,
    )


def _all_source_names(refs: Iterable[_LocalNetRef]) -> set[str]:
    names: set[str] = set()
    for ref in refs:
        names.update(_label_names(ref.local_net))
        names.update(_power_names(ref.local_net))
        names.update(_port_names(ref.local_net))
        names.update(_sheet_entry_names(ref.local_net))
        names.update(_harness_member_names(ref.local_net))
    return names


def _label_names(local_net: AltiumLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(label.name) or "" for label in local_net.net_labels)


def _power_names(local_net: AltiumLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(power_port.name) or "" for power_port in local_net.power_ports)


def _port_names(local_net: AltiumLocalNet) -> list[str]:
    names: list[str] = []
    for port in local_net.ports:
        if port.harness_type:
            continue
        name = _mergeable_name(port.name)
        if name is not None:
            names.append(name)
    return _dedupe(names)


def _sheet_entry_names(local_net: AltiumLocalNet) -> list[str]:
    names: list[str] = []
    for entry in local_net.sheet_entries:
        if entry.harness_type:
            continue
        name = _mergeable_name(entry.name)
        if name is not None:
            names.append(name)
    return _dedupe(names)


def _harness_member_names(local_net: AltiumLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(member.name) or "" for member in local_net.harness_members)


def _mergeable_name(name: str) -> str | None:
    cleaned = _clean_name(name)
    if not cleaned:
        return None
    if parse_bus_notation(cleaned) is not None:
        return None
    return cleaned


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


def _component_identity(pin_occurrence: AltiumPinOccurrence) -> str:
    source_id = pin_occurrence.component_source_id
    if source_id:
        return source_id
    occurrence_source_id = _component_occurrence_source_id(pin_occurrence)
    if occurrence_source_id:
        return f"{pin_occurrence.scope_id}:{occurrence_source_id}"
    return f"{pin_occurrence.scope_id}:pin-owner:{pin_occurrence.id}"


def _source_component_identity(pin_occurrence: AltiumPinOccurrence) -> str:
    source_id = pin_occurrence.component_source_id
    if source_id:
        return source_id
    occurrence_source_id = _component_occurrence_source_id(pin_occurrence)
    if occurrence_source_id:
        return f"{pin_occurrence.scope_id}:{occurrence_source_id}"
    return f"{pin_occurrence.scope_id}:pin-owner:{pin_occurrence.id}"


def _component_occurrence_source_id(pin_occurrence: AltiumPinOccurrence) -> str:
    if pin_occurrence.component_occurrence_source_id:
        return pin_occurrence.component_occurrence_source_id
    return pin_occurrence.component_source_id


def _component_occurrence_metadata(pin_occurrence: AltiumPinOccurrence) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if pin_occurrence.component_source_id:
        metadata["altium_component_source_id"] = pin_occurrence.component_source_id
    return metadata


def _pin_occurrence_metadata(pin_occurrence: AltiumPinOccurrence) -> dict[str, str]:
    metadata: dict[str, str] = {}
    component_occurrence_source_id = _component_occurrence_source_id(pin_occurrence)
    if component_occurrence_source_id:
        metadata["altium_component_occurrence_source_id"] = component_occurrence_source_id
    if pin_occurrence.component_source_id:
        metadata["altium_component_source_id"] = pin_occurrence.component_source_id
    return metadata


def _component_source_ids_by_component_id(
    pin_occurrences: Iterable[AltiumPinOccurrence],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    seen_by_component_id: dict[str, set[str]] = {}
    for pin_occurrence in pin_occurrences:
        source_id = pin_occurrence.component_source_id
        if not source_id:
            continue
        component_id = _component_identity(pin_occurrence)
        seen = seen_by_component_id.setdefault(component_id, set())
        if source_id in seen:
            continue
        seen.add(source_id)
        result.setdefault(component_id, []).append(source_id)
    return result


def _component_metadata_for_pin(
    pin_occurrence: AltiumPinOccurrence,
    component_source_ids: list[str],
) -> dict[str, str]:
    metadata = dict(pin_occurrence.component_metadata)
    if component_source_ids:
        metadata["altium_component_source_ids"] = ",".join(component_source_ids)
    return metadata
