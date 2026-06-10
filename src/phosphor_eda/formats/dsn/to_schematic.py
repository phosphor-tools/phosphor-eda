"""Convert raw DSN parse results into source and schematic domain models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.formats.dsn.resolver import resolve_dsn_source
from phosphor_eda.formats.dsn.source import (
    DsnGlobal,
    DsnHierarchyMapping,
    DsnOffPageConnector,
    DsnPageNet,
    DsnPageSource,
    DsnPinOccurrence,
    DsnPort,
    DsnSourceDesign,
    DsnWire,
    DsnWireAlias,
    dsn_name_key,
)
from phosphor_eda.domain.schematic import Schematic, ScopeId
from phosphor_eda.formats.common.text import strip_overline

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.common.raw_models import GraphicInst
    from phosphor_eda.formats.common.raw_models import ParsedDesign as RawDesign
    from phosphor_eda.formats.common.raw_models import SchematicPage as RawPage


def _page_id(raw_page: RawPage) -> str:
    return f"page:{_page_scope_name(raw_page)}"


def _page_scope_name(raw_page: RawPage) -> str:
    return raw_page.name or "unnamed"


def _local_net_id(page_id: str, net_id: int) -> str:
    return f"{page_id}:net:{net_id}"


def _pin_name(
    pin_number: str,
    symbol_pin_names: list[str],
    ctx: ParseContext | None = None,
    reference: str = "",
) -> str:
    try:
        pn = int(pin_number)
    except (ValueError, TypeError):
        if ctx is not None:
            ctx.warn(
                "dsn_pin_number",
                f"{reference}: non-numeric pin number {pin_number!r}; pin name left blank",
            )
        return ""
    if not 1 <= pn <= len(symbol_pin_names):
        return ""
    pin_name, _overline = strip_overline(symbol_pin_names[pn - 1])
    return pin_name


def _source_net_ids_at(raw_page: RawPage, location: tuple[int, int]) -> list[int]:
    return sorted(raw_page.wire_net_map.get(location, set()))


def _pin_source_net_id(
    raw_page: RawPage,
    page_net_ids: set[int],
    pin_net_id: int,
    location: tuple[int, int],
) -> int:
    if pin_net_id in page_net_ids:
        return pin_net_id
    coord_net_ids = _source_net_ids_at(raw_page, location)
    if coord_net_ids:
        return coord_net_ids[0]
    return pin_net_id


def _source_props(props: dict[str, str]) -> dict[str, str]:
    return dict(props)


def _add_page_net_if_missing(
    *,
    page_nets_by_id: dict[int, DsnPageNet],
    page_source: DsnPageSource,
    page_id: str,
    scope_id: ScopeId,
    net_id: int,
) -> DsnPageNet:
    page_net = page_nets_by_id.get(net_id)
    if page_net is not None:
        return page_net

    page_net = DsnPageNet(
        id=_local_net_id(page_id, net_id),
        scope_id=scope_id,
        net_id=net_id,
        name=f"N{net_id:08d}",
        name_key=dsn_name_key(f"N{net_id:08d}"),
    )
    page_nets_by_id[net_id] = page_net
    page_source.nets.append(page_net)
    return page_net


def _graphic_sources(
    *,
    raw_page: RawPage,
    graphics: list[GraphicInst],
    page_source: DsnPageSource,
    page_nets_by_id: dict[int, DsnPageNet],
    page_id: str,
    scope_id: ScopeId,
    kind: str,
) -> None:
    for index, graphic in enumerate(graphics):
        location = (graphic.loc_x, graphic.loc_y)
        for ordinal, net_id in enumerate(_source_net_ids_at(raw_page, location)):
            local_net_id = _local_net_id(page_id, net_id)
            object_id = f"{page_id}:{kind}:{index}"
            if ordinal > 0:
                object_id = f"{object_id}:{ordinal}"
            page_net = _add_page_net_if_missing(
                page_nets_by_id=page_nets_by_id,
                page_source=page_source,
                page_id=page_id,
                scope_id=scope_id,
                net_id=net_id,
            )
            if kind == "port":
                port = DsnPort(
                    id=object_id,
                    scope_id=scope_id,
                    local_net_id=local_net_id,
                    source_net_id=net_id,
                    name=graphic.name,
                    name_key=dsn_name_key(graphic.name),
                    location=location,
                    props=_source_props(graphic.props),
                )
                page_source.ports.append(port)
                page_net.port_ids.append(port.id)
            elif kind == "global":
                global_ = DsnGlobal(
                    id=object_id,
                    scope_id=scope_id,
                    local_net_id=local_net_id,
                    source_net_id=net_id,
                    name=graphic.name,
                    name_key=dsn_name_key(graphic.name),
                    location=location,
                    props=_source_props(graphic.props),
                )
                page_source.globals.append(global_)
                page_net.global_ids.append(global_.id)
            elif kind == "off_page_connector":
                connector = DsnOffPageConnector(
                    id=object_id,
                    scope_id=scope_id,
                    local_net_id=local_net_id,
                    source_net_id=net_id,
                    name=graphic.name,
                    name_key=dsn_name_key(graphic.name),
                    location=location,
                    props=_source_props(graphic.props),
                )
                page_source.off_page_connectors.append(connector)
                page_net.off_page_connector_ids.append(connector.id)


def _source_page(
    raw_page: RawPage, raw: RawDesign, ctx: ParseContext | None = None
) -> DsnPageSource:
    page_id = _page_id(raw_page)
    scope_id = ScopeId(path=(_page_scope_name(raw_page),))
    page_source = DsnPageSource(
        id=page_id,
        name=raw_page.name,
        scope_id=scope_id,
        nets=[],
        wires=[],
        pin_occurrences=[],
        ports=[],
        globals=[],
        off_page_connectors=[],
    )
    page_nets_by_id: dict[int, DsnPageNet] = {}
    for raw_net in raw_page.nets:
        page_net = DsnPageNet(
            id=_local_net_id(page_id, raw_net.net_id),
            scope_id=scope_id,
            net_id=raw_net.net_id,
            name=raw_net.name,
            name_key=dsn_name_key(raw_net.name),
        )
        page_source.nets.append(page_net)
        page_nets_by_id[raw_net.net_id] = page_net
    source_page_net_ids = set(page_nets_by_id)

    for index, raw_wire in enumerate(raw_page.wires):
        page_net = _add_page_net_if_missing(
            page_nets_by_id=page_nets_by_id,
            page_source=page_source,
            page_id=page_id,
            scope_id=scope_id,
            net_id=raw_wire.wire_id,
        )
        aliases = [
            DsnWireAlias(
                id=f"{page_id}:wire:{index}:alias:{alias_index}",
                scope_id=scope_id,
                name=alias.name,
                name_key=dsn_name_key(alias.name),
                location=(alias.x, alias.y),
                color=alias.color,
                rotation=alias.rotation,
                font_idx=alias.font_idx,
            )
            for alias_index, alias in enumerate(raw_wire.aliases)
        ]
        wire = DsnWire(
            id=f"{page_id}:wire:{index}",
            scope_id=scope_id,
            local_net_id=page_net.id,
            source_net_id=raw_wire.wire_id,
            start=(raw_wire.start_x, raw_wire.start_y),
            end=(raw_wire.end_x, raw_wire.end_y),
            points=list(raw_wire.points),
            aliases=aliases,
            is_bus=raw_wire.is_bus,
            color=raw_wire.color,
        )
        page_source.wires.append(wire)
        page_net.wire_ids.append(wire.id)

    for instance_index, raw_inst in enumerate(raw_page.instances):
        pkg = raw_inst.package_name.replace(".Normal", "")
        sym_pins = raw.symbol_pin_names.get(pkg, [])
        component_source_id = f"{page_id}:component:{raw_inst.db_id or instance_index}"
        for pin_index, raw_pin in enumerate(raw_inst.pin_connections):
            location = (raw_pin.pin_x, raw_pin.pin_y)
            source_net_id = _pin_source_net_id(
                raw_page,
                source_page_net_ids,
                raw_pin.net_id,
                location,
            )
            page_net = _add_page_net_if_missing(
                page_nets_by_id=page_nets_by_id,
                page_source=page_source,
                page_id=page_id,
                scope_id=scope_id,
                net_id=source_net_id,
            )
            pin = DsnPinOccurrence(
                id=f"{component_source_id}:pin:{pin_index}",
                scope_id=scope_id,
                local_net_id=page_net.id,
                source_net_id=source_net_id,
                component_source_id=component_source_id,
                component_reference=raw_inst.reference,
                component_part=pkg,
                pin_designator=raw_pin.pin_number,
                pin_name=_pin_name(raw_pin.pin_number, sym_pins, ctx, raw_inst.reference),
                location=location,
            )
            page_source.pin_occurrences.append(pin)
            page_net.pin_ids.append(pin.id)

    _graphic_sources(
        raw_page=raw_page,
        graphics=raw_page.ports,
        page_source=page_source,
        page_nets_by_id=page_nets_by_id,
        page_id=page_id,
        scope_id=scope_id,
        kind="port",
    )
    _graphic_sources(
        raw_page=raw_page,
        graphics=raw_page.globals,
        page_source=page_source,
        page_nets_by_id=page_nets_by_id,
        page_id=page_id,
        scope_id=scope_id,
        kind="global",
    )
    _graphic_sources(
        raw_page=raw_page,
        graphics=raw_page.off_page_connectors,
        page_source=page_source,
        page_nets_by_id=page_nets_by_id,
        page_id=page_id,
        scope_id=scope_id,
        kind="off_page_connector",
    )
    return page_source


def dsn_to_source(
    raw: RawDesign, name: str = "", ctx: ParseContext | None = None
) -> DsnSourceDesign:
    """Extract OrCAD DSN-native source connectivity from already parsed records."""
    return DsnSourceDesign(
        name=name,
        pages=[_source_page(raw_page, raw, ctx) for raw_page in raw.pages],
        hierarchy_mappings=[
            DsnHierarchyMapping(
                id=f"hierarchy:net:{index}",
                db_id=mapping.db_id,
                name=mapping.name,
                name_key=dsn_name_key(mapping.name),
            )
            for index, mapping in enumerate(raw.net_id_mappings)
        ],
    )


def dsn_to_design(raw: RawDesign, name: str = "", ctx: ParseContext | None = None) -> Schematic:
    """Convert a raw DSN ParsedDesign to a Schematic.

    Non-fatal pin-resolution issues are recorded on *ctx* when provided and
    surfaced as ``parse_issue_count`` in the design metadata.
    """
    return resolve_dsn_source(dsn_to_source(raw, name=name, ctx=ctx), ctx=ctx)
