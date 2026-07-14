"""Netlist construction from parsed OrCAD DSN designs."""

from phosphor_eda.formats.common.diagnostics import ParseContext, warn_optional
from phosphor_eda.formats.dsn.pins import resolve_pin_name
from phosphor_eda.formats.dsn.raw_models import (
    NetlistEntry,
    ParsedDesign,
    PinConnection,
    SchematicPage,
)

# Net-id sentinels that carry no page-net assignment (mirrors to_schematic).
_SENTINEL_NET_IDS = frozenset({0, 0xFFFFFFFF})


def _pin_net_ids(page: SchematicPage, pin: PinConnection, known_net_ids: set[int]) -> list[int]:
    """Net ids a pin belongs to, preferring the pin's own net_id evidence.

    Mirrors ``to_schematic._pin_source_net_id``: a non-sentinel ``net_id`` that
    names a known page net wins outright; otherwise the coordinate-matched wire
    net ids resolve the pin, and a lone non-sentinel ``net_id`` is the last
    resort.
    """
    if pin.net_id not in _SENTINEL_NET_IDS and pin.net_id in known_net_ids:
        return [pin.net_id]
    coord_net_ids = [
        net_id
        for net_id in sorted(page.wire_net_map.get((pin.pin_x, pin.pin_y), set()))
        if net_id not in _SENTINEL_NET_IDS
    ]
    if coord_net_ids:
        return coord_net_ids
    if pin.net_id in _SENTINEL_NET_IDS:
        return []
    return [pin.net_id]


def build_netlist(
    design: ParsedDesign, ctx: ParseContext | None = None
) -> dict[str, list[NetlistEntry]]:
    """Build a netlist mapping net names to component pins.

    Merges data across all pages. Each pin's net is resolved from its own
    ``net_id`` when that names a known page net, falling back to coordinate
    matching against wire endpoints (each ``(x,y)`` knows its net_id). A net id
    with no stored page-net name yields a ``NET_<id>`` placeholder and a
    ``dsn_netlist`` diagnostic rather than a silent synthesized name.

    Pin names are resolved from the Cache symbol definitions. The pin order
    in the Cache matches T0x10 pin_number (1-indexed).
    """
    pin_names = design.symbol_pin_names
    netlist: dict[str, list[NetlistEntry]] = {}

    for page in design.pages:
        net_by_id = {n.net_id: n.name for n in page.nets}
        known_net_ids = set(net_by_id)

        for inst in page.instances:
            for pin in inst.pin_connections:
                pin_name = resolve_pin_name(
                    inst.package_name,
                    pin.pin_number,
                    pin_names,
                    ctx,
                    inst.reference,
                    inst.pin_name_overrides,
                )

                for net_id in _pin_net_ids(page, pin, known_net_ids):
                    net_name = net_by_id.get(net_id)
                    if net_name is None:
                        net_name = f"NET_{net_id}"
                        warn_optional(
                            ctx,
                            "dsn_netlist",
                            f"pin {inst.reference}.{pin.pin_number} on net id {net_id} "
                            f"has no stored net name; using placeholder {net_name!r}",
                        )
                    entry = NetlistEntry(
                        reference=inst.reference,
                        pin_number=pin.pin_number,
                        pin_name=pin_name,
                        net_name=net_name,
                    )
                    netlist.setdefault(net_name, []).append(entry)

    return netlist
