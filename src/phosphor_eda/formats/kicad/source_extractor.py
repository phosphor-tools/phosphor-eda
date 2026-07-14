"""KiCad sheet source-object extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from phosphor_eda.domain.buses import expand_bus_members
from phosphor_eda.domain.schematic import SchematicDirective, SchematicDirectiveKind
from phosphor_eda.formats.kicad.lib_symbols import strip_kicad_markup
from phosphor_eda.formats.kicad.source import (
    KiCadBusAlias,
    KiCadBusEntry,
    KiCadBusLabel,
    KiCadBusSheetPin,
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadLocalNet,
    KiCadNetclassFlag,
    KiCadPinOccurrence,
    KiCadPowerSymbol,
    KiCadSheetAnnotation,
    KiCadSheetPin,
    KiCadSheetSymbol,
)
from phosphor_eda.formats.kicad.source_candidates import extract_source_candidates
from phosphor_eda.formats.kicad.wire_graph import build_bus_graph, build_wire_graph

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.domain.schematic import ScopeId
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.kicad.lib_symbols import LibPins, LibPowerKinds
    from phosphor_eda.formats.kicad.sheet_loader import LoadedSheet
    from phosphor_eda.formats.kicad.source import KiCadPoint


@dataclass(slots=True)
class _ExtractedSheet:
    local_nets: list[KiCadLocalNet]
    pin_occurrences: list[KiCadPinOccurrence]
    local_labels: list[KiCadLocalLabel]
    global_labels: list[KiCadGlobalLabel]
    hierarchical_labels: list[KiCadHierarchicalLabel]
    bus_labels: list[KiCadBusLabel]
    bus_aliases: list[KiCadBusAlias]
    bus_entries: list[KiCadBusEntry]
    power_symbols: list[KiCadPowerSymbol]
    sheet_symbols: list[KiCadSheetSymbol]
    sheet_pins: list[KiCadSheetPin]
    bus_sheet_pins: list[KiCadBusSheetPin]
    annotations: list[KiCadSheetAnnotation]


class _HasLocalNetId(Protocol):
    local_net_id: str


class _BusNameSource(Protocol):
    """A named attachment to a bus group: a bus label or a bus sheet pin."""

    id: str
    scope_id: ScopeId
    source_index: int
    name: str


def extract_sheet_sources(
    loaded: LoadedSheet,
    lib_pins: LibPins,
    lib_descs: dict[str, str],
    lib_power_kinds: LibPowerKinds,
    loaded_scopes: set[ScopeId],
    root_uuid: str = "",
    text_variables: Mapping[str, str] | None = None,
    ctx: ParseContext | None = None,
) -> _ExtractedSheet:
    scope_id = loaded.instance.scope_id
    data = loaded.data
    wire_graph = build_wire_graph(data)
    bus_graph = build_bus_graph(data)

    candidates = extract_source_candidates(
        data,
        scope_id,
        lib_pins,
        lib_descs,
        lib_power_kinds,
        wire_graph,
        bus_graph,
        root_uuid,
        text_variables,
        ctx,
    )

    root_to_points = wire_graph.root_to_points()
    root_to_net_id = _local_net_ids(scope_id, root_to_points)

    local_labels = [
        KiCadLocalLabel(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            name=candidate.name,
            location=candidate.location,
            local_net_id=root_to_net_id[wire_graph.find(candidate.location)],
        )
        for candidate in candidates.local_labels
    ]
    global_labels = [
        KiCadGlobalLabel(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            name=candidate.name,
            location=candidate.location,
            local_net_id=root_to_net_id[wire_graph.find(candidate.location)],
        )
        for candidate in candidates.global_labels
    ]
    hierarchical_labels = [
        KiCadHierarchicalLabel(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            name=candidate.name,
            location=candidate.location,
            local_net_id=root_to_net_id[wire_graph.find(candidate.location)],
        )
        for candidate in candidates.hierarchical_labels
    ]
    bus_labels = [
        KiCadBusLabel(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            name=candidate.name,
            location=candidate.location,
            kind=candidate.kind,
            bus_group_id=candidate.bus_group_id,
        )
        for candidate in candidates.bus_labels
    ]
    bus_aliases = [
        KiCadBusAlias(
            id=candidate.id,
            scope_id=candidate.scope_id,
            name=candidate.name,
            members=candidate.members,
        )
        for candidate in candidates.bus_aliases
    ]
    bus_entries = [
        KiCadBusEntry(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            start=candidate.start,
            end=candidate.end,
            wire_point=candidate.wire_point,
            bus_point=candidate.bus_point,
            local_net_id=root_to_net_id[wire_graph.find(candidate.wire_point)],
            bus_group_id=candidate.bus_group_id,
        )
        for candidate in candidates.bus_entries
    ]
    power_symbols = [
        KiCadPowerSymbol(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            name=candidate.name,
            reference=candidate.reference,
            lib_id=candidate.lib_id,
            location=candidate.location,
            local_net_id=root_to_net_id[wire_graph.find(candidate.location)],
            power_kind=candidate.power_kind,
        )
        for candidate in candidates.power_symbols
    ]
    sheet_pins = [
        KiCadSheetPin(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            sheet_symbol_id=candidate.sheet_symbol_id,
            child_scope_id=candidate.child_scope_id,
            name=candidate.name,
            direction=candidate.direction,
            location=candidate.location,
            local_net_id=root_to_net_id[wire_graph.find(candidate.location)],
        )
        for candidate in candidates.sheet_pins
        if candidate.child_scope_id in loaded_scopes
    ]
    bus_sheet_pins = [
        KiCadBusSheetPin(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            sheet_symbol_id=candidate.sheet_symbol_id,
            child_scope_id=candidate.child_scope_id,
            name=candidate.name,
            direction=candidate.direction,
            location=candidate.location,
            bus_group_id=candidate.bus_group_id,
        )
        for candidate in candidates.bus_sheet_pins
        if candidate.child_scope_id in loaded_scopes
    ]
    sheet_symbols = [
        symbol for symbol in candidates.sheet_symbols if symbol.child_scope_id in loaded_scopes
    ]
    annotations = [
        KiCadSheetAnnotation(
            scope_id=candidate.scope_id,
            text=candidate.text,
        )
        for candidate in candidates.annotations
    ]

    _assign_bus_entry_members(
        bus_entries,
        bus_labels=bus_labels,
        bus_aliases=bus_aliases,
        bus_sheet_pins=bus_sheet_pins,
        local_labels=local_labels,
        global_labels=global_labels,
        hierarchical_labels=hierarchical_labels,
        power_symbols=power_symbols,
        sheet_pins=sheet_pins,
    )

    netclass_flags = [
        KiCadNetclassFlag(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            local_net_id=root_to_net_id[wire_graph.find(candidate.location)],
            location=candidate.location,
            rotation=candidate.rotation,
            net_class=candidate.net_class,
            component_class=candidate.component_class,
            metadata=dict(candidate.metadata),
        )
        for candidate in candidates.netclass_flags
    ]
    pin_occurrences = [
        KiCadPinOccurrence(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            local_net_id=root_to_net_id[wire_graph.find(candidate.location)],
            component_source_id=candidate.component_source_id,
            component_identity_source_id=candidate.component_identity_source_id,
            component_unit=candidate.component_unit,
            component_has_multiple_units=candidate.component_has_multiple_units,
            component_reference=candidate.component_reference,
            component_value=candidate.component_value,
            component_footprint=candidate.component_footprint,
            component_datasheet=candidate.component_datasheet,
            component_description=candidate.component_description,
            component_x=candidate.component_x,
            component_y=candidate.component_y,
            component_rotation=candidate.component_rotation,
            component_mirror=candidate.component_mirror,
            component_info=candidate.component_info,
            component_attr_metadata=candidate.component_attr_metadata,
            pin_designator=candidate.pin_designator,
            pin_name=candidate.pin_name,
            pin_net_name=candidate.pin_net_name,
            pin_type=candidate.pin_type,
            location=candidate.location,
            no_connect=candidate.no_connect,
        )
        for candidate in candidates.pin_occurrences
    ]

    local_nets = _build_local_nets(
        scope_id=scope_id,
        root_to_points=root_to_points,
        root_to_net_id=root_to_net_id,
        local_labels=local_labels,
        global_labels=global_labels,
        hierarchical_labels=hierarchical_labels,
        power_symbols=power_symbols,
        sheet_pins=sheet_pins,
        bus_entries=bus_entries,
        netclass_flags=netclass_flags,
        pin_occurrences=pin_occurrences,
    )

    return _ExtractedSheet(
        local_nets=local_nets,
        pin_occurrences=pin_occurrences,
        local_labels=local_labels,
        global_labels=global_labels,
        hierarchical_labels=hierarchical_labels,
        bus_labels=bus_labels,
        bus_aliases=bus_aliases,
        bus_entries=bus_entries,
        power_symbols=power_symbols,
        sheet_symbols=sheet_symbols,
        sheet_pins=sheet_pins,
        bus_sheet_pins=bus_sheet_pins,
        annotations=annotations,
    )


def _local_net_ids(
    scope_id: ScopeId,
    root_to_points: dict[KiCadPoint, set[KiCadPoint]],
) -> dict[KiCadPoint, str]:
    result: dict[KiCadPoint, str] = {}
    for ordinal, root in enumerate(sorted(root_to_points)):
        source_key = f"{ordinal:04d}:{root[0]:.4f}:{root[1]:.4f}"
        result[root] = _source_id(scope_id, "local_net", source_key)
    return result


def _assign_bus_entry_members(
    bus_entries: list[KiCadBusEntry],
    *,
    bus_labels: list[KiCadBusLabel],
    bus_aliases: list[KiCadBusAlias],
    bus_sheet_pins: list[KiCadBusSheetPin],
    local_labels: list[KiCadLocalLabel],
    global_labels: list[KiCadGlobalLabel],
    hierarchical_labels: list[KiCadHierarchicalLabel],
    power_symbols: list[KiCadPowerSymbol],
    sheet_pins: list[KiCadSheetPin],
) -> None:
    labels_by_bus_group: dict[str, list[_BusNameSource]] = {}
    for label in bus_labels:
        if label.bus_group_id:
            labels_by_bus_group.setdefault(label.bus_group_id, []).append(label)
    # A bus sheet pin names its bus too; used only when the group has no
    # explicit bus label.
    pins_by_bus_group: dict[str, list[_BusNameSource]] = {}
    for bus_pin in bus_sheet_pins:
        pins_by_bus_group.setdefault(bus_pin.bus_group_id, []).append(bus_pin)

    scalar_names_by_net = _scalar_names_by_net(
        local_labels,
        global_labels,
        hierarchical_labels,
        power_symbols,
        sheet_pins,
    )

    entries_by_bus_group: dict[str, list[KiCadBusEntry]] = {}
    for entry in bus_entries:
        if entry.bus_group_id:
            entries_by_bus_group.setdefault(entry.bus_group_id, []).append(entry)

    for bus_group_id, entries in entries_by_bus_group.items():
        label = _best_bus_group_label(
            labels_by_bus_group.get(bus_group_id) or pins_by_bus_group.get(bus_group_id, [])
        )
        if label is None:
            continue
        aliases = {
            alias.name: alias.members for alias in bus_aliases if alias.scope_id == label.scope_id
        }
        members = tuple(expand_bus_members(label.name, aliases=aliases) or ())
        if not members:
            continue

        remaining_members = list(members)
        member_set = set(members)
        sorted_entries = sorted(entries, key=_bus_entry_sort_key)
        for entry in sorted_entries:
            explicit_name = _explicit_bus_entry_member(
                scalar_names_by_net.get(entry.local_net_id, ()),
                member_set,
            )
            if explicit_name is None:
                continue
            entry.member_name = explicit_name
            entry.member_label_id = label.id
            if explicit_name in remaining_members:
                remaining_members.remove(explicit_name)

        for entry in sorted_entries:
            if entry.member_name or not remaining_members:
                continue
            entry.member_name = remaining_members.pop(0)
            entry.member_label_id = label.id


def _scalar_names_by_net(
    local_labels: list[KiCadLocalLabel],
    global_labels: list[KiCadGlobalLabel],
    hierarchical_labels: list[KiCadHierarchicalLabel],
    power_symbols: list[KiCadPowerSymbol],
    sheet_pins: list[KiCadSheetPin],
) -> dict[str, tuple[str, ...]]:
    names: dict[str, list[str]] = {}
    for item in (*local_labels, *global_labels, *hierarchical_labels, *power_symbols, *sheet_pins):
        name = strip_kicad_markup(item.name)
        if name:
            names.setdefault(item.local_net_id, []).append(name)
    return {net_id: tuple(net_names) for net_id, net_names in names.items()}


def _best_bus_group_label(labels: list[_BusNameSource]) -> _BusNameSource | None:
    if not labels:
        return None
    return min(labels, key=lambda label: label.source_index)


def _explicit_bus_entry_member(
    scalar_names: tuple[str, ...],
    member_names: set[str],
) -> str | None:
    for name in scalar_names:
        if name in member_names:
            return name
    return None


def _bus_entry_sort_key(entry: KiCadBusEntry) -> tuple[float, float, int]:
    return entry.bus_point[1], entry.bus_point[0], entry.source_index


def _build_local_nets(
    *,
    scope_id: ScopeId,
    root_to_points: dict[KiCadPoint, set[KiCadPoint]],
    root_to_net_id: dict[KiCadPoint, str],
    local_labels: list[KiCadLocalLabel],
    global_labels: list[KiCadGlobalLabel],
    hierarchical_labels: list[KiCadHierarchicalLabel],
    power_symbols: list[KiCadPowerSymbol],
    sheet_pins: list[KiCadSheetPin],
    bus_entries: list[KiCadBusEntry],
    netclass_flags: list[KiCadNetclassFlag],
    pin_occurrences: list[KiCadPinOccurrence],
) -> list[KiCadLocalNet]:
    local_labels_by_net = _items_by_net(local_labels)
    global_labels_by_net = _items_by_net(global_labels)
    hierarchical_labels_by_net = _items_by_net(hierarchical_labels)
    power_symbols_by_net = _items_by_net(power_symbols)
    sheet_pins_by_net = _items_by_net(sheet_pins)
    bus_entries_by_net = _items_by_net(bus_entries)
    netclass_flags_by_net = _items_by_net(netclass_flags)
    pin_ids_by_net: dict[str, list[str]] = {}
    for pin in pin_occurrences:
        pin_ids_by_net.setdefault(pin.local_net_id, []).append(pin.id)

    local_nets: list[KiCadLocalNet] = []
    for root in sorted(root_to_points):
        local_net_id = root_to_net_id[root]
        local_nets.append(
            KiCadLocalNet(
                id=local_net_id,
                scope_id=scope_id,
                wire_points=set(root_to_points[root]),
                pin_ids=pin_ids_by_net.get(local_net_id, []),
                local_labels=local_labels_by_net.get(local_net_id, []),
                global_labels=global_labels_by_net.get(local_net_id, []),
                hierarchical_labels=hierarchical_labels_by_net.get(local_net_id, []),
                power_symbols=power_symbols_by_net.get(local_net_id, []),
                sheet_pins=sheet_pins_by_net.get(local_net_id, []),
                bus_entries=bus_entries_by_net.get(local_net_id, []),
                generated_name=generated_local_net_name(
                    local_net_id,
                    local_labels_by_net.get(local_net_id, []),
                    global_labels_by_net.get(local_net_id, []),
                    hierarchical_labels_by_net.get(local_net_id, []),
                    power_symbols_by_net.get(local_net_id, []),
                    sheet_pins_by_net.get(local_net_id, []),
                ),
                netclass_flags=netclass_flags_by_net.get(local_net_id, []),
                directives=_netclass_flag_directives(
                    netclass_flags_by_net.get(local_net_id, []),
                ),
            ),
        )
    return local_nets


def _netclass_flag_directives(flags: list[KiCadNetclassFlag]) -> list[SchematicDirective]:
    directives: list[SchematicDirective] = []
    for flag in flags:
        if flag.net_class:
            directives.append(
                SchematicDirective(
                    kind=SchematicDirectiveKind.NET_CLASS,
                    value=flag.net_class,
                    source="kicad",
                    source_id=flag.id,
                    native_name="Netclass",
                    x=flag.location[0],
                    y=flag.location[1],
                    metadata=dict(flag.metadata),
                )
            )
        if flag.component_class:
            directives.append(
                SchematicDirective(
                    kind=SchematicDirectiveKind.COMPONENT_CLASS,
                    value=flag.component_class,
                    source="kicad",
                    source_id=flag.id,
                    native_name="Component Class",
                    x=flag.location[0],
                    y=flag.location[1],
                    metadata=dict(flag.metadata),
                )
            )
    return directives


def _items_by_net[T: _HasLocalNetId](items: list[T]) -> dict[str, list[T]]:
    result: dict[str, list[T]] = {}
    for item in items:
        result.setdefault(item.local_net_id, []).append(item)
    return result


def generated_local_net_name(
    local_net_id: str,
    local_labels: list[KiCadLocalLabel],
    global_labels: list[KiCadGlobalLabel],
    hierarchical_labels: list[KiCadHierarchicalLabel],
    power_symbols: list[KiCadPowerSymbol],
    sheet_pins: list[KiCadSheetPin],
) -> str:
    for named_items in (
        local_labels,
        global_labels,
        hierarchical_labels,
        power_symbols,
        sheet_pins,
    ):
        if named_items:
            return named_items[0].name
    _prefix, _sep, source_key = local_net_id.partition(":local_net:")
    return source_key or local_net_id


def _source_id(scope_id: ScopeId, kind: str, source_key: str) -> str:
    scope_key = "root" if not scope_id.path else "/".join(scope_id.path)
    return f"{scope_key}:{kind}:{source_key}"
