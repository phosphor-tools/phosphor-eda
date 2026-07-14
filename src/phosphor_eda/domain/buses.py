"""Helpers for bus expansion and resolved bus membership."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import Bus, BusKind, Net, Schematic

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

_VECTOR_RE = re.compile(r"^(?P<prefix>.*)\[(?P<start>-?\d+)\.\.(?P<end>-?\d+)\]$")
_GROUP_RE = re.compile(r"^(?P<prefix>[^{]*)\{(?P<body>.*)\}$")

# Upper bound on members from a single vector range (repo rule: every string
# bounded). Untrusted ``A[0..n]`` notation would otherwise allocate one string
# per index; a bus far wider than this is malformed rather than real.
MAX_VECTOR_BUS_MEMBERS = 8192


@dataclass(frozen=True, slots=True)
class BusDefinition:
    """Source-level bus evidence before member names are resolved to nets."""

    id: str
    name: str
    kind: BusKind
    member_names: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Copy the caller's dict so a later mutation of it cannot reach into
        # this frozen definition (no shared mutable state between instances).
        object.__setattr__(self, "metadata", dict(self.metadata))


def expand_vector_bus(name: str) -> list[str] | None:
    """Expand ``NAME[m..n]`` notation, including comma-separated mixed terms."""
    text = _clean(name)
    if not text:
        return None
    parts = _split_top_level(text, ",")
    expanded: list[str] = []
    saw_range = False
    for part in parts:
        range_members = _expand_vector_part(part)
        if range_members is None:
            expanded.append(part)
            continue
        saw_range = True
        expanded.extend(range_members)
    return expanded if saw_range else None


def expand_group_bus(
    name: str,
    *,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> list[str] | None:
    """Expand KiCad-style ``GROUP{A B}`` notation to member net names."""
    return _expand_group_bus(name, aliases or {}, alias_stack=frozenset())


def _expand_group_bus(
    name: str,
    aliases: Mapping[str, Sequence[str]],
    *,
    alias_stack: frozenset[str],
) -> list[str] | None:
    text = _clean(name)
    match = _GROUP_RE.fullmatch(text)
    if match is None:
        return None
    prefix = _clean(match.group("prefix"))
    body = match.group("body")
    members: list[str] = []
    for token in _split_group_body(body):
        for member in _expand_group_token(token, aliases, alias_stack=alias_stack):
            members.append(f"{prefix}.{member}" if prefix else member)
    return members


def expand_bus_members(
    name: str,
    *,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> list[str] | None:
    """Expand either vector or group bus notation."""
    return _expand_bus_members(name, aliases or {}, alias_stack=frozenset())


def _expand_bus_members(
    name: str,
    aliases: Mapping[str, Sequence[str]],
    *,
    alias_stack: frozenset[str],
) -> list[str] | None:
    group_members = _expand_group_bus(name, aliases, alias_stack=alias_stack)
    if group_members is not None:
        return group_members
    return expand_vector_bus(name)


def bus_kind_for_name(
    name: str,
    *,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> BusKind | None:
    if expand_group_bus(name, aliases=aliases) is not None:
        return BusKind.GROUP
    if expand_vector_bus(name) is not None:
        return BusKind.VECTOR
    return None


def member_nets_for_names(design: Schematic, member_names: Iterable[str]) -> list[Net]:
    """Resolve member names against public net names and aliases."""
    nets_by_name: dict[str, list[Net]] = {}
    for net in design.nets:
        nets_by_name.setdefault(net.name, []).append(net)
        for alias in net.aliases:
            nets_by_name.setdefault(alias, []).append(net)

    result: list[Net] = []
    seen_net_ids: set[str] = set()
    for member_name in member_names:
        for net in nets_by_name.get(member_name, []):
            if net.id in seen_net_ids:
                continue
            seen_net_ids.add(net.id)
            result.append(net)
    return result


def build_buses_from_definitions(
    design: Schematic,
    definitions: Iterable[BusDefinition],
) -> list[Bus]:
    """Resolve source bus definitions to public domain buses."""
    buses: list[Bus] = []
    seen_ids: set[str] = set()
    for definition in definitions:
        member_names = definition.member_names or tuple(expand_bus_members(definition.name) or ())
        members = member_nets_for_names(design, member_names)
        if not members:
            continue
        bus_id = _unique_bus_id(definition.id, seen_ids)
        buses.append(
            Bus(
                id=bus_id,
                name=definition.name,
                kind=definition.kind,
                members=members,
                metadata=dict(definition.metadata),
            )
        )
    return buses


def bus_memberships(design: Schematic, net: Net) -> list[Bus]:
    """Return buses containing *net*, preserving design order."""
    return [bus for bus in design.buses if any(member.id == net.id for member in bus.members)]


def _expand_vector_part(part: str) -> list[str] | None:
    match = _VECTOR_RE.fullmatch(_clean(part))
    if match is None:
        return None
    prefix = match.group("prefix")
    start = int(match.group("start"))
    end = int(match.group("end"))
    count = abs(end - start) + 1
    if count > MAX_VECTOR_BUS_MEMBERS:
        msg = (
            f"bus vector '{part}' expands to {count} members; "
            f"the maximum is {MAX_VECTOR_BUS_MEMBERS}"
        )
        raise ValueError(msg)
    step = 1 if end >= start else -1
    return [f"{prefix}{index}" for index in range(start, end + step, step)]


def _expand_group_token(
    token: str,
    aliases: Mapping[str, Sequence[str]],
    *,
    alias_stack: frozenset[str],
) -> list[str]:
    if token in aliases:
        if token in alias_stack:
            return []
        next_stack = alias_stack | {token}
        members: list[str] = []
        for alias_member in aliases[token]:
            members.extend(
                _expand_group_token(
                    _clean(alias_member),
                    aliases,
                    alias_stack=next_stack,
                )
            )
        return members
    vector_members = expand_vector_bus(token)
    if vector_members is not None:
        return vector_members
    nested_group = _expand_group_bus(token, aliases, alias_stack=alias_stack)
    if nested_group is not None:
        return nested_group
    return [_clean(token)]


def _split_group_body(body: str) -> list[str]:
    members: list[str] = []
    for part in _split_top_level(body, ","):
        members.extend(_split_top_level(part, " "))
    return members


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    for char in text:
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
        is_separator = char == separator or (separator == " " and char.isspace())
        if is_separator and brace_depth == 0 and bracket_depth == 0:
            part = _clean("".join(current))
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    part = _clean("".join(current))
    if part:
        parts.append(part)
    return parts


def _unique_bus_id(candidate: str, seen_ids: set[str]) -> str:
    bus_id = candidate
    suffix = 2
    while bus_id in seen_ids:
        bus_id = f"{candidate}:{suffix}"
        suffix += 1
    seen_ids.add(bus_id)
    return bus_id


def _clean(text: str) -> str:
    return text.replace("\\", "").strip()
