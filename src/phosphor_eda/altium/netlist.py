"""Net resolution for Altium schematics.

Builds a netlist by loading typed records from each sheet, resolving
wire connectivity, and matching pin tip coordinates to named nets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.altium.sheet_builder import resolve_nets
from phosphor_eda.altium.to_schematic import load_project_sheets
from phosphor_eda.models import NetlistEntry

if TYPE_CHECKING:
    from pathlib import Path


def build_netlist(path: Path) -> dict[str, list[NetlistEntry]]:
    """Build a netlist from an Altium .PrjPcb or single .SchDoc file.

    For each sheet, resolves wire connectivity using typed records and
    spatial indices, then matches pin tip coordinates to named nets.
    """
    sheets = load_project_sheets(path)
    netlist: dict[str, list[NetlistEntry]] = {}

    for sheet in sheets.values():
        coord_to_net, _nc = resolve_nets(sheet)

        # Build component index for designator lookup
        comp_keys: dict[int, int] = {}  # owner_index → display_mode
        desig_by_owner: dict[int, str] = {}

        for comp_rec in sheet.components:
            key = comp_rec.index - 1
            comp_keys[key] = comp_rec.display_mode

        for desig in sheet.designators:
            if desig.owner_index >= 0:
                desig_by_owner[desig.owner_index] = desig.text

        # Match pins to nets using PinRec.tip coordinates
        for pin in sheet.pins:
            if pin.owner_index < 0 or not pin.designator:
                continue
            # Filter by display mode
            display_mode = comp_keys.get(pin.owner_index)
            if display_mode is not None and pin.owner_part_display_mode != display_mode:
                continue

            reference = desig_by_owner.get(pin.owner_index, "")
            if not reference:
                continue

            net_name = coord_to_net.get(pin.tip)
            if net_name:
                netlist.setdefault(net_name, []).append(
                    NetlistEntry(
                        reference=reference,
                        pin_number=pin.designator,
                        pin_name=pin.name,
                        net_name=net_name,
                    )
                )

    return netlist
