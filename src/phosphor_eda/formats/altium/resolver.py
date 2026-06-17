"""Resolve Altium-native source connectivity into the public schematic model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.domain.buses import (
    BusDefinition,
    build_buses_from_definitions,
    bus_kind_for_name,
    expand_bus_members,
)
from phosphor_eda.domain.schematic import Bus, BusKind, NetName, NetNameKind
from phosphor_eda.formats.altium._helpers import parse_bus_notation
from phosphor_eda.formats.altium.project import AltiumHierarchyMode
from phosphor_eda.formats.common.net_union import NetUnion
from phosphor_eda.formats.common.paths import resolve_document_reference
from phosphor_eda.formats.common.resolved_graph import (
    ResolutionInputError,
    ResolvedComponentOccurrenceInput,
    ResolvedLocalNetInput,
    ResolvedNetInput,
    ResolvedPageInput,
    ResolvedPinInput,
    build_resolved_schematic,
)
from phosphor_eda.formats.common.spatial import UnionFind

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.domain.schematic import Net, Schematic, ScopeId
    from phosphor_eda.formats.altium.annotation import AnnotationDesignator
    from phosphor_eda.formats.altium.project import AltiumProject
    from phosphor_eda.formats.altium.source import (
        AltiumLocalNet,
        AltiumNetLabel,
        AltiumPinOccurrence,
        AltiumSheetEntry,
        AltiumSheetSource,
        AltiumSheetSymbol,
        AltiumSourceDesign,
    )
    from phosphor_eda.formats.common.diagnostics import ParseContext


@dataclass(slots=True)
class _LocalNetRef:
    sheet: AltiumSheetSource
    local_net: AltiumLocalNet


@dataclass(frozen=True, slots=True)
class _NameCandidate:
    """One piece of net-name evidence, in document order."""

    name: str
    kind: NetNameKind
    scope: ScopeId | None
    source: str


@dataclass(slots=True)
class _NameEvidence:
    """Per-tier name candidates collected across a resolved net's local nets."""

    labels: list[_NameCandidate]
    powers: list[_NameCandidate]
    sheet_entries: list[_NameCandidate]
    ports: list[_NameCandidate]
    harness_members: list[_NameCandidate]
    extra_names: list[_NameCandidate]


def resolve_altium_source(source: AltiumSourceDesign, ctx: ParseContext | None = None) -> Schematic:
    """Resolve an Altium source design into the public schematic graph.

    Non-fatal issues accumulated on *ctx* are surfaced as
    ``parse_issue_count`` in the resulting schematic metadata.
    """
    _warn_unverified_naming_options(source.project, ctx)
    local_refs = _collect_local_refs(source)
    net_union = NetUnion(ref.local_net.id for ref in local_refs)
    local_net_by_id = {ref.local_net.id: ref for ref in local_refs}
    pin_occurrences = _collect_pin_occurrences(source)
    effective_mode = _effective_hierarchy_mode(source)
    _validate_source_refs(source, local_net_by_id)

    _merge_repeated_logical_pins(net_union, pin_occurrences)
    _merge_source_names(source, local_refs, net_union, effective_mode)
    _merge_hierarchy(source, local_refs, local_net_by_id, net_union, effective_mode, ctx)

    component_source_ids_by_component_id = _component_source_ids_by_component_id(pin_occurrences)
    members_by_local_net = _net_members_by_local_net(pin_occurrences)

    metadata = {
        "altium_hierarchy_mode": source.project.hierarchy_mode.name,
        "altium_effective_hierarchy_mode": effective_mode.name,
    }
    if ctx is not None and ctx.issues:
        metadata["parse_issue_count"] = str(len(ctx.issues))

    symbol_uid_by_id = {
        symbol.id: symbol.unique_id
        for sheet in source.sheets.values()
        for symbol in sheet.sheet_symbols
        if symbol.unique_id
    }

    design = build_resolved_schematic(
        name=source.name,
        pages=_page_inputs(source),
        local_nets=_local_net_inputs(local_refs),
        pins=_pin_inputs(
            pin_occurrences,
            component_source_ids_by_component_id,
            source.physical_designators,
            symbol_uid_by_id,
        ),
        net_union=net_union,
        net_factory=lambda net_index, root_id, group_local_nets: _altium_net_input_for_group(
            source,
            local_net_by_id,
            members_by_local_net,
            net_index,
            root_id,
            group_local_nets,
        ),
        include_net=_include_altium_net,
        metadata=metadata,
    )
    design.buses = _altium_buses(source, design)
    return design


