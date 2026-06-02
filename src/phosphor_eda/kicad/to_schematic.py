"""Extract KiCad-native schematic source objects.

The source extractor keeps KiCad labels, sheet pins, sheet instances, power
symbols, and local wire groups separate. It intentionally does not construct
the public schematic graph; Task 9 will resolve these source objects into the
public ``Schematic`` model.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import sexpdata

from phosphor_eda.kicad import sexp
from phosphor_eda.kicad.source import (
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadLocalNet,
    KiCadPinOccurrence,
    KiCadPoint,
    KiCadPowerSymbol,
    KiCadSheetInstance,
    KiCadSheetPin,
    KiCadSheetSymbol,
    KiCadSourceDesign,
)
from phosphor_eda.schematic import ScopeId

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.kicad.sexp import SExpNode
    from phosphor_eda.schematic import Schematic


type _PinDefinition = tuple[str, str, str, float, float]
type _LibPins = dict[str, dict[int, list[_PinDefinition]]]

# KiCad overline: ~{TEXT} means TEXT with overline bar.
# Bare ~ means "no name" (unnamed pin).
_OVERLINE_RE = re.compile(r"~\{([^}]+)\}")
_SUB_SYMBOL_UNIT_RE = re.compile(r"_(\d+)_(\d+)$")
_LIB_ID_SUFFIX_RE = re.compile(r"_\d+$")


def _strip_kicad_markup(name: str) -> str:
    """Strip KiCad text markup from a name."""
    if not name or name == "~":
        return ""
    return _OVERLINE_RE.sub(r"\1", name)


def _atom_text(value: object) -> str:
    if isinstance(value, sexpdata.Symbol):
        return value.value()
    return str(value)


def _parse_lib_symbols(
    lib_syms: SExpNode,
) -> tuple[_LibPins, dict[str, str]]:
    """Parse embedded lib_symbols into per-unit pin definitions and descriptions."""
    pins_result: _LibPins = {}
    desc_result: dict[str, str] = {}
    for sym in lib_syms[1:]:
        if sexp.tag(sym) != "symbol" or not isinstance(sym, list):
            continue
        lib_id = str(sym[1])
        desc = sexp.find_property(sym[2:], "ki_description")
        if desc:
            desc_result[lib_id] = desc
        units: dict[int, list[_PinDefinition]] = {}
        for child in sym[2:]:
            if sexp.tag(child) != "symbol" or not isinstance(child, list):
                continue
            sub_name = str(child[1])
            match = _SUB_SYMBOL_UNIT_RE.search(sub_name)
            unit_num = int(match.group(1)) if match else 1
            for elem in child[1:]:
                if sexp.tag(elem) != "pin" or not isinstance(elem, list):
                    continue
                pin_type = str(elem[1])
                pnum = pname = ""
                px = py = 0.0
                for pe in elem[3:]:
                    if not isinstance(pe, list):
                        continue
                    t = sexp.tag(pe)
                    if t == "number":
                        pnum = sexp.val(pe)
                    elif t == "name":
                        pname = _strip_kicad_markup(sexp.val(pe))
                    elif t == "at":
                        px = sexp.num(pe, 1)
                        py = sexp.num(pe, 2)
                units.setdefault(unit_num, []).append((pnum, pname, pin_type, px, py))
        pins_result[lib_id] = units
    return pins_result, desc_result


def _resolve_lib_pins(lib_id: str, lib_pins: _LibPins) -> dict[int, list[_PinDefinition]]:
    """Resolve a placed instance's lib_id to its pin definitions."""
    if lib_id in lib_pins:
        return lib_pins[lib_id]
    base = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    for key, units in lib_pins.items():
        key_base = _LIB_ID_SUFFIX_RE.sub("", key)
        if key_base == base:
            return units
    return {}


def _transform_pin(
    lib_x: float,
    lib_y: float,
    comp_x: float,
    comp_y: float,
    comp_rot: float,
    mirror: str | None = None,
) -> KiCadPoint:
    """Transform a pin from library coordinates to schematic coordinates."""
    lx, ly = lib_x, lib_y
    if mirror == "y":
        lx = -lx
    elif mirror == "x":
        ly = -ly
    ly = -ly
    rad = math.radians(comp_rot)
    rx = lx * math.cos(rad) - ly * math.sin(rad)
    ry = lx * math.sin(rad) + ly * math.cos(rad)
    return round(comp_x + rx, 4), round(comp_y + ry, 4)


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[KiCadPoint, KiCadPoint] = {}

    def find(self, p: KiCadPoint) -> KiCadPoint:
        if p not in self._parent:
            self._parent[p] = p
        while self._parent[p] != p:
            self._parent[p] = self._parent[self._parent[p]]
            p = self._parent[p]
        return p

    def union(self, a: KiCadPoint, b: KiCadPoint) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


