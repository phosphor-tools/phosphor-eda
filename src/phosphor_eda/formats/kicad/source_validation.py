"""Referential-integrity checks for KiCad source designs.

Run before resolution: every label, pin, sheet pin, and bus entry must
reference a known scope and its own containing local net, so the resolver
never builds a schematic graph from dangling source references.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.formats.common.resolved_graph import ResolutionInputError

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import ScopeId
    from phosphor_eda.formats.kicad.source import KiCadLocalNet, KiCadSourceDesign


def validate_source_refs(
    source: KiCadSourceDesign,
    local_nets_by_id: dict[str, KiCadLocalNet],
) -> None:
    scopes = {instance.scope_id for instance in source.sheet_instances}
    attached_sheet_pin_local_net_ids: dict[str, str] = {}
    attached_bus_entry_local_net_ids: dict[str, str] = {}
    for local_net in source.local_nets:
        if local_net.scope_id not in scopes:
            msg = f"local net {local_net.id!r} references unknown scope {local_net.scope_id}"
            raise ResolutionInputError(msg)
        for label in local_net.local_labels:
            _validate_scoped_local_net_ref(
                kind="local label",
                id_=label.id,
                scope_id=label.scope_id,
                local_net_id=label.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for label in local_net.global_labels:
            _validate_scoped_local_net_ref(
                kind="global label",
                id_=label.id,
                scope_id=label.scope_id,
                local_net_id=label.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for label in local_net.hierarchical_labels:
            _validate_scoped_local_net_ref(
                kind="hierarchical label",
                id_=label.id,
                scope_id=label.scope_id,
                local_net_id=label.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for symbol in local_net.power_symbols:
            _validate_scoped_local_net_ref(
                kind="power symbol",
                id_=symbol.id,
                scope_id=symbol.scope_id,
                local_net_id=symbol.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
        for sheet_pin in local_net.sheet_pins:
            _validate_scoped_local_net_ref(
                kind="sheet pin",
                id_=sheet_pin.id,
                scope_id=sheet_pin.scope_id,
                local_net_id=sheet_pin.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
            if sheet_pin.child_scope_id not in scopes:
                msg = (
                    f"sheet pin {sheet_pin.id!r} references unknown child scope "
                    f"{sheet_pin.child_scope_id}"
                )
                raise ResolutionInputError(msg)
            attached_sheet_pin_local_net_ids[sheet_pin.id] = local_net.id
        for bus_entry in local_net.bus_entries:
            _validate_scoped_local_net_ref(
                kind="bus entry",
                id_=bus_entry.id,
                scope_id=bus_entry.scope_id,
                local_net_id=bus_entry.local_net_id,
                containing_local_net_id=local_net.id,
                scopes=scopes,
                local_nets_by_id=local_nets_by_id,
            )
            attached_bus_entry_local_net_ids[bus_entry.id] = local_net.id
    for sheet_pin in source.sheet_pins:
        _validate_top_level_sheet_pin_ref(
            id_=sheet_pin.id,
            scope_id=sheet_pin.scope_id,
            child_scope_id=sheet_pin.child_scope_id,
            local_net_id=sheet_pin.local_net_id,
            scopes=scopes,
            local_nets_by_id=local_nets_by_id,
        )
        attached_local_net_id = attached_sheet_pin_local_net_ids.get(sheet_pin.id)
        if attached_local_net_id is None:
            msg = (
                f"sheet pin {sheet_pin.id!r} is not attached to local net "
                f"{sheet_pin.local_net_id!r}"
            )
            raise ResolutionInputError(msg)
        if attached_local_net_id != sheet_pin.local_net_id:
            msg = (
                f"sheet pin {sheet_pin.id!r} references local net "
                f"{sheet_pin.local_net_id!r} but is attached to local net "
                f"{attached_local_net_id!r}"
            )
            raise ResolutionInputError(msg)
    for bus_sheet_pin in source.bus_sheet_pins:
        # Bus sheet pins attach to a bus group, not a local net, so only the
        # scope references need checking.
        if bus_sheet_pin.scope_id not in scopes:
            msg = (
                f"bus sheet pin {bus_sheet_pin.id!r} references unknown scope "
                f"{bus_sheet_pin.scope_id}"
            )
            raise ResolutionInputError(msg)
        if bus_sheet_pin.child_scope_id not in scopes:
            msg = (
                f"bus sheet pin {bus_sheet_pin.id!r} references unknown child scope "
                f"{bus_sheet_pin.child_scope_id}"
            )
            raise ResolutionInputError(msg)
    for bus_entry in source.bus_entries:
        _validate_scoped_local_net_ref(
            kind="bus entry",
            id_=bus_entry.id,
            scope_id=bus_entry.scope_id,
            local_net_id=bus_entry.local_net_id,
            containing_local_net_id=bus_entry.local_net_id,
            scopes=scopes,
            local_nets_by_id=local_nets_by_id,
        )
        attached_local_net_id = attached_bus_entry_local_net_ids.get(bus_entry.id)
        if attached_local_net_id is None:
            msg = (
                f"bus entry {bus_entry.id!r} is not attached to local net "
                f"{bus_entry.local_net_id!r}"
            )
            raise ResolutionInputError(msg)
        if attached_local_net_id != bus_entry.local_net_id:
            msg = (
                f"bus entry {bus_entry.id!r} references local net "
                f"{bus_entry.local_net_id!r} but is attached to local net "
                f"{attached_local_net_id!r}"
            )
            raise ResolutionInputError(msg)
    for pin in source.pin_occurrences:
        _validate_pin_ref(
            id_=pin.id,
            scope_id=pin.scope_id,
            local_net_id=pin.local_net_id,
            scopes=scopes,
            local_nets_by_id=local_nets_by_id,
        )


def _validate_top_level_sheet_pin_ref(
    *,
    id_: str,
    scope_id: ScopeId,
    child_scope_id: ScopeId,
    local_net_id: str,
    scopes: set[ScopeId],
    local_nets_by_id: dict[str, KiCadLocalNet],
) -> None:
    _validate_scoped_local_net_ref(
        kind="sheet pin",
        id_=id_,
        scope_id=scope_id,
        local_net_id=local_net_id,
        containing_local_net_id=local_net_id,
        scopes=scopes,
        local_nets_by_id=local_nets_by_id,
    )
    if child_scope_id not in scopes:
        msg = f"sheet pin {id_!r} references unknown child scope {child_scope_id}"
        raise ResolutionInputError(msg)


def _validate_pin_ref(
    *,
    id_: str,
    scope_id: ScopeId,
    local_net_id: str,
    scopes: set[ScopeId],
    local_nets_by_id: dict[str, KiCadLocalNet],
) -> None:
    if scope_id not in scopes:
        msg = f"pin {id_!r} references unknown scope {scope_id}"
        raise ResolutionInputError(msg)
    local_net = local_nets_by_id.get(local_net_id)
    if local_net is None:
        msg = f"pin {id_!r} references unknown local net {local_net_id!r}"
        raise ResolutionInputError(msg)
    if local_net.scope_id != scope_id:
        msg = (
            f"pin {id_!r} scope {scope_id} does not match "
            f"local net {local_net_id!r} scope {local_net.scope_id}"
        )
        raise ResolutionInputError(msg)


def _validate_scoped_local_net_ref(
    *,
    kind: str,
    id_: str,
    scope_id: ScopeId,
    local_net_id: str,
    containing_local_net_id: str,
    scopes: set[ScopeId],
    local_nets_by_id: dict[str, KiCadLocalNet],
) -> None:
    if scope_id not in scopes:
        msg = f"{kind} {id_!r} references unknown scope {scope_id}"
        raise ResolutionInputError(msg)
    local_net = local_nets_by_id.get(local_net_id)
    if local_net is None:
        msg = f"{kind} {id_!r} references unknown local net {local_net_id!r}"
        raise ResolutionInputError(msg)
    if local_net_id != containing_local_net_id:
        msg = (
            f"{kind} {id_!r} references local net {local_net_id!r} "
            f"but is attached to local net {containing_local_net_id!r}"
        )
        raise ResolutionInputError(msg)
    if local_net.scope_id != scope_id:
        msg = (
            f"{kind} {id_!r} scope {scope_id} does not match "
            f"local net {local_net_id!r} scope {local_net.scope_id}"
        )
        raise ResolutionInputError(msg)