def _altium_buses(source: AltiumSourceDesign, design: Schematic) -> list[Bus]:
    buses = build_buses_from_definitions(design, _altium_bus_definitions(source))
    buses.extend(_altium_harness_buses(source, design))
    return buses


def _altium_bus_definitions(source: AltiumSourceDesign) -> list[BusDefinition]:
    definitions: list[BusDefinition] = []
    seen: set[tuple[BusKind, str]] = set()
    bus_index = 0
    for sheet in source.sheets.values():
        for local_net in sheet.local_nets:
            sources: list[tuple[str, str, str]] = []
            sources.extend((label.id, label.name, label.kind) for label in local_net.net_labels)
            sources.extend((port.id, port.name, port.kind) for port in local_net.ports)
            sources.extend((entry.id, entry.name, entry.kind) for entry in local_net.sheet_entries)
            for source_id, raw_name, source_kind in sources:
                name = _clean_name(raw_name)
                kind = bus_kind_for_name(name)
                if kind is None or (kind, name) in seen:
                    continue
                seen.add((kind, name))
                member_names = tuple(expand_bus_members(name) or ())
                if not member_names:
                    continue
                bus_index += 1
                definitions.append(
                    BusDefinition(
                        id=f"altium:bus:{kind.value}:{bus_index:04d}",
                        name=name,
                        kind=kind,
                        member_names=member_names,
                        metadata={
                            "source_format": "altium",
                            "source_id": source_id,
                            "source_kind": source_kind,
                            "source_sheet": sheet.name,
                        },
                    )
                )
        for bus_line in sheet.generic_bus_lines:
            name = _clean_name(bus_line.name)
            kind = bus_kind_for_name(name)
            if kind is None or (kind, name) in seen:
                continue
            seen.add((kind, name))
            member_names = tuple(expand_bus_members(name) or ())
            if not member_names:
                continue
            bus_index += 1
            definitions.append(
                BusDefinition(
                    id=f"altium:bus:{kind.value}:{bus_index:04d}",
                    name=name,
                    kind=kind,
                    member_names=member_names,
                    metadata={
                        "source_format": "altium",
                        "source_id": bus_line.id,
                        "source_kind": bus_line.kind,
                        "source_sheet": sheet.name,
                    },
                )
            )
    return definitions


def _altium_harness_buses(source: AltiumSourceDesign, design: Schematic) -> list[Bus]:
    nets_by_local_id = _nets_by_source_local_id(design)
    members_by_bus: dict[str, list[Net]] = {}
    metadata_by_bus: dict[str, dict[str, str]] = {}
    seen_by_bus: dict[str, set[str]] = {}
    for sheet in source.sheets.values():
        for local_net in sheet.local_nets:
            net = nets_by_local_id.get(local_net.id)
            if net is None:
                continue
            for member in local_net.harness_members:
                bus_name = _clean_name(member.port_name)
                if not bus_name:
                    continue
                seen_net_ids = seen_by_bus.setdefault(bus_name, set())
                if net.id in seen_net_ids:
                    continue
                seen_net_ids.add(net.id)
                members_by_bus.setdefault(bus_name, []).append(net)
                _ = metadata_by_bus.setdefault(
                    bus_name,
                    {
                        "source_format": "altium",
                        "source_kind": "harness",
                        "source_sheet": sheet.name,
                    },
                )
    buses: list[Bus] = []
    for index, (bus_name, members) in enumerate(members_by_bus.items(), start=1):
        buses.append(
            Bus(
                id=f"altium:bus:harness:{index:04d}",
                name=bus_name,
                kind=BusKind.HARNESS,
                members=list(members),
                metadata=metadata_by_bus[bus_name],
            )
        )
    return buses


