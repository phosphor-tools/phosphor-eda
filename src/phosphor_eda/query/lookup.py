"""Single-object lookups over the schematic domain model.

``find_net``/``find_component``/``find_bus`` resolve one object by name with
scoped-net-aware, ambiguity-reporting semantics shared by the CLI, the text
formatters, and signal tracing. This is the lowest query layer: it depends only
on the domain model, so both ``query.query`` and ``query.trace`` import it
downward without a cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import Bus, Component, Net, Schematic


def net_page_names(net: Net) -> list[str]:
    """Sorted page names a net spans (falling back to its pins' pages)."""
    if net.pages:
        return sorted({page.name for page in net.pages})
    return sorted({page.name for pin in net.pins for page in pin.component.pages})


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
