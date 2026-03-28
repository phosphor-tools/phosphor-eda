"""Convert raw DSN parse results into the schematic domain model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.schematic import Component, Design, Net, Page, Pin, merge_pages
from phosphor_eda.text import strip_overline

if TYPE_CHECKING:
    from phosphor_eda.models import ParsedDesign as RawDesign


def dsn_to_design(raw: RawDesign, name: str = "") -> Design:
    """Convert a raw DSN ParsedDesign to a schematic Design."""
    pin_names_map = raw.symbol_pin_names
    pages: list[Page] = []

    for raw_page in raw.pages:
        page = Page(name=raw_page.name)
        net_by_id: dict[int, Net] = {}

        for raw_net in raw_page.nets:
            net = Net(name=raw_net.name)
            net_by_id[raw_net.net_id] = net
            page.nets.append(net)

        # Build coord -> net lookup from wire_net_map.
        # Wire net IDs not in the page's named net list are unnamed nets
        # (wires connecting components without an explicit net label).
        # Create synthetic nets for these so pins stay connected.
        coord_to_nets: dict[tuple[int, int], list[Net]] = {}
        for coord, net_ids in raw_page.wire_net_map.items():
            for nid in net_ids:
                if nid not in net_by_id:
                    unnamed = Net(name=f"N{nid:08d}")
                    net_by_id[nid] = unnamed
                    page.nets.append(unnamed)
                coord_to_nets.setdefault(coord, []).append(net_by_id[nid])

        for raw_inst in raw_page.instances:
            pkg = raw_inst.package_name.replace(".Normal", "")
            sym_pins = pin_names_map.get(pkg, [])

            comp = Component(
                reference=raw_inst.reference,
                part=pkg,
                description="",
                pages=[page],
            )

            for raw_pin in raw_inst.pin_connections:
                pin_name = ""
                try:
                    pn = int(raw_pin.pin_number)
                    if 1 <= pn <= len(sym_pins):
                        pin_name, _overline = strip_overline(sym_pins[pn - 1])
                except (ValueError, TypeError):
                    pass

                coord = (raw_pin.pin_x, raw_pin.pin_y)
                nets = coord_to_nets.get(coord, [])
                net = nets[0] if nets else None

                pin = Pin(
                    designator=raw_pin.pin_number,
                    name=pin_name,
                    component=comp,
                    net=net,
                )
                comp.pins.append(pin)
                if net is not None:
                    net.pins.append(pin)

            page.components.append(comp)

        pages.append(page)

    return merge_pages(name, pages)
