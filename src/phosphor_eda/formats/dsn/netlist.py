"""Netlist construction from parsed OrCAD DSN designs."""

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.raw_models import NetlistEntry, ParsedDesign


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
            # Look up pin name list for this symbol
            pkg = inst.package_name.replace(".Normal", "")
            sym_pins = pin_names.get(pkg, [])

            for pin in inst.pin_connections:
                # Resolve pin name (1-indexed: pin_number="1" -> index 0)
                pin_name = ""
                try:
                    pn = int(pin.pin_number)
                    if 1 <= pn <= len(sym_pins):
                        pin_name = sym_pins[pn - 1]
                except (ValueError, TypeError):
                    if ctx is not None:
                        ctx.warn(
                            "dsn_pin_number",
                            f"{inst.reference}: non-numeric pin number "
                            f"{pin.pin_number!r}; pin name left blank",
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