def _nets_by_source_local_id(design: Schematic) -> dict[str, Net]:
    result: dict[str, Net] = {}
    for net in design.nets:
        for occurrence in net.occurrences:
            result[occurrence.source_local_net_id] = net
    return result


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
    pin_occurrences_tuple = tuple(pin_occurrences)
    duplicate_pin_keys = _duplicate_visible_source_pin_keys(pin_occurrences_tuple)
    net_ids_by_pin: dict[tuple[str, str], list[str]] = {}
    for pin_occurrence in pin_occurrences_tuple:
        if not pin_occurrence.local_net_id:
            continue
        key = (
            _source_component_identity(pin_occurrence),
            _source_logical_pin_key(pin_occurrence, duplicate_pin_keys),
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
    ctx: ParseContext | None,
) -> None:
    if effective_mode not in (
        AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        AltiumHierarchyMode.HIERARCHICAL_POWER_LOCAL,
    ):
        return

    known_source_files = _known_source_files(source)
    child_sheets_by_file = _child_sheets_by_source_file(source)
    repeated_child_files = _repeated_child_source_files(source, known_source_files, ctx)
    child_interface_nets = _child_interface_net_ids(source, source.project)
    bus_member_net_ids: dict[tuple[str, str, str], list[str]] = {}
    for ref in local_refs:
        referencing_dir = _parent_dir(_source_file_key(ref.sheet.source_file))
        for entry in ref.local_net.sheet_entries:
            entry_name = _mergeable_name(entry.name)
            entry_members = _mergeable_bus_member_names(entry.name)
            if entry_name is None and not entry_members:
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
                known_source_files,
                referencing_dir,
                ctx,
            )
            for child_sheet in child_sheets:
                if entry_members:
                    bus_name = _clean_name(entry.name)
                    for member_name in entry_members:
                        key = (ref.sheet.id, bus_name, member_name)
                        bus_member_net_ids.setdefault(key, []).extend(
                            child_interface_nets.get((child_sheet.id, member_name), [])
                        )
                    continue
                if entry_name is not None:
                    for child_net_id in child_interface_nets.get((child_sheet.id, entry_name), []):
                        if child_net_id in local_net_by_id:
                            _ = net_union.union(ref.local_net.id, child_net_id)

    for net_ids in bus_member_net_ids.values():
        mergeable_ids = [net_id for net_id in net_ids if net_id in local_net_by_id]
        for net_id in mergeable_ids[1:]:
            _ = net_union.union(mergeable_ids[0], net_id)

    _merge_harness_members(
        source,
        local_refs,
        net_union,
        known_source_files,
        child_sheets_by_file,
        repeated_child_files,
        ctx,
    )


def _merge_harness_members(
    source: AltiumSourceDesign,
    local_refs: Iterable[_LocalNetRef],
    net_union: NetUnion,
    known_source_files: list[str],
    child_sheets_by_file: dict[str, list[AltiumSheetSource]],
    repeated_child_files: set[str],
    ctx: ParseContext | None,
) -> None:
    """Merge harness member nets across the sheet hierarchy.

    A signal harness carries a bundle of named members. Harness-typed ports
    and sheet entries are excluded from plain name merging (two unrelated
    ``SPI`` harnesses must not short together), so harness connectivity is
    resolved structurally instead:

    - a harness *interface* is ``(sheet_id, port_name)`` — a child sheet's
      harness port, whose member nets are the local nets holding that
      connector's harness entries
    - a local net joining harness-typed sheet entries (and/or a harness
      port) is a *conduit* that bundles those interfaces together
    - member nets union pairwise by member name across each bundle of
      connected interfaces; the conduit net itself stays separate (it has
      no pins and unioning it would short all members together)
    """
    members_by_interface: dict[tuple[str, str], dict[str, list[str]]] = {}
    label_ids_by_sheet_and_name: dict[tuple[str, str], list[str]] = {}
    for sheet in source.sheets.values():
        for local_net in sheet.local_nets:
            for label_name in _label_names(local_net):
                label_ids_by_sheet_and_name.setdefault((sheet.id, label_name), []).append(
                    local_net.id
                )
            for member in local_net.harness_members:
                port_name = _clean_name(member.port_name)
                member_name = _clean_name(member.name)
                if not port_name or not member_name:
                    continue
                members_by_interface.setdefault((sheet.id, port_name), {}).setdefault(
                    member_name, []
                ).append(local_net.id)

    interface_union: UnionFind[tuple[str, str]] = UnionFind()
    seen_interfaces: set[tuple[str, str]] = set()
    for ref in local_refs:
        interfaces: list[tuple[str, str]] = []
        for port in ref.local_net.ports:
            if not port.harness_type:
                continue
            port_name = _clean_name(port.name)
            if port_name:
                interfaces.append((ref.sheet.id, port_name))
        referencing_dir = _parent_dir(_source_file_key(ref.sheet.source_file))
        for entry in ref.local_net.sheet_entries:
            if not entry.harness_type:
                continue
            entry_name = _clean_name(entry.name)
            if not entry_name:
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
                known_source_files,
                referencing_dir,
                ctx,
            )
            interfaces.extend((child_sheet.id, entry_name) for child_sheet in child_sheets)
        if len(interfaces) < 2:
            continue
        seen_interfaces.update(interfaces)
        for interface in interfaces[1:]:
            interface_union.union(interfaces[0], interface)

    bundles: dict[tuple[str, str], list[tuple[str, str]]] = {}
    # Sorted so bundle (and therefore union-root) order is stable across
    # processes — set iteration order varies with PYTHONHASHSEED.
    for interface in sorted(seen_interfaces):
        bundles.setdefault(interface_union.find(interface), []).append(interface)

    for bundle in bundles.values():
        nets_by_member: dict[str, list[str]] = {}
        for interface in bundle:
            for member_name, net_ids in members_by_interface.get(interface, {}).items():
                nets_by_member.setdefault(member_name, []).extend(net_ids)
        for interface in bundle:
            sheet_id, _port_name = interface
            for member_name in tuple(nets_by_member):
                nets_by_member[member_name].extend(
                    label_ids_by_sheet_and_name.get((sheet_id, member_name), [])
                )
        for net_ids in nets_by_member.values():
            for net_id in net_ids[1:]:
                _ = net_union.union(net_ids[0], net_id)


