"""Convert raw DSN parse results into source and schematic domain models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import Schematic, ScopeId, TitleBlock
from phosphor_eda.formats.common.electrical import set_pin_electrical
from phosphor_eda.formats.dsn.parser import DsnSchematicPage, RawTitleBlock
from phosphor_eda.formats.dsn.pins import (
    ORCAD_PORT_TYPES,
    normalize_package_name,
    resolve_pin_name,
    resolve_symbol_pin,
)
from phosphor_eda.formats.dsn.resolver import resolve_dsn_source
from phosphor_eda.formats.dsn.source import (
    DsnBundleMember,
    DsnBusEntry,
    DsnGlobal,
    DsnHierarchyMapping,
    DsnNetBundle,
    DsnOffPageConnector,
    DsnPageNet,
    DsnPageSource,
    DsnPinOccurrence,
    DsnPort,
    DsnSourceDesign,
    DsnWire,
    DsnWireAlias,
    dsn_component_source_id,
    dsn_name_key,
    dsn_page_id,
)

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.common.raw_models import DsnSymbolPin, GraphicInst
    from phosphor_eda.formats.common.raw_models import ParsedDesign as RawDesign
    from phosphor_eda.formats.common.raw_models import SchematicPage as RawPage


def _page_id(raw_page: RawPage) -> str:
    return dsn_page_id(_page_scope_name(raw_page))


def _page_scope_name(raw_page: RawPage) -> str:
    return raw_page.name or "unnamed"


def _local_net_id(page_id: str, net_id: int) -> str:
    return f"{page_id}:net:{net_id}"


# Net ids 0 and 0xFFFFFFFF mean "no net assignment" in Capture page streams
# (seen on pins whose connection comes from a power symbol at the pin
# coordinate). They must never materialize as nets.
_SENTINEL_NET_IDS = frozenset({0, 0xFFFFFFFF})


def _source_net_ids_at(raw_page: RawPage, location: tuple[int, int]) -> list[int]:
    return sorted(raw_page.wire_net_map.get(location, set()))


def _pin_source_net_id(
    raw_page: RawPage,
    page_net_ids: set[int],
    pin_net_id: int,
    location: tuple[int, int],
) -> int | None:
    """Resolve a pin's source net id; ``None`` means no assignment exists."""
    if pin_net_id not in _SENTINEL_NET_IDS and pin_net_id in page_net_ids:
        return pin_net_id
    coord_net_ids = [
        nid for nid in _source_net_ids_at(raw_page, location) if nid not in _SENTINEL_NET_IDS
    ]
    if coord_net_ids:
        return coord_net_ids[0]
    if pin_net_id in _SENTINEL_NET_IDS:
        return None
    return pin_net_id


def _global_anchor(raw_page: RawPage, location: tuple[int, int]) -> GraphicInst | None:
    """The power symbol whose body covers *location*, if any.

    A pin connected only to a power symbol carries a sentinel net id; the
    symbol's anchor point is its placement location, but the electrical
    touch point can be anywhere inside its bounding box.
    """
    for graphic in raw_page.globals:
        if (graphic.loc_x, graphic.loc_y) == location:
            return graphic
        x1, x2 = sorted((graphic.bbox_x1, graphic.bbox_x2))
        y1, y2 = sorted((graphic.bbox_y1, graphic.bbox_y2))
        if (x1, y1) != (x2, y2) and x1 <= location[0] <= x2 and y1 <= location[1] <= y2:
            return graphic
    return None


def _symbol_pin_metadata(symbol_pin: DsnSymbolPin | None) -> dict[str, str]:
    if symbol_pin is None:
        return {}
    metadata = {
        "dsn_symbol_pin_port_type": symbol_pin.port_type_name,
        "dsn_symbol_pin_shape": str(symbol_pin.pin_shape),
        "dsn_symbol_pin_start": f"{symbol_pin.start_x},{symbol_pin.start_y}",
        "dsn_symbol_pin_hotpt": f"{symbol_pin.hotpt_x},{symbol_pin.hotpt_y}",
        "dsn_symbol_pin_structure": ("bus" if symbol_pin.structure_type == 27 else "scalar"),
    }
    if symbol_pin.display_prop_count:
        metadata["dsn_symbol_pin_display_props"] = str(symbol_pin.display_prop_count)
    port_type = ORCAD_PORT_TYPES.get(symbol_pin.port_type)
    set_pin_electrical(metadata, port_type.electrical if port_type is not None else None)
    return metadata


