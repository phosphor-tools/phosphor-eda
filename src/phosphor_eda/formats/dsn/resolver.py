"""Resolve OrCAD DSN-native source connectivity into the public schematic model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.buses import (
    BusDefinition,
    build_buses_from_definitions,
    bus_kind_for_name,
    expand_bus_members,
)
from phosphor_eda.domain.schematic import (
    FootprintModel,
    LibraryLink,
    NetName,
    NetNameKind,
    Parameter,
)
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
from phosphor_eda.formats.dsn.source import dsn_name_key

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from phosphor_eda.domain.schematic import Net, Schematic, ScopeId
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.dsn.source import (
        DsnHierarchyMapping,
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
    """Per-local-net name evidence; graphic *symbol* names are never included."""

    page_names: list[str] = field(default_factory=list)
    power_nets: list[str] = field(default_factory=list)
    off_page_nets: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    wire_dbids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class _NetNameDecision:
    """The resolved canonical name and full evidence list for one net cluster."""

    name: str
    names: tuple[NetName, ...]
    metadata: dict[str, str] = field(default_factory=dict)


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
    name_decisions = _resolve_net_names(source, local_refs, net_union, name_evidence, ctx)
    metadata = {"dsn_resolver": "source"}
    if ctx is not None and ctx.issues:
        metadata["parse_issue_count"] = str(len(ctx.issues))
    design = build_resolved_schematic(
        name=source.name,
        pages=_page_inputs(source.pages),
        local_nets=_local_net_inputs(local_refs, name_evidence),
        pins=_pin_inputs(pin_occurrences),
        net_union=net_union,
        net_factory=lambda net_index, root_id, _group_local_nets: _dsn_net_input_for_group(
            name_decisions,
            net_index,
            root_id,
        ),
        include_net=_include_dsn_net,
        net_ordering=_order_dsn_nets,
        metadata=metadata,
    )
    design.buses = build_buses_from_definitions(design, _dsn_bus_definitions(source))
    return design


def _dsn_bus_definitions(source: DsnSourceDesign) -> list[BusDefinition]:
    definitions: list[BusDefinition] = []
    seen: set[tuple[str, str]] = set()
    bus_index = 0
    for page in source.pages:
        for wire in page.wires:
            if not wire.is_bus:
                continue
            for alias in wire.aliases:
                name = alias.name.strip()
                kind = bus_kind_for_name(name)
                member_names = tuple(expand_bus_members(name) or ())
                if kind is None or not member_names or (kind.value, name) in seen:
                    continue
                seen.add((kind.value, name))
                bus_index += 1
                definitions.append(
                    BusDefinition(
                        id=f"dsn:bus:{kind.value}:{bus_index:04d}",
                        name=name,
                        kind=kind,
                        member_names=member_names,
                        metadata={
                            "source_format": "dsn",
                            "source_id": alias.id,
                            "source_kind": "wire_alias",
                            "source_page": page.name,
                        },
                    )
                )
    return definitions


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
            if page_net.name:
                evidence.page_names.append(page_net.name)
        for wire in page.wires:
            evidence = evidence_by_local_id.setdefault(wire.local_net_id, _NameEvidence())
            for alias in wire.aliases:
                alias_name = alias.name.strip()
                if alias_name and bus_kind_for_name(alias_name) is None:
                    evidence.aliases.append(alias_name)
            if wire.db_id > 0:
                evidence.wire_dbids.append(wire.db_id)
        for port in page.ports:
            evidence = evidence_by_local_id.setdefault(port.local_net_id, _NameEvidence())
            evidence.ports.append(port.name)
        for global_ in page.globals:
            evidence = evidence_by_local_id.setdefault(global_.local_net_id, _NameEvidence())
            evidence.power_nets.append(global_.name)
        for connector in page.off_page_connectors:
            evidence = evidence_by_local_id.setdefault(connector.local_net_id, _NameEvidence())
            evidence.off_page_nets.append(connector.name)
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
    name_decisions: dict[str, _NetNameDecision],
    net_index: int,
    root_id: str,
) -> ResolvedNetInput:
    decision = name_decisions[root_id]
    metadata = {"dsn_root_local_net_id": root_id}
    metadata.update(decision.metadata)
    return ResolvedNetInput(
        id=f"dsn:net:{net_index:04d}",
        name=decision.name,
        names=decision.names,
        metadata=metadata,
    )


# Capture autonames are "N" + decimal seed-wire dbid, zero-padded to a
# minimum of five digits (N00529); corpus maxima reach ten digits.
_AUTONAME_FORM = re.compile(r"N\d{5,}")


def _is_autoname_form(name: str) -> bool:
    return _AUTONAME_FORM.fullmatch(name) is not None


def _stored_name_kind(name: str) -> NetNameKind:
    return NetNameKind.TOOL_AUTO if _is_autoname_form(name) else NetNameKind.LABEL


def _resolve_net_names(
    source: DsnSourceDesign,
    local_refs: list[_LocalNetRef],
    net_union: NetUnion,
    evidence_by_local_id: dict[str, _NameEvidence],
    ctx: ParseContext | None,
) -> dict[str, _NetNameDecision]:
    """Decide each net cluster's canonical name from stored evidence.

    Capture materializes every resolved net name into the DSN, so this
    reads rather than reconstructs: a page net list name wins outright;
    anonymous clusters take the ``N{min seed-wire dbid:05d}`` autoname
    (validated against the Hierarchy mapping when one exists); remaining
    clusters are reconciled against leftover mapping autonames by
    elimination; only then is a name synthesized, flagged and diagnosed.
    """
    groups: dict[str, list[DsnPageNet]] = {}
    for ref in local_refs:
        groups.setdefault(net_union.find(ref.local_net.id), []).append(ref.local_net)

    mappings = source.hierarchy_mappings
    mapping_keys = {mapping.name_key for mapping in mappings}

    decisions: dict[str, _NetNameDecision] = {}
    unresolved: list[str] = []
    claimed_keys: set[str] = set()

    for root_id, group in groups.items():
        evidence_names = _group_evidence_names(group, evidence_by_local_id)
        seed_dbid = _min_seed_wire_dbid(group, evidence_by_local_id)
        derived = f"N{seed_dbid:05d}" if seed_dbid is not None else ""

        canonical = ""
        metadata: dict[str, str] = {}
        if evidence_names:
            # The page net list name is the materialized resolved name and
            # wins outright; _group_evidence_names lists page names first.
            # When a cluster carries several stored names (hierarchical
            # block occurrences), the Hierarchy mapping holds the one
            # Capture resolved to — prefer it.
            canonical = _select_canonical(evidence_names, mapping_keys)
        elif derived and (not mappings or dsn_name_key(derived) in mapping_keys):
            # An anonymous cluster carries Capture's autoname: N + the
            # minimum seed-wire dbid. When a Hierarchy mapping exists it
            # must confirm the derivation (a missing entry means the seed
            # wire was deleted and the derived number is stale).
            canonical = derived
            evidence_names.append(
                NetName(
                    name=derived,
                    kind=NetNameKind.TOOL_AUTO,
                    scope=None,
                    source="seed_wire_dbid",
                )
            )
            metadata["dsn_seed_wire_dbid"] = str(seed_dbid)

        if canonical:
            if mappings and dsn_name_key(canonical) in mapping_keys:
                evidence_names.append(
                    NetName(
                        name=canonical,
                        kind=_stored_name_kind(canonical),
                        scope=None,
                        source="hierarchy_mapping",
                    )
                )
            claimed_keys.add(dsn_name_key(canonical))
            decisions[root_id] = _NetNameDecision(
                name=canonical,
                names=tuple(evidence_names),
                metadata=metadata,
            )
        else:
            unresolved.append(root_id)

    _resolve_unnamed_groups(groups, unresolved, mappings, claimed_keys, decisions, ctx)
    return decisions


def _select_canonical(evidence_names: list[NetName], mapping_keys: set[str]) -> str:
    """The strongest evidence name, preferring one the Hierarchy mapping confirms.

    Stored page names always beat alias-grade evidence: the mapping
    preference only arbitrates within the strongest source present.
    """
    page_entries = [entry for entry in evidence_names if entry.source == "page_net"]
    pool = page_entries or evidence_names
    if mapping_keys:
        for entry in pool:
            if dsn_name_key(entry.name) in mapping_keys:
                return entry.name
    return pool[0].name


def _group_evidence_names(
    group: list[DsnPageNet],
    evidence_by_local_id: dict[str, _NameEvidence],
) -> list[NetName]:
    """Typed name evidence for a cluster, strongest source first.

    Page net list names lead (they are Capture's stored resolved name);
    wire aliases, power-symbol net names, off-page connector net names,
    and port names follow as alias-grade evidence.
    """
    entries: list[NetName] = []
    seen: set[tuple[str, NetNameKind, ScopeId | None, str]] = set()

    def add(name: str, kind: NetNameKind, scope: ScopeId | None, source: str) -> None:
        key = (name, kind, scope, source)
        if name and key not in seen:
            seen.add(key)
            entries.append(NetName(name=name, kind=kind, scope=scope, source=source))

    for local_net in group:
        evidence = evidence_by_local_id.get(local_net.id, _NameEvidence())
        for name in evidence.page_names:
            add(name, _stored_name_kind(name), local_net.scope_id, "page_net")
    label_sources: tuple[tuple[Callable[[_NameEvidence], list[str]], str], ...] = (
        (lambda evidence: evidence.aliases, "wire_alias"),
        (lambda evidence: evidence.power_nets, "power_symbol"),
        (lambda evidence: evidence.off_page_nets, "off_page_connector"),
        (lambda evidence: evidence.ports, "port"),
    )
    for names_of, source_label in label_sources:
        for local_net in group:
            evidence = evidence_by_local_id.get(local_net.id, _NameEvidence())
            for name in names_of(evidence):
                add(name, NetNameKind.LABEL, local_net.scope_id, source_label)
    return entries


def _min_seed_wire_dbid(
    group: list[DsnPageNet],
    evidence_by_local_id: dict[str, _NameEvidence],
) -> int | None:
    """The cluster's minimum seed-wire dbid — the number in its autoname."""
    dbids = [
        dbid
        for local_net in group
        for dbid in evidence_by_local_id.get(local_net.id, _NameEvidence()).wire_dbids
    ]
    return min(dbids) if dbids else None


def _resolve_unnamed_groups(
    groups: dict[str, list[DsnPageNet]],
    unresolved: list[str],
    mappings: list[DsnHierarchyMapping],
    claimed_keys: set[str],
    decisions: dict[str, _NetNameDecision],
    ctx: ParseContext | None,
) -> None:
    """Name the clusters no stored evidence reached.

    A mapping-only autoname (its seed wire was deleted) is adopted by
    elimination when exactly one leftover autoname and one unnamed cluster
    remain; anything else gets a synthesized placeholder plus a diagnostic.
    Bus objects (``…[a..b]``) stay in the mapping but are not net names.
    """
    leftover_autonames = [
        mapping
        for mapping in mappings
        if _is_autoname_form(mapping.name) and mapping.name_key not in claimed_keys
    ]
    if len(unresolved) == 1 and len(leftover_autonames) == 1:
        root_id = unresolved[0]
        mapping = leftover_autonames[0]
        decisions[root_id] = _NetNameDecision(
            name=mapping.name,
            names=(
                NetName(
                    name=mapping.name,
                    kind=NetNameKind.TOOL_AUTO,
                    scope=None,
                    source="hierarchy_mapping",
                ),
            ),
        )
        return

    for root_id in unresolved:
        name = _synthesized_name(root_id, groups[root_id])
        if ctx is not None:
            ctx.warn(
                "dsn_net_name_synthesized",
                f"net {root_id!r} has no stored name in the DSN; synthesized placeholder {name!r}",
            )
        decisions[root_id] = _NetNameDecision(
            name=name,
            names=(
                NetName(
                    name=name,
                    kind=NetNameKind.SYNTHESIZED,
                    scope=None,
                    source="synthesized",
                ),
            ),
        )


def _synthesized_name(root_id: str, group: list[DsnPageNet]) -> str:
    """Placeholder for a net Capture stored no name for (N{page-net id:08d})."""
    net_ids = [local_net.net_id for local_net in group if local_net.net_id >= 0]
    if net_ids:
        return f"N{min(net_ids):08d}"
    return root_id


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
    footprint = props.get(_FOOTPRINT_PROP_KEY, "")
    lib: LibraryLink | None = None
    if pin_occurrence.component_part or _design_item_id(props):
        lib = LibraryLink(
            symbol=pin_occurrence.component_part,
            design_item_id=_design_item_id(props),
        )
    return ResolvedComponentInfo(
        parameters=tuple(Parameter(name=name, value=value) for name, value in props.items()),
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


def _source_names(evidence: _NameEvidence) -> set[str]:
    names: set[str] = set()
    names.update(name for name in evidence.page_names if name)
    names.update(name for name in evidence.power_nets if name)
    names.update(name for name in evidence.off_page_nets if name)
    names.update(name for name in evidence.ports if name)
    names.update(name for name in evidence.aliases if name)
    return names


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
