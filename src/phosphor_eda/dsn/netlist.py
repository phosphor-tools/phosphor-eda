"""Netlist construction and output from parsed OrCAD DSN designs."""

from collections import Counter
from pathlib import Path

from ecad_tools.dsn.models import NetlistEntry, ParsedDesign


def build_netlist(design: ParsedDesign) -> dict[str, list[NetlistEntry]]:
    """Build a netlist mapping net names to component pins.

    Uses coordinate matching: each wire endpoint (x,y) knows its net_id,
    and each T0x10 pin instance has coordinates. When they match, we know
    which net the pin is on.

    Pin names are resolved from the Cache symbol definitions. The pin order
    in the Cache matches T0x10 pin_number (1-indexed).
    """
    net_by_id = {n.net_id: n.name for n in design.page_nets}
    wire_net_map = getattr(design, "_wire_net_map", {})
    pin_names = design.symbol_pin_names

    netlist: dict[str, list[NetlistEntry]] = {}

    for inst in design.instances:
        # Look up pin name list for this symbol
        pkg = inst.package_name.replace(".Normal", "")
        sym_pins = pin_names.get(pkg, [])

        for pin in inst.pin_connections:
            # Resolve pin name (1-indexed: pin_number=1 -> index 0)
            pin_name = ""
            if 1 <= pin.pin_number <= len(sym_pins):
                pin_name = sym_pins[pin.pin_number - 1]

            coord = (pin.pin_x, pin.pin_y)
            net_ids = wire_net_map.get(coord, set())
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


def print_design(design: ParsedDesign) -> None:
    """Print a summary of the parsed design."""
    print("\n" + "=" * 70)
    print("PARSED DESIGN SUMMARY")
    print("=" * 70)

    print(f"\nPage: {design.page_name} (size: {design.page_size})")

    # Component list
    print(f"\n--- Components ({len(design.instances)}) ---")
    for inst in sorted(design.instances, key=lambda i: i.reference or "zzz"):
        props = getattr(inst, "_props", {})
        mfr_pn = props.get("manufacturer_pn", "")
        desc = props.get("description", "")
        label = mfr_pn or inst.package_name.replace(".Normal", "")
        print(f"  {inst.reference:6s}  {label:30s}  {desc[:50]}")

    # Power nets from globals
    print(f"\n--- Power Nets (from {len(design.globals)} global symbols) ---")
    net_counts: Counter[str] = Counter()
    for g in design.globals:
        props = getattr(g, "_props", {})
        net_name = props.get("_net_name", g.name)
        net_counts[net_name] += 1
    for net_name, count in sorted(net_counts.items()):
        print(f"  {net_name:20s}  ({count} instances)")

    # Netlist
    netlist = build_netlist(design)
    print(f"\n--- Netlist ({len(netlist)} nets) ---")
    for net_name in sorted(netlist.keys()):
        pins = netlist[net_name]
        pin_strs = []
        for e in sorted(pins, key=lambda p: (p.reference, p.pin_number)):
            if e.pin_name:
                pin_strs.append(f"{e.reference}.{e.pin_number}/{e.pin_name}")
            else:
                pin_strs.append(f"{e.reference}.{e.pin_number}")
        print(f"  {net_name:20s}  {', '.join(pin_strs)}")


def write_netlist(design: ParsedDesign, output_path: Path) -> None:
    """Write the parsed design to a text file."""
    netlist = build_netlist(design)
    lines: list[str] = []

    lines.append("=" * 70)
    lines.append("PARSED DESIGN SUMMARY")
    lines.append("=" * 70)

    lines.append(f"\nPage: {design.page_name} (size: {design.page_size})")

    # Component list
    lines.append(f"\n--- Components ({len(design.instances)}) ---")
    for inst in sorted(design.instances, key=lambda i: i.reference or "zzz"):
        props = getattr(inst, "_props", {})
        mfr_pn = props.get("manufacturer_pn", "")
        desc = props.get("description", "")
        label = mfr_pn or inst.package_name.replace(".Normal", "")
        lines.append(f"  {inst.reference:6s}  {label:30s}  {desc[:50]}")

    # Power nets from globals
    lines.append(f"\n--- Power Nets (from {len(design.globals)} global symbols) ---")
    net_counts: Counter[str] = Counter()
    for g in design.globals:
        props = getattr(g, "_props", {})
        net_name = props.get("_net_name", g.name)
        net_counts[net_name] += 1
    for net_name, count in sorted(net_counts.items()):
        lines.append(f"  {net_name:20s}  ({count} instances)")

    # Netlist
    lines.append(f"\n--- Netlist ({len(netlist)} nets) ---")
    for net_name in sorted(netlist.keys()):
        pins = netlist[net_name]
        pin_strs = []
        for e in sorted(pins, key=lambda p: (p.reference, p.pin_number)):
            if e.pin_name:
                pin_strs.append(f"{e.reference}.{e.pin_number}/{e.pin_name}")
            else:
                pin_strs.append(f"{e.reference}.{e.pin_number}")
        lines.append(f"  {net_name:20s}  {', '.join(pin_strs)}")

    output_path.write_text("\n".join(lines) + "\n")
