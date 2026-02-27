"""Format-agnostic schematic domain model.

A schematic is a graph: nets connect pins, pins belong to components,
ports bridge nets across pages. See docs/plans/2026-02-25-ecad-tools-package-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pin:
    """A component pin. References its parent component and connected net."""

    designator: str
    name: str
    component: Component
    net: Net | None = None
    no_connect: bool = False
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Component:
    """A placed component (IC, resistor, connector, etc.)."""

    reference: str
    part: str
    description: str
    pins: list[Pin] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Net:
    """A named electrical connection between pins."""

    name: str
    pins: list[Pin] = field(default_factory=list)
    bus: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Port:
    """A cross-page connection point. Bridges a net to another page."""

    name: str
    page: Page
    net: Net
    harness: str | None = None


@dataclass
class Page:
    """A single schematic sheet."""

    name: str
    components: list[Component] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Design:
    """A complete schematic design. The top-level container."""

    name: str
    pages: list[Page] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