def _source_props(props: dict[str, str]) -> dict[str, str]:
    return dict(props)


# Title block prefix-pair names mapped onto typed TitleBlock fields; all raw
# non-empty pairs still land in TitleBlock.metadata.
_TITLE_BLOCK_PLACEHOLDERS = frozenset({"", "*", "~"})
_TITLE_BLOCK_FIELD_BY_NAME = {
    "approver": "approved_by",
    "author": "author",
    "cage code": "cage_code",
    "check name": "checked_by",
    "date": "date",
    "designer": "drawn_by",
    "designer name": "drawn_by",
    "doc": "document_number",
    "drawnby": "drawn_by",
    "orgname": "organization",
    "page count": "sheet_total",
    "page number": "sheet_number",
    "revcode": "revision",
    "title": "title",
}
# When several source aliases target the same field, the first parsed
# non-placeholder value wins; every raw alias remains available in metadata.


def _title_value(value: str) -> str:
    text = value.strip()
    return "" if text in _TITLE_BLOCK_PLACEHOLDERS else text


def _page_title_block(raw_page: RawPage) -> TitleBlock | None:
    """The page's title block, from the first parsed title block record."""
    if not isinstance(raw_page, DsnSchematicPage) or not raw_page.title_blocks:
        return None
    return _title_block(raw_page.title_blocks[0])


def _title_block(raw_block: RawTitleBlock) -> TitleBlock:
    block = TitleBlock()
    address_lines: dict[int, str] = {}
    for name, value in raw_block.props.items():
        if value:
            block.metadata[name] = value
        typed_value = _title_value(value)
        if not typed_value:
            continue
        name_key = name.casefold()
        field_name = _TITLE_BLOCK_FIELD_BY_NAME.get(name_key)
        if field_name is not None:
            if not getattr(block, field_name):
                setattr(block, field_name, typed_value)
        elif name_key.startswith("orgaddr") and name_key[7:].isdigit():
            address_lines.setdefault(int(name_key[7:]), typed_value)
    if address_lines:
        block.org_address = "\n".join(value for _number, value in sorted(address_lines.items()))
    if raw_block.name:
        block.metadata["dsn_title_block_symbol"] = raw_block.name
    return block


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

    # Nets without a page net list entry have no stored name; Capture's
    # autoname is recovered later from the cluster's seed-wire dbid.
    page_net = DsnPageNet(
        id=_local_net_id(page_id, net_id),
        scope_id=scope_id,
        net_id=net_id,
        name="",
        name_key="",
    )
    page_nets_by_id[net_id] = page_net
    page_source.nets.append(page_net)
    return page_net


def _synthetic_net_at(
    *,
    synthetic_nets_by_location: dict[tuple[int, int], DsnPageNet],
    page_source: DsnPageSource,
    page_id: str,
    scope_id: ScopeId,
    location: tuple[int, int],
) -> DsnPageNet:
    """A nameless local net anchored at a coordinate.

    Connects a sentinel-net-id pin to the power symbol sitting on the same
    point when no wire passes through it; the symbol's name evidence then
    names (and merges) the resolved net.
    """
    page_net = synthetic_nets_by_location.get(location)
    if page_net is None:
        page_net = DsnPageNet(
            id=f"{page_id}:net:loc:{location[0]}:{location[1]}",
            scope_id=scope_id,
            net_id=-1,
            name="",
            name_key="",
        )
        synthetic_nets_by_location[location] = page_net
        page_source.nets.append(page_net)
    return page_net


def _graphic_net_name(graphic: GraphicInst, ctx: ParseContext | None, kind: str) -> str:
    """The net name a power symbol or off-page connector carries.

    The graphic's own ``name`` is the symbol name (``VCC_ARROW``,
    ``OFFPAGE_4``) — never a net name. The net name rides in the
    ``_net_name`` string index; when it's missing the symbol name is the
    only spelling available and is used with a diagnostic.
    """
    net_name = graphic.props.get("_net_name", "")
    if net_name:
        return net_name
    if ctx is not None:
        ctx.warn(
            f"dsn_{kind}_net_name",
            f"{kind} symbol {graphic.name!r} carries no net name; falling back to the symbol name",
        )
    return graphic.name