def _known_source_files(source: AltiumSourceDesign) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for sheet in source.sheets.values():
        key = _source_file_key(sheet.source_file)
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _canonical_child_key(
    sheet_symbol: AltiumSheetSymbol,
    known_source_files: list[str],
    referencing_dir: str,
    ctx: ParseContext | None,
) -> str:
    """Resolve a sheet symbol's child reference to a canonical source-file key.

    Sheet symbols reference a child by bare filename while the project lists
    documents with a directory prefix; reconcile the two spellings so the
    canonical key matches the resolved child sheets' source files. Basename
    collisions warn on *ctx* as ``ambiguous_document_reference``.
    """
    resolved = resolve_document_reference(
        sheet_symbol.child_source_file,
        referencing_dir=referencing_dir,
        known_documents=known_source_files,
        ctx=ctx,
    )
    if resolved is not None:
        return resolved
    return _source_file_key(sheet_symbol.child_source_file)


def _child_sheets_by_source_file(
    source: AltiumSourceDesign,
) -> dict[str, list[AltiumSheetSource]]:
    result: dict[str, list[AltiumSheetSource]] = {}
    for sheet in source.sheets.values():
        result.setdefault(_source_file_key(sheet.source_file), []).append(sheet)
        result.setdefault(sheet.name, []).append(sheet)
    return result


def _repeated_child_source_files(
    source: AltiumSourceDesign,
    known_source_files: list[str],
    ctx: ParseContext | None,
) -> set[str]:
    symbol_counts: dict[str, int] = {}
    for sheet in source.sheets.values():
        referencing_dir = _parent_dir(_source_file_key(sheet.source_file))
        for symbol in sheet.sheet_symbols:
            if symbol.child_source_file:
                child_source_file = _canonical_child_key(
                    symbol,
                    known_source_files,
                    referencing_dir,
                    ctx,
                )
                symbol_counts[child_source_file] = symbol_counts.get(child_source_file, 0) + 1
    return {child_source_file for child_source_file, count in symbol_counts.items() if count > 1}


def _child_sheets_for_symbol(
    child_sheets_by_file: dict[str, list[AltiumSheetSource]],
    repeated_child_files: set[str],
    sheet_symbol: AltiumSheetSymbol,
    known_source_files: list[str],
    referencing_dir: str,
    ctx: ParseContext | None,
) -> list[AltiumSheetSource]:
    canonical = _canonical_child_key(sheet_symbol, known_source_files, referencing_dir, ctx)
    child_sheets = child_sheets_by_file.get(canonical, [])
    instance_scoped = [
        sheet
        for sheet in child_sheets
        if _scope_matches_sheet_symbol(sheet.scope_id.path, sheet_symbol)
    ]
    if instance_scoped:
        return instance_scoped
    if canonical in repeated_child_files:
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


