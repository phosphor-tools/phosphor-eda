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


def merge_pages(
    name: str,
    pages: list[Page],
    metadata: dict[str, str] | None = None,
) -> Design:
    """Merge per-page sub-graphs into a single resolved Design.

    1. Nets with the same name across pages merge into one Net.
    2. Ports with matching names bridge their respective nets.
    3. Components with the same reference designator merge into one.
    """
    merged_nets: dict[str, Net] = {}
    merged_components: dict[str, Component] = {}

    # Pass 1: Merge nets by name and components by reference
    for page in pages:
        for net in page.nets:
            if net.name in merged_nets:
                target = merged_nets[net.name]
                for pin in net.pins:
                    pin.net = target
                    target.pins.append(pin)
                if net.bus and not target.bus:
                    target.bus = net.bus
                target.metadata.update(net.metadata)
            else:
                merged_nets[net.name] = net

        for comp in page.components:
            if comp.reference in merged_components:
                target = merged_components[comp.reference]
                for pin in comp.pins:
                    pin.component = target
                    target.pins.append(pin)
                if page not in target.pages:
                    target.pages.append(page)
                target.metadata.update(comp.metadata)
            else:
                merged_components[comp.reference] = comp

    # Pass 2: Bridge nets via ports with matching names
    ports_by_name: dict[str, list[Port]] = {}
    for page in pages:
        for port in page.ports:
            ports_by_name.setdefault(port.name, []).append(port)

    for port_list in ports_by_name.values():
        if len(port_list) < 2:
            continue
        # Resolve the target net (may already have been merged by name)
        target = _resolve_net(merged_nets, port_list[0].net)
        for port in port_list[1:]:
            other = _resolve_net(merged_nets, port.net)
            if other is target:
                continue
            # Move all pins from other net to target
            for pin in other.pins:
                pin.net = target
                target.pins.append(pin)
            # Remove other from merged_nets
            for k, v in list(merged_nets.items()):
                if v is other:
                    del merged_nets[k]

    return Design(
        name=name,
        pages=pages,
        nets=sorted(merged_nets.values(), key=lambda n: n.name),
        components=sorted(merged_components.values(), key=lambda c: c.reference),
        metadata=metadata or {},
    )


def _resolve_net(merged_nets: dict[str, Net], net: Net) -> Net:
    """Follow merges to find the canonical net object."""
    for v in merged_nets.values():
        if v is net:
            return v
    # Net was already merged into another — find which one has our pins
    for v in merged_nets.values():
        if any(p.net is v for p in net.pins):
            return v
    return net
