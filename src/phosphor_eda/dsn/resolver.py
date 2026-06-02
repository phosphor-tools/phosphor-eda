"""Resolve OrCAD DSN-native source connectivity into the public schematic model."""

from __future__ import annotations

from dataclasses import dataclass, field
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

    from phosphor_eda.dsn.source import (
        DsnPageNet,
        DsnPageSource,
        DsnPinOccurrence,
        DsnSourceDesign,
    )


@dataclass(slots=True)
class _LocalNetRef:
    page: DsnPageSource
    local_net: DsnPageNet


@dataclass(slots=True)
class _NameEvidence:
    page_names: list[str] = field(default_factory=list)
    globals: list[str] = field(default_factory=list)
    off_page_connectors: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


def resolve_dsn_source(source: DsnSourceDesign) -> Schematic:
    """Resolve an OrCAD DSN source design into the public schematic graph."""
    local_refs = _collect_local_refs(source.pages)
    local_net_ids = {ref.local_net.id for ref in local_refs}
    net_union = NetUnion(ref.local_net.id for ref in local_refs)
    pin_occurrences = _collect_pin_occurrences(source.pages)

    _merge_repeated_logical_pins(net_union, pin_occurrences, local_net_ids)
    _merge_globals(source.pages, net_union, local_net_ids)
    _merge_known_scope_off_page_connectors(source.pages, net_union, local_net_ids)

    pages = _build_pages(source.pages)
    name_evidence = _collect_name_evidence(source.pages)
    nets_by_local_id = _build_nets(local_refs, net_union, pages, name_evidence)
    components = _build_components(pages, nets_by_local_id, pin_occurrences)

    return Schematic(
        name=source.name,
        pages=list(pages.values()),
        nets=_ordered_nets(nets_by_local_id),
        components=components,
        metadata={
            "dsn_resolver": "source",
        },
    )


def _collect_local_refs(pages: Iterable[DsnPageSource]) -> list[_LocalNetRef]:
    refs: list[_LocalNetRef] = []
    seen: set[str] = set()
    for page in pages:
        for local_net in page.nets:
            if local_net.id in seen:
                continue
            seen.add(local_net.id)
            refs.append(_LocalNetRef(page=page, local_net=local_net))
    return refs


def _collect_pin_occurrences(pages: Iterable[DsnPageSource]) -> list[DsnPinOccurrence]:
    occurrences: list[DsnPinOccurrence] = []
    for page in pages:
        occurrences.extend(page.pin_occurrences)
    return occurrences


def _merge_repeated_logical_pins(
    net_union: NetUnion,
    pin_occurrences: Iterable[DsnPinOccurrence],
    local_net_ids: set[str],
) -> None:
    net_ids_by_pin: dict[tuple[str, str], list[str]] = {}
    for pin_occurrence in pin_occurrences:
        if pin_occurrence.local_net_id not in local_net_ids:
            continue
        key = (_component_identity(pin_occurrence), pin_occurrence.pin_designator)
        net_ids_by_pin.setdefault(key, []).append(pin_occurrence.local_net_id)

    for net_ids in net_ids_by_pin.values():
        _merge_ids(net_union, net_ids)


def _merge_globals(
    pages: Iterable[DsnPageSource],
    net_union: NetUnion,
    local_net_ids: set[str],
) -> None:
    ids_by_name: dict[str, list[str]] = {}
    for page in pages:
        for global_ in page.globals:
            if global_.local_net_id in local_net_ids and global_.name_key:
                ids_by_name.setdefault(global_.name_key, []).append(global_.local_net_id)

    for net_ids in ids_by_name.values():
        _merge_ids(net_union, net_ids)


def _merge_known_scope_off_page_connectors(
    pages: Iterable[DsnPageSource],
    net_union: NetUnion,
    local_net_ids: set[str],
) -> None:
    ids_by_scope_name: dict[tuple[tuple[str, ...], str], list[str]] = {}
    for page in pages:
        for connector in page.off_page_connectors:
            if connector.local_net_id not in local_net_ids or not connector.name_key:
                continue
            scope_key = _off_page_scope_key(connector.scope_id)
            ids_by_scope_name.setdefault((scope_key, connector.name_key), []).append(
                connector.local_net_id
            )

    for net_ids in ids_by_scope_name.values():
        _merge_ids(net_union, net_ids)


def _off_page_scope_key(scope_id: ScopeId) -> tuple[str, ...]:
    if len(scope_id.path) > 1:
        return scope_id.path[:-1]
    return scope_id.path


def _merge_ids(net_union: NetUnion, net_ids: list[str]) -> None:
    if len(net_ids) < 2:
        return
    first_id = net_ids[0]
    for net_id in net_ids[1:]:
        net_union.union(first_id, net_id)


