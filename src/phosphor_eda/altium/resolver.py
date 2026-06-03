"""Resolve Altium-native source connectivity into the public schematic model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.altium._helpers import parse_bus_notation
from phosphor_eda.altium.project import AltiumHierarchyMode
from phosphor_eda.net_union import NetUnion
from phosphor_eda.schematic import (
    Component,
    ComponentOccurrence,
    Net,
    NetOccurrence,
    Page,
    Pin,
    PinOccurrence,
    Schematic,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.altium.source import (
        AltiumLocalNet,
        AltiumPinOccurrence,
        AltiumSheetSource,
        AltiumSheetSymbol,
        AltiumSourceDesign,
    )


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


def resolve_altium_source(source: AltiumSourceDesign) -> Schematic:
    """Resolve an Altium source design into the public schematic graph."""
    local_refs = _collect_local_refs(source)
    net_union = NetUnion(ref.local_net.id for ref in local_refs)
    local_net_by_id = {ref.local_net.id: ref for ref in local_refs}
    pin_occurrences = _collect_pin_occurrences(source)

    _merge_repeated_logical_pins(net_union, pin_occurrences)
    effective_mode = _effective_hierarchy_mode(source)
    _merge_source_names(source, local_refs, net_union, effective_mode)
    _merge_hierarchy(source, local_refs, local_net_by_id, net_union, effective_mode)

    pages = _build_pages(source)
    nets_by_local_id = _build_nets(
        source,
        local_refs,
        net_union,
        pages,
        pin_occurrences,
    )
    components = _build_components(source, pages, nets_by_local_id, pin_occurrences)

    return Schematic(
        name=source.name,
        pages=list(pages.values()),
        nets=_ordered_nets(nets_by_local_id),
        components=components,
        metadata={
            "altium_hierarchy_mode": source.project.hierarchy_mode.name,
            "altium_effective_hierarchy_mode": effective_mode.name,
        },
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


def _build_pages(source: AltiumSourceDesign) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    for sheet in source.sheets.values():
        pages[sheet.id] = Page(
            id=sheet.id,
            name=sheet.name,
            source_file=sheet.source_file,
            scope_id=sheet.scope_id,
        )
    return pages


def _build_nets(
    source: AltiumSourceDesign,
    local_refs: list[_LocalNetRef],
    net_union: NetUnion,
    pages: dict[str, Page],
    pin_occurrences: Iterable[AltiumPinOccurrence],
) -> dict[str, Net]:
    pin_counts_by_local_net_id: dict[str, int] = {}
    for pin_occurrence in pin_occurrences:
        if pin_occurrence.local_net_id:
            pin_counts_by_local_net_id[pin_occurrence.local_net_id] = (
                pin_counts_by_local_net_id.get(pin_occurrence.local_net_id, 0) + 1
            )

    refs_by_group: dict[str, list[_LocalNetRef]] = {}
    for ref in local_refs:
        root_id = net_union.find(ref.local_net.id)
        refs_by_group.setdefault(root_id, []).append(ref)

    result: dict[str, Net] = {}
    net_index = 0
    for root_id, refs in refs_by_group.items():
        if not _group_has_pins(refs, pin_counts_by_local_net_id):
            continue
        net_index += 1
        net_id = f"net:{net_index:04d}"
        name = _select_net_name(source, refs)
        aliases = _all_source_names(refs)
        aliases.discard(name)
        net = Net(
            id=net_id,
            name=name,
            aliases=aliases,
            metadata={
                "altium_root_local_net_id": root_id,
            },
        )
        for ref in refs:
            page = pages[ref.sheet.id]
            occurrence = NetOccurrence(
                id=f"{net.id}:occ:{len(net.occurrences) + 1:04d}",
                net=net,
                page=page,
                scope_id=ref.local_net.scope_id,
                source_local_net_id=ref.local_net.id,
                source_names=_all_source_names([ref]),
            )
            net.occurrences.append(occurrence)
            _append_unique_page(net.pages, page)
            _append_unique_net(page.nets, net)
            result[ref.local_net.id] = net
    return result


def _ordered_nets(nets_by_local_id: dict[str, Net]) -> list[Net]:
    nets: list[Net] = []
    seen: set[str] = set()
    for net in nets_by_local_id.values():
        if net.id in seen:
            continue
        seen.add(net.id)
        nets.append(net)
    return nets


def _build_components(
    source: AltiumSourceDesign,
    pages: dict[str, Page],
    nets_by_local_id: dict[str, Net],
    pin_occurrences: Iterable[AltiumPinOccurrence],
) -> list[Component]:
    components_by_id: dict[str, Component] = {}
    occurrences_by_component_page_source: set[tuple[str, str, str]] = set()
    pin_occurrences_by_pin_source: set[tuple[str, str]] = set()
    pins_by_component_designator: dict[tuple[str, str], Pin] = {}

    for pin_occurrence in pin_occurrences:
        component_id = _component_identity(pin_occurrence)
        sheet = _sheet_for_scope(source, pin_occurrence.scope_id.path)
        if sheet is None:
            continue
        page = pages[sheet.id]

        component = components_by_id.get(component_id)
        if component is None:
            component = Component(
                id=component_id,
                reference=pin_occurrence.component_reference,
                part=pin_occurrence.component_part,
                description=pin_occurrence.component_description,
                metadata=dict(pin_occurrence.component_metadata),
            )
            components_by_id[component_id] = component
        _merge_component_fields(component, pin_occurrence)
        _add_component_source_id(component, pin_occurrence.component_source_id)

        _append_unique_page(component.pages, page)
        _append_unique_component(page.components, component)
        component_occurrence_source_id = _component_occurrence_source_id(pin_occurrence)
        occurrence_key = (component.id, page.id, component_occurrence_source_id)
        if occurrence_key not in occurrences_by_component_page_source:
            occurrences_by_component_page_source.add(occurrence_key)
            component.occurrences.append(
                ComponentOccurrence(
                    id=f"{component.id}:occ:{len(component.occurrences) + 1:04d}",
                    component=component,
                    page=page,
                    scope_id=pin_occurrence.scope_id,
                    source_id=component_occurrence_source_id,
                    part_id=pin_occurrence.component_part_id,
                    metadata=_component_occurrence_metadata(pin_occurrence),
                ),
            )

        pin_key = (component.id, pin_occurrence.pin_designator)
        pin = pins_by_component_designator.get(pin_key)
        if pin is None:
            pin = Pin(
                id=f"{component.id}:pin:{pin_occurrence.pin_designator}",
                designator=pin_occurrence.pin_designator,
                name=_clean_name(pin_occurrence.pin_name),
                component=component,
                no_connect=pin_occurrence.no_connect,
                metadata={
                    "altium_pin_source_id": pin_occurrence.id,
                },
            )
            pins_by_component_designator[pin_key] = pin
            component.pins.append(pin)

        pin_occurrence_key = (pin.id, pin_occurrence.id)
        if pin_occurrence_key not in pin_occurrences_by_pin_source:
            pin_occurrences_by_pin_source.add(pin_occurrence_key)
            pin.occurrences.append(
                PinOccurrence(
                    id=f"{pin.id}:occ:{len(pin.occurrences) + 1:04d}",
                    pin=pin,
                    page=page,
                    scope_id=pin_occurrence.scope_id,
                    source_id=pin_occurrence.id,
                    metadata=_pin_occurrence_metadata(pin_occurrence),
                ),
            )

        net = nets_by_local_id.get(pin_occurrence.local_net_id)
        if net is not None:
            if pin.net is not None and pin.net.id != net.id:
                _remove_pin(pin.net.pins, pin)
            pin.net = net
            _append_unique_pin(net.pins, pin)

    return list(components_by_id.values())


def _sheet_for_scope(source: AltiumSourceDesign, path: tuple[str, ...]) -> AltiumSheetSource | None:
    if not path:
        return None
    sheet = source.sheets.get(path[-1])
    if sheet is not None:
        return sheet
    for candidate in source.sheets.values():
        if candidate.scope_id.path == path:
            return candidate
    return None


def _source_file_key(source_file: str) -> str:
    return source_file.replace("\\", "/")


def _group_has_pins(
    refs: Iterable[_LocalNetRef],
    pin_counts_by_local_net_id: dict[str, int],
) -> bool:
    return any(pin_counts_by_local_net_id.get(ref.local_net.id, 0) > 0 for ref in refs)


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


def _merge_component_fields(component: Component, pin_occurrence: AltiumPinOccurrence) -> None:
    if not component.part and pin_occurrence.component_part:
        component.part = pin_occurrence.component_part
    if not component.description and pin_occurrence.component_description:
        component.description = pin_occurrence.component_description
    for key, value in pin_occurrence.component_metadata.items():
        if key and value and key not in component.metadata:
            component.metadata[key] = value


def _add_component_source_id(component: Component, source_id: str) -> None:
    if not source_id:
        return
    existing = component.metadata.get("altium_component_source_ids", "")
    source_ids = [value for value in existing.split(",") if value]
    if source_id in source_ids:
        return
    source_ids.append(source_id)
    component.metadata["altium_component_source_ids"] = ",".join(source_ids)


def _append_unique_page(pages: list[Page], page: Page) -> None:
    if not any(existing.id == page.id for existing in pages):
        pages.append(page)


def _append_unique_net(nets: list[Net], net: Net) -> None:
    if not any(existing.id == net.id for existing in nets):
        nets.append(net)


def _append_unique_component(components: list[Component], component: Component) -> None:
    if not any(existing.id == component.id for existing in components):
        components.append(component)


def _append_unique_pin(pins: list[Pin], pin: Pin) -> None:
    if not any(existing.id == pin.id for existing in pins):
        pins.append(pin)


def _remove_pin(pins: list[Pin], pin: Pin) -> None:
    remaining = [existing for existing in pins if existing.id != pin.id]
    if len(remaining) != len(pins):
        pins[:] = remaining
