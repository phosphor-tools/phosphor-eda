"""Resolve KiCad-native source connectivity into the public schematic model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.net_union import NetUnion
from phosphor_eda.schematic import (
    Component,
    ComponentOccurrence,
    Net,
    NetOccurrence,
    Page,
    Pin,
    Schematic,
    ScopeId,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.kicad.source import (
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


def resolve_kicad_source(source: KiCadSourceDesign) -> Schematic:
    """Resolve a KiCad source design into the public schematic graph."""
    net_union = NetUnion(local_net.id for local_net in source.local_nets)

    _merge_repeated_logical_pins(net_union, source.pin_occurrences)
    _merge_same_scope_names(net_union, source.local_nets)
    _merge_global_labels(net_union, source.local_nets)
    _merge_power_symbols(net_union, source.local_nets)
    _merge_hierarchical_sheet_pins(source, net_union)

    pages = _build_pages(source)
    nets_by_local_id = _build_nets(source.local_nets, net_union, pages, source.pin_occurrences)
    components = _build_components(
        pages,
        nets_by_local_id,
        source.pin_occurrences,
    )

    return Schematic(
        name=source.name,
        pages=list(pages.values()),
        nets=_ordered_nets(nets_by_local_id),
        components=components,
        metadata={
            "kicad_root_source_file": source.root_source_file,
        },
    )


def _merge_repeated_logical_pins(
    net_union: NetUnion,
    pin_occurrences: Iterable[KiCadPinOccurrence],
) -> None:
    net_ids_by_pin: dict[tuple[str, str], list[str]] = {}
    for pin_occurrence in pin_occurrences:
        key = (_component_identity(pin_occurrence), pin_occurrence.pin_designator)
        net_ids_by_pin.setdefault(key, []).append(pin_occurrence.local_net_id)

    for net_ids in net_ids_by_pin.values():
        _merge_ids(net_union, net_ids)


def _merge_same_scope_names(net_union: NetUnion, local_nets: Iterable[KiCadLocalNet]) -> None:
    local_label_ids: dict[tuple[ScopeId, str], list[str]] = {}
    hierarchical_label_ids: dict[tuple[ScopeId, str], list[str]] = {}

    for local_net in local_nets:
        for label in local_net.local_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                local_label_ids.setdefault((label.scope_id, name), []).append(local_net.id)
        for label in local_net.hierarchical_labels:
            name = _mergeable_name(label.name)
            if name is not None:
                hierarchical_label_ids.setdefault((label.scope_id, name), []).append(local_net.id)

    for net_ids in local_label_ids.values():
        _merge_ids(net_union, net_ids)
    for net_ids in hierarchical_label_ids.values():
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
            net_union.union(sheet_pin.local_net_id, child_net_id)


def _merge_ids(net_union: NetUnion, net_ids: list[str]) -> None:
    if len(net_ids) < 2:
        return
    first_id = net_ids[0]
    for net_id in net_ids[1:]:
        net_union.union(first_id, net_id)


def _build_pages(source: KiCadSourceDesign) -> dict[ScopeId, Page]:
    pages: dict[ScopeId, Page] = {}
    for instance in source.sheet_instances:
        pages[instance.scope_id] = Page(
            id=instance.id,
            name=instance.sheet_name,
            source_file=instance.source_file,
            scope_id=instance.scope_id,
            metadata={
                "kicad_sheet_symbol_id": instance.sheet_symbol_id,
            },
        )
    return pages


def _build_nets(
    local_nets: list[KiCadLocalNet],
    net_union: NetUnion,
    pages: dict[ScopeId, Page],
    pin_occurrences: Iterable[KiCadPinOccurrence],
) -> dict[str, Net]:
    pin_counts_by_local_net_id: dict[str, int] = {}
    for pin_occurrence in pin_occurrences:
        pin_counts_by_local_net_id[pin_occurrence.local_net_id] = (
            pin_counts_by_local_net_id.get(pin_occurrence.local_net_id, 0) + 1
        )

    local_nets_by_group: dict[str, list[KiCadLocalNet]] = {}
    for local_net in local_nets:
        root_id = net_union.find(local_net.id)
        local_nets_by_group.setdefault(root_id, []).append(local_net)

    result: dict[str, Net] = {}
    net_index = 0
    for root_id, group_local_nets in local_nets_by_group.items():
        if not _group_has_pins(group_local_nets, pin_counts_by_local_net_id):
            continue
        net_index += 1
        name = select_kicad_net_name(group_local_nets)
        aliases = _all_alias_names(group_local_nets)
        aliases.discard(name)
        net = Net(
            id=f"net:{net_index:04d}",
            name=name,
            aliases=aliases,
            metadata={
                "kicad_root_local_net_id": root_id,
            },
        )
        for local_net in group_local_nets:
            page = _page_for_scope(pages, local_net.scope_id)
            occurrence = NetOccurrence(
                id=f"{net.id}:occ:{len(net.occurrences) + 1:04d}",
                net=net,
                page=page,
                scope_id=local_net.scope_id,
                source_local_net_id=local_net.id,
                source_names=_source_names(local_net),
            )
            net.occurrences.append(occurrence)
            _append_unique_page(net.pages, page)
            _append_unique_net(page.nets, net)
            result[local_net.id] = net
    return result


def select_kicad_net_name(local_nets: Iterable[KiCadLocalNet]) -> str:
    """Select a public net name using the Task 9 KiCad priority order."""
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


def _build_components(
    pages: dict[ScopeId, Page],
    nets_by_local_id: dict[str, Net],
    pin_occurrences: Iterable[KiCadPinOccurrence],
) -> list[Component]:
    components_by_id: dict[str, Component] = {}
    occurrences_by_component_page_source: set[tuple[str, str, str]] = set()
    pins_by_component_designator: dict[tuple[str, str], Pin] = {}

    for pin_occurrence in pin_occurrences:
        page = _page_for_scope(pages, pin_occurrence.scope_id)
        component_id = _component_identity(pin_occurrence)
        component = components_by_id.get(component_id)
        if component is None:
            component = Component(
                id=component_id,
                reference=pin_occurrence.component_reference,
                part=pin_occurrence.component_value,
                description=pin_occurrence.component_description,
                metadata=_component_metadata(pin_occurrence),
            )
            components_by_id[component_id] = component
        elif not component.part and pin_occurrence.component_value:
            component.part = pin_occurrence.component_value
            component.description = pin_occurrence.component_description
            component.metadata.update(_component_metadata(pin_occurrence))
        _add_component_source_id(component, pin_occurrence.component_source_id)

        _append_unique_page(component.pages, page)
        _append_unique_component(page.components, component)
        occurrence_key = (component.id, page.id, pin_occurrence.component_source_id)
        if occurrence_key not in occurrences_by_component_page_source:
            occurrences_by_component_page_source.add(occurrence_key)
            component.occurrences.append(
                ComponentOccurrence(
                    id=f"{component.id}:occ:{len(component.occurrences) + 1:04d}",
                    component=component,
                    page=page,
                    scope_id=pin_occurrence.scope_id,
                    source_id=pin_occurrence.component_source_id,
                    part_id=pin_occurrence.component_value,
                    x=pin_occurrence.component_x,
                    y=pin_occurrence.component_y,
                    rotation=pin_occurrence.component_rotation,
                    mirror=pin_occurrence.component_mirror,
                )
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
                    "kicad_pin_source_id": pin_occurrence.id,
                },
            )
            pins_by_component_designator[pin_key] = pin
            component.pins.append(pin)

        net = nets_by_local_id.get(pin_occurrence.local_net_id)
        if net is not None:
            if pin.net is not None and pin.net.id != net.id:
                _remove_pin(pin.net.pins, pin)
            pin.net = net
            _append_unique_pin(net.pins, pin)

    return list(components_by_id.values())


def _component_metadata(pin_occurrence: KiCadPinOccurrence) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if pin_occurrence.component_value:
        metadata["Value"] = pin_occurrence.component_value
    if pin_occurrence.component_footprint:
        metadata["Footprint"] = pin_occurrence.component_footprint
    if pin_occurrence.component_datasheet:
        metadata["Datasheet"] = pin_occurrence.component_datasheet
    return metadata


def _component_identity(pin_occurrence: KiCadPinOccurrence) -> str:
    scope_key = _scope_key(pin_occurrence.scope_id)
    source_id = pin_occurrence.component_source_id
    if source_id:
        return f"kicad:component:{scope_key}:{source_id}"
    return f"kicad:component:{scope_key}:{pin_occurrence.component_reference}"


def _scope_key(scope_id: ScopeId) -> str:
    return "root" if not scope_id.path else "/".join(scope_id.path)


def _add_component_source_id(component: Component, source_id: str) -> None:
    if not source_id:
        return
    existing = component.metadata.get("kicad_component_source_ids", "")
    source_ids = [value for value in existing.split(",") if value]
    if source_id in source_ids:
        return
    source_ids.append(source_id)
    component.metadata["kicad_component_source_ids"] = ",".join(source_ids)


def _group_has_pins(
    local_nets: Iterable[KiCadLocalNet],
    pin_counts_by_local_net_id: dict[str, int],
) -> bool:
    return any(pin_counts_by_local_net_id.get(local_net.id, 0) > 0 for local_net in local_nets)


def _page_for_scope(pages: dict[ScopeId, Page], scope_id: ScopeId) -> Page:
    page = pages.get(scope_id)
    if page is not None:
        return page
    fallback = Page(
        id=f"sheet:{_scope_key(scope_id)}",
        name=str(scope_id),
        scope_id=scope_id,
    )
    pages[scope_id] = fallback
    return fallback


def _ordered_nets(nets_by_local_id: dict[str, Net]) -> list[Net]:
    nets: list[Net] = []
    seen: set[str] = set()
    for net in nets_by_local_id.values():
        if net.id in seen:
            continue
        seen.add(net.id)
        nets.append(net)
    return nets


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
