"""OrCAD DSN-native schematic source objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import ScopeId, TitleBlock

type DsnPoint = tuple[int, int]


def dsn_name_key(name: str) -> str:
    """Return the DSN comparison key for a source spelling."""
    return name.casefold()


def dsn_page_id(page_name: str) -> str:
    """Public source ID for a DSN schematic page."""
    return f"page:{page_name or 'unnamed'}"


def dsn_component_source_id(page_id: str, db_id: int, instance_index: int) -> str:
    """Public source ID for a DSN placed component instance."""
    return f"{page_id}:component:{db_id or instance_index}"


def dsn_component_public_id(component_source_id: str) -> str:
    """Public component ID for a DSN placed component instance."""
    return f"dsn:component:{component_source_id}"


def dsn_pin_public_id(component_source_id: str, pin_designator: str) -> str:
    """Public pin ID for a DSN placed pin."""
    return f"{dsn_component_public_id(component_source_id)}:pin:{pin_designator}"


@dataclass(slots=True)
class DsnWireAlias:
    id: str
    scope_id: ScopeId
    name: str
    name_key: str
    location: DsnPoint
    color: int = 0
    rotation: int = 0
    font_idx: int = 0
    kind: str = field(default="wire_alias", init=False)


@dataclass(slots=True)
class DsnWire:
    id: str
    scope_id: ScopeId
    local_net_id: str
    source_net_id: int
    start: DsnPoint
    end: DsnPoint
    points: list[DsnPoint]
    aliases: list[DsnWireAlias]
    is_bus: bool = False
    color: int = 0
    # Persistent wire dbid; min over a net cluster yields the stored
    # N##### autoname. 0 means the dbid was not parsed.
    db_id: int = 0
    kind: str = field(default="wire", init=False)


@dataclass(slots=True)
class DsnBusEntry:
    id: str
    scope_id: ScopeId
    start: DsnPoint
    end: DsnPoint
    color: int = 0
    kind: str = field(default="bus_entry", init=False)


@dataclass(frozen=True, slots=True)
class DsnBundleMember:
    name: str
    name_key: str
    wire_type: int = 0


@dataclass(slots=True)
class DsnNetBundle:
    id: str
    name: str
    name_key: str
    members: tuple[DsnBundleMember, ...]
    source_kind: str = "net_bundle_map"
    kind: str = field(default="net_bundle", init=False)


@dataclass(slots=True)
class DsnPinOccurrence:
    id: str
    scope_id: ScopeId
    local_net_id: str | None
    source_net_id: int
    component_source_id: str
    component_reference: str
    component_part: str
    pin_designator: str
    pin_name: str
    location: DsnPoint
    no_connect: bool = False
    # Instance-level evidence shared by all pins of a placed instance:
    # name/value properties (insertion-ordered as parsed) and placement
    # coordinates in raw DSN units.
    component_props: dict[str, str] = field(default_factory=dict)
    component_props_list: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    pin_metadata: dict[str, str] = field(default_factory=dict)
    component_x: float | None = None
    component_y: float | None = None
    pin_metadata: dict[str, str] = field(default_factory=dict)
    pin_occurrence_metadata: dict[str, str] = field(default_factory=dict)
    component_metadata: dict[str, str] = field(default_factory=dict)
    kind: str = field(default="pin", init=False)


@dataclass(slots=True)
class DsnPort:
    id: str
    scope_id: ScopeId
    local_net_id: str
    source_net_id: int
    name: str
    name_key: str
    location: DsnPoint
    props: dict[str, str] = field(default_factory=dict)
    kind: str = field(default="port", init=False)


@dataclass(slots=True)
class DsnGlobal:
    """A power symbol occurrence.

    ``name`` is the net name the symbol carries (its net-name property),
    not the graphic symbol name (``VCC_ARROW``, ``VCC_BAR``); the symbol
    name lives in ``symbol``.
    """

    id: str
    scope_id: ScopeId
    local_net_id: str
    source_net_id: int
    name: str
    name_key: str
    location: DsnPoint
    symbol: str = ""
    props: dict[str, str] = field(default_factory=dict)
    kind: str = field(default="global", init=False)


@dataclass(slots=True)
class DsnOffPageConnector:
    """An off-page connector occurrence.

    ``name`` is the net name the connector carries, not the graphic symbol
    name (``OFFPAGELEFT-L``); the symbol name lives in ``symbol``.
    """

    id: str
    scope_id: ScopeId
    local_net_id: str
    source_net_id: int
    name: str
    name_key: str
    location: DsnPoint
    symbol: str = ""
    props: dict[str, str] = field(default_factory=dict)
    kind: str = field(default="off_page_connector", init=False)


@dataclass(slots=True)
class DsnPageNet:
    id: str
    scope_id: ScopeId
    net_id: int
    name: str
    name_key: str
    pin_ids: list[str] = field(default_factory=list)
    wire_ids: list[str] = field(default_factory=list)
    port_ids: list[str] = field(default_factory=list)
    global_ids: list[str] = field(default_factory=list)
    off_page_connector_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DsnPageSource:
    id: str
    name: str
    scope_id: ScopeId
    nets: list[DsnPageNet]
    wires: list[DsnWire]
    pin_occurrences: list[DsnPinOccurrence]
    ports: list[DsnPort]
    globals: list[DsnGlobal]
    off_page_connectors: list[DsnOffPageConnector]
    bus_entries: list[DsnBusEntry] = field(default_factory=list)
    title_block: TitleBlock | None = None


@dataclass(slots=True)
class DsnHierarchyMapping:
    id: str
    db_id: int
    name: str
    name_key: str


@dataclass(slots=True)
class DsnSourceDesign:
    name: str
    pages: list[DsnPageSource]
    hierarchy_mappings: list[DsnHierarchyMapping]
    net_bundles: list[DsnNetBundle] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
