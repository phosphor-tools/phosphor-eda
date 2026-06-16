"""Format-agnostic public schematic domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import override


@dataclass(frozen=True, slots=True)
class ScopeId:
    """Hierarchical source scope for a public schematic object occurrence."""

    path: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return "/" + "/".join(self.path)


class ComponentKind(StrEnum):
    """Functional class of a component for BOM/netlist purposes."""

    STANDARD = "standard"
    MECHANICAL = "mechanical"
    GRAPHICAL = "graphical"
    NET_TIE = "net_tie"
    OTHER = "other"


class DnpSource(StrEnum):
    """Provenance of a component's do-not-populate status."""

    EXPLICIT = "explicit"  # native flag (KiCad dnp attribute)
    CONVENTION = "convention"  # whole-value parameter/comment match


@dataclass(frozen=True)
class Parameter:
    """One source parameter/property occurrence — ordered, duplicate-tolerant.

    ``metadata`` keeps the normalized convenience dict (first occurrence of a
    name wins on collision); this is the faithful record.
    """

    name: str
    value: str
    visible: bool = False
    indirect: bool = False  # Altium "=Name" reference — value holds resolved text
    source: str = ""


@dataclass(frozen=True)
class LibraryLink:
    """Where a component's symbol came from."""

    symbol: str = ""  # Altium LIBREFERENCE / KiCad lib_id name / OrCAD cache part
    library: str = ""  # source library name/nickname
    design_item_id: str = ""  # Altium DesignItemId / CIS Part_Number — DB-library key
    source: str = ""  # "embedded" | "project" | "global" | "database"


@dataclass(frozen=True)
class FootprintModel:
    """One footprint binding (Altium stores N models, one current)."""

    name: str
    library: str = ""
    is_current: bool = False
    description: str = ""


@dataclass(frozen=True)
class PartNumber:
    """A manufacturer (or supplier) part identity."""

    manufacturer: str
    number: str


@dataclass
class TitleBlock:
    """Structured per-sheet title block fields."""

    title: str = ""
    revision: str = ""
    date: str = ""
    organization: str = ""
    org_address: str = ""
    document_number: str = ""
    sheet_number: str = ""
    sheet_total: str = ""
    author: str = ""
    drawn_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    created_date: str = ""
    modified_date: str = ""
    cage_code: str = ""
    comments: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(repr=False)
class Pin:
    """A logical component pin and its resolved electrical connection."""

    id: str
    designator: str
    name: str
    component: Component
    net: Net | None = None
    no_connect: bool = False
    occurrences: list[PinOccurrence] = field(default_factory=list, kw_only=True)
    metadata: dict[str, str] = field(default_factory=dict)

    @override
    def __repr__(self) -> str:
        net_name = self.net.name if self.net else None
        return (
            f"Pin({self.id!r}, {self.designator!r}, "
            f"component={self.component.reference!r}, net={net_name!r})"
        )


@dataclass(slots=True)
class PinOccurrence:
    """Source placement/provenance for a logical pin."""

    id: str
    pin: Pin
    page: Page
    scope_id: ScopeId
    source_id: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(repr=False)
class Component:
    """A logical component the agent should reason about."""

    id: str
    reference: str
    part: str
    description: str
    pins: list[Pin] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    occurrences: list[ComponentOccurrence] = field(default_factory=list)
    kind: ComponentKind = ComponentKind.STANDARD
    parameters: list[Parameter] = field(default_factory=list)
    lib: LibraryLink | None = None
    footprints: list[FootprintModel] = field(default_factory=list)
    part_numbers: list[PartNumber] = field(default_factory=list)
    datasheet: str = ""
    dnp: bool = False
    dnp_source: DnpSource | None = None
    exclude_from_bom: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def footprint(self) -> FootprintModel | None:
        """The current footprint model, if any."""
        for model in self.footprints:
            if model.is_current:
                return model
        return self.footprints[0] if self.footprints else None

    @override
    def __repr__(self) -> str:
        return (
            f"Component({self.id!r}, {self.reference!r}, part={self.part!r}, pins={len(self.pins)})"
        )


@dataclass(slots=True)
class ComponentOccurrence:
    """Source placement/provenance for a logical component."""

    id: str
    component: Component
    page: Page
    scope_id: ScopeId
    source_id: str
    part_id: str = ""
    x: float | None = None
    y: float | None = None
    rotation: float = 0.0
    mirror: bool = False
    # Per-instance physical reference designator (e.g. "U1.3") from an Altium
    # .Annotation file. Empty for single-instance components and formats/designs
    # without per-instance annotation. The logical ``component.reference`` stays
    # the identity; this is occurrence-level metadata, never a substitute.
    physical_designator: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class NetNameKind(StrEnum):
    """Provenance class of a net name."""

    LABEL = "label"  # designer-assigned
    TOOL_AUTO = "tool_auto"  # tool-generated, read or replicated from the source tool
    SYNTHESIZED = "synthesized"  # ours — only when the tool defines none


@dataclass(frozen=True)
class NetName:
    """One piece of net-name evidence."""

    name: str
    kind: NetNameKind
    scope: ScopeId | None = None
    source: str = ""


class SchematicDirectiveKind(StrEnum):
    """Schematic-side assignment directives anchored to source net evidence."""

    NET_CLASS = "net_class"
    COMPONENT_CLASS = "component_class"
    DIFF_PAIR = "diff_pair"
    DIFF_PAIR_CLASS = "diff_pair_class"


@dataclass(frozen=True)
class SchematicDirective:
    """A source-anchored net/class assignment from schematic markup."""

    kind: SchematicDirectiveKind
    value: str
    source: str
    source_id: str = ""
    native_name: str = ""
    x: float | None = None
    y: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(repr=False)
class Net:
    """A resolved electrical connection between pins.

    ``name`` is the canonical name selected per the source tool's own
    policy; ``names`` holds all evidence; ``aliases`` is the derived set of
    non-canonical evidence names (kept as a plain set for query surfaces).
    """

    id: str
    name: str
    pins: list[Pin] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    occurrences: list[NetOccurrence] = field(default_factory=list)
    names: list[NetName] = field(default_factory=list)
    aliases: set[str] = field(default_factory=set)
    directives: list[SchematicDirective] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    @override
    def __repr__(self) -> str:
        return f"Net({self.id!r}, {self.name!r}, pins={len(self.pins)})"


@dataclass(slots=True)
class NetOccurrence:
    """Source local-net evidence for a resolved net."""

    id: str
    net: Net
    page: Page
    scope_id: ScopeId
    source_local_net_id: str
    source_names: set[str] = field(default_factory=set)
    directives: list[SchematicDirective] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


class BusKind(StrEnum):
    """How a source schematic groups bus member nets."""

    VECTOR = "vector"
    GROUP = "group"
    HARNESS = "harness"


@dataclass(repr=False)
class Bus:
    """A resolved source bus and the nets that belong to it.

    Buses are one-way grouping relationships over already-resolved nets. A net
    can belong to multiple buses, so membership lives here rather than on
    :class:`Net`.
    """

    id: str
    name: str
    kind: BusKind
    members: list[Net] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    @override
    def __repr__(self) -> str:
        return f"Bus({self.id!r}, {self.name!r}, kind={self.kind!r}, members={len(self.members)})"


@dataclass
class Page:
    """A single schematic sheet."""

    id: str
    name: str
    source_file: str = ""
    scope_id: ScopeId = field(default_factory=lambda: ScopeId(path=()))
    components: list[Component] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    title_block: TitleBlock | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Schematic:
    """A complete schematic design. The top-level public container."""

    name: str
    pages: list[Page] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    buses: list[Bus] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