@dataclass(slots=True)
class _LoadedSheet:
    instance: KiCadSheetInstance
    source_path: Path
    data: SExpNode


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
    component_reference: str
    pin_designator: str
    pin_name: str
    location: KiCadPoint
    no_connect: bool


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


def kicad_to_design(path: Path, name: str = "") -> Schematic:
    """Parse a KiCad schematic into the public model.

    The previous generic conversion depended on public ``Port`` and
    ``merge_pages`` types that have already been removed. Task 9 will route
    this through the KiCad resolver.
    """
    source = kicad_to_source(path, name)
    msg = (
        "KiCad public schematic conversion requires the Task 9 KiCad resolver; "
        f"source extraction loaded {len(source.sheet_instances)} sheet instance(s)."
    )
    raise NotImplementedError(msg)


def kicad_to_source(path: Path, name: str = "") -> KiCadSourceDesign:
    """Extract KiCad-native source connectivity from a root schematic file."""
    loaded_sheets: list[_LoadedSheet] = []
    sheet_instances: list[KiCadSheetInstance] = []
    all_lib_pins: _LibPins = {}
    all_lib_descs: dict[str, str] = {}
    root_scope = ScopeId(path=())

    _load_sheet_tree(
        path=path,
        sheet_name=name or path.stem,
        scope_id=root_scope,
        parent_scope_id=None,
        sheet_symbol_id="",
        loaded_sheets=loaded_sheets,
        sheet_instances=sheet_instances,
        lib_pins=all_lib_pins,
        lib_descs=all_lib_descs,
        ancestor_files=(path.resolve(),),
    )

    local_nets: list[KiCadLocalNet] = []
    pin_occurrences: list[KiCadPinOccurrence] = []
    local_labels: list[KiCadLocalLabel] = []
    global_labels: list[KiCadGlobalLabel] = []
    hierarchical_labels: list[KiCadHierarchicalLabel] = []
    power_symbols: list[KiCadPowerSymbol] = []
    sheet_symbols: list[KiCadSheetSymbol] = []
    sheet_pins: list[KiCadSheetPin] = []

    for loaded in loaded_sheets:
        extracted = _extract_sheet_sources(loaded, all_lib_pins)
        local_nets.extend(extracted.local_nets)
        pin_occurrences.extend(extracted.pin_occurrences)
        local_labels.extend(extracted.local_labels)
        global_labels.extend(extracted.global_labels)
        hierarchical_labels.extend(extracted.hierarchical_labels)
        power_symbols.extend(extracted.power_symbols)
        sheet_symbols.extend(extracted.sheet_symbols)
        sheet_pins.extend(extracted.sheet_pins)

    return KiCadSourceDesign(
        name=name or path.stem,
        root_source_file=str(path),
        root_scope_id=root_scope,
        sheet_instances=sheet_instances,
        local_nets=local_nets,
        pin_occurrences=pin_occurrences,
        local_labels=local_labels,
        global_labels=global_labels,
        hierarchical_labels=hierarchical_labels,
        power_symbols=power_symbols,
        sheet_symbols=sheet_symbols,
        sheet_pins=sheet_pins,
    )


