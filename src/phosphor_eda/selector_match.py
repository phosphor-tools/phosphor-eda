"""Shell-style glob selector parsing and matching over plain strings.

Pure string utilities shared by the query layer (object resolution) and the
render layer (highlight matching). Depends only on the standard library, so
neither layer needs to reach into the other for selector semantics.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class SelectorTerm:
    raw: str
    pattern: str
    negative: bool
    is_glob: bool


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


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")
