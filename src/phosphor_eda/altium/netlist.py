"""Net resolution for Altium schematics.

Builds a netlist by resolving wire connectivity on each sheet, then
matching pin coordinates to named wire groups.
"""

from ecad_tools.altium.sheet_builder import load_sheet, resolve_nets
from ecad_tools.models import NetlistEntry, PageNetEntry, ParsedDesign


def build_netlist(design: ParsedDesign) -> dict[str, list[NetlistEntry]]:
    """Build a netlist from an Altium ParsedDesign.

    For each page, resolves wire connectivity using typed records and
    spatial indices, then matches pin coordinates to named wire groups.
    """
    netlist: dict[str, list[NetlistEntry]] = {}

    for page in design.pages:
        schdoc_path = getattr(page, "_schdoc_path", None)
        if schdoc_path is None:
            continue

        sheet = load_sheet(str(schdoc_path))
        coord_to_net, _nc = resolve_nets(sheet)

        # Populate page.nets from discovered net names
        net_names = sorted(set(coord_to_net.values()))
        page.nets = [PageNetEntry(name=n, net_id=i) for i, n in enumerate(net_names)]

        # Match pins to nets
        for inst in page.instances:
            for pin in inst.pin_connections:
                coord = (pin.pin_x, pin.pin_y)
                net_name = coord_to_net.get(coord)
                if net_name:
                    entry = NetlistEntry(
                        reference=inst.reference,
                        pin_number=pin.pin_number,
                        pin_name="",
                        net_name=net_name,
                    )
                    netlist.setdefault(net_name, []).append(entry)

    return netlist