def _load_sheet_tree(
    *,
    path: Path,
    sheet_name: str,
    scope_id: ScopeId,
    parent_scope_id: ScopeId | None,
    sheet_symbol_id: str,
    loaded_sheets: list[_LoadedSheet],
    sheet_instances: list[KiCadSheetInstance],
    lib_pins: _LibPins,
    lib_descs: dict[str, str],
    ancestor_files: tuple[Path, ...],
) -> None:
    data = _load_kicad_file(path)
    instance_id = _source_id(scope_id, "sheet_instance", "root" if not scope_id.path else "self")
    instance = KiCadSheetInstance(
        id=instance_id,
        scope_id=scope_id,
        sheet_name=sheet_name,
        source_file=str(path),
        parent_scope_id=parent_scope_id,
        sheet_symbol_id=sheet_symbol_id,
    )
    sheet_instances.append(instance)
    loaded_sheets.append(_LoadedSheet(instance=instance, source_path=path, data=data))

    lib_syms_node = sexp.find(data[1:], "lib_symbols")
    if lib_syms_node is not None:
        sheet_lib_pins, sheet_lib_descs = _parse_lib_symbols(lib_syms_node)
        for key, value in sheet_lib_pins.items():
            if key not in lib_pins:
                lib_pins[key] = value
        for key, value in sheet_lib_descs.items():
            if key not in lib_descs:
                lib_descs[key] = value

    for sheet_index, sheet_node in enumerate(sexp.find_all(data[1:], "sheet")):
        sheet_uuid = _node_value(sheet_node[1:], "uuid") or f"sheet-{sheet_index}"
        child_name, child_file = _parse_sheet_info(sheet_node)
        if not child_file:
            continue
        child_scope = ScopeId(path=(*scope_id.path, sheet_uuid))
        child_path = path.parent / child_file.replace("\\", "/")
        symbol_id = _source_id(scope_id, "sheet_symbol", sheet_uuid)
        if not child_path.exists():
            print(
                f"Warning: child sheet not found: {child_file} (resolved to {child_path})",
                file=sys.stderr,
            )
            continue
        child_resolved_path = child_path.resolve()
        if child_resolved_path in ancestor_files:
            print(
                f"Warning: child sheet cycle skipped: {child_file} "
                f"(resolved to {child_path})",
                file=sys.stderr,
            )
            continue
        _load_sheet_tree(
            path=child_path,
            sheet_name=child_name or child_path.stem,
            scope_id=child_scope,
            parent_scope_id=scope_id,
            sheet_symbol_id=symbol_id,
            loaded_sheets=loaded_sheets,
            sheet_instances=sheet_instances,
            lib_pins=lib_pins,
            lib_descs=lib_descs,
            ancestor_files=(*ancestor_files, child_resolved_path),
        )


def _load_kicad_file(path: Path) -> SExpNode:
    return sexpdata.loads(path.read_text(encoding="utf-8"))


def _parse_sheet_info(sheet_node: SExpNode) -> tuple[str, str]:
    """Extract name and filename from a sheet S-expression node."""
    sheet_name = ""
    sheet_file = ""
    for sub in sheet_node[1:]:
        if sexp.tag(sub) == "property" and isinstance(sub, list):
            prop_name = str(sub[1])
            prop_val = str(sub[2]) if len(sub) > 2 else ""
            if prop_name == "Sheetname":
                sheet_name = prop_val
            elif prop_name == "Sheetfile":
                sheet_file = prop_val
    return sheet_name, sheet_file