def _child_interface_net_ids(
    source: AltiumSourceDesign,
    project: AltiumProject,
) -> dict[tuple[str, str], list[str]]:
    result = _child_port_net_ids(source)
    if not project.allow_sheet_entry_net_names:
        return result
    for sheet in source.sheets.values():
        for local_net in sheet.local_nets:
            for name in _label_names(local_net):
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
            title_block=sheet.title_block,
        )
        for sheet in source.sheets.values()
    ]


def _local_net_inputs(local_refs: Iterable[_LocalNetRef]) -> list[ResolvedLocalNetInput]:
    return [
        ResolvedLocalNetInput(
            id=ref.local_net.id,
            scope_id=ref.local_net.scope_id,
            source_names=frozenset(_all_source_names([ref])),
            directives=tuple(ref.local_net.directives),
        )
        for ref in local_refs
    ]


def _altium_net_input_for_group(
    source: AltiumSourceDesign,
    local_net_by_id: dict[str, _LocalNetRef],
    members_by_local_net: dict[str, list[tuple[str, str]]],
    net_index: int,
    root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
) -> ResolvedNetInput:
    refs = [local_net_by_id[local_net.id] for local_net in group_local_nets]
    evidence = _collect_name_evidence(source.project, refs)
    names = [
        *evidence.labels,
        *evidence.powers,
        *evidence.sheet_entries,
        *evidence.ports,
        *evidence.harness_members,
        *evidence.extra_names,
    ]
    canonical = _select_canonical(source.project, evidence)
    if canonical is None:
        members = [
            member
            for local_net in group_local_nets
            for member in members_by_local_net.get(local_net.id, [])
        ]
        canonical = _autoname_candidate(members, net_index)
        names.append(canonical)
    return ResolvedNetInput(
        id=f"net:{net_index:04d}",
        name=canonical.name,
        names=tuple(
            NetName(
                name=candidate.name,
                kind=candidate.kind,
                scope=candidate.scope,
                source=candidate.source,
            )
            for candidate in names
        ),
        metadata={
            "altium_root_local_net_id": root_id,
        },
    )


def _select_canonical(
    project: AltiumProject,
    evidence: _NameEvidence,
) -> _NameCandidate | None:
    """Pick the canonical name per Altium's priority ladder, or ``None``.

    Documented ladder: net labels > power ports (order swapped by
    ``PowerPortNamesTakePriority`` — documented behavior only, no
    conflicting corpus sample exists) > sheet entries (gated by
    ``AllowSheetEntryNetNames``) > ports (gated by ``AllowPortNetNames``)
    > harness ``Bundle.Signal`` fallback. Within the winning tier the
    case-insensitive alphabetical minimum wins; ties break by document
    order (single corpus witness — an approximation).
    """
    label_tiers = [evidence.labels, evidence.powers]
    if project.power_port_names_take_priority:
        label_tiers.reverse()
    tiers = [
        *label_tiers,
        evidence.sheet_entries if project.allow_sheet_entry_net_names else [],
        evidence.ports if project.allow_port_net_names else [],
        evidence.harness_members,
    ]
    for tier in tiers:
        if tier:
            return min(
                enumerate(tier),
                key=lambda item: (item[1].name.casefold(), item[0]),
            )[1]
    return None


_DIGIT_RUN = re.compile(r"([0-9]+)")


def _natural_key(text: str) -> tuple[tuple[int, int, str], ...]:
    """Natural-sort key: digit runs compare numerically, text runs lexically."""
    key: list[tuple[int, int, str]] = []
    for index, run in enumerate(_DIGIT_RUN.split(text)):
        if not run:
            continue
        if index % 2 == 1:  # odd indices are the captured digit runs
            key.append((0, int(run), ""))
        else:
            key.append((1, 0, run))
    return tuple(key)


