"""Derive the OrCAD schematic sheet tree (scope instances) from raw records.

OrCAD Capture stores each schematic *view* as a flat set of pages plus a
Hierarchy stream describing how block instances (``0x0c`` DrawnInst records)
instantiate child views. A design that places the same child view under more
than one block (RFSoC ``DAC_ADC_CHANNEL`` x8) shares net and component names
across every occurrence, so name-keyed identity would falsely collapse them
(finding H2). This module turns that raw structure into a public *sheet tree*:
one :class:`ScopePlan` per page occurrence carrying its ``ScopeId`` path
(KiCad-style), whether it is a repeated instantiation, and the per-occurrence
refdes map so the resolver can build occurrence-scoped identity.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import ScopeId
from phosphor_eda.formats.common.diagnostics import warn_optional
from phosphor_eda.formats.dsn.source import dsn_page_id

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.dsn.raw_models import (
        DsnBlockInstance,
        DsnHierarchy,
        ParsedDesign,
        SchematicPage,
    )


@dataclass(slots=True)
class ScopePlan:
    """One page occurrence in the sheet tree.

    ``page_id`` is scope-qualified only for *repeated* instantiations;
    singly-instantiated and flat pages keep the unscoped
    ``page:{name}`` id so their public identity stays stable. ``scope_id`` is
    always the full hierarchical path. ``merge_domain`` partitions cross-page
    net-name merging: root and singly-instantiated pages share ``"root"``;
    each repeated occurrence gets its own domain so identical child net names
    never merge across occurrences.
    """

    scope_id: ScopeId
    page_id: str
    raw_page: SchematicPage
    parent_scope_id: ScopeId | None
    merge_domain: str
    repeated: bool
    refdes_by_db_id: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class BlockBindingPlan:
    """A block sheet pin bound to a parent-page net.

    The T0x10 binding stores the parent *wire db id*; ``parent_net_id`` is that
    wire's runtime net id (the parent page's net id), resolved via the parent
    page's wire db-id map. ``0`` means the binding did not resolve to a wire.
    """

    sheet_pin_name: str
    port_type_name: str
    parent_net_id: int


@dataclass(slots=True)
class BlockLinkPlan:
    """A block occurrence linking a parent page scope to child page scopes."""

    parent_scope_id: ScopeId
    parent_page_id: str
    block_reference: str
    child_schematic: str
    child_scope_ids: tuple[ScopeId, ...]
    bindings: tuple[BlockBindingPlan, ...]


@dataclass(slots=True)
class SheetTree:
    scopes: list[ScopePlan]
    block_links: list[BlockLinkPlan]


@dataclass(slots=True)
class _BlockEntryInfo:
    child_schematic: str
    refdes_by_db_id: dict[int, str]


def _block_entries_by_db_id(hierarchy: DsnHierarchy) -> dict[int, _BlockEntryInfo]:
    """Map each block placement's instance db id to its child edge + refdes map.

    Block occurrences are the entries carrying a ``child_schematic`` edge; each
    owns the contiguous occurrence-id range up to the next block occurrence
    (the parent-by-occurrence-id-range invariant locked in Wave 4a). Member
    entries in that range contribute their per-occurrence refdes keyed by
    instance db id.
    """
    block_occ_ids = sorted(
        entry.occurrence_id for entry in hierarchy.entries if entry.child_schematic
    )
    result: dict[int, _BlockEntryInfo] = {}
    for entry in hierarchy.entries:
        if not entry.child_schematic:
            continue
        start = entry.occurrence_id
        end = _next_occurrence_boundary(block_occ_ids, start)
        refdes_by_db_id: dict[int, str] = {}
        for member in hierarchy.entries:
            if member.child_schematic or not member.refdes:
                continue
            if start <= member.occurrence_id < end:
                refdes_by_db_id.setdefault(member.instance_db_id, member.refdes)
        result[entry.instance_db_id] = _BlockEntryInfo(
            child_schematic=entry.child_schematic,
            refdes_by_db_id=refdes_by_db_id,
        )
    return result


def _next_occurrence_boundary(block_occ_ids: list[int], start: int) -> float:
    for occ_id in block_occ_ids:
        if occ_id > start:
            return occ_id
    return float("inf")


def build_sheet_tree(raw: ParsedDesign, ctx: ParseContext | None = None) -> SheetTree:
    """Build the public sheet tree from raw views, hierarchy, and block instances."""
    pages_by_view: dict[str, list[SchematicPage]] = {}
    for page in raw.pages:
        pages_by_view.setdefault(page.view_name, []).append(page)

    block_entries: dict[int, _BlockEntryInfo] = {}
    for hierarchy in raw.hierarchies:
        for db_id, info in _block_entries_by_db_id(hierarchy).items():
            block_entries.setdefault(db_id, info)

    # A child view's instantiation count decides whether its pages are scoped
    # (repeated) or keep stable unscoped ids (singly-instantiated).
    view_multiplicity: Counter[str] = Counter(
        info.child_schematic for info in block_entries.values()
    )
    child_view_names = set(view_multiplicity)
    root_pages = [page for page in raw.pages if page.view_name not in child_view_names]

    scopes: list[ScopePlan] = []
    block_links: list[BlockLinkPlan] = []
    seen_scopes: set[tuple[str, ...]] = set()

    def process_page(
        raw_page: SchematicPage,
        scope_id: ScopeId,
        parent_scope_id: ScopeId | None,
        refdes_by_db_id: dict[int, str],
        ancestor_views: frozenset[str],
    ) -> None:
        if scope_id.path in seen_scopes:
            reason = (
                "block reference is not unique on its page"
                if parent_scope_id is not None
                else "duplicate top-level page name"
            )
            warn_optional(ctx, "dsn_scope_collision", f"duplicate sheet scope {scope_id}; {reason}")
            return
        seen_scopes.add(scope_id.path)

        repeated = view_multiplicity.get(raw_page.view_name, 0) > 1
        if repeated:
            page_id = dsn_page_id("/".join(scope_id.path))
            merge_domain = "/".join(scope_id.path[:-1]) or "root"
        else:
            page_id = dsn_page_id(raw_page.name or "unnamed")
            merge_domain = "root"
        scopes.append(
            ScopePlan(
                scope_id=scope_id,
                page_id=page_id,
                raw_page=raw_page,
                parent_scope_id=parent_scope_id,
                merge_domain=merge_domain,
                repeated=repeated,
                refdes_by_db_id=refdes_by_db_id,
            )
        )

        for block in raw_page.block_instances:
            info = block_entries.get(block.db_id)
            if info is None:
                warn_optional(
                    ctx,
                    "dsn_block_unresolved",
                    f"block {block.reference!r} (db {block.db_id}) on {raw_page.name!r} "
                    "has no Hierarchy child-schematic edge; child sheet not instantiated",
                )
                continue
            child_view = info.child_schematic
            if child_view in ancestor_views:
                warn_optional(
                    ctx,
                    "dsn_block_cycle",
                    f"block {block.reference!r} instantiates ancestor view {child_view!r}; "
                    "skipping to avoid a cycle",
                )
                continue
            child_pages = pages_by_view.get(child_view, [])
            if not child_pages:
                warn_optional(
                    ctx,
                    "dsn_block_no_child_pages",
                    f"block {block.reference!r} references view {child_view!r} with no pages",
                )
                continue
            occ_prefix = (*scope_id.path, block.reference)
            child_scope_ids: list[ScopeId] = []
            for child_page in child_pages:
                child_scope = ScopeId(path=(*occ_prefix, child_page.name or "unnamed"))
                child_scope_ids.append(child_scope)
                process_page(
                    child_page,
                    child_scope,
                    scope_id,
                    info.refdes_by_db_id,
                    ancestor_views | {raw_page.view_name},
                )
            wire_net_by_db_id = {wire.db_id: wire.wire_id for wire in raw_page.wires if wire.db_id}
            block_links.append(
                BlockLinkPlan(
                    parent_scope_id=scope_id,
                    parent_page_id=page_id,
                    block_reference=block.reference,
                    child_schematic=child_view,
                    child_scope_ids=tuple(child_scope_ids),
                    bindings=_block_bindings(block, wire_net_by_db_id),
                )
            )

    for root_page in root_pages:
        root_scope = ScopeId(path=(root_page.name or "unnamed",))
        process_page(root_page, root_scope, None, {}, frozenset())

    return SheetTree(scopes=scopes, block_links=block_links)


def _block_bindings(
    block: DsnBlockInstance,
    wire_net_by_db_id: dict[int, int],
) -> tuple[BlockBindingPlan, ...]:
    """Pair a block's sheet pins with their parent nets, resolved by wire db id.

    The T0x10 binding stores the parent wire's db id keyed by pin order; look up
    the wire's runtime net id to get the parent page net the sheet pin connects
    to (RFSoC CH3 ``DAC_P`` db 8004 -> net ``DAC_03_P``).
    """
    db_id_by_order = {binding.pin_order: binding.net_id for binding in block.net_bindings}
    bindings: list[BlockBindingPlan] = []
    for order, sheet_pin in enumerate(block.sheet_pins, start=1):
        wire_db_id = db_id_by_order.get(order, 0)
        bindings.append(
            BlockBindingPlan(
                sheet_pin_name=sheet_pin.name,
                port_type_name=sheet_pin.port_type_name,
                parent_net_id=wire_net_by_db_id.get(wire_db_id, 0),
            )
        )
    return tuple(bindings)
