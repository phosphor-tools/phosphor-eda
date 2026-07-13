"""Filtering over the schematic domain model.

The ``filter_*`` functions select nets, components, and pages by AND-composed
criteria. Single-object resolution (``find_net``/``find_component``/
``find_bus``) lives in ``query.lookup``, the lower layer both this module and
``query.trace`` build on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.buses import bus_memberships
from phosphor_eda.domain.schematic import BusKind
from phosphor_eda.query.classify import PASSIVE_PREFIXES, is_power_net, ref_prefix
from phosphor_eda.query.trace import trace_from_net

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import Bus, Component, Net, Page, Schematic


def _net_page_ids(net: Net) -> set[str]:
    """Page ids a net spans, falling back to its pins' component pages."""
    if net.pages:
        return {page.id for page in net.pages}
    return {page.id for pin in net.pins for page in pin.component.pages}


def filter_nets(
    design: Schematic,
    *,
    net_ids: set[str] | None = None,
    component_ids: set[str] | None = None,
    page_ids: set[str] | None = None,
    power: bool | None = None,
    min_pins: int | None = None,
    multi_page: bool = False,
    trace: bool = False,
    bus_ids: set[str] | None = None,
) -> list[Net]:
    """Filter nets from a design.  All criteria are AND-composed."""
    result = list(design.nets)

    if net_ids is not None:
        result = [n for n in result if n.id in net_ids]

    if power is True:
        result = [n for n in result if is_power_net(n.name, n)]
    elif power is False:
        result = [n for n in result if not is_power_net(n.name, n)]

    if page_ids is not None:
        result = [n for n in result if _net_page_ids(n) & page_ids]

    if min_pins is not None:
        result = [n for n in result if len(n.pins) >= min_pins]

    if multi_page:
        result = [n for n in result if len(_net_page_ids(n)) > 1]

    if bus_ids is not None:
        result = [
            n
            for n in result
            if any(member_bus.id in bus_ids for member_bus in bus_memberships(design, n))
        ]

    if component_ids is not None:
        if trace:
            # Expand each net's component reach through 2-pin passives
            def _reaches(net: Net) -> set[str]:
                refs = {pin.component.id for pin in net.pins}
                for tr in trace_from_net(net):
                    if tr.terminal_pin is not None:
                        refs.add(tr.terminal_pin.component.id)
                return refs

            result = [n for n in result if _reaches(n) & component_ids]
        else:
            result = [n for n in result if {pin.component.id for pin in n.pins} & component_ids]

    return result


def filter_components(
    design: Schematic,
    *,
    component_ids: set[str] | None = None,
    page_ids: set[str] | None = None,
    passive: bool | None = None,
    min_pins: int | None = None,
    net_ids: set[str] | None = None,
) -> list[Component]:
    """Filter components from a design.  All criteria are AND-composed."""
    result = list(design.components)

    if component_ids is not None:
        result = [c for c in result if c.id in component_ids]

    if page_ids is not None:
        result = [c for c in result if page_ids & {p.id for p in c.pages}]

    if passive is True:
        result = [c for c in result if ref_prefix(c.reference) in PASSIVE_PREFIXES]
    elif passive is False:
        result = [c for c in result if ref_prefix(c.reference) not in PASSIVE_PREFIXES]

    if min_pins is not None:
        result = [c for c in result if len(c.pins) >= min_pins]

    if net_ids is not None:
        result = [
            c
            for c in result
            if any(pin.net is not None and pin.net.id in net_ids for pin in c.pins)
        ]

    return result


def filter_pages(
    design: Schematic,
    *,
    page_ids: set[str] | None = None,
    net_ids: set[str] | None = None,
    component_ids: set[str] | None = None,
) -> list[Page]:
    """Filter pages from a design.  All criteria are AND-composed."""
    result = list(design.pages)

    if page_ids is not None:
        result = [p for p in result if p.id in page_ids]

    if net_ids is not None:
        result = [p for p in result if net_ids & {n.id for n in p.nets}]

    if component_ids is not None:
        result = [p for p in result if component_ids & {c.id for c in p.components}]

    return result


def filter_buses(
    design: Schematic,
    *,
    bus_ids: set[str] | None = None,
    kind: str | None = None,
    net_ids: set[str] | None = None,
    min_members: int | None = None,
) -> list[Bus]:
    """Filter buses from a design. All criteria are AND-composed."""
    result = list(design.buses)

    if bus_ids is not None:
        result = [bus for bus in result if bus.id in bus_ids]

    if kind is not None:
        try:
            requested_kind = BusKind(kind)
        except ValueError as exc:
            choices = ", ".join(bus_kind.value for bus_kind in BusKind)
            raise ValueError(f"Bus kind '{kind}' is invalid; choose one of: {choices}.") from exc
        result = [bus for bus in result if bus.kind == requested_kind]

    if net_ids is not None:
        result = [bus for bus in result if any(member.id in net_ids for member in bus.members)]

    if min_members is not None:
        if min_members < 0:
            msg = "min_members must be >= 0."
            raise ValueError(msg)
        result = [bus for bus in result if len(bus.members) >= min_members]

    return result
