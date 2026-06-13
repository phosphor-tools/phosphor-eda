"""Resolve OrCAD DSN-native source connectivity into the public schematic model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import FootprintModel, LibraryLink, Parameter
from phosphor_eda.formats.common.net_union import NetUnion
from phosphor_eda.formats.common.resolved_graph import (
    ResolutionInputError,
    ResolvedComponentInfo,
    ResolvedComponentOccurrenceInput,
    ResolvedLocalNetInput,
    ResolvedNetInput,
    ResolvedPageInput,
    ResolvedPinInput,
    build_resolved_schematic,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.domain.schematic import Net, Schematic, ScopeId
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.dsn.source import (
        DsnPageNet,
        DsnPageSource,
        DsnPinOccurrence,
        DsnSourceDesign,
    )


@dataclass(slots=True)
class _LocalNetRef:
    local_net: DsnPageNet


@dataclass(slots=True)
class _NameEvidence:
    page_names: list[str] = field(default_factory=list)
    globals: list[str] = field(default_factory=list)
    off_page_connectors: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


def resolve_dsn_source(source: DsnSourceDesign, ctx: ParseContext | None = None) -> Schematic:
    """Resolve an OrCAD DSN source design into the public schematic graph.

    Non-fatal issues accumulated on *ctx* are surfaced as
    ``parse_issue_count`` in the resulting schematic metadata.
    """
    local_refs = _collect_local_refs(source.pages)
    local_net_ids = {ref.local_net.id for ref in local_refs}
    local_nets_by_id = {ref.local_net.id: ref.local_net for ref in local_refs}
    _validate_evidence_refs(source.pages, local_nets_by_id)
    net_union = NetUnion(ref.local_net.id for ref in local_refs)
    pin_occurrences = _collect_pin_occurrences(source.pages)

    _merge_repeated_logical_pins(net_union, pin_occurrences, local_net_ids)
    _merge_globals(source.pages, net_union, local_net_ids)
    _merge_known_scope_off_page_connectors(source.pages, net_union, local_net_ids)

    name_evidence = _collect_name_evidence(source.pages)
    metadata = {"dsn_resolver": "source"}
    if ctx is not None and ctx.issues:
        metadata["parse_issue_count"] = str(len(ctx.issues))
    return build_resolved_schematic(
        name=source.name,
        pages=_page_inputs(source.pages),
        local_nets=_local_net_inputs(local_refs, name_evidence),
        pins=_pin_inputs(pin_occurrences),
        net_union=net_union,
        net_factory=lambda net_index, root_id, group_local_nets: _dsn_net_input_for_group(
            name_evidence,
            net_index,
            root_id,
            group_local_nets,
        ),
        include_net=_include_dsn_net,
        net_ordering=_order_dsn_nets,
        metadata=metadata,
    )


def _collect_local_refs(pages: Iterable[DsnPageSource]) -> list[_LocalNetRef]:
    refs: list[_LocalNetRef] = []
    seen: set[str] = set()
    scopes = {page.scope_id for page in pages}
    for page in pages:
        for local_net in page.nets:
            if local_net.id in seen:
                continue
            if local_net.scope_id not in scopes:
                msg = f"local net {local_net.id!r} references unknown scope {local_net.scope_id}"
                raise ResolutionInputError(msg)
            if local_net.scope_id != page.scope_id:
                msg = (
                    f"local net {local_net.id!r} scope {local_net.scope_id} "
                    f"does not match page scope {page.scope_id}"
                )
                raise ResolutionInputError(msg)
            seen.add(local_net.id)
            refs.append(_LocalNetRef(local_net=local_net))
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


def _validate_evidence_refs(
    pages: Iterable[DsnPageSource],
    local_nets_by_id: dict[str, DsnPageNet],
) -> None:
    scopes = {page.scope_id for page in pages}
    for page in pages:
        for wire in page.wires:
            _validate_scoped_local_net_ref(
                kind="wire",
                id_=wire.id,
                scope_id=wire.scope_id,
                local_net_id=wire.local_net_id,
                page_scope_id=page.scope_id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
            for alias in wire.aliases:
                if alias.scope_id != wire.scope_id:
                    msg = (
                        f"wire alias {alias.id!r} scope {alias.scope_id} "
                        f"does not match wire scope {wire.scope_id}"
                    )
                    raise ResolutionInputError(msg)
                if alias.scope_id not in scopes:
                    msg = f"wire alias {alias.id!r} references unknown scope {alias.scope_id}"
                    raise ResolutionInputError(msg)
        for port in page.ports:
            _validate_scoped_local_net_ref(
                kind="port",
                id_=port.id,
                scope_id=port.scope_id,
                local_net_id=port.local_net_id,
                page_scope_id=page.scope_id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for global_ in page.globals:
            _validate_scoped_local_net_ref(
                kind="global",
                id_=global_.id,
                scope_id=global_.scope_id,
                local_net_id=global_.local_net_id,
                page_scope_id=page.scope_id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for connector in page.off_page_connectors:
            _validate_scoped_local_net_ref(
                kind="off-page connector",
                id_=connector.id,
                scope_id=connector.scope_id,
                local_net_id=connector.local_net_id,
                page_scope_id=page.scope_id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )


def _validate_scoped_local_net_ref(
    *,
    kind: str,
    id_: str,
    scope_id: ScopeId,
    local_net_id: str,
    page_scope_id: ScopeId,
    scopes: set[ScopeId],
    local_nets_by_id: dict[str, DsnPageNet],
) -> None:
    if scope_id not in scopes:
        msg = f"{kind} {id_!r} references unknown scope {scope_id}"
        raise ResolutionInputError(msg)
    if scope_id != page_scope_id:
        msg = f"{kind} {id_!r} scope {scope_id} does not match page scope {page_scope_id}"
        raise ResolutionInputError(msg)
    local_net = local_nets_by_id.get(local_net_id)
    if local_net is None:
        msg = f"{kind} {id_!r} references unknown local net {local_net_id!r}"
        raise ResolutionInputError(msg)
    if local_net.scope_id != scope_id:
        msg = (
            f"{kind} {id_!r} scope {scope_id} does not match "
            f"local net {local_net_id!r} scope {local_net.scope_id}"
        )
        raise ResolutionInputError(msg)


def _off_page_scope_key(scope_id: ScopeId) -> tuple[str, ...]:
    if len(scope_id.path) > 1:
        return scope_id.path[:-1]
    return scope_id.path


def _merge_ids(net_union: NetUnion, net_ids: list[str]) -> None:
    if len(net_ids) < 2:
        return
    first_id = net_ids[0]
    for net_id in net_ids[1:]:
        _ = net_union.union(first_id, net_id)


def _page_inputs(source_pages: Iterable[DsnPageSource]) -> list[ResolvedPageInput]:
    return [
        ResolvedPageInput(
            id=source_page.id,
            name=source_page.name,
            scope_id=source_page.scope_id,
            title_block=source_page.title_block,
        )
        for source_page in source_pages
    ]


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


def _local_net_inputs(
    local_refs: list[_LocalNetRef],
    name_evidence: dict[str, _NameEvidence],
) -> list[ResolvedLocalNetInput]:
    return [
        ResolvedLocalNetInput(
            id=ref.local_net.id,
            scope_id=ref.local_net.scope_id,
            source_names=frozenset(
                _source_names(name_evidence.get(ref.local_net.id, _NameEvidence()))
            ),
            metadata={
                "dsn_source_net_id": str(ref.local_net.net_id),
            },
        )
        for ref in local_refs
    ]


def _dsn_net_input_for_group(
    name_evidence: dict[str, _NameEvidence],
    net_index: int,
    root_id: str,
    group_local_nets: tuple[ResolvedLocalNetInput, ...],
) -> ResolvedNetInput:
    evidences = [name_evidence.get(local_net.id, _NameEvidence()) for local_net in group_local_nets]
    name = _select_net_name(root_id, evidences)
    aliases = _all_alias_names(evidences)
    aliases.discard(name)
    return ResolvedNetInput(
        id=f"dsn:net:{net_index:04d}",
        name=name,
        aliases=frozenset(aliases),
        metadata={
            "dsn_root_local_net_id": root_id,
        },
    )


# OrCAD CIS designs name the internal part-database key column either
# "Part Number" or "part_number"; matched case-insensitively.
_PART_NUMBER_PROP_KEYS = frozenset({"part number", "part_number"})

# Exact instance property key carrying the footprint binding.
_FOOTPRINT_PROP_KEY = "PCB Footprint"


def _design_item_id(component_props: dict[str, str]) -> str:
    for name, value in component_props.items():
        if name.casefold() in _PART_NUMBER_PROP_KEYS and value:
            return value
    return ""


def _component_info(pin_occurrence: DsnPinOccurrence) -> ResolvedComponentInfo:
    """Typed enrichment evidence from a placed instance's properties.

    ``explicit_dnp`` stays ``None`` — OrCAD has no native fit flag, so the
    shared DNP convention ladder decides from the parameters.
    """
    props = pin_occurrence.component_props
    prop_entries = pin_occurrence.component_props_list or tuple(props.items())
    footprint = props.get(_FOOTPRINT_PROP_KEY, "")
    lib: LibraryLink | None = None
    if pin_occurrence.component_part or _design_item_id(props):
        lib = LibraryLink(
            symbol=pin_occurrence.component_part,
            design_item_id=_design_item_id(props),
        )
    return ResolvedComponentInfo(
        parameters=tuple(Parameter(name=name, value=value) for name, value in prop_entries),
        lib=lib,
        footprints=(FootprintModel(name=footprint, is_current=True),) if footprint else (),
    )


def _component_metadata(pin_occurrence: DsnPinOccurrence) -> dict[str, str]:
    """Convenience dict of instance properties; empty values are dropped."""
    metadata = {name: value for name, value in pin_occurrence.component_props.items() if value}
    metadata["dsn_component_source_ids"] = pin_occurrence.component_source_id
    return metadata


def _pin_inputs(pin_occurrences: Iterable[DsnPinOccurrence]) -> list[ResolvedPinInput]:
    result: list[ResolvedPinInput] = []
    # All pins of a placed instance share the same property evidence; build
    # the component info once per component identity.
    info_by_component: dict[str, ResolvedComponentInfo] = {}
    for pin_occurrence in pin_occurrences:
        component_id = _component_identity(pin_occurrence)
        component_info = info_by_component.get(component_id)
        if component_info is None:
            component_info = _component_info(pin_occurrence)
            info_by_component[component_id] = component_info
        result.append(
            ResolvedPinInput(
                id=pin_occurrence.id,
                scope_id=pin_occurrence.scope_id,
                local_net_id=pin_occurrence.local_net_id,
                component_id=component_id,
                component_reference=pin_occurrence.component_reference,
                component_part=pin_occurrence.component_part,
                component_description="",
                pin_id=f"{component_id}:pin:{pin_occurrence.pin_designator}",
                pin_designator=pin_occurrence.pin_designator,
                pin_name=pin_occurrence.pin_name,
                no_connect=False,
                component_occurrence=ResolvedComponentOccurrenceInput(
                    source_id=pin_occurrence.component_source_id,
                    part_id=pin_occurrence.component_part,
                    x=pin_occurrence.component_x,
                    y=pin_occurrence.component_y,
                ),
                pin_metadata={
                    "dsn_pin_source_id": pin_occurrence.id,
                },
                pin_occurrence_metadata={
                    "dsn_source_net_id": str(pin_occurrence.source_net_id),
                    "dsn_local_net_id": pin_occurrence.local_net_id or "",
                },
                component_metadata=_component_metadata(pin_occurrence),
                component_info=component_info,
            )
        )
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


def _component_identity(pin_occurrence: DsnPinOccurrence) -> str:
    if pin_occurrence.component_source_id:
        return f"dsn:component:{pin_occurrence.component_source_id}"
    scope_key = _scope_key(pin_occurrence.scope_id)
    return f"dsn:component:{scope_key}:{pin_occurrence.component_reference}"


def _order_dsn_nets(nets: list[Net]) -> list[Net]:
    return sorted(nets, key=lambda net: (len(net.pins) == 0, net.id))


def _include_dsn_net(
    _root_id: str,
    _local_nets: tuple[ResolvedLocalNetInput, ...],
    _pins: tuple[ResolvedPinInput, ...],
) -> bool:
    return True


def _scope_key(scope_id: ScopeId) -> str:
    return "root" if not scope_id.path else "/".join(scope_id.path)


def _dedupe(names: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result
