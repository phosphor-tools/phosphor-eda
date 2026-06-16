"""Shared construction of public resolved schematic graphs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import (
    Component,
    ComponentKind,
    ComponentOccurrence,
    DnpSource,
    Net,
    NetName,
    NetOccurrence,
    Page,
    Pin,
    PinOccurrence,
    Schematic,
)
from phosphor_eda.formats.common.part_fields import resolve_part_fields

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import (
        FootprintModel,
        LibraryLink,
        Parameter,
        SchematicDirective,
        ScopeId,
        TitleBlock,
    )
    from phosphor_eda.formats.common.net_union import NetUnion


class ResolutionInputError(ValueError):
    """Raised when format-specific resolution emits inconsistent graph input."""


@dataclass(frozen=True, slots=True)
class ResolvedPageInput:
    id: str
    name: str
    scope_id: ScopeId
    source_file: str = ""
    title_block: TitleBlock | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedLocalNetInput:
    id: str
    scope_id: ScopeId
    source_names: frozenset[str] = field(default_factory=frozenset)
    directives: tuple[SchematicDirective, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedNetInput:
    """Format-resolved net identity.

    ``names`` carries typed name evidence; when present and ``aliases`` is
    empty, the alias set is derived from it (every evidence name except the
    canonical one). Formats without name evidence yet keep supplying
    ``aliases`` directly.
    """

    id: str
    name: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    names: tuple[NetName, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedComponentOccurrenceInput:
    source_id: str
    part_id: str = ""
    x: float | None = None
    y: float | None = None
    rotation: float = 0.0
    mirror: bool = False
    physical_designator: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedComponentInfo:
    """Typed component enrichment supplied by a format resolver.

    ``explicit_dnp`` is the format's native fit flag (KiCad ``dnp``);
    ``None`` means the format has no such flag and the shared DNP
    convention ladder decides instead.
    """

    parameters: tuple[Parameter, ...] = ()
    lib: LibraryLink | None = None
    footprints: tuple[FootprintModel, ...] = ()
    kind: ComponentKind = ComponentKind.STANDARD
    explicit_dnp: bool | None = None
    exclude_from_bom: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedPinInput:
    id: str
    scope_id: ScopeId
    local_net_id: str | None
    component_id: str
    component_reference: str
    component_part: str
    component_description: str
    pin_id: str
    pin_designator: str
    pin_name: str
    no_connect: bool
    component_occurrence: ResolvedComponentOccurrenceInput
    pin_metadata: dict[str, str] = field(default_factory=dict)
    pin_occurrence_metadata: dict[str, str] = field(default_factory=dict)
    component_metadata: dict[str, str] = field(default_factory=dict)
    component_info: ResolvedComponentInfo | None = None


type ResolvedNetFactory = Callable[
    [int, str, tuple[ResolvedLocalNetInput, ...]],
    ResolvedNetInput,
]
type ResolvedNetOrdering = Callable[[list[Net]], list[Net]]
type ResolvedNetInclusion = Callable[
    [str, tuple[ResolvedLocalNetInput, ...], tuple[ResolvedPinInput, ...]],
    bool,
]


def build_resolved_schematic(
    *,
    name: str,
    pages: Iterable[ResolvedPageInput],
    local_nets: Iterable[ResolvedLocalNetInput],
    pins: Iterable[ResolvedPinInput],
    net_union: NetUnion,
    net_factory: ResolvedNetFactory,
    include_net: ResolvedNetInclusion,
    net_ordering: ResolvedNetOrdering | None = None,
    metadata: dict[str, str] | None = None,
) -> Schematic:
    """Build the public schematic graph from format-resolved inputs."""
    pages_by_scope = _build_pages(pages)
    local_nets_by_id = _build_local_nets(local_nets, pages_by_scope, net_union)
    pin_inputs = list(pins)
    _validate_pins(pin_inputs, pages_by_scope, local_nets_by_id)
    nets_by_local_id = _build_nets(
        local_nets_by_id.values(),
        pages_by_scope,
        net_union,
        net_factory,
        include_net,
        tuple(pin_inputs),
    )
    _validate_pin_nets_are_included(pin_inputs, nets_by_local_id)
    components = _build_components(pages_by_scope, nets_by_local_id, pin_inputs)

    return Schematic(
        name=name,
        pages=list(pages_by_scope.values()),
        nets=_ordered_nets(nets_by_local_id, net_ordering),
        components=components,
        metadata={} if metadata is None else dict(metadata),
    )


def _build_pages(pages: Iterable[ResolvedPageInput]) -> dict[ScopeId, Page]:
    result: dict[ScopeId, Page] = {}
    page_ids: set[str] = set()
    for page_input in pages:
        if page_input.scope_id in result:
            msg = f"duplicate page scope {page_input.scope_id}"
            raise ResolutionInputError(msg)
        if page_input.id in page_ids:
            msg = f"duplicate page id {page_input.id!r}"
            raise ResolutionInputError(msg)
        page_ids.add(page_input.id)
        result[page_input.scope_id] = Page(
            id=page_input.id,
            name=page_input.name,
            source_file=page_input.source_file,
            scope_id=page_input.scope_id,
            title_block=page_input.title_block,
            metadata=dict(page_input.metadata),
        )
    return result


def _build_local_nets(
    local_nets: Iterable[ResolvedLocalNetInput],
    pages_by_scope: dict[ScopeId, Page],
    net_union: NetUnion,
) -> dict[str, ResolvedLocalNetInput]:
    result: dict[str, ResolvedLocalNetInput] = {}
    for local_net in local_nets:
        if local_net.id in result:
            msg = f"duplicate local net id {local_net.id!r}"
            raise ResolutionInputError(msg)
        if local_net.scope_id not in pages_by_scope:
            msg = f"local net {local_net.id!r} references unknown scope {local_net.scope_id}"
            raise ResolutionInputError(msg)
        _ = _require_union_id(net_union, local_net.id)
        result[local_net.id] = local_net
    return result


def _validate_pins(
    pins: Iterable[ResolvedPinInput],
    pages_by_scope: dict[ScopeId, Page],
    local_nets_by_id: dict[str, ResolvedLocalNetInput],
) -> None:
    for pin in pins:
        if pin.scope_id not in pages_by_scope:
            msg = f"pin {pin.id!r} references unknown scope {pin.scope_id}"
            raise ResolutionInputError(msg)
        if pin.local_net_id is None:
            continue
        if pin.local_net_id not in local_nets_by_id:
            msg = f"pin {pin.id!r} references unknown local net {pin.local_net_id!r}"
            raise ResolutionInputError(msg)
        local_net = local_nets_by_id[pin.local_net_id]
        if local_net.scope_id != pin.scope_id:
            msg = (
                f"pin {pin.id!r} scope {pin.scope_id} does not match "
                f"local net {pin.local_net_id!r} scope {local_net.scope_id}"
            )
            raise ResolutionInputError(msg)


def _build_nets(
    local_nets: Iterable[ResolvedLocalNetInput],
    pages_by_scope: dict[ScopeId, Page],
    net_union: NetUnion,
    net_factory: ResolvedNetFactory,
    include_net: ResolvedNetInclusion,
    pins: tuple[ResolvedPinInput, ...],
) -> dict[str, Net]:
    local_nets_by_group: dict[str, list[ResolvedLocalNetInput]] = {}
    for local_net in local_nets:
        root_id = _require_union_id(net_union, local_net.id)
        local_nets_by_group.setdefault(root_id, []).append(local_net)

    result: dict[str, Net] = {}
    page_net_ids: dict[str, set[str]] = {
        page.id: {net.id for net in page.nets} for page in pages_by_scope.values()
    }
    net_index = 0
    for root_id, group_local_nets in local_nets_by_group.items():
        if not include_net(root_id, tuple(group_local_nets), pins):
            continue
        net_index += 1
        net_input = net_factory(net_index, root_id, tuple(group_local_nets))
        aliases = set(net_input.aliases)
        if net_input.names and not aliases:
            aliases = {evidence.name for evidence in net_input.names} - {net_input.name}
        net = Net(
            id=net_input.id,
            name=net_input.name,
            names=list(net_input.names),
            aliases=aliases,
            metadata=dict(net_input.metadata),
        )
        net_page_ids: set[str] = set()
        net_directive_keys: set[tuple[str, str, str, str, str]] = set()
        for local_net in group_local_nets:
            page = _page_for_scope(pages_by_scope, local_net.scope_id)
            occurrence = NetOccurrence(
                id=f"{net.id}:occ:{len(net.occurrences) + 1:04d}",
                net=net,
                page=page,
                scope_id=local_net.scope_id,
                source_local_net_id=local_net.id,
                source_names=set(local_net.source_names),
                directives=list(local_net.directives),
                metadata=dict(local_net.metadata),
            )
            net.occurrences.append(occurrence)
            _append_unique_directives(
                net.directives,
                local_net.directives,
                seen_keys=net_directive_keys,
            )
            _append_unique_page(net.pages, page, seen_ids=net_page_ids)
            _append_unique_net(
                page.nets,
                net,
                seen_ids=page_net_ids.setdefault(page.id, {existing.id for existing in page.nets}),
            )
            result[local_net.id] = net
    return result


def _validate_pin_nets_are_included(
    pins: Iterable[ResolvedPinInput],
    nets_by_local_id: dict[str, Net],
) -> None:
    for pin in pins:
        if pin.local_net_id is None:
            continue
        if pin.local_net_id not in nets_by_local_id:
            msg = f"pin {pin.id!r} references filtered local net {pin.local_net_id!r}"
            raise ResolutionInputError(msg)


def _build_components(
    pages_by_scope: dict[ScopeId, Page],
    nets_by_local_id: dict[str, Net],
    pins: Iterable[ResolvedPinInput],
) -> list[Component]:
    components_by_id: dict[str, Component] = {}
    occurrences_by_component_page_source: set[tuple[str, str, str]] = set()
    pins_by_id: dict[str, Pin] = {}
    component_page_ids: dict[str, set[str]] = {}
    page_component_ids: dict[str, set[str]] = {
        page.id: {component.id for component in page.components} for page in pages_by_scope.values()
    }
    net_pin_ids: dict[str, set[str]] = {
        net.id: {pin.id for pin in net.pins} for net in nets_by_local_id.values()
    }

    for pin_input in pins:
        page = _page_for_scope(pages_by_scope, pin_input.scope_id)
        component = components_by_id.get(pin_input.component_id)
        if component is None:
            component = Component(
                id=pin_input.component_id,
                reference=pin_input.component_reference,
                part=pin_input.component_part,
                description=pin_input.component_description,
                metadata=dict(pin_input.component_metadata),
            )
            components_by_id[component.id] = component
            _apply_component_info(component, pin_input.component_info)
        else:
            part_backfilled = False
            if not component.part and pin_input.component_part:
                component.part = pin_input.component_part
                part_backfilled = True
            if not component.description and pin_input.component_description:
                component.description = pin_input.component_description
            _merge_missing_metadata(component.metadata, pin_input.component_metadata)
            if part_backfilled or not component.parameters:
                _apply_component_info(component, pin_input.component_info)

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
        occurrence_key = (
            component.id,
            page.id,
            pin_input.component_occurrence.source_id,
        )
        if occurrence_key not in occurrences_by_component_page_source:
            occurrences_by_component_page_source.add(occurrence_key)
            component.occurrences.append(
                ComponentOccurrence(
                    id=f"{component.id}:occ:{len(component.occurrences) + 1:04d}",
                    component=component,
                    page=page,
                    scope_id=pin_input.scope_id,
                    source_id=pin_input.component_occurrence.source_id,
                    part_id=pin_input.component_occurrence.part_id,
                    x=pin_input.component_occurrence.x,
                    y=pin_input.component_occurrence.y,
                    rotation=pin_input.component_occurrence.rotation,
                    mirror=pin_input.component_occurrence.mirror,
                    physical_designator=pin_input.component_occurrence.physical_designator,
                    metadata=dict(pin_input.component_occurrence.metadata),
                )
            )

        pin = pins_by_id.get(pin_input.pin_id)
        if pin is None:
            pin = Pin(
                id=pin_input.pin_id,
                designator=pin_input.pin_designator,
                name=pin_input.pin_name,
                component=component,
                no_connect=pin_input.no_connect,
                metadata=dict(pin_input.pin_metadata),
            )
            pins_by_id[pin_input.pin_id] = pin
            component.pins.append(pin)
        else:
            if not pin.name and pin_input.pin_name:
                pin.name = pin_input.pin_name
            _merge_missing_metadata(pin.metadata, pin_input.pin_metadata)

        pin.occurrences.append(
            PinOccurrence(
                id=f"{pin.id}:occ:{len(pin.occurrences) + 1:04d}",
                pin=pin,
                page=page,
                scope_id=pin_input.scope_id,
                source_id=pin_input.id,
                metadata=dict(pin_input.pin_occurrence_metadata),
            )
        )

        if pin_input.local_net_id is not None:
            net = nets_by_local_id[pin_input.local_net_id]
            if pin.net is not None and pin.net.id != net.id:
                _remove_pin(pin.net.pins, pin)
            pin.net = net
            _append_unique_pin(
                net.pins,
                pin,
                seen_ids=net_pin_ids.setdefault(net.id, {existing.id for existing in net.pins}),
            )

    return list(components_by_id.values())


def _apply_component_info(component: Component, info: ResolvedComponentInfo | None) -> None:
    """Populate a component's typed enrichment fields from resolver evidence.

    Runs the shared recognized-name resolution over the parameter list, then
    the DNP ladder: a native explicit flag decides when the format has one;
    otherwise the whole-value convention match does (decision 25).
    """
    if info is None:
        return
    component.kind = info.kind
    component.parameters = list(info.parameters)
    component.lib = info.lib
    component.footprints = list(info.footprints)
    component.exclude_from_bom = info.exclude_from_bom

    fields = resolve_part_fields(component.parameters, part=component.part)
    component.part_numbers = fields.part_numbers
    component.datasheet = fields.datasheet
    if info.explicit_dnp is not None:
        component.dnp = info.explicit_dnp
        component.dnp_source = DnpSource.EXPLICIT if info.explicit_dnp else None
    else:
        component.dnp = fields.dnp_convention
        component.dnp_source = DnpSource.CONVENTION if fields.dnp_convention else None


def _require_union_id(net_union: NetUnion, local_net_id: str) -> str:
    try:
        return net_union.find(local_net_id)
    except KeyError as exc:
        msg = f"local net {local_net_id!r} is missing from net union"
        raise ResolutionInputError(msg) from exc


def _page_for_scope(pages: dict[ScopeId, Page], scope_id: ScopeId) -> Page:
    page = pages.get(scope_id)
    if page is not None:
        return page
    msg = f"unknown page scope {scope_id}"
    raise ResolutionInputError(msg)


def _ordered_nets(
    nets_by_local_id: dict[str, Net],
    net_ordering: ResolvedNetOrdering | None,
) -> list[Net]:
    nets: list[Net] = []
    seen: set[str] = set()
    for net in nets_by_local_id.values():
        if net.id in seen:
            continue
        seen.add(net.id)
        nets.append(net)
    if net_ordering is None:
        return nets
    return net_ordering(nets)


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


def _append_unique_directives(
    target: list[SchematicDirective],
    directives: Iterable[SchematicDirective],
    *,
    seen_keys: set[tuple[str, str, str, str, str]],
) -> None:
    for directive in directives:
        key = (
            directive.kind.value,
            directive.value,
            directive.source,
            directive.source_id,
            directive.native_name,
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        target.append(directive)


def _merge_missing_metadata(target: dict[str, str], source: dict[str, str]) -> None:
    for key, value in source.items():
        if key and value and key not in target:
            target[key] = value
