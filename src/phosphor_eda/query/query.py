"""Filtering and lookup over the schematic domain model.

The ``filter_*`` functions select nets, components, and pages by AND-composed
criteria; ``find_net``/``find_component`` resolve a single object by name with
scoped-net-aware, ambiguity-reporting lookups shared by the CLI and the text
formatters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.buses import bus_memberships
from phosphor_eda.domain.schematic import BusKind
from phosphor_eda.query.classify import PASSIVE_PREFIXES, is_power_net, ref_prefix
from phosphor_eda.query.trace import trace_from_net

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import Bus, Component, Net, Page, Schematic


def net_page_names(net: Net) -> list[str]:
    """Sorted page names a net spans (falling back to its pins' pages)."""
    if net.pages:
        return sorted({page.name for page in net.pages})
    return sorted({page.name for pin in net.pins for page in pin.component.pages})


def _net_pages(net: Net) -> set[str]:
    """Page names a net spans."""
    return set(net_page_names(net))


def _net_components(net: Net) -> set[str]:
    """Component references on a net."""
    return {pin.component.reference for pin in net.pins}


def filter_nets(
    design: Schematic,
    *,
    components: list[str] | None = None,
    pages: list[str] | None = None,
    power: bool | None = None,
    min_pins: int | None = None,
    multi_page: bool = False,
    trace: bool = False,
    bus: str | None = None,
) -> list[Net]:
    """Filter nets from a design.  All criteria are AND-composed."""
    result = list(design.nets)

    if power is True:
        result = [n for n in result if is_power_net(n.name, n)]
    elif power is False:
        result = [n for n in result if not is_power_net(n.name, n)]

    if pages:
        page_set = set(pages)
        result = [n for n in result if _net_pages(n) & page_set]

    if min_pins is not None:
        result = [n for n in result if len(n.pins) >= min_pins]

    if multi_page:
        result = [n for n in result if len(_net_pages(n)) > 1]

    if bus is not None:
        result = [
            n
            for n in result
            if any(member_bus.name == bus for member_bus in bus_memberships(design, n))
        ]

    if components:
        _require_components(design, components)
        comp_set = set(components)
        if trace:
            # Expand each net's component reach through 2-pin passives
            def _reaches(net: Net) -> set[str]:
                refs = _net_components(net)
                for tr in trace_from_net(net):
                    if tr.terminal_pin is not None:
                        refs.add(tr.terminal_pin.component.reference)
                return refs

            result = [n for n in result if comp_set <= _reaches(n)]
        else:
            result = [n for n in result if comp_set <= _net_components(n)]

    return result


def filter_components(
    design: Schematic,
    *,
    pages: list[str] | None = None,
    prefixes: list[str] | None = None,
    passive: bool | None = None,
    min_pins: int | None = None,
    net: str | None = None,
) -> list[Component]:
    """Filter components from a design.  All criteria are AND-composed."""
    result = list(design.components)

    if pages:
        page_set = set(pages)
        result = [c for c in result if page_set & {p.name for p in c.pages}]

    if prefixes:
        prefix_set = set(prefixes)
        result = [c for c in result if ref_prefix(c.reference) in prefix_set]

    if passive is True:
        result = [c for c in result if ref_prefix(c.reference) in PASSIVE_PREFIXES]
    elif passive is False:
        result = [c for c in result if ref_prefix(c.reference) not in PASSIVE_PREFIXES]

    if min_pins is not None:
        result = [c for c in result if len(c.pins) >= min_pins]

    if net is not None:
        net_obj = find_net(design, net)
        refs_on_net = {pin.component.reference for pin in net_obj.pins}
        result = [c for c in result if c.reference in refs_on_net]

    return result


def filter_pages(
    design: Schematic,
    *,
    nets: list[str] | None = None,
    components: list[str] | None = None,
) -> list[Page]:
    """Filter pages from a design.  All criteria are AND-composed."""
    result = list(design.pages)

    if nets:
        # Resolve each input through find_net so scoped ids and aliases match
        # the same nets they resolve to, not just literal name equality.
        requested_ids = {find_net(design, name).id for name in nets}
        result = [p for p in result if requested_ids & {n.id for n in p.nets}]

    if components:
        _require_components(design, components)
        comp_set = set(components)
        result = [p for p in result if comp_set & {c.reference for c in p.components}]

    return result


def filter_buses(
    design: Schematic,
    *,
    kind: str | None = None,
    net: str | None = None,
    min_members: int | None = None,
) -> list[Bus]:
    """Filter buses from a design. All criteria are AND-composed."""
    result = list(design.buses)

    if kind is not None:
        try:
            requested_kind = BusKind(kind)
        except ValueError as exc:
            choices = ", ".join(bus_kind.value for bus_kind in BusKind)
            raise ValueError(f"Bus kind '{kind}' is invalid; choose one of: {choices}.") from exc
        result = [bus for bus in result if bus.kind == requested_kind]

    if net is not None:
        net_obj = find_net(design, net)
        result = [bus for bus in result if any(member.id == net_obj.id for member in bus.members)]

    if min_members is not None:
        if min_members < 0:
            msg = "min_members must be >= 0."
            raise ValueError(msg)
        result = [bus for bus in result if len(bus.members) >= min_members]

    return result


def find_net(design: Schematic, name: str) -> Net:
    """Find a net by scoped id, name, or alias.  Raises ValueError if not found.

    Scoped nets are matched by ``net.id`` first (a unique scoped id), then by
    ``net.name``, then by ``net.aliases``.
    """
    id_matches = [net for net in design.nets if net.id == name]
    if len(id_matches) == 1:
        return id_matches[0]

    matches = [net for net in design.nets if net.name == name]
    if not matches:
        matches = [net for net in design.nets if name in net.aliases]
    if not matches:
        raise ValueError(f"Net '{name}' not found in design.")
    if len(matches) > 1:
        page_parts = [
            f"{net.id} ({net.name} on {', '.join(net_page_names(net))})" for net in matches
        ]
        raise ValueError(f"Net '{name}' is ambiguous; matches: {', '.join(page_parts)}.")
    return matches[0]


def find_bus(design: Schematic, name: str) -> Bus:
    """Find a bus by id or name. Raises ValueError if not found or ambiguous."""
    id_matches = [bus for bus in design.buses if bus.id == name]
    if len(id_matches) == 1:
        return id_matches[0]

    matches = [bus for bus in design.buses if bus.name == name]
    if not matches:
        raise ValueError(f"Bus '{name}' not found in design.")
    if len(matches) > 1:
        choices = ", ".join(
            f"{bus.id} ({bus.name}, {bus.kind.value}, {len(bus.members)} members)"
            for bus in matches
        )
        raise ValueError(f"Bus '{name}' is ambiguous; use a bus id: {choices}.")
    return matches[0]


def component_physical_designator(comp: Component) -> str:
    """The per-instance physical designator for a component, or ``""``.

    Repeated/multi-channel instances carry a distinct physical designator (e.g.
    ``U1.3``) on their occurrences. Returns the first non-empty one. Empty for
    single-instance and un-annotated components.
    """
    for occurrence in comp.occurrences:
        if occurrence.physical_designator:
            return occurrence.physical_designator
    return ""


def find_component(design: Schematic, ref: str) -> Component:
    """Find a component by logical reference or physical designator.

    ``ref`` may be a logical reference (``U1``) or an exact per-instance
    physical designator (``U1.3``). A physical designator resolves to that
    specific occurrence's component; an ambiguous logical reference raises,
    naming the physical designators so the caller can disambiguate.
    """
    matches = [comp for comp in design.components if comp.reference == ref]
    if not matches:
        matches = [comp for comp in design.components if component_physical_designator(comp) == ref]
        if not matches:
            raise ValueError(f"Component '{ref}' not found in design.")
    if len(matches) > 1:
        locations: list[str] = []
        for comp in matches:
            page_names = sorted({page.name for page in comp.pages})
            location = ", ".join(page_names) if page_names else "unknown page"
            designator = component_physical_designator(comp)
            label = f"{comp.reference} [{designator}]" if designator else comp.reference
            locations.append(f"{label} on {location}")
        raise ValueError(f"Component '{ref}' is ambiguous; matches: {', '.join(locations)}.")
    return matches[0]


def _require_components(design: Schematic, refs: list[str]) -> None:
    """Validate that every requested component reference exists.

    Mirrors ``find_net`` so an unknown ``-c`` filter raises instead of silently
    producing an empty result.
    """
    known = {c.reference for c in design.components}
    for ref in refs:
        if ref not in known:
            raise ValueError(f"Component '{ref}' not found in design.")