def _extract_sheet_sources(loaded: _LoadedSheet, lib_pins: _LibPins) -> _ExtractedSheet:
    scope_id = loaded.instance.scope_id
    data = loaded.data
    uf = _UnionFind()
    wire_segments: list[tuple[KiCadPoint, KiCadPoint]] = []
    wire_points: set[KiCadPoint] = set()

    for wire_node in sexp.find_all(data[1:], "wire"):
        pts_node = sexp.find(wire_node[1:], "pts")
        if pts_node is None:
            continue
        points: list[KiCadPoint] = []
        for xy in sexp.find_all(pts_node[1:], "xy"):
            points.append((round(sexp.num(xy, 1), 4), round(sexp.num(xy, 2), 4)))
        for index in range(len(points) - 1):
            uf.union(points[index], points[index + 1])
            wire_segments.append((points[index], points[index + 1]))
            wire_points.add(points[index])
            wire_points.add(points[index + 1])

    for junc in sexp.find_all(data[1:], "junction"):
        at_node = sexp.find(junc[1:], "at")
        if at_node is not None:
            point = _point_from_at(at_node)
            _connect_point(uf, point, wire_segments, wire_points, merge_all=True)

    local_label_candidates = _label_candidates(
        data,
        scope_id,
        "label",
        "local_label",
        uf,
        wire_segments,
        wire_points,
    )
    global_label_candidates = _label_candidates(
        data,
        scope_id,
        "global_label",
        "global_label",
        uf,
        wire_segments,
        wire_points,
    )
    hierarchical_label_candidates = _label_candidates(
        data,
        scope_id,
        "hierarchical_label",
        "hierarchical_label",
        uf,
        wire_segments,
        wire_points,
    )
    sheet_symbols, sheet_pin_candidates = _sheet_symbol_sources(
        data,
        scope_id,
        uf,
        wire_segments,
        wire_points,
    )
    power_candidates = _power_symbol_candidates(
        data,
        scope_id,
        lib_pins,
        uf,
        wire_segments,
        wire_points,
    )
    pin_candidates = _pin_candidates(
        data,
        scope_id,
        lib_pins,
        uf,
        wire_segments,
        wire_points,
    )

    root_to_points = _group_wire_points(uf, wire_points)
    root_to_net_id = _local_net_ids(scope_id, root_to_points)

    local_labels = [
        KiCadLocalLabel(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            name=candidate.name,
            location=candidate.location,
            local_net_id=root_to_net_id[uf.find(candidate.location)],
        )
        for candidate in local_label_candidates
    ]
    global_labels = [
        KiCadGlobalLabel(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            name=candidate.name,
            location=candidate.location,
            local_net_id=root_to_net_id[uf.find(candidate.location)],
        )
        for candidate in global_label_candidates
    ]
    hierarchical_labels = [
        KiCadHierarchicalLabel(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            name=candidate.name,
            location=candidate.location,
            local_net_id=root_to_net_id[uf.find(candidate.location)],
        )
        for candidate in hierarchical_label_candidates
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
            local_net_id=root_to_net_id[uf.find(candidate.location)],
        )
        for candidate in power_candidates
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
            local_net_id=root_to_net_id[uf.find(candidate.location)],
        )
        for candidate in sheet_pin_candidates
    ]
    pin_occurrences = [
        KiCadPinOccurrence(
            id=candidate.id,
            scope_id=candidate.scope_id,
            source_index=candidate.source_index,
            local_net_id=root_to_net_id[uf.find(candidate.location)],
            component_source_id=candidate.component_source_id,
            component_reference=candidate.component_reference,
            pin_designator=candidate.pin_designator,
            pin_name=candidate.pin_name,
            location=candidate.location,
            no_connect=candidate.no_connect,
        )
        for candidate in pin_candidates
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


def _label_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    tag_name: str,
    id_kind: str,
    uf: _UnionFind,
    wire_segments: list[tuple[KiCadPoint, KiCadPoint]],
    wire_points: set[KiCadPoint],
) -> list[_LabelCandidate]:
    candidates: list[_LabelCandidate] = []
    for index, label in enumerate(sexp.find_all(data[1:], tag_name)):
        label_name = _strip_kicad_markup(str(label[1]))
        if "{" in label_name:
            continue
        at_node = sexp.find(label[2:], "at")
        if at_node is None:
            continue
        location = _point_from_at(at_node)
        _connect_point(uf, location, wire_segments, wire_points)
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
    uf: _UnionFind,
    wire_segments: list[tuple[KiCadPoint, KiCadPoint]],
    wire_points: set[KiCadPoint],
) -> tuple[list[KiCadSheetSymbol], list[_SheetPinCandidate]]:
    symbols: list[KiCadSheetSymbol] = []
    pins: list[_SheetPinCandidate] = []
    for sheet_index, sheet_node in enumerate(sexp.find_all(data[1:], "sheet")):
        sheet_uuid = _node_value(sheet_node[1:], "uuid") or f"sheet-{sheet_index}"
        sheet_name, sheet_file = _parse_sheet_info(sheet_node)
        symbol_id = _source_id(scope_id, "sheet_symbol", sheet_uuid)
        child_scope_id = ScopeId(path=(*scope_id.path, sheet_uuid))
        at_node = sexp.find(sheet_node[1:], "at")
        size_node = sexp.find(sheet_node[1:], "size")
        location = _point_from_at(at_node) if at_node is not None else (0.0, 0.0)
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
            pin_name = _strip_kicad_markup(str(pin_node[1]))
            if "{" in pin_name:
                continue
            at_pin = sexp.find(pin_node[3:], "at")
            if at_pin is None:
                continue
            pin_location = _point_from_at(at_pin)
            _connect_point(uf, pin_location, wire_segments, wire_points)
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
    lib_pins: _LibPins,
    uf: _UnionFind,
    wire_segments: list[tuple[KiCadPoint, KiCadPoint]],
    wire_points: set[KiCadPoint],
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

        unit_pins = _resolve_lib_pins(lib_id, lib_pins)
        pin_locations = [
            _transform_pin(px, py, comp_x, comp_y, comp_rot, mirror)
            for pins in unit_pins.values()
            for _pnum, _pname, _ptype, px, py in pins
        ]
        if not pin_locations:
            pin_locations = [(round(comp_x, 4), round(comp_y, 4))]
        for location in pin_locations:
            _connect_point(uf, location, wire_segments, wire_points)
        symbol_uuid = _node_value(sym_node[1:], "uuid") or str(index)
        candidates.append(
            _PowerCandidate(
                id=_source_id(scope_id, "power_symbol", symbol_uuid),
                scope_id=scope_id,
                source_index=index,
                name=value,
                reference=ref,
                lib_id=lib_id,
                location=pin_locations[0],
            ),
        )
    return candidates