def _autoname_candidate(
    members: list[tuple[str, str]],
    net_index: int,
) -> _NameCandidate:
    """Altium's autoname: ``Net<designator>_<pin>`` over the natural-sort
    minimum member ``(designator, pin)`` pair (709/709 corpus fit)."""
    if not members:
        # Unreachable for included nets (inclusion requires a pin); kept as a
        # flagged synthetic fallback rather than an assertion.
        return _NameCandidate(
            name=f"__net_{net_index:04d}",
            kind=NetNameKind.SYNTHESIZED,
            scope=None,
            source="altium:synthesized",
        )
    designator, pin = min(
        members,
        key=lambda member: (_natural_key(member[0]), _natural_key(member[1])),
    )
    return _NameCandidate(
        name=f"Net{designator}_{pin}",
        kind=NetNameKind.TOOL_AUTO,
        scope=None,
        source="altium:autoname",
    )


def _net_members_by_local_net(
    pin_occurrences: Iterable[AltiumPinOccurrence],
) -> dict[str, list[tuple[str, str]]]:
    """Map local net id to its member ``(designator, pin)`` pairs."""
    result: dict[str, list[tuple[str, str]]] = {}
    for pin_occurrence in pin_occurrences:
        if not pin_occurrence.local_net_id:
            continue
        result.setdefault(pin_occurrence.local_net_id, []).append(
            (pin_occurrence.component_reference, pin_occurrence.pin_designator)
        )
    return result


def _warn_unverified_naming_options(project: AltiumProject, ctx: ParseContext | None) -> None:
    """Warn when a project enables a naming option we have no verified sample for.

    ``NameNetsHierarchically`` is 0 in every public corpus project; its exact
    output format is undocumented, so the resolver proceeds without the prefix
    transform.
    """
    if ctx is None:
        return
    unverified = (("NameNetsHierarchically", project.name_nets_hierarchically),)
    for option, enabled in unverified:
        if enabled:
            ctx.warn(
                "unverified_naming_option",
                f"naming option {option} unverified; net names computed without it",
            )


def _include_altium_net(
    _root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
    pins: tuple[ResolvedPinInput, ...],
) -> bool:
    group_local_net_ids = {local_net.id for local_net in group_local_nets}
    return any(local_net.directives for local_net in group_local_nets) or any(
        pin.local_net_id in group_local_net_ids for pin in pins
    )


def _pin_inputs(
    pin_occurrences: Iterable[AltiumPinOccurrence],
    component_source_ids_by_component_id: dict[str, list[str]],
    physical_designators: dict[str, AnnotationDesignator],
    symbol_uid_by_id: dict[str, str],
) -> list[ResolvedPinInput]:
    pin_occurrences_tuple = tuple(pin_occurrences)
    duplicate_pin_keys = _duplicate_visible_pin_keys(pin_occurrences_tuple)
    result: list[ResolvedPinInput] = []
    seen_pin_occurrences: set[tuple[str, str]] = set()
    for pin_occurrence in pin_occurrences_tuple:
        component_id = _component_identity(pin_occurrence)
        pin_id = _logical_pin_id(pin_occurrence, component_id, duplicate_pin_keys)
        pin_occurrence_key = (pin_id, pin_occurrence.id)
        if pin_occurrence_key in seen_pin_occurrences:
            continue
        seen_pin_occurrences.add(pin_occurrence_key)
        pin_metadata = {"altium_pin_source_id": pin_occurrence.id}
        if pin_occurrence.pin_unique_id:
            pin_metadata["altium_pin_unique_id"] = pin_occurrence.pin_unique_id
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
                    physical_designator=_physical_designator(
                        pin_occurrence,
                        physical_designators,
                        symbol_uid_by_id,
                    ),
                    metadata=_component_occurrence_metadata(pin_occurrence),
                ),
                pin_metadata=pin_metadata,
                pin_occurrence_metadata=_pin_occurrence_metadata(pin_occurrence),
                component_metadata=_component_metadata_for_pin(
                    pin_occurrence,
                    component_source_ids_by_component_id.get(component_id, []),
                ),
                component_info=pin_occurrence.component_info,
            )
        )
    return result


