"""Signal path tracing through 2-pin passives.

Walks the net graph, treating 2-pin passives (resistors, capacitors, ferrite
beads, etc.) as transparent waypoints rather than endpoints.  This lets callers
answer "where does this signal actually go?" without manually hopping through
series components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.classify import PASSIVE_PREFIXES, is_power_net, ref_prefix

if TYPE_CHECKING:
    from phosphor_eda.schematic import Component, Net, Pin, Schematic


@dataclass
class Waypoint:
    """A 2-pin passive traversed during a trace."""

    component: Component
    entry_pin: Pin
    exit_pin: Pin
    exit_net: Net


@dataclass
class TraceResult:
    """Result of tracing from a pin through series passives."""

    origin_pin: Pin
    origin_net: Net
    series_path: list[Waypoint] = field(default_factory=list)
    terminal_pin: Pin | None = None
    terminal_net: Net | None = None
    shunts: list[tuple[Component, Net]] = field(default_factory=list)


@dataclass
class ConnectionPath:
    """A signal path between two components."""

    left_pin: Pin
    right_pin: Pin
    series: list[Component] = field(default_factory=list)
    shunts: list[tuple[Component, Net]] = field(default_factory=list)


def is_two_pin_passive(comp: Component) -> bool:
    """True if *comp* is a passive with exactly 2 pins."""
    return len(comp.pins) == 2 and ref_prefix(comp.reference) in PASSIVE_PREFIXES


def _same_component(left: Component, right: Component) -> bool:
    return left.id == right.id


def trace_from_net(
    net: Net,
    origin_comp: Component | None = None,
) -> list[TraceResult]:
    """Trace through 2-pin passives reachable from *net*.

    For each 2-pin passive on *net*, follows through to the net on its other
    pin.  Recursion continues until an active component, a power net, or a
    previously visited net is reached.

    *origin_comp* is excluded from the passive scan (it's the component we're
    tracing *from*, not through).

    Returns one :class:`TraceResult` per distinct active endpoint found.
    Shunt passives (one pin on a power net) are recorded in each result but
    do not produce their own result entry.
    """
    # Classify passives on this net as series or shunt
    series_pins: list[Pin] = []
    net_shunts: list[tuple[Component, Net]] = []

    for pin in net.pins:
        comp = pin.component
        if origin_comp is not None and _same_component(comp, origin_comp):
            continue
        if not is_two_pin_passive(comp):
            continue
        other = other_pin(comp, pin)
        if other.net is not None and is_power_net(other.net.name, other.net):
            net_shunts.append((comp, other.net))
        else:
            series_pins.append(pin)

    # Follow each series passive, attaching shunts from this net
    results: list[TraceResult] = []
    for pin in series_pins:
        result = TraceResult(origin_pin=pin, origin_net=net, shunts=list(net_shunts))
        _walk(
            passive=pin.component,
            entry_pin=pin,
            net=net,
            result=result,
            visited={net.id},
        )
        results.append(result)

    return results


def _walk(
    passive: Component,
    entry_pin: Pin,
    net: Net,
    result: TraceResult,
    visited: set[str],
) -> None:
    """Recursive walk through a chain of 2-pin passives."""
    exit_pin = other_pin(passive, entry_pin)
    exit_net = exit_pin.net

    # Dead end — pin has no net
    if exit_net is None:
        result.series_path.append(
            Waypoint(passive, entry_pin, exit_pin, net),
        )
        result.terminal_net = net
        return

    # Shunt to power — record but don't follow
    if is_power_net(exit_net.name, exit_net):
        result.shunts.append((passive, exit_net))
        return

    waypoint = Waypoint(passive, entry_pin, exit_pin, exit_net)
    result.series_path.append(waypoint)

    if exit_net.id in visited:
        # Cycle — stop here
        result.terminal_net = exit_net
        return

    visited.add(exit_net.id)

    # Look at what's on the other side of exit_net
    active_pins: list[Pin] = []
    next_passives: list[Pin] = []

    for p in exit_net.pins:
        if p is exit_pin:
            continue
        if is_two_pin_passive(p.component):
            next_passives.append(p)
        else:
            active_pins.append(p)

    # Collect shunts from passives on exit_net whose other side is power
    for p in next_passives:
        other = other_pin(p.component, p)
        if other.net is not None and is_power_net(other.net.name, other.net):
            result.shunts.append((p.component, other.net))

    if active_pins:
        # Reached an active component — pick the first as terminal
        result.terminal_pin = active_pins[0]
        result.terminal_net = exit_net
    elif next_passives:
        # Only more passives — keep walking through series ones (non-shunt)
        series_passives = [
            p
            for p in next_passives
            if not (
                (other_net := other_pin(p.component, p).net) is not None
                and is_power_net(other_net.name, other_net)
            )
        ]
        if series_passives:
            _walk(
                series_passives[0].component,
                series_passives[0],
                exit_net,
                result,
                visited,
            )
        else:
            result.terminal_net = exit_net
    else:
        # Dead end net
        result.terminal_net = exit_net


def other_pin(comp: Component, pin: Pin) -> Pin:
    """Return the other pin on a 2-pin component."""
    for p in comp.pins:
        if p is not pin:
            return p
    raise ValueError(f"{comp.reference} does not have a second pin")


def find_paths(design: Schematic, ref_a: str, ref_b: str) -> list[ConnectionPath]:
    """Find all signal paths between two components, tracing through passives.

    Returns one :class:`ConnectionPath` per signal-level connection between
    the components identified by *ref_a* and *ref_b*.
    """
    comp_a = _find_component(design, ref_a)
    comp_b = _find_component(design, ref_b)

    paths: list[ConnectionPath] = []

    for pin_a in comp_a.pins:
        if pin_a.net is None or pin_a.no_connect:
            continue
        if is_power_net(pin_a.net.name, pin_a.net):
            continue

        # Direct connection — comp_b is on the same net
        for pin_b in pin_a.net.pins:
            if _same_component(pin_b.component, comp_b):
                path = ConnectionPath(left_pin=pin_a, right_pin=pin_b)
                # Collect shunts on this net
                for p in pin_a.net.pins:
                    if is_two_pin_passive(p.component):
                        other = other_pin(p.component, p)
                        if other.net is not None and is_power_net(other.net.name, other.net):
                            path.shunts.append((p.component, other.net))
                paths.append(path)

        # Indirect connection — trace through passives
        for trace_result in trace_from_net(pin_a.net, origin_comp=comp_a):
            if trace_result.terminal_pin is None:
                continue
            if not _same_component(trace_result.terminal_pin.component, comp_b):
                continue
            paths.append(
                ConnectionPath(
                    left_pin=pin_a,
                    right_pin=trace_result.terminal_pin,
                    series=[w.component for w in trace_result.series_path],
                    shunts=trace_result.shunts,
                )
            )

    return sorted(paths, key=lambda p: p.left_pin.designator)


def _find_component(design: Schematic, ref: str) -> Component:
    matches = [comp for comp in design.components if comp.reference == ref]
    if not matches:
        raise ValueError(f"Component '{ref}' not found in design.")
    if len(matches) > 1:
        locations: list[str] = []
        for comp in matches:
            page_names = sorted({page.name for page in comp.pages})
            location = ", ".join(page_names) if page_names else "unknown page"
            locations.append(f"{comp.reference} on {location}")
        raise ValueError(
            f"Component reference '{ref}' is ambiguous; matches: {', '.join(locations)}."
        )
    return matches[0]