def _graphic_sources(
    *,
    raw_page: RawPage,
    graphics: list[GraphicInst],
    page_source: DsnPageSource,
    page_nets_by_id: dict[int, DsnPageNet],
    synthetic_nets_by_location: dict[tuple[int, int], DsnPageNet],
    page_id: str,
    scope_id: ScopeId,
    kind: str,
    ctx: ParseContext | None = None,
) -> None:
    for index, graphic in enumerate(graphics):
        location = (graphic.loc_x, graphic.loc_y)
        wire_net_ids = [
            net_id
            for net_id in _source_net_ids_at(raw_page, location)
            if net_id not in _SENTINEL_NET_IDS
        ]
        net_targets: list[tuple[str, DsnPageNet, int]] = []
        for net_id in wire_net_ids:
            net_targets.append(
                (
                    _local_net_id(page_id, net_id),
                    _add_page_net_if_missing(
                        page_nets_by_id=page_nets_by_id,
                        page_source=page_source,
                        page_id=page_id,
                        scope_id=scope_id,
                        net_id=net_id,
                    ),
                    net_id,
                )
            )
        if not net_targets and location in synthetic_nets_by_location:
            # A sentinel-net-id pin at this coordinate already created an
            # anchor net; attach the symbol so its name applies.
            synthetic_net = synthetic_nets_by_location[location]
            net_targets.append((synthetic_net.id, synthetic_net, synthetic_net.net_id))
        for ordinal, (local_net_id, page_net, net_id) in enumerate(net_targets):
            object_id = f"{page_id}:{kind}:{index}"
            if ordinal > 0:
                object_id = f"{object_id}:{ordinal}"
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
                net_name = _graphic_net_name(graphic, ctx, kind)
                global_ = DsnGlobal(
                    id=object_id,
                    scope_id=scope_id,
                    local_net_id=local_net_id,
                    source_net_id=net_id,
                    name=net_name,
                    name_key=dsn_name_key(net_name),
                    location=location,
                    symbol=graphic.name,
                    props=_source_props(graphic.props),
                )
                page_source.globals.append(global_)
                page_net.global_ids.append(global_.id)
            elif kind == "off_page_connector":
                net_name = _graphic_net_name(graphic, ctx, kind)
                connector = DsnOffPageConnector(
                    id=object_id,
                    scope_id=scope_id,
                    local_net_id=local_net_id,
                    source_net_id=net_id,
                    name=net_name,
                    name_key=dsn_name_key(net_name),
                    location=location,
                    symbol=graphic.name,
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
        bus_entries=[],
        title_block=_page_title_block(raw_page),
    )
    page_nets_by_id: dict[int, DsnPageNet] = {}
    synthetic_nets_by_location: dict[tuple[int, int], DsnPageNet] = {}
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
            db_id=raw_wire.db_id,
        )
        page_source.wires.append(wire)
        page_net.wire_ids.append(wire.id)

    for index, raw_entry in enumerate(raw_page.bus_entries):
        page_source.bus_entries.append(
            DsnBusEntry(
                id=f"{page_id}:bus_entry:{index}",
                scope_id=scope_id,
                start=(raw_entry.start_x, raw_entry.start_y),
                end=(raw_entry.end_x, raw_entry.end_y),
                color=raw_entry.color,
            )
        )

    for instance_index, raw_inst in enumerate(raw_page.instances):
        pkg = normalize_package_name(raw_inst.package_name)
        component_source_id = dsn_component_source_id(
            page_id,
            raw_inst.db_id,
            instance_index,
        )
        # Instance-level evidence shared by every pin of this placement.
        component_props = dict(raw_inst.props)
        component_props_list = raw_inst.props_list or tuple(component_props.items())
        component_x = float(raw_inst.loc_x)
        component_y = float(raw_inst.loc_y)
        for pin_index, raw_pin in enumerate(raw_inst.pin_connections):
            pin_name = resolve_pin_name(
                raw_inst.package_name,
                raw_pin.pin_number,
                raw.symbol_pin_names,
                ctx,
                raw_inst.reference,
                raw_inst.pin_name_overrides,
            )
            location = (raw_pin.pin_x, raw_pin.pin_y)
            source_net_id = _pin_source_net_id(
                raw_page,
                source_page_net_ids,
                raw_pin.net_id,
                location,
            )
            page_net: DsnPageNet | None = None
            if source_net_id is not None:
                page_net = _add_page_net_if_missing(
                    page_nets_by_id=page_nets_by_id,
                    page_source=page_source,
                    page_id=page_id,
                    scope_id=scope_id,
                    net_id=source_net_id,
                )
            elif (anchor := _global_anchor(raw_page, location)) is not None:
                page_net = _synthetic_net_at(
                    synthetic_nets_by_location=synthetic_nets_by_location,
                    page_source=page_source,
                    page_id=page_id,
                    scope_id=scope_id,
                    location=(anchor.loc_x, anchor.loc_y),
                )
            elif ctx is not None:
                ctx.warn(
                    "dsn_netless_pin",
                    f"{raw_inst.reference} pin {raw_pin.pin_number} has a sentinel "
                    "net id and no wire or power symbol at its location; pin is netless",
                )
            pin_metadata = _symbol_pin_metadata(
                resolve_symbol_pin(
                    raw_inst.package_name,
                    raw_pin.pin_number,
                    raw.symbol_pins,
                    pin_name,
                    raw.symbol_pin_names,
                )
            )
            pin_metadata.update(raw_pin.no_connect_metadata)
            pin = DsnPinOccurrence(
                id=f"{component_source_id}:pin:{pin_index}",
                scope_id=scope_id,
                local_net_id=page_net.id if page_net is not None else None,
                source_net_id=source_net_id if source_net_id is not None else raw_pin.net_id,
                component_source_id=component_source_id,
                component_reference=raw_inst.reference,
                component_part=pkg,
                pin_designator=raw_pin.pin_number,
                pin_name=pin_name,
                location=location,
                no_connect=raw_pin.no_connect,
                component_props=component_props,
                component_props_list=component_props_list,
                pin_metadata=pin_metadata,
                component_x=component_x,
                component_y=component_y,
            )
            page_source.pin_occurrences.append(pin)
            if page_net is not None:
                page_net.pin_ids.append(pin.id)

    _graphic_sources(
        raw_page=raw_page,
        graphics=raw_page.ports,
        page_source=page_source,
        page_nets_by_id=page_nets_by_id,
        synthetic_nets_by_location=synthetic_nets_by_location,
        page_id=page_id,
        scope_id=scope_id,
        kind="port",
    )
    _graphic_sources(
        raw_page=raw_page,
        graphics=raw_page.globals,
        page_source=page_source,
        page_nets_by_id=page_nets_by_id,
        synthetic_nets_by_location=synthetic_nets_by_location,
        page_id=page_id,
        scope_id=scope_id,
        kind="global",
        ctx=ctx,
    )
    _graphic_sources(
        raw_page=raw_page,
        graphics=raw_page.off_page_connectors,
        page_source=page_source,
        page_nets_by_id=page_nets_by_id,
        synthetic_nets_by_location=synthetic_nets_by_location,
        page_id=page_id,
        scope_id=scope_id,
        kind="off_page_connector",
        ctx=ctx,
    )
    return page_source


