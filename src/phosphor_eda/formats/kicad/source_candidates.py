"""KiCad sheet source candidate extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sexpdata

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.domain.schematic import ScopeId
from phosphor_eda.formats.kicad.lib_symbols import (
    LibPins,
    lib_description,
    resolve_lib_pins,
    strip_kicad_markup,
    transform_pin,
)
from phosphor_eda.formats.kicad.sheet_loader import parse_sheet_info
from phosphor_eda.formats.kicad.source import (
    KiCadPoint,
    KiCadSheetSymbol,
)
from phosphor_eda.formats.kicad.wire_graph import WireGraph, point_from_at

if TYPE_CHECKING:
    from phosphor_eda.formats.kicad.sexp import SExpNode


@dataclass(slots=True)
class _LabelCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: KiCadPoint


@dataclass(slots=True)
class _PowerCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    reference: str
    lib_id: str
    location: KiCadPoint


@dataclass(slots=True)
class _PinCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    component_source_id: str
    component_identity_source_id: str
    component_unit: int
    component_reference: str
    component_value: str
    component_footprint: str
    component_datasheet: str
    component_description: str
    component_x: float | None
    component_y: float | None
    component_rotation: float
    component_mirror: bool
    pin_designator: str
    pin_name: str
    pin_type: str
    location: KiCadPoint
    no_connect: bool


@dataclass(slots=True)
class _SheetPinCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    sheet_symbol_id: str
    child_scope_id: ScopeId
    name: str
    direction: str
    location: KiCadPoint


@dataclass(slots=True)
class SheetCandidates:
    local_labels: list[_LabelCandidate]
    global_labels: list[_LabelCandidate]
    hierarchical_labels: list[_LabelCandidate]
    power_symbols: list[_PowerCandidate]
    sheet_symbols: list[KiCadSheetSymbol]
    sheet_pins: list[_SheetPinCandidate]
    pin_occurrences: list[_PinCandidate]


def extract_source_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    lib_pins: LibPins,
    lib_descs: dict[str, str],
    wire_graph: WireGraph,
) -> SheetCandidates:
    local_label_candidates = _label_candidates(
        data,
        scope_id,
        "label",
        "local_label",
        wire_graph,
    )
    global_label_candidates = _label_candidates(
        data,
        scope_id,
        "global_label",
        "global_label",
        wire_graph,
    )
    hierarchical_label_candidates = _label_candidates(
        data,
        scope_id,
        "hierarchical_label",
        "hierarchical_label",
        wire_graph,
    )
    sheet_symbols, sheet_pin_candidates = _sheet_symbol_sources(
        data,
        scope_id,
        wire_graph,
    )
    power_candidates = _power_symbol_candidates(
        data,
        scope_id,
        lib_pins,
        wire_graph,
    )
    pin_candidates = _pin_candidates(
        data,
        scope_id,
        lib_pins,
        lib_descs,
        wire_graph,
    )
    return SheetCandidates(
        local_labels=local_label_candidates,
        global_labels=global_label_candidates,
        hierarchical_labels=hierarchical_label_candidates,
        power_symbols=power_candidates,
        sheet_symbols=sheet_symbols,
        sheet_pins=sheet_pin_candidates,
        pin_occurrences=pin_candidates,
    )


def _atom_text(value: object) -> str:
    if isinstance(value, sexpdata.Symbol):
        return value.value()
    return str(value)


def _label_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    tag_name: str,
    id_kind: str,
    wire_graph: WireGraph,
) -> list[_LabelCandidate]:
    candidates: list[_LabelCandidate] = []
    for index, label in enumerate(sexp.find_all(data[1:], tag_name)):
        label_name = strip_kicad_markup(str(label[1]))
        if "{" in label_name:
            continue
        at_node = sexp.find(label[2:], "at")
        if at_node is None:
            continue
        location = point_from_at(at_node)
        wire_graph.connect_point(location)
        source_key = _node_value(label[2:], "uuid") or str(index)
        candidates.append(
            _LabelCandidate(
                id=_source_id(scope_id, id_kind, source_key),
                scope_id=scope_id,
                source_index=index,
                name=label_name,
                location=location,
            ),
        )
    return candidates


def _sheet_symbol_sources(
    data: SExpNode,
    scope_id: ScopeId,
    wire_graph: WireGraph,
) -> tuple[list[KiCadSheetSymbol], list[_SheetPinCandidate]]:
    symbols: list[KiCadSheetSymbol] = []
    pins: list[_SheetPinCandidate] = []
    for sheet_index, sheet_node in enumerate(sexp.find_all(data[1:], "sheet")):
        sheet_uuid = _node_value(sheet_node[1:], "uuid") or f"sheet-{sheet_index}"
        sheet_name, sheet_file = parse_sheet_info(sheet_node)
        symbol_id = _source_id(scope_id, "sheet_symbol", sheet_uuid)
        child_scope_id = ScopeId(path=(*scope_id.path, sheet_uuid))
        at_node = sexp.find(sheet_node[1:], "at")
        size_node = sexp.find(sheet_node[1:], "size")
        location = point_from_at(at_node) if at_node is not None else (0.0, 0.0)
        size = (
            (round(sexp.num(size_node, 1), 4), round(sexp.num(size_node, 2), 4))
            if size_node is not None
            else (0.0, 0.0)
        )
        symbols.append(
            KiCadSheetSymbol(
                id=symbol_id,
                scope_id=scope_id,
                source_index=sheet_index,
                name=sheet_name,
                child_source_file=sheet_file,
                child_scope_id=child_scope_id,
                location=location,
                size=size,
            ),
        )
        for pin_index, pin_node in enumerate(sexp.find_all(sheet_node[1:], "pin")):
            if len(pin_node) < 3:
                continue
            pin_name = strip_kicad_markup(str(pin_node[1]))
            if "{" in pin_name:
                continue
            at_pin = sexp.find(pin_node[3:], "at")
            if at_pin is None:
                continue
            pin_location = point_from_at(at_pin)
            wire_graph.connect_point(pin_location)
            pin_uuid = _node_value(pin_node[3:], "uuid") or f"{sheet_uuid}:pin:{pin_index}"
            pins.append(
                _SheetPinCandidate(
                    id=_source_id(scope_id, "sheet_pin", pin_uuid),
                    scope_id=scope_id,
                    source_index=pin_index,
                    sheet_symbol_id=symbol_id,
                    child_scope_id=child_scope_id,
                    name=pin_name,
                    direction=_atom_text(pin_node[2]),
                    location=pin_location,
                ),
            )
    return symbols, pins


def _power_symbol_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    lib_pins: LibPins,
    wire_graph: WireGraph,
) -> list[_PowerCandidate]:
    candidates: list[_PowerCandidate] = []
    for index, sym_node in enumerate(sexp.find_all(data[1:], "symbol")):
        ref = sexp.find_property(sym_node[1:], "Reference")
        if not ref.startswith("#PWR") and not ref.startswith("#FLG"):
            continue
        value = sexp.find_property(sym_node[1:], "Value")
        if not value:
            continue
        lib_id_node = sexp.find(sym_node[1:], "lib_id")
        lib_id = sexp.val(lib_id_node) if lib_id_node is not None else ""
        at_node = sexp.find(sym_node[1:], "at")
        if at_node is None:
            continue
        comp_x = sexp.num(at_node, 1)
        comp_y = sexp.num(at_node, 2)
        comp_rot = sexp.num(at_node, 3) if len(at_node) > 3 else 0.0
        mirror_node = sexp.find(sym_node[1:], "mirror")
        mirror = sexp.val(mirror_node) if mirror_node is not None else None

        unit_pins = resolve_lib_pins(lib_id, lib_pins)
        pin_locations = [
            transform_pin(pin.x, pin.y, comp_x, comp_y, comp_rot, mirror)
            for pins in unit_pins.values()
            for pin in pins
        ]
        if not pin_locations:
            pin_locations = [(round(comp_x, 4), round(comp_y, 4))]
        symbol_uuid = _node_value(sym_node[1:], "uuid") or str(index)
        has_multiple_locations = len(pin_locations) > 1
        for pin_index, location in enumerate(pin_locations, start=1):
            wire_graph.connect_point(location)
            source_key = (
                f"{symbol_uuid}:pin:{pin_index:04d}" if has_multiple_locations else symbol_uuid
            )
            candidates.append(
                _PowerCandidate(
                    id=_source_id(scope_id, "power_symbol", source_key),
                    scope_id=scope_id,
                    source_index=index,
                    name=value,
                    reference=ref,
                    lib_id=lib_id,
                    location=location,
                ),
            )
    return candidates


def _pin_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    lib_pins: LibPins,
    lib_descs: dict[str, str],
    wire_graph: WireGraph,
) -> list[_PinCandidate]:
    no_connect_positions = _no_connect_positions(data)
    candidates: list[_PinCandidate] = []
    source_index = 0
    for sym_node in sexp.find_all(data[1:], "symbol"):
        ref = sexp.find_property(sym_node[1:], "Reference")
        if ref.startswith("#"):
            continue
        lib_id_node = sexp.find(sym_node[1:], "lib_id")
        lib_id = sexp.val(lib_id_node) if lib_id_node is not None else ""
        value = sexp.find_property(sym_node[1:], "Value")
        footprint = sexp.find_property(sym_node[1:], "Footprint")
        datasheet = sexp.find_property(sym_node[1:], "Datasheet")
        description = lib_description(lib_id, lib_descs)
        at_node = sexp.find(sym_node[1:], "at")
        comp_x = sexp.num(at_node, 1) if at_node is not None else 0.0
        comp_y = sexp.num(at_node, 2) if at_node is not None else 0.0
        comp_rot = sexp.num(at_node, 3) if at_node is not None and len(at_node) > 3 else 0.0
        mirror_node = sexp.find(sym_node[1:], "mirror")
        mirror = sexp.val(mirror_node) if mirror_node is not None else None
        mirrored = mirror is not None
        unit_node = sexp.find(sym_node[1:], "unit")
        inst_unit = int(sexp.num(unit_node, 1)) if unit_node is not None else 1
        symbol_uuid = _node_value(sym_node[1:], "uuid") or ref or str(source_index)
        component_source_id = _source_id(scope_id, "component", symbol_uuid)
        component_identity_source_id = _resolved_component_identity_source_id(
            scope_id,
            sym_node,
            component_source_id,
        )
        pin_uuids = _pin_uuids_by_designator(sym_node)

        unit_pins = resolve_lib_pins(lib_id, lib_pins)
        sym_pins = unit_pins.get(inst_unit, []) + unit_pins.get(0, [])
        for pin in sym_pins:
            location = transform_pin(pin.x, pin.y, comp_x, comp_y, comp_rot, mirror)
            wire_graph.connect_point(location)
            pin_uuid = pin_uuids.get(pin.number, f"{symbol_uuid}:pin:{pin.number}")
            candidates.append(
                _PinCandidate(
                    id=_source_id(scope_id, "pin", pin_uuid),
                    scope_id=scope_id,
                    source_index=source_index,
                    component_source_id=component_source_id,
                    component_identity_source_id=component_identity_source_id,
                    component_unit=inst_unit,
                    component_reference=ref,
                    component_value=value,
                    component_footprint=footprint,
                    component_datasheet=datasheet,
                    component_description=description,
                    component_x=comp_x,
                    component_y=comp_y,
                    component_rotation=comp_rot,
                    component_mirror=mirrored,
                    pin_designator=pin.number,
                    pin_name=pin.name,
                    pin_type=pin.pin_type,
                    location=location,
                    no_connect=pin.pin_type == "no_connect"
                    or _matches_point(location, no_connect_positions),
                ),
            )
            source_index += 1
    return candidates


def _resolved_component_identity_source_id(
    scope_id: ScopeId,
    sym_node: SExpNode,
    component_source_id: str,
) -> str:
    instance_path = _symbol_instance_path(sym_node)
    if not instance_path:
        return component_source_id
    return _source_id(scope_id, "component_instance", instance_path)


def _symbol_instance_path(sym_node: SExpNode) -> str:
    instances_node = sexp.find(sym_node[1:], "instances")
    if instances_node is None:
        return ""
    for project_node in sexp.find_all(instances_node[1:], "project"):
        path_node = sexp.find(project_node[1:], "path")
        if path_node is not None:
            return sexp.val(path_node)
    return ""


def _pin_uuids_by_designator(sym_node: SExpNode) -> dict[str, str]:
    result: dict[str, str] = {}
    for pin_node in sexp.find_all(sym_node[1:], "pin"):
        if len(pin_node) < 2:
            continue
        designator = str(pin_node[1])
        pin_uuid = _node_value(pin_node[2:], "uuid")
        if pin_uuid:
            result[designator] = pin_uuid
    return result


def _no_connect_positions(data: SExpNode) -> set[KiCadPoint]:
    positions: set[KiCadPoint] = set()
    for nc_node in sexp.find_all(data[1:], "no_connect"):
        at_node = sexp.find(nc_node[1:], "at")
        if at_node is not None:
            positions.add(point_from_at(at_node))
    return positions


def _matches_point(point: KiCadPoint, points: set[KiCadPoint]) -> bool:
    return any(
        abs(other[0] - point[0]) < 0.01 and abs(other[1] - point[1]) < 0.01 for other in points
    )


def _source_id(scope_id: ScopeId, kind: str, source_key: str) -> str:
    scope_key = "root" if not scope_id.path else "/".join(scope_id.path)
    return f"{scope_key}:{kind}:{source_key}"


def _node_value(items: SExpNode, tag_name: str) -> str:
    node = sexp.find(items, tag_name)
    return sexp.val(node) if node is not None else ""
