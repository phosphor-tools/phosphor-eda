"""Candidate objects produced while scanning a KiCad sheet.

Candidates carry raw per-sheet evidence (name, location, scope) before wire
connectivity assigns local net ids; the source extractor converts them into
the KiCad-native source objects in :mod:`phosphor_eda.formats.kicad.source`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import ScopeId
    from phosphor_eda.formats.common.resolved_graph import ResolvedComponentInfo
    from phosphor_eda.formats.kicad.source import KiCadPoint, KiCadSheetSymbol


@dataclass(slots=True)
class LabelCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: KiCadPoint


@dataclass(slots=True)
class PowerCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    reference: str
    lib_id: str
    power_kind: str
    location: KiCadPoint


@dataclass(slots=True)
class PinCandidate:
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
class SheetPinCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    sheet_symbol_id: str
    child_scope_id: ScopeId
    name: str
    direction: str
    location: KiCadPoint


@dataclass(slots=True)
class BusSheetPinCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    sheet_symbol_id: str
    child_scope_id: ScopeId
    name: str
    direction: str
    location: KiCadPoint
    bus_group_id: str


@dataclass(slots=True)
class BusLabelCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: KiCadPoint
    kind: str
    bus_group_id: str


@dataclass(slots=True)
class BusAliasCandidate:
    id: str
    scope_id: ScopeId
    name: str
    members: tuple[str, ...]


@dataclass(slots=True)
class BusEntryCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    start: KiCadPoint
    end: KiCadPoint
    wire_point: KiCadPoint
    bus_point: KiCadPoint
    bus_group_id: str


@dataclass(slots=True)
class NetclassFlagCandidate:
    id: str
    scope_id: ScopeId
    source_index: int
    location: KiCadPoint
    rotation: float
    net_class: str
    component_class: str
    metadata: dict[str, str]


@dataclass(slots=True)
class AnnotationCandidate:
    scope_id: ScopeId
    text: str


@dataclass(slots=True)
class SheetCandidates:
    local_labels: list[LabelCandidate]
    global_labels: list[LabelCandidate]
    hierarchical_labels: list[LabelCandidate]
    bus_labels: list[BusLabelCandidate]
    bus_aliases: list[BusAliasCandidate]
    bus_entries: list[BusEntryCandidate]
    power_symbols: list[PowerCandidate]
    sheet_symbols: list[KiCadSheetSymbol]
    sheet_pins: list[SheetPinCandidate]
    bus_sheet_pins: list[BusSheetPinCandidate]
    pin_occurrences: list[PinCandidate]
    netclass_flags: list[NetclassFlagCandidate]
    annotations: list[AnnotationCandidate]
