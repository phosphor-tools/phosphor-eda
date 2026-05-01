"""Format-agnostic schematic domain model.

A schematic is a graph: nets connect pins, pins belong to components,
ports bridge nets across pages. See docs/plans/2026-02-25-ecad-tools-package-design.md.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(repr=False)
class Pin:
    """A component pin. References its parent component and connected net."""

    designator: str
    name: str
    component: Component
    net: Net | None = None
    no_connect: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        net_name = self.net.name if self.net else None
        return f"Pin({self.designator!r}, component={self.component.reference!r}, net={net_name!r})"


@dataclass(repr=False)
class Component:
    """A placed component (IC, resistor, connector, etc.)."""

    reference: str
    part: str
    description: str
    pins: list[Pin] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    x: float | None = None
    y: float | None = None
    rotation: float = 0.0
    mirror: bool = False

    def __repr__(self) -> str:
        return f"Component({self.reference!r}, part={self.part!r}, pins={len(self.pins)})"


@dataclass(repr=False)
class Net:
    """A named electrical connection between pins."""

    name: str
    pins: list[Pin] = field(default_factory=list)
    aliases: set[str] = field(default_factory=set)
    bus: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Net({self.name!r}, pins={len(self.pins)})"


@dataclass(repr=False)
class Port:
    """A cross-page connection point. Bridges a net to another page."""

    name: str
    page: Page
    net: Net
    harness: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Port({self.name!r}, page={self.page.name!r}, net={self.net.name!r})"


@dataclass
class Page:
    """A single schematic sheet."""

    name: str
    components: list[Component] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    annotations: list[str] = field(default_factory=list)
    """Free-text annotations placed on the schematic sheet — revision notes,
    design rationale, change history, configuration documentation. Each entry
    is one text block as placed by the designer."""


@dataclass
class Schematic:
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
) -> Schematic:
    """Merge per-page sub-graphs into a single resolved Schematic.

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
                # Deduplicate pins by designator — shared pins
                # (owner_part_id==0) appear on every page, and we only
                # want one entry per designator in the merged component.
                existing_pins: dict[str, Pin] = {p.designator: p for p in target.pins}
                for pin in comp.pins:
                    if pin.designator not in existing_pins:
                        pin.component = target
                        target.pins.append(pin)
                        existing_pins[pin.designator] = pin
                    else:
                        existing = existing_pins[pin.designator]
                        # Upgrade if the new pin has a net but the old one
                        # doesn't (or has a no-connect marker).
                        if (
                            existing.net is None
                            and not existing.no_connect
                            and (pin.net is not None or pin.no_connect)
                        ):
                            # Use identity removal — dataclass __eq__
                            # recurses on circular Pin↔Component refs.
                            target.pins = [p for p in target.pins if p is not existing]
                            pin.component = target
                            target.pins.append(pin)
                            existing_pins[pin.designator] = pin
                        else:
                            # Discard duplicate — remove from its net
                            if pin.net is not None:
                                pin.net.pins = [p for p in pin.net.pins if p is not pin]
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
        # Resolve the target net (may already have been merged by name).
        target = _resolve_net(merged_nets, port_list[0].net)
        for port in port_list[1:]:
            other = _resolve_net(merged_nets, port.net)
            target = _unify_nets(merged_nets, target, other)

    # Pass 3: Hierarchical net resolution — bridge child-page nets
    # through parent-page net identity.
    #
    # If two ports (A and B) on the SAME page share the same net
    # (because they're wired together on that page), then ports with
    # matching names on OTHER pages should have their nets unified.
    #
    # Example: parent page has sheet entries DRDY and GPIO_B1_09 both
    # wired to net ADC_DRDY.  Child pages have ports DRDY → local net
    # ADC_DRDY and GPIO_B1_09 → local net GPIO_B1_09.  Pass 2 bridges
    # each port name individually, but doesn't know the parent net
    # links them.  Pass 3 finds the shared parent net and unifies.
    page_net_groups: dict[tuple[str, int], list[Port]] = defaultdict(list)
    for page in pages:
        for port in page.ports:
            resolved = _resolve_net(merged_nets, port.net)
            page_net_groups[(page.name, id(resolved))].append(port)

    for group_ports in page_net_groups.values():
        if len(group_ports) < 2:
            continue
        # Collect nets from matching ports on other pages
        nets_to_unify: list[Net] = []
        parent_page = group_ports[0].page.name
        for gport in group_ports:
            for other_port in ports_by_name.get(gport.name, []):
                if other_port.page.name != parent_page:
                    nets_to_unify.append(
                        _resolve_net(merged_nets, other_port.net),
                    )
        # Unify all collected nets
        if len(nets_to_unify) >= 2:
            target = nets_to_unify[0]
            for other in nets_to_unify[1:]:
                target = _unify_nets(merged_nets, target, other)

    return Schematic(
        name=name,
        pages=pages,
        nets=sorted(merged_nets.values(), key=lambda n: n.name),
        components=sorted(merged_components.values(), key=lambda c: c.reference),
        metadata=metadata or {},
    )


def _unify_nets(
    merged_nets: dict[str, Net],
    target: Net,
    other: Net,
) -> Net:
    """Merge *other* into *target*, returning the surviving net.

    Prefers the net whose name looks "primary" (no ``:``) so that
    canonical signal names survive over synthetic harness names.

    Guards against duplicate pin appends: after a net is absorbed its
    ``.pins`` list becomes stale (still references the moved pins).  If
    a later pass re-encounters the absorbed net and calls ``_unify_nets``
    again, the guard prevents the same Pin objects from appearing twice
    in ``target.pins``.
    """
    if other is target:
        return target
    if ":" in target.name and ":" not in other.name:
        target, other = other, target
    existing = {id(p) for p in target.pins}
    for pin in other.pins:
        pin.net = target
        if id(pin) not in existing:
            target.pins.append(pin)
            existing.add(id(pin))
    if other.bus and not target.bus:
        target.bus = other.bus
    target.metadata.update(other.metadata)
    # Preserve all names: the absorbed net's name and aliases become
    # aliases on the surviving net so every name remains searchable.
    if other.name != target.name:
        target.aliases.add(other.name)
    target.aliases |= other.aliases
    target.aliases.discard(target.name)
    for k, v in list(merged_nets.items()):
        if v is other:
            del merged_nets[k]
    return target


def _resolve_net(merged_nets: dict[str, Net], net: Net) -> Net:
    """Follow merges to find the canonical net object."""
    # 1. Identity check — is this the canonical object?
    for v in merged_nets.values():
        if v is net:
            return v
    # 2. Pin-based — find which canonical net owns our pins
    for v in merged_nets.values():
        if any(p.net is v for p in net.pins):
            return v
    # 3. Name-based — stale net whose key still exists
    if net.name in merged_nets:
        return merged_nets[net.name]
    # 4. Alias-based — net was absorbed and its name became an alias
    for v in merged_nets.values():
        if net.name in v.aliases:
            return v
    return net
