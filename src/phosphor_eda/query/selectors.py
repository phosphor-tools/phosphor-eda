"""Shared shell-style selector resolution for schematic query commands."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.query.format import find_page_for_detail
from phosphor_eda.query.query import (
    component_physical_designator,
    find_bus,
    find_component,
    find_net,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from phosphor_eda.domain.schematic import Bus, Component, Net, Page, Schematic


@dataclass(frozen=True)
class SelectorTerm:
    raw: str
    pattern: str
    negative: bool
    is_glob: bool


@dataclass(frozen=True)
class Selectable[T]:
    item: T
    key: str
    search_keys: tuple[str, ...]


def parse_selector(raw: str) -> SelectorTerm:
    """Parse a user selector.

    Leading ``!`` excludes matches. Prefix ``\\!`` selects a literal leading
    bang. Glob syntax follows ``fnmatchcase``: ``*``, ``?``, and character
    classes such as ``[0-9]`` or ``[!x]``.
    """
    if raw.startswith(r"\!"):
        pattern = raw[1:]
        negative = False
    elif raw.startswith("!"):
        pattern = raw[1:]
        negative = True
    else:
        pattern = raw
        negative = False
    return SelectorTerm(
        raw=raw,
        pattern=pattern,
        negative=negative,
        is_glob=_has_glob(pattern),
    )


def selector_matches(pattern: str, values: Sequence[str]) -> bool:
    """Return whether *pattern* matches any candidate value."""
    return pattern in values or any(fnmatch.fnmatchcase(value, pattern) for value in values)


def resolve_string_selectors(
    selectors: Sequence[str],
    values: Sequence[str],
    *,
    default_all: bool = False,
) -> tuple[str, ...]:
    """Resolve selectors over plain string values without exact-miss errors."""
    unique_values = tuple(value for value in dict.fromkeys(values) if value)
    if not selectors:
        return unique_values if default_all else ()

    terms = [parse_selector(selector) for selector in selectors]
    include_terms = [term for term in terms if not term.negative]
    exclude_terms = [term for term in terms if term.negative]
    selected: set[str]
    if include_terms:
        selected = {
            value
            for term in include_terms
            for value in unique_values
            if selector_matches(term.pattern, (value,))
        }
    else:
        selected = set(unique_values)

    for term in exclude_terms:
        selected.difference_update(
            value for value in unique_values if selector_matches(term.pattern, (value,))
        )
    return tuple(value for value in unique_values if value in selected)


def resolve_selectors[T](
    selectors: Sequence[str],
    choices: Sequence[Selectable[T]],
    *,
    object_name: str,
    exact_resolver: Callable[[str], T] | None = None,
    item_key: Callable[[T], str],
    default_all: bool = False,
) -> list[T]:
    """Resolve include/exclude selector terms to concrete objects.

    Exact-looking positive selectors use *exact_resolver* so existing scoped-id
    and ambiguity behavior is preserved. Glob-looking selectors may match zero
    objects. Negative-only selector lists start from all objects.
    """
    del object_name
    if not selectors:
        return [choice.item for choice in choices] if default_all else []

    terms = [parse_selector(selector) for selector in selectors]
    include_terms = [term for term in terms if not term.negative]
    exclude_terms = [term for term in terms if term.negative]
    selected: dict[str, T] = {}

    if include_terms:
        for term in include_terms:
            for item in _resolve_positive(term, choices, exact_resolver, item_key):
                selected[item_key(item)] = item
    else:
        selected = {item_key(choice.item): choice.item for choice in choices}

    for term in exclude_terms:
        for choice in choices:
            if selector_matches(term.pattern, choice.search_keys):
                selected.pop(item_key(choice.item), None)

    return [choice.item for choice in choices if item_key(choice.item) in selected]


def component_selectables(design: Schematic) -> list[Selectable[Component]]:
    return [
        Selectable(
            item=component,
            key=component.id,
            search_keys=_unique(
                component.reference,
                component.id,
                component_physical_designator(component),
                *(
                    occurrence.physical_designator
                    for occurrence in component.occurrences
                    if occurrence.physical_designator
                ),
            ),
        )
        for component in design.components
    ]


def net_selectables(design: Schematic) -> list[Selectable[Net]]:
    return [
        Selectable(
            item=net,
            key=net.id,
            search_keys=_unique(net.id, net.name, *sorted(net.aliases)),
        )
        for net in design.nets
    ]


def page_selectables(design: Schematic) -> list[Selectable[Page]]:
    return [
        Selectable(item=page, key=page.id, search_keys=_unique(page.id, page.name))
        for page in design.pages
    ]


def bus_selectables(design: Schematic) -> list[Selectable[Bus]]:
    return [
        Selectable(item=bus, key=bus.id, search_keys=_unique(bus.id, bus.name))
        for bus in design.buses
    ]


def resolve_components(
    design: Schematic, selectors: Sequence[str], *, default_all: bool = False
) -> list[Component]:
    return resolve_selectors(
        selectors,
        component_selectables(design),
        object_name="component",
        exact_resolver=lambda ref: find_component(design, ref),
        item_key=lambda component: component.id,
        default_all=default_all,
    )


def resolve_nets(
    design: Schematic, selectors: Sequence[str], *, default_all: bool = False
) -> list[Net]:
    return resolve_selectors(
        selectors,
        net_selectables(design),
        object_name="net",
        exact_resolver=lambda name: find_net(design, name),
        item_key=lambda net: net.id,
        default_all=default_all,
    )


def resolve_pages(
    design: Schematic, selectors: Sequence[str], *, default_all: bool = False
) -> list[Page]:
    return resolve_selectors(
        selectors,
        page_selectables(design),
        object_name="page",
        exact_resolver=lambda name: find_page_for_detail(design, name),
        item_key=lambda page: page.id,
        default_all=default_all,
    )


def resolve_buses(
    design: Schematic, selectors: Sequence[str], *, default_all: bool = False
) -> list[Bus]:
    return resolve_selectors(
        selectors,
        bus_selectables(design),
        object_name="bus",
        exact_resolver=lambda name: find_bus(design, name),
        item_key=lambda bus: bus.id,
        default_all=default_all,
    )


def _resolve_positive[T](
    term: SelectorTerm,
    choices: Sequence[Selectable[T]],
    exact_resolver: Callable[[str], T] | None,
    item_key: Callable[[T], str],
) -> list[T]:
    if exact_resolver is not None:
        try:
            return [exact_resolver(term.pattern)]
        except ValueError:
            if not term.is_glob:
                raise
    return [choice.item for choice in choices if selector_matches(term.pattern, choice.search_keys)]


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _unique(*values: str) -> tuple[str, ...]:
    return tuple(value for value in dict.fromkeys(values) if value)