def _build_pages(source_pages: Iterable[DsnPageSource]) -> dict[ScopeId, Page]:
    pages: dict[ScopeId, Page] = {}
    for source_page in source_pages:
        pages[source_page.scope_id] = Page(
            id=source_page.id,
            name=source_page.name,
            scope_id=source_page.scope_id,
        )
    return pages


def _collect_name_evidence(pages: Iterable[DsnPageSource]) -> dict[str, _NameEvidence]:
    evidence_by_local_id: dict[str, _NameEvidence] = {}
    for page in pages:
        for page_net in page.nets:
            evidence = evidence_by_local_id.setdefault(page_net.id, _NameEvidence())
            evidence.page_names.append(page_net.name)
        for wire in page.wires:
            evidence = evidence_by_local_id.setdefault(wire.local_net_id, _NameEvidence())
            evidence.aliases.extend(alias.name for alias in wire.aliases)
        for port in page.ports:
            evidence = evidence_by_local_id.setdefault(port.local_net_id, _NameEvidence())
            evidence.ports.append(port.name)
        for global_ in page.globals:
            evidence = evidence_by_local_id.setdefault(global_.local_net_id, _NameEvidence())
            evidence.globals.append(global_.name)
        for connector in page.off_page_connectors:
            evidence = evidence_by_local_id.setdefault(connector.local_net_id, _NameEvidence())
            evidence.off_page_connectors.append(connector.name)
    return evidence_by_local_id


def _build_nets(
    local_refs: list[_LocalNetRef],
    net_union: NetUnion,
    pages: dict[ScopeId, Page],
    name_evidence: dict[str, _NameEvidence],
) -> dict[str, Net]:
    refs_by_group: dict[str, list[_LocalNetRef]] = {}
    for ref in local_refs:
        refs_by_group.setdefault(net_union.find(ref.local_net.id), []).append(ref)

    result: dict[str, Net] = {}
    page_net_ids: dict[str, set[str]] = {
        page.id: {net.id for net in page.nets} for page in pages.values()
    }
    for net_index, (root_id, group_refs) in enumerate(refs_by_group.items(), start=1):
        evidences = [name_evidence.get(ref.local_net.id, _NameEvidence()) for ref in group_refs]
        name = _select_net_name(root_id, evidences)
        aliases = _all_alias_names(evidences)
        aliases.discard(name)
        net = Net(
            id=f"dsn:net:{net_index:04d}",
            name=name,
            aliases=aliases,
            metadata={
                "dsn_root_local_net_id": root_id,
            },
        )
        net_page_ids: set[str] = set()
        for ref in group_refs:
            page = _page_for_scope(pages, ref.page.scope_id)
            occurrence = NetOccurrence(
                id=f"{net.id}:occ:{len(net.occurrences) + 1:04d}",
                net=net,
                page=page,
                scope_id=ref.local_net.scope_id,
                source_local_net_id=ref.local_net.id,
                source_names=_source_names(name_evidence.get(ref.local_net.id, _NameEvidence())),
                metadata={
                    "dsn_source_net_id": str(ref.local_net.net_id),
                },
            )
            net.occurrences.append(occurrence)
            _append_unique_page(net.pages, page, seen_ids=net_page_ids)
            _append_unique_net(
                page.nets,
                net,
                seen_ids=page_net_ids.setdefault(page.id, {existing.id for existing in page.nets}),
            )
            result[ref.local_net.id] = net
    return result


def _select_net_name(root_id: str, evidences: Iterable[_NameEvidence]) -> str:
    generated_page_names: list[str] = []
    non_generated_page_names: list[str] = []
    globals_: list[str] = []
    off_page_connectors: list[str] = []
    ports: list[str] = []
    aliases: list[str] = []
    for evidence in evidences:
        globals_.extend(evidence.globals)
        off_page_connectors.extend(evidence.off_page_connectors)
        ports.extend(evidence.ports)
        aliases.extend(evidence.aliases)
        for page_name in evidence.page_names:
            if _is_generated_page_net_name(page_name):
                generated_page_names.append(page_name)
            else:
                non_generated_page_names.append(page_name)

    for names in (
        globals_,
        off_page_connectors,
        ports,
        non_generated_page_names,
        aliases,
        generated_page_names,
    ):
        for name in _dedupe(names):
            if name:
                return name
    return root_id


def _all_alias_names(evidences: Iterable[_NameEvidence]) -> set[str]:
    names: set[str] = set()
    for evidence in evidences:
        names.update(name for name in evidence.page_names if name)
        names.update(name for name in evidence.globals if name)
        names.update(name for name in evidence.off_page_connectors if name)
        names.update(name for name in evidence.ports if name)
        names.update(name for name in evidence.aliases if name)
    return names


def _source_names(evidence: _NameEvidence) -> set[str]:
    names: set[str] = set()
    names.update(name for name in evidence.page_names if name)
    names.update(name for name in evidence.globals if name)
    names.update(name for name in evidence.off_page_connectors if name)
    names.update(name for name in evidence.ports if name)
    names.update(name for name in evidence.aliases if name)
    return names