def _pin_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    lib_pins: _LibPins,
    uf: _UnionFind,
    wire_segments: list[tuple[KiCadPoint, KiCadPoint]],
    wire_points: set[KiCadPoint],
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
        at_node = sexp.find(sym_node[1:], "at")
        comp_x = sexp.num(at_node, 1) if at_node is not None else 0.0
        comp_y = sexp.num(at_node, 2) if at_node is not None else 0.0
        comp_rot = sexp.num(at_node, 3) if at_node is not None and len(at_node) > 3 else 0.0
        mirror_node = sexp.find(sym_node[1:], "mirror")
        mirror = sexp.val(mirror_node) if mirror_node is not None else None
        unit_node = sexp.find(sym_node[1:], "unit")
        inst_unit = int(sexp.num(unit_node, 1)) if unit_node is not None else 1
        symbol_uuid = _node_value(sym_node[1:], "uuid") or ref or str(source_index)
        component_source_id = _source_id(scope_id, "component", symbol_uuid)
        pin_uuids = _pin_uuids_by_designator(sym_node)

        unit_pins = _resolve_lib_pins(lib_id, lib_pins)
        sym_pins = unit_pins.get(inst_unit, []) + unit_pins.get(0, [])
        for pnum, pname, ptype, px, py in sym_pins:
            location = _transform_pin(px, py, comp_x, comp_y, comp_rot, mirror)
            _connect_point(uf, location, wire_segments, wire_points)
            pin_uuid = pin_uuids.get(pnum, f"{symbol_uuid}:pin:{pnum}")
            candidates.append(
                _PinCandidate(
                    id=_source_id(scope_id, "pin", pin_uuid),
                    scope_id=scope_id,
                    source_index=source_index,
                    component_source_id=component_source_id,
                    component_reference=ref,
                    pin_designator=pnum,
                    pin_name=pname,
                    location=location,
                    no_connect=ptype == "no_connect"
                    or _matches_point(location, no_connect_positions),
                ),
            )
            source_index += 1
    return candidates


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
            positions.add(_point_from_at(at_node))
    return positions


def _matches_point(point: KiCadPoint, points: set[KiCadPoint]) -> bool:
    return any(
        abs(other[0] - point[0]) < 0.01 and abs(other[1] - point[1]) < 0.01
        for other in points
    )


def _group_wire_points(
    uf: _UnionFind,
    wire_points: set[KiCadPoint],
) -> dict[KiCadPoint, set[KiCadPoint]]:
    root_to_points: dict[KiCadPoint, set[KiCadPoint]] = {}
    for point in wire_points:
        root_to_points.setdefault(uf.find(point), set()).add(point)
    return root_to_points


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
    return local_net_id.rsplit(":", 1)[-1]


def _source_id(scope_id: ScopeId, kind: str, source_key: str) -> str:
    scope_key = "root" if not scope_id.path else "/".join(scope_id.path)
    return f"{scope_key}:{kind}:{source_key}"


def _node_value(items: SExpNode, tag_name: str) -> str:
    node = sexp.find(items, tag_name)
    return sexp.val(node) if node is not None else ""


def _point_from_at(at_node: SExpNode) -> KiCadPoint:
    return round(sexp.num(at_node, 1), 4), round(sexp.num(at_node, 2), 4)


def _point_on_segment(
    point: KiCadPoint,
    seg_start: KiCadPoint,
    seg_end: KiCadPoint,
    tol: float = 0.01,
) -> bool:
    """Check if a point lies on a horizontal or vertical line segment."""
    px, py = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    if abs(y1 - y2) < tol and abs(py - y1) < tol:
        lo, hi = (min(x1, x2) - tol, max(x1, x2) + tol)
        return lo <= px <= hi
    if abs(x1 - x2) < tol and abs(px - x1) < tol:
        lo, hi = (min(y1, y2) - tol, max(y1, y2) + tol)
        return lo <= py <= hi
    return False


def _connect_point(
    uf: _UnionFind,
    point: KiCadPoint,
    wire_segments: list[tuple[KiCadPoint, KiCadPoint]],
    wire_points: set[KiCadPoint],
    *,
    merge_all: bool = False,
) -> None:
    """Connect a point to the local wire network."""
    wire_points.add(point)
    for wp in wire_points:
        if wp != point and abs(wp[0] - point[0]) < 0.01 and abs(wp[1] - point[1]) < 0.01:
            uf.union(point, wp)
            if not merge_all:
                return
    for seg_start, seg_end in wire_segments:
        if _point_on_segment(point, seg_start, seg_end):
            uf.union(point, seg_start)
            if not merge_all:
                return