def _library_header_metadata(raw: RawDesign) -> dict[str, str]:
    header = raw.library_header
    if header is None:
        return {}
    metadata = {
        "dsn_library_version": f"{header.version_major}.{header.version_minor}",
        "dsn_library_created_timestamp": str(header.created_timestamp),
        "dsn_library_modified_timestamp": str(header.modified_timestamp),
    }
    if header.intro:
        metadata["dsn_library_intro"] = header.intro
    return metadata


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
        net_bundles=[
            DsnNetBundle(
                id=f"net_bundle_map:{index}",
                name=bundle.name,
                name_key=dsn_name_key(bundle.name),
                members=tuple(
                    DsnBundleMember(
                        name=member.name,
                        name_key=dsn_name_key(member.name),
                        wire_type=member.wire_type,
                    )
                    for member in bundle.members
                ),
            )
            for index, bundle in enumerate(raw.net_bundle_maps)
        ],
        metadata=_library_header_metadata(raw),
    )


def dsn_to_design(raw: RawDesign, name: str = "", ctx: ParseContext | None = None) -> Schematic:
    """Convert a raw DSN ParsedDesign to a Schematic.

    Non-fatal pin-resolution issues are recorded on *ctx* when provided and
    surfaced as ``parse_issue_count`` in the design metadata.
    """
    return resolve_dsn_source(dsn_to_source(raw, name=name, ctx=ctx), ctx=ctx)
