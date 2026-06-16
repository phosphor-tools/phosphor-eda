"""KiCad-native schematic source objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import SchematicDirective, ScopeId, TitleBlock
    from phosphor_eda.formats.common.resolved_graph import ResolvedComponentInfo

type KiCadPoint = tuple[float, float]


@dataclass(slots=True)
class KiCadSheetInstance:
    id: str
    scope_id: ScopeId
    sheet_name: str
    source_file: str
    parent_scope_id: ScopeId | None = None
    sheet_symbol_id: str = ""
    title_block: TitleBlock | None = None


@dataclass(slots=True)
class KiCadLocalLabel:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: KiCadPoint
    local_net_id: str
    kind: str = field(default="local_label", init=False)


@dataclass(slots=True)
class KiCadGlobalLabel:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: KiCadPoint
    local_net_id: str
    kind: str = field(default="global_label", init=False)


@dataclass(slots=True)
class KiCadHierarchicalLabel:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: KiCadPoint
    local_net_id: str
    kind: str = field(default="hierarchical_label", init=False)


@dataclass(slots=True)
class KiCadPowerSymbol:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    reference: str
    lib_id: str
    location: KiCadPoint
    local_net_id: str
    power_kind: str = "global"
    kind: str = field(default="power_symbol", init=False)


@dataclass(slots=True)
class KiCadSheetSymbol:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    child_source_file: str
    child_scope_id: ScopeId
    location: KiCadPoint
    size: KiCadPoint


@dataclass(slots=True)
class KiCadSheetPin:
    id: str
    scope_id: ScopeId
    source_index: int
    sheet_symbol_id: str
    child_scope_id: ScopeId
    name: str
    direction: str
    location: KiCadPoint
    local_net_id: str
    kind: str = field(default="sheet_pin", init=False)


@dataclass(slots=True)
class KiCadBusLabel:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: KiCadPoint
    kind: str


@dataclass(slots=True)
class KiCadBusAlias:
    id: str
    scope_id: ScopeId
    name: str
    members: tuple[str, ...]


@dataclass(slots=True)
class KiCadNetclassFlag:
    id: str
    scope_id: ScopeId
    source_index: int
    local_net_id: str
    location: KiCadPoint
    rotation: float
    net_class: str = ""
    component_class: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class KiCadPinOccurrence:
    id: str
    scope_id: ScopeId
    source_index: int
    local_net_id: str
    component_source_id: str
    component_identity_source_id: str
    component_unit: int
    component_has_multiple_units: bool
    component_reference: str
    pin_designator: str
    pin_name: str
    pin_net_name: str
    location: KiCadPoint
    pin_type: str = ""
    no_connect: bool = False
    component_value: str = ""
    component_footprint: str = ""
    component_datasheet: str = ""
    component_description: str = ""
    component_x: float | None = None
    component_y: float | None = None
    component_rotation: float = 0.0
    component_mirror: bool = False
    component_info: ResolvedComponentInfo | None = None
    component_attr_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class KiCadLocalNet:
    id: str
    scope_id: ScopeId
    wire_points: set[KiCadPoint]
    pin_ids: list[str]
    local_labels: list[KiCadLocalLabel]
    global_labels: list[KiCadGlobalLabel]
    hierarchical_labels: list[KiCadHierarchicalLabel]
    power_symbols: list[KiCadPowerSymbol]
    sheet_pins: list[KiCadSheetPin]
    generated_name: str
    netclass_flags: list[KiCadNetclassFlag] = field(default_factory=list)
    directives: list[SchematicDirective] = field(default_factory=list)


@dataclass(slots=True)
class KiCadSourceDesign:
    name: str
    root_source_file: str
    root_scope_id: ScopeId
    sheet_instances: list[KiCadSheetInstance]
    local_nets: list[KiCadLocalNet]
    pin_occurrences: list[KiCadPinOccurrence]
    local_labels: list[KiCadLocalLabel]
    global_labels: list[KiCadGlobalLabel]
    hierarchical_labels: list[KiCadHierarchicalLabel]
    bus_labels: list[KiCadBusLabel]
    bus_aliases: list[KiCadBusAlias]
    power_symbols: list[KiCadPowerSymbol]
    sheet_symbols: list[KiCadSheetSymbol]
    sheet_pins: list[KiCadSheetPin]
    schematic_version: int = 20231120
