"""KiCad sheet source candidate extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sexpdata

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.domain.buses import bus_kind_for_name
from phosphor_eda.domain.schematic import (
    FootprintModel,
    LibraryLink,
    Parameter,
    ScopeId,
)
from phosphor_eda.formats.common.resolved_graph import ResolvedComponentInfo
from phosphor_eda.formats.kicad.lib_symbols import (
    LibPins,
    LibPowerKinds,
    lib_description,
    resolve_lib_pins,
    resolve_lib_power_kind,
    strip_kicad_markup,
    transform_pin,
)
from phosphor_eda.formats.kicad.sheet_loader import parse_sheet_info
from phosphor_eda.formats.kicad.source import (
    KiCadPoint,
    KiCadSheetSymbol,
)
from phosphor_eda.formats.kicad.wire_graph import (
    BusGraph,
    WireGraph,
    point_from_at,
    points_from_pts_node,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.kicad.sexp import SExpNode

_TEXT_VARIABLE_RE = re.compile(r"\$\{([^}]+)\}")


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
    power_kind: str
    location: KiCadPoint


@dataclass(slots=True)
class _PinCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    component_source_id: str
    component_identity_source_id: str
    component_unit: int
    component_has_multiple_units: bool
    component_reference: str
    component_value: str
    component_footprint: str
    component_datasheet: str
    component_description: str
    component_x: float | None
    component_y: float | None
    component_rotation: float
    component_mirror: bool
    component_info: ResolvedComponentInfo | None
    component_attr_metadata: dict[str, str]
    pin_designator: str
    pin_name: str
    pin_net_name: str
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
class _BusLabelCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: KiCadPoint
    kind: str
    bus_group_id: str


@dataclass(slots=True)
class _BusAliasCandidate:
    id: str
    scope_id: ScopeId
    name: str
    members: tuple[str, ...]


@dataclass(slots=True)
class _BusEntryCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    start: KiCadPoint
    end: KiCadPoint
    wire_point: KiCadPoint
    bus_point: KiCadPoint
    bus_group_id: str


@dataclass(slots=True)
class _NetclassFlagCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    location: KiCadPoint
    rotation: float
    net_class: str
    component_class: str
    metadata: dict[str, str]


@dataclass(slots=True)
class SheetCandidates:
    local_labels: list[_LabelCandidate]
    global_labels: list[_LabelCandidate]
    hierarchical_labels: list[_LabelCandidate]
    bus_labels: list[_BusLabelCandidate]
    bus_aliases: list[_BusAliasCandidate]
    bus_entries: list[_BusEntryCandidate]
    power_symbols: list[_PowerCandidate]
    sheet_symbols: list[KiCadSheetSymbol]
    sheet_pins: list[_SheetPinCandidate]
    pin_occurrences: list[_PinCandidate]
    netclass_flags: list[_NetclassFlagCandidate]


def extract_source_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    lib_pins: LibPins,
    lib_descs: dict[str, str],
    lib_power_kinds: LibPowerKinds,
    wire_graph: WireGraph,
    bus_graph: BusGraph,
    root_uuid: str = "",
    text_variables: Mapping[str, str] | None = None,
    ctx: ParseContext | None = None,
) -> SheetCandidates:
    variables = text_variables or {}
    warned_variables: set[str] = set()
    local_label_candidates = _label_candidates(
        data,
        scope_id,
        "label",
        "local_label",
        wire_graph,
        variables,
        ctx,
        warned_variables,
    )
    global_label_candidates = _label_candidates(
        data,
        scope_id,
        "global_label",
        "global_label",
        wire_graph,
        variables,
        ctx,
        warned_variables,
    )
    hierarchical_label_candidates = _label_candidates(
        data,
        scope_id,
        "hierarchical_label",
        "hierarchical_label",
        wire_graph,
        variables,
        ctx,
        warned_variables,
    )
    bus_label_candidates = _bus_label_candidates(
        data,
        scope_id,
        bus_graph,
        variables,
        ctx,
        warned_variables,
    )
    bus_alias_candidates = _bus_alias_candidates(data, scope_id)
    bus_entry_candidates = _bus_entry_candidates(data, scope_id, wire_graph, bus_graph)
    sheet_symbols, sheet_pin_candidates = _sheet_symbol_sources(
        data,
        scope_id,
        wire_graph,
        variables,
        ctx,
        warned_variables,
    )
    power_candidates = _power_symbol_candidates(
        data,
        scope_id,
        lib_pins,
        lib_power_kinds,
        wire_graph,
    )
    pin_candidates = _pin_candidates(
        data,
        scope_id,
        lib_pins,
        lib_descs,
        wire_graph,
        root_uuid,
    )
    netclass_flag_candidates = _netclass_flag_candidates(data, scope_id, wire_graph)
    return SheetCandidates(
        local_labels=local_label_candidates,
        global_labels=global_label_candidates,
        hierarchical_labels=hierarchical_label_candidates,
        bus_labels=bus_label_candidates,
        bus_aliases=bus_alias_candidates,
        bus_entries=bus_entry_candidates,
        power_symbols=power_candidates,
        sheet_symbols=sheet_symbols,
        sheet_pins=sheet_pin_candidates,
        pin_occurrences=pin_candidates,
        netclass_flags=netclass_flag_candidates,
    )


def _atom_text(value: object) -> str:
    if isinstance(value, sexpdata.Symbol):
        return value.value()
    return str(value)


def _resolve_text_variables(
    text: str,
    text_variables: Mapping[str, str],
    ctx: ParseContext | None,
    warned_variables: set[str],
) -> str:
    if "${" not in text:
        return text

    def replace(match: re.Match[str]) -> str:
        variable_name = match.group(1)
        value = text_variables.get(variable_name)
        if value is not None:
            return value
        if ctx is not None and variable_name not in warned_variables:
            warned_variables.add(variable_name)
            ctx.warn(
                category="kicad_unresolved_text_variable",
                message=(f"Unresolved KiCad text variable ${{{variable_name}}} in text '{text}'"),
            )
        return match.group(0)

    return _TEXT_VARIABLE_RE.sub(replace, text)


def _label_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    tag_name: str,
    id_kind: str,
    wire_graph: WireGraph,
    text_variables: Mapping[str, str],
    ctx: ParseContext | None,
    warned_variables: set[str],
) -> list[_LabelCandidate]:
    candidates: list[_LabelCandidate] = []
    for index, label in enumerate(sexp.find_all(data[1:], tag_name)):
        label_name = _resolve_text_variables(
            _atom_text(label[1]),
            text_variables,
            ctx,
            warned_variables,
        )
        if _is_bus_label_text(label_name):
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


def _bus_label_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    bus_graph: BusGraph,
    text_variables: Mapping[str, str],
    ctx: ParseContext | None,
    warned_variables: set[str],
) -> list[_BusLabelCandidate]:
    candidates: list[_BusLabelCandidate] = []
    label_specs = (
        ("label", "local_label"),
        ("global_label", "global_label"),
        ("hierarchical_label", "hierarchical_label"),
    )
    for tag_name, id_kind in label_specs:
        for index, label in enumerate(sexp.find_all(data[1:], tag_name)):
            label_name = _resolve_text_variables(
                _atom_text(label[1]),
                text_variables,
                ctx,
                warned_variables,
            )
            bus_name = _bus_syntax_text(label_name)
            if bus_name is None:
                continue
            at_node = sexp.find(label[2:], "at")
            if at_node is None:
                continue
            location = point_from_at(at_node)
            if bus_graph.touches_bus(location):
                bus_graph.connect_point(location)
                bus_group_id = _source_id(
                    scope_id,
                    "bus_group",
                    _point_key(bus_graph.find(location)),
                )
            else:
                bus_group_id = ""
            source_key = _node_value(label[2:], "uuid") or str(index)
            candidates.append(
                _BusLabelCandidate(
                    id=_source_id(scope_id, f"bus_{id_kind}", source_key),
                    scope_id=scope_id,
                    source_index=index,
                    name=bus_name,
                    location=location,
                    kind=id_kind,
                    bus_group_id=bus_group_id,
                )
            )
    return candidates


def _bus_alias_candidates(data: SExpNode, scope_id: ScopeId) -> list[_BusAliasCandidate]:
    aliases: list[_BusAliasCandidate] = []
    for index, alias_node in enumerate(sexp.find_all(data[1:], "bus_alias")):
        if len(alias_node) < 2:
            continue
        name = strip_kicad_markup(str(alias_node[1]))
        members_node = sexp.find(alias_node[2:], "members")
        if members_node is None:
            continue
        members = tuple(strip_kicad_markup(str(member)) for member in members_node[1:])
        if not name or not members:
            continue
        aliases.append(
            _BusAliasCandidate(
                id=_source_id(scope_id, "bus_alias", f"{index}:{name}"),
                scope_id=scope_id,
                name=name,
                members=members,
            )
        )
    return aliases


def _bus_entry_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    wire_graph: WireGraph,
    bus_graph: BusGraph,
) -> list[_BusEntryCandidate]:
    entries: list[_BusEntryCandidate] = []
    for index, entry_node in enumerate(sexp.find_all(data[1:], "bus_entry")):
        pts_node = sexp.find(entry_node[1:], "pts")
        if pts_node is None:
            continue
        points = points_from_pts_node(pts_node)
        if len(points) < 2:
            continue
        start, end = points[0], points[-1]
        start_is_wire = wire_graph.touches_wire(start)
        end_is_wire = wire_graph.touches_wire(end)
        start_is_bus = bus_graph.touches_bus(start)
        end_is_bus = bus_graph.touches_bus(end)

        if start_is_wire and end_is_bus:
            wire_point, bus_point = start, end
        elif end_is_wire and start_is_bus:
            wire_point, bus_point = end, start
        else:
            continue

        wire_graph.connect_point(wire_point)
        bus_graph.connect_point(bus_point)
        source_key = _node_value(entry_node[1:], "uuid") or str(index)
        entries.append(
            _BusEntryCandidate(
                id=_source_id(scope_id, "bus_entry", source_key),
                scope_id=scope_id,
                source_index=index,
                start=start,
                end=end,
                wire_point=wire_point,
                bus_point=bus_point,
                bus_group_id=_source_id(
                    scope_id,
                    "bus_group",
                    _point_key(bus_graph.find(bus_point)),
                ),
            )
        )
    return entries


def _sheet_symbol_sources(
    data: SExpNode,
    scope_id: ScopeId,
    wire_graph: WireGraph,
    text_variables: Mapping[str, str],
    ctx: ParseContext | None,
    warned_variables: set[str],
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
            pin_name = _resolve_text_variables(
                _atom_text(pin_node[1]),
                text_variables,
                ctx,
                warned_variables,
            )
            if _is_bus_label_text(pin_name):
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


def _is_bus_label_text(text: str) -> bool:
    return _bus_syntax_text(text) is not None


def _bus_syntax_text(text: str) -> str | None:
    if "${" in text:
        return None
    stripped = strip_kicad_markup(text)
    return stripped if bus_kind_for_name(stripped) is not None else None


def _power_symbol_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    lib_pins: LibPins,
    lib_power_kinds: LibPowerKinds,
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
        power_kind = resolve_lib_power_kind(lib_id, lib_power_kinds) or "global"
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
        if ref.startswith("#FLG"):
            # PWR_FLAG symbols mark a wire as intentionally driven; their
            # Value ("PWR_FLAG") is not a net name. Anchor the wire point but
            # contribute no name evidence, or every flagged rail would merge.
            for location in pin_locations:
                wire_graph.connect_point(location)
            continue
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
                    power_kind=power_kind,
                    location=location,
                ),
            )
    return candidates


def _property_visible(prop_node: SExpNode) -> bool:
    """A property is visible unless its effects carry a hide token.

    KiCad writes ``(effects … hide)`` (v6) or ``(effects … (hide yes))``
    (v7+).
    """
    effects = sexp.find(prop_node[3:], "effects")
    if effects is None:
        return True
    for item in effects[1:]:
        if isinstance(item, sexpdata.Symbol) and item.value() == "hide":
            return False
        if sexp.tag(item) == "hide" and isinstance(item, list):
            return not (len(item) > 1 and str(item[1]) == "yes")
    return True


def _symbol_parameters(sym_node: SExpNode) -> tuple[Parameter, ...]:
    """All ``(property …)`` fields of a symbol, in document order."""
    parameters: list[Parameter] = []
    for prop_node in sexp.find_all(sym_node[1:], "property"):
        if len(prop_node) < 3:
            continue
        parameters.append(
            Parameter(
                name=str(prop_node[1]),
                value=str(prop_node[2]),
                visible=_property_visible(prop_node),
            )
        )
    return tuple(parameters)


def _bool_attr(sym_node: SExpNode, tag: str) -> bool | None:
    """Read a ``(tag yes|no)`` symbol attribute; None when absent."""
    node = sexp.find(sym_node[1:], tag)
    if node is None or len(node) < 2:
        return None
    return sexp.val(node) == "yes"


def _library_link(lib_id: str) -> LibraryLink | None:
    if not lib_id:
        return None
    library, sep, symbol = lib_id.partition(":")
    if not sep:
        return LibraryLink(symbol=lib_id)
    return LibraryLink(symbol=symbol, library=library)


def _footprint_models(footprint: str) -> tuple[FootprintModel, ...]:
    if not footprint:
        return ()
    library, sep, name = footprint.partition(":")
    if not sep:
        return (FootprintModel(name=footprint, is_current=True),)
    return (FootprintModel(name=name, library=library, is_current=True),)


def _component_info(sym_node: SExpNode, lib_id: str, footprint: str) -> ResolvedComponentInfo:
    in_bom = _bool_attr(sym_node, "in_bom")
    return ResolvedComponentInfo(
        parameters=_symbol_parameters(sym_node),
        lib=_library_link(lib_id),
        footprints=_footprint_models(footprint),
        explicit_dnp=_bool_attr(sym_node, "dnp"),
        exclude_from_bom=in_bom is False,
    )


def _component_attr_metadata(sym_node: SExpNode) -> dict[str, str]:
    """Non-default fit attributes preserved as metadata keys."""
    metadata: dict[str, str] = {}
    if _bool_attr(sym_node, "on_board") is False:
        metadata["kicad_on_board"] = "no"
    if _bool_attr(sym_node, "exclude_from_sim") is True:
        metadata["kicad_exclude_from_sim"] = "yes"
    return metadata


def _netclass_flag_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    wire_graph: WireGraph,
) -> list[_NetclassFlagCandidate]:
    candidates: list[_NetclassFlagCandidate] = []
    for index, flag_node in enumerate(sexp.find_all(data[1:], "netclass_flag")):
        at_node = sexp.find(flag_node[1:], "at")
        if at_node is None:
            continue
        location = point_from_at(at_node)
        wire_graph.connect_point(location)
        rotation = sexp.num(at_node, 3) if len(at_node) > 3 else 0.0
        metadata = _property_metadata(flag_node)
        net_class = metadata.get("Netclass", "").strip()
        component_class = metadata.get("Component Class", "").strip()
        if not net_class and not component_class:
            continue
        source_key = _node_value(flag_node[1:], "uuid") or str(index)
        candidates.append(
            _NetclassFlagCandidate(
                id=_source_id(scope_id, "netclass_flag", source_key),
                scope_id=scope_id,
                source_index=index,
                location=location,
                rotation=rotation,
                net_class=net_class,
                component_class=component_class,
                metadata=metadata,
            )
        )
    return candidates


def _property_metadata(node: SExpNode) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for prop_node in sexp.find_all(node[1:], "property"):
        if len(prop_node) < 3:
            continue
        metadata[str(prop_node[1])] = str(prop_node[2])
    return metadata


def _pin_candidates(
    data: SExpNode,
    scope_id: ScopeId,
    lib_pins: LibPins,
    lib_descs: dict[str, str],
    wire_graph: WireGraph,
    root_uuid: str = "",
) -> list[_PinCandidate]:
    no_connect_positions = _no_connect_positions(data)
    instance_path = _scope_instance_path(root_uuid, scope_id)
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
        # A sheet file instantiated by several sheet symbols stores one
        # (reference, unit) assignment per instance path; the file-level
        # Reference property only reflects one of them.
        instance_ref, instance_unit = _instance_assignment(sym_node, instance_path)
        if instance_ref:
            ref = instance_ref
        if instance_unit is not None:
            inst_unit = instance_unit
        symbol_uuid = _node_value(sym_node[1:], "uuid") or ref or str(source_index)
        component_source_id = _source_id(scope_id, "component", symbol_uuid)
        component_identity_source_id = _resolved_component_identity_source_id(
            scope_id,
            sym_node,
            component_source_id,
            instance_path,
        )
        pin_uuids = _pin_uuids_by_designator(sym_node)
        component_info = _component_info(sym_node, lib_id, footprint)
        component_attr_metadata = _component_attr_metadata(sym_node)

        unit_pins = resolve_lib_pins(lib_id, lib_pins)
        has_multiple_units = len([unit for unit in unit_pins if unit != 0]) > 1
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
                    component_has_multiple_units=has_multiple_units,
                    component_reference=ref,
                    component_value=value,
                    component_footprint=footprint,
                    component_datasheet=datasheet,
                    component_description=description,
                    component_x=comp_x,
                    component_y=comp_y,
                    component_rotation=comp_rot,
                    component_mirror=mirrored,
                    component_info=component_info,
                    component_attr_metadata=component_attr_metadata,
                    pin_designator=pin.number,
                    pin_name=pin.name,
                    pin_net_name=pin.net_name,
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
    scope_instance_path: str,
) -> str:
    instance_path = _symbol_instance_path(sym_node, scope_instance_path)
    if not instance_path:
        return component_source_id
    return _source_id(scope_id, "component_instance", instance_path)


def _scope_instance_path(root_uuid: str, scope_id: ScopeId) -> str:
    """The KiCad instance-path string for this scope: /<root-uuid>/<sheet-uuids>."""
    if not root_uuid:
        return ""
    return "/" + "/".join((root_uuid, *scope_id.path))


def _instance_assignment(sym_node: SExpNode, scope_instance_path: str) -> tuple[str, int | None]:
    """Look up the (reference, unit) assigned to this symbol for one instance path."""
    path_node = _matching_instance_path_node(sym_node, scope_instance_path)
    if path_node is None:
        return "", None
    ref_node = sexp.find(path_node[2:], "reference")
    unit_node = sexp.find(path_node[2:], "unit")
    reference = sexp.val(ref_node) if ref_node is not None else ""
    unit = int(sexp.num(unit_node, 1)) if unit_node is not None else None
    return reference, unit


def _matching_instance_path_node(
    sym_node: SExpNode,
    scope_instance_path: str,
) -> SExpNode | None:
    if not scope_instance_path:
        return None
    instances_node = sexp.find(sym_node[1:], "instances")
    if instances_node is None:
        return None
    for project_node in sexp.find_all(instances_node[1:], "project"):
        for path_node in sexp.find_all(project_node[1:], "path"):
            if len(path_node) > 1 and str(path_node[1]) == scope_instance_path:
                return path_node
    return None


def _symbol_instance_path(sym_node: SExpNode, scope_instance_path: str) -> str:
    if _matching_instance_path_node(sym_node, scope_instance_path) is not None:
        return scope_instance_path
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


def _point_key(point: KiCadPoint) -> str:
    return f"{point[0]:.4f}:{point[1]:.4f}"


def _node_value(items: SExpNode, tag_name: str) -> str:
    node = sexp.find(items, tag_name)
    return sexp.val(node) if node is not None else ""
