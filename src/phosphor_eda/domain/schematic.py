"""Format-agnostic public schematic domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import override


@dataclass(frozen=True, slots=True)
class ScopeId:
    """Hierarchical source scope for a public schematic object occurrence."""

    path: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return "/" + "/".join(self.path)


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
    metadata: dict[str, str] = field(default_factory=dict)

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
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(repr=False)
class Net:
    """A resolved electrical connection between pins."""

    id: str
    name: str
    pins: list[Pin] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    occurrences: list[NetOccurrence] = field(default_factory=list)
    aliases: set[str] = field(default_factory=set)
    bus: str | None = None
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
    metadata: dict[str, str] = field(default_factory=dict)


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
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Schematic:
    """A complete schematic design. The top-level public container."""

    name: str
    pages: list[Page] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