def _duplicate_visible_pin_keys(
    pin_occurrences: Iterable[AltiumPinOccurrence],
) -> set[tuple[str, str]]:
    counts: dict[tuple[str, str], int] = {}
    for pin_occurrence in pin_occurrences:
        key = (_component_identity(pin_occurrence), pin_occurrence.pin_designator)
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def _duplicate_visible_source_pin_keys(
    pin_occurrences: Iterable[AltiumPinOccurrence],
) -> set[tuple[str, str]]:
    counts: dict[tuple[str, str], int] = {}
    for pin_occurrence in pin_occurrences:
        key = (_source_component_identity(pin_occurrence), pin_occurrence.pin_designator)
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def _source_logical_pin_key(
    pin_occurrence: AltiumPinOccurrence,
    duplicate_pin_keys: set[tuple[str, str]],
) -> str:
    if (
        _source_component_identity(pin_occurrence),
        pin_occurrence.pin_designator,
    ) not in duplicate_pin_keys:
        return pin_occurrence.pin_designator
    return pin_occurrence.pin_unique_id or pin_occurrence.id or pin_occurrence.pin_designator


def _logical_pin_id(
    pin_occurrence: AltiumPinOccurrence,
    component_id: str,
    duplicate_pin_keys: set[tuple[str, str]],
) -> str:
    base_id = f"{component_id}:pin:{pin_occurrence.pin_designator}"
    if (component_id, pin_occurrence.pin_designator) not in duplicate_pin_keys:
        return base_id
    suffix = pin_occurrence.pin_unique_id or pin_occurrence.id
    return f"{base_id}:{suffix}" if suffix else base_id


def _physical_designator(
    pin_occurrence: AltiumPinOccurrence,
    physical_designators: dict[str, AnnotationDesignator],
    symbol_uid_by_id: dict[str, str],
) -> str:
    """Resolve a pin occurrence's per-instance physical designator, or ``""``.

    For components inside repeated/multi-channel sheets the logical designator
    (e.g. ``U1``) collides across instances. The ``.Annotation`` file assigns a
    distinct physical designator keyed by the component's hierarchical unique-id
    path ``\\<sheet-symbol-uid>...\\<component-uid>``. The logical reference is
    never substituted — the physical designator is occurrence-level metadata.
    Empty when no entry resolves (single-instance / un-annotated components).
    """
    if not physical_designators:
        return ""
    component_uid = pin_occurrence.component_metadata.get("altium_component_unique_id")
    if not component_uid:
        return ""
    symbol_uids = [
        symbol_uid_by_id[element]
        for element in pin_occurrence.scope_id.path
        if element in symbol_uid_by_id
    ]
    unique_id_path = "\\" + "\\".join([*symbol_uids, component_uid])
    entry = physical_designators.get(unique_id_path)
    return entry.physical_designator if entry is not None else ""


def _source_file_key(source_file: str) -> str:
    return source_file.replace("\\", "/")


def _parent_dir(source_file_key: str) -> str:
    """Return the directory portion of a normalized source-file key, or ``""``."""
    head, sep, _ = source_file_key.rpartition("/")
    return head if sep else ""


def _collect_name_evidence(project: AltiumProject, refs: Iterable[_LocalNetRef]) -> _NameEvidence:
    """Collect per-tier name candidates in document order."""
    labels: list[_NameCandidate] = []
    powers: list[_NameCandidate] = []
    sheet_entries: list[_NameCandidate] = []
    ports: list[_NameCandidate] = []
    harness_members: list[_NameCandidate] = []
    extra_names: list[_NameCandidate] = []

    for ref in refs:
        scope = ref.local_net.scope_id
        for label in ref.local_net.net_labels:
            _append_label_candidate(project, ref, label, labels, extra_names)
        for power_port in ref.local_net.power_ports:
            _append_candidate(powers, power_port.name, NetNameKind.LABEL, scope, power_port.id)
        for entry in ref.local_net.sheet_entries:
            if entry.harness_type:
                continue
            _append_candidate(sheet_entries, entry.name, NetNameKind.LABEL, scope, entry.id)
        for port in ref.local_net.ports:
            if port.harness_type:
                continue
            _append_candidate(ports, port.name, NetNameKind.LABEL, scope, port.id)
        for member in ref.local_net.harness_members:
            member_name = _mergeable_name(member.name)
            if member_name is None:
                continue
            port_name = _clean_name(member.port_name)
            qualified = f"{port_name}.{member_name}" if port_name else member_name
            harness_members.append(
                _NameCandidate(
                    name=qualified,
                    kind=NetNameKind.TOOL_AUTO,
                    scope=scope,
                    source=member.id,
                )
            )
        for member_name in ref.local_net.generic_bus_members:
            name = _mergeable_name(member_name)
            if name is None:
                continue
            harness_members.append(
                _NameCandidate(
                    name=name,
                    kind=NetNameKind.TOOL_AUTO,
                    scope=scope,
                    source="altium:generic_bus_member",
                )
            )

    return _NameEvidence(
        labels=labels,
        powers=powers,
        sheet_entries=sheet_entries,
        ports=ports,
        harness_members=harness_members,
        extra_names=extra_names,
    )