def _is_generated_page_net_name(name: str) -> bool:
    return len(name) == 9 and name.startswith("N") and name[1:].isdigit()


def _build_components(
    pages: dict[ScopeId, Page],
    nets_by_local_id: dict[str, Net],
    pin_occurrences: Iterable[DsnPinOccurrence],
) -> list[Component]:
    components_by_id: dict[str, Component] = {}
    occurrences_by_component_page_source: set[tuple[str, str, str]] = set()
    pins_by_component_designator: dict[tuple[str, str], Pin] = {}
    component_page_ids: dict[str, set[str]] = {}
    page_component_ids: dict[str, set[str]] = {
        page.id: {component.id for component in page.components} for page in pages.values()
    }
    net_pin_ids: dict[str, set[str]] = {
        net.id: {pin.id for pin in net.pins} for net in nets_by_local_id.values()
    }

    for pin_occurrence in pin_occurrences:
        page = _page_for_scope(pages, pin_occurrence.scope_id)
        component_id = _component_identity(pin_occurrence)
        component = components_by_id.get(component_id)
        if component is None:
            component = Component(
                id=component_id,
                reference=pin_occurrence.component_reference,
                part=pin_occurrence.component_part,
                description="",
                metadata={
                    "dsn_component_source_ids": pin_occurrence.component_source_id,
                },
            )
            components_by_id[component_id] = component
        elif not component.part and pin_occurrence.component_part:
            component.part = pin_occurrence.component_part

        _append_unique_page(
            component.pages,
            page,
            seen_ids=component_page_ids.setdefault(
                component.id,
                {existing.id for existing in component.pages},
            ),
        )
        _append_unique_component(
            page.components,
            component,
            seen_ids=page_component_ids.setdefault(
                page.id,
                {existing.id for existing in page.components},
            ),
        )
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
                    part_id=pin_occurrence.component_part,
                )
            )

        pin_key = (component.id, pin_occurrence.pin_designator)
        pin = pins_by_component_designator.get(pin_key)
        if pin is None:
            pin = Pin(
                id=f"{component.id}:pin:{pin_occurrence.pin_designator}",
                designator=pin_occurrence.pin_designator,
                name=pin_occurrence.pin_name,
                component=component,
                metadata={
                    "dsn_pin_source_id": pin_occurrence.id,
                },
            )
            pins_by_component_designator[pin_key] = pin
            component.pins.append(pin)
        elif not pin.name and pin_occurrence.pin_name:
            pin.name = pin_occurrence.pin_name

        net = nets_by_local_id.get(pin_occurrence.local_net_id)
        if net is not None:
            if pin.net is not None and pin.net.id != net.id:
                _remove_pin(pin.net.pins, pin)
            pin.net = net
            _append_unique_pin(
                net.pins,
                pin,
                seen_ids=net_pin_ids.setdefault(
                    net.id,
                    {existing.id for existing in net.pins},
                ),
            )

    return list(components_by_id.values())


def _component_identity(pin_occurrence: DsnPinOccurrence) -> str:
    if pin_occurrence.component_source_id:
        return f"dsn:component:{pin_occurrence.component_source_id}"
    scope_key = _scope_key(pin_occurrence.scope_id)
    return f"dsn:component:{scope_key}:{pin_occurrence.component_reference}"


def _scope_key(scope_id: ScopeId) -> str:
    return "root" if not scope_id.path else "/".join(scope_id.path)


def _page_for_scope(pages: dict[ScopeId, Page], scope_id: ScopeId) -> Page:
    page = pages.get(scope_id)
    if page is not None:
        return page
    fallback = Page(
        id=f"page:{_scope_key(scope_id)}",
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
    return sorted(nets, key=lambda net: (len(net.pins) == 0, net.id))


def _append_unique_page(pages: list[Page], page: Page, *, seen_ids: set[str]) -> None:
    if page.id not in seen_ids:
        seen_ids.add(page.id)
        pages.append(page)


def _append_unique_net(nets: list[Net], net: Net, *, seen_ids: set[str]) -> None:
    if net.id not in seen_ids:
        seen_ids.add(net.id)
        nets.append(net)


def _append_unique_component(
    components: list[Component],
    component: Component,
    *,
    seen_ids: set[str],
) -> None:
    if component.id not in seen_ids:
        seen_ids.add(component.id)
        components.append(component)


def _append_unique_pin(pins: list[Pin], pin: Pin, *, seen_ids: set[str]) -> None:
    if pin.id not in seen_ids:
        seen_ids.add(pin.id)
        pins.append(pin)


def _remove_pin(pins: list[Pin], pin: Pin) -> None:
    remaining = [existing for existing in pins if existing.id != pin.id]
    if len(remaining) != len(pins):
        pins[:] = remaining


def _dedupe(names: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result
