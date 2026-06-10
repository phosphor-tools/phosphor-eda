"""Netlist construction from parsed OrCAD DSN designs."""

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.raw_models import NetlistEntry, ParsedDesign
from phosphor_eda.formats.dsn.pins import resolve_pin_name


def build_netlist(
    design: ParsedDesign, ctx: ParseContext | None = None
) -> dict[str, list[NetlistEntry]]:
    """Build a netlist mapping net names to component pins.

    Merges data across all pages. Uses coordinate matching: each wire
    endpoint (x,y) knows its net_id, and each T0x10 pin instance has
    coordinates. When they match, we know which net the pin is on.

    Pin names are resolved from the Cache symbol definitions. The pin order
    in the Cache matches T0x10 pin_number (1-indexed).
    """
    pin_names = design.symbol_pin_names
    netlist: dict[str, list[NetlistEntry]] = {}

    for page in design.pages:
        net_by_id = {n.net_id: n.name for n in page.nets}

        for inst in page.instances:
            for pin in inst.pin_connections:
                pin_name = resolve_pin_name(
                    inst.package_name,
                    pin.pin_number,
                    pin_names,
                    ctx,
                    inst.reference,
                )

                coord = (pin.pin_x, pin.pin_y)
                net_ids = page.wire_net_map.get(coord, set())
                for net_id in net_ids:
                    net_name = net_by_id.get(net_id, f"NET_{net_id}")
                    entry = NetlistEntry(
                        reference=inst.reference,
                        pin_number=pin.pin_number,
                        pin_name=pin_name,
                        net_name=net_name,
                    )
                    netlist.setdefault(net_name, []).append(entry)

    return netlist