def _append_label_candidate(
    project: AltiumProject,
    ref: _LocalNetRef,
    label: AltiumNetLabel,
    candidates: list[_NameCandidate],
    extra_names: list[_NameCandidate],
) -> None:
    name = _mergeable_name(label.name)
    if name is None:
        return
    sheet_number = (
        _sheet_number_suffix(ref)
        if project.append_sheet_number_to_local_nets and _is_local_label_net(ref.local_net)
        else ""
    )
    if not sheet_number:
        candidates.append(
            _NameCandidate(name=name, kind=NetNameKind.LABEL, scope=label.scope_id, source=label.id)
        )
        return
    candidates.append(
        _NameCandidate(
            name=_append_sheet_number(name, sheet_number),
            kind=NetNameKind.LABEL,
            scope=label.scope_id,
            source=label.id,
        )
    )
    extra_names.append(
        _NameCandidate(name=name, kind=NetNameKind.LABEL, scope=label.scope_id, source=label.id)
    )


def _sheet_number_suffix(ref: _LocalNetRef) -> str:
    title_block = ref.sheet.title_block
    if title_block is None:
        return ""
    return title_block.sheet_number.strip()


def _append_sheet_number(name: str, sheet_number: str) -> str:
    stem, sep, suffix = name.rpartition("_")
    if sep and suffix in {"P", "N"} and stem:
        if stem.endswith(f"_{sheet_number}"):
            return name
        return f"{stem}_{sheet_number}_{suffix}"
    if name.endswith(f"_{sheet_number}"):
        return name
    return f"{name}_{sheet_number}"


def _is_local_label_net(local_net: AltiumLocalNet) -> bool:
    return not local_net.ports and not local_net.harness_members


def _append_candidate(
    candidates: list[_NameCandidate],
    raw_name: str,
    kind: NetNameKind,
    scope: ScopeId,
    source: str,
) -> None:
    name = _mergeable_name(raw_name)
    if name is None:
        return
    candidates.append(_NameCandidate(name=name, kind=kind, scope=scope, source=source))


def _all_source_names(refs: Iterable[_LocalNetRef]) -> set[str]:
    names: set[str] = set()
    for ref in refs:
        names.update(_label_names(ref.local_net))
        names.update(_power_names(ref.local_net))
        names.update(_port_names(ref.local_net))
        names.update(_sheet_entry_names(ref.local_net))
        names.update(_harness_member_names(ref.local_net))
        names.update(_generic_bus_member_names(ref.local_net))
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


def _mergeable_bus_member_names(name: str) -> list[str]:
    cleaned = _clean_name(name)
    if not cleaned:
        return []
    members = expand_bus_members(cleaned)
    if members is None:
        return []
    return _dedupe(member for member in (_mergeable_name(member) for member in members) if member)


def _harness_member_names(local_net: AltiumLocalNet) -> list[str]:
    """Qualified ``Bundle.Signal`` names (Altium's harness fallback form) —
    bare member names (CLK, CS) are too generic to use as net names or merge
    keys across unrelated harnesses."""
    names: list[str] = []
    for member in local_net.harness_members:
        name = _mergeable_name(member.name)
        if name is None:
            continue
        port_name = _clean_name(member.port_name)
        names.append(f"{port_name}.{name}" if port_name else name)
    return _dedupe(names)


def _generic_bus_member_names(local_net: AltiumLocalNet) -> list[str]:
    return _dedupe(_mergeable_name(name) or "" for name in local_net.generic_bus_members)


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
