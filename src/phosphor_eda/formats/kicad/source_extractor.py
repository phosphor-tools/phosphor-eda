"""KiCad sheet source-object extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from phosphor_eda.formats.kicad.source import (
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadLocalNet,
    KiCadPinOccurrence,
    KiCadPowerSymbol,
    KiCadSheetPin,
    KiCadSheetSymbol,
)
from phosphor_eda.formats.kicad.source_candidates import extract_source_candidates
from phosphor_eda.formats.kicad.wire_graph import build_wire_graph

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import ScopeId
    from phosphor_eda.formats.kicad.lib_symbols import LibPins
    from phosphor_eda.formats.kicad.sheet_loader import LoadedSheet
    from phosphor_eda.formats.kicad.source import KiCadPoint


@dataclass(slots=True)
class _ExtractedSheet:
    local_nets: list[KiCadLocalNet]
    pin_occurrences: list[KiCadPinOccurrence]
    local_labels: list[KiCadLocalLabel]
    global_labels: list[KiCadGlobalLabel]
    hierarchical_labels: list[KiCadHierarchicalLabel]
    power_symbols: list[KiCadPowerSymbol]
    sheet_symbols: list[KiCadSheetSymbol]
    sheet_pins: list[KiCadSheetPin]


class _HasLocalNetId(Protocol):
    local_net_id: str


def extract_sheet_sources(
    loaded: LoadedSheet,
    lib_pins: LibPins,
    lib_descs: dict[str, str],
    loaded_scopes: set[ScopeId],
    root_uuid: str = "",
) -> _ExtractedSheet:
    scope_id = loaded.instance.scope_id
    data = loaded.data
    wire_graph = build_wire_graph(data)

    candidates = extract_source_candidates(
        data,
        scope_id,
        lib_pins,
        lib_descs,
        wire_graph,
        root_uuid,
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
    sheet_symbols = [
        symbol for symbol in candidates.sheet_symbols if symbol.child_scope_id in loaded_scopes
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
        pin_occurrences=pin_occurrences,
    )

    return _ExtractedSheet(
        local_nets=local_nets,
        pin_occurrences=pin_occurrences,
        local_labels=local_labels,
        global_labels=global_labels,
        hierarchical_labels=hierarchical_labels,
        power_symbols=power_symbols,
        sheet_symbols=sheet_symbols,
        sheet_pins=sheet_pins,
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
    pin_occurrences: list[KiCadPinOccurrence],
) -> list[KiCadLocalNet]:
    local_labels_by_net = _items_by_net(local_labels)
    global_labels_by_net = _items_by_net(global_labels)
    hierarchical_labels_by_net = _items_by_net(hierarchical_labels)
    power_symbols_by_net = _items_by_net(power_symbols)
    sheet_pins_by_net = _items_by_net(sheet_pins)
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
                generated_name=_generated_local_net_name(
                    local_net_id,
                    local_labels_by_net.get(local_net_id, []),
                    global_labels_by_net.get(local_net_id, []),
                    hierarchical_labels_by_net.get(local_net_id, []),
                    power_symbols_by_net.get(local_net_id, []),
                    sheet_pins_by_net.get(local_net_id, []),
                ),
            ),
        )
    return local_nets


def _items_by_net[T: _HasLocalNetId](items: list[T]) -> dict[str, list[T]]:
    result: dict[str, list[T]] = {}
    for item in items:
        result.setdefault(item.local_net_id, []).append(item)
    return result


def _generated_local_net_name(
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
