"""Convert raw Altium parse results into the schematic domain model."""

from __future__ import annotations

from ecad_tools.altium.netlist import _resolve_sheet_nets
from ecad_tools.altium.record_parser import read_schematic_records
from ecad_tools.models import ParsedDesign as RawDesign
from ecad_tools.schematic import Component, Design, Net, Page, Pin, Port, merge_pages


def _int(props: dict[str, str], key: str, default: int = 0) -> int:
    val = props.get(key, "")
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def altium_to_design(raw: RawDesign, name: str = "") -> Design:
    """Convert a raw Altium ParsedDesign to a schematic Design."""
    pages: list[Page] = []

    for raw_page in raw.pages:
        schdoc_path = getattr(raw_page, "_schdoc_path", None)
        if schdoc_path is None:
            continue

        records = read_schematic_records(str(schdoc_path))
        coord_to_net_name = _resolve_sheet_nets(records)

        page = Page(name=raw_page.name)

        # Build Net objects for this page
        nets_by_name: dict[str, Net] = {}
        for nname in sorted(set(coord_to_net_name.values())):
            net = Net(name=nname)
            nets_by_name[nname] = net
            page.nets.append(net)

        # Collect no-connect marker coordinates (RECORD=22)
        nc_coords: set[tuple[int, int]] = set()
        for rec in records:
            if rec.get("RECORD") == "22":
                nc_coords.add((_int(rec, "Location.X"), _int(rec, "Location.Y")))

        # Index component records by OwnerIndex-compatible index
        # OwnerIndex=N refers to records[N+1], so comp index = i-1
        comp_record_indices: dict[int, dict[str, str]] = {}
        for i, rec in enumerate(records):
            if rec.get("RECORD") == "1":
                comp_record_indices[i - 1] = rec

        # Collect designator text (RECORD=34) keyed by OwnerIndex
        designator_by_owner: dict[int, str] = {}
        for rec in records:
            if rec.get("RECORD") == "34":
                owner = _int(rec, "OwnerIndex", -1)
                designator_by_owner[owner] = rec.get("Text", "")

        # Collect pin names (RECORD=2) keyed by (OwnerIndex, Designator)
        pin_name_by_key: dict[tuple[int, str], str] = {}
        for rec in records:
            if rec.get("RECORD") == "2":
                owner = _int(rec, "OwnerIndex", -1)
                desig = rec.get("Designator", "")
                pname = rec.get("Name", "")
                if owner >= 0 and desig:
                    pin_name_by_key[(owner, desig)] = pname

        # Collect parameters (RECORD=41) keyed by OwnerIndex
        params_by_owner: dict[int, dict[str, str]] = {}
        for rec in records:
            if rec.get("RECORD") == "41":
                owner = _int(rec, "OwnerIndex", -1)
                pname = rec.get("Name", "")
                ptext = rec.get("Text", "")
                if owner >= 0 and pname and ptext and ptext != "*":
                    params_by_owner.setdefault(owner, {})[pname] = ptext

        # Build Component and Pin objects from the raw page instances
        for raw_inst in raw_page.instances:
            comp = Component(
                reference=raw_inst.reference,
                part=raw_inst.package_name,
                description="",
                pages=[page],
            )

            # Find the OwnerIndex for this component to get metadata and pin names
            comp_owner_idx: int | None = None
            for idx, ref_text in designator_by_owner.items():
                if ref_text == raw_inst.reference and idx in comp_record_indices:
                    comp_owner_idx = idx
                    break

            # Apply RECORD=41 parameters as metadata
            if comp_owner_idx is not None and comp_owner_idx in params_by_owner:
                comp.metadata.update(params_by_owner[comp_owner_idx])

            # Apply description from metadata
            if "Description" in comp.metadata:
                comp.description = comp.metadata.pop("Description")

            for raw_pin in raw_inst.pin_connections:
                coord = (raw_pin.pin_x, raw_pin.pin_y)
                net_name = coord_to_net_name.get(coord)
                net = nets_by_name.get(net_name) if net_name else None
                is_nc = coord in nc_coords

                # Resolve pin name from RECORD=2
                pin_name = ""
                if comp_owner_idx is not None:
                    pin_name = pin_name_by_key.get(
                        (comp_owner_idx, raw_pin.pin_number), ""
                    )

                pin = Pin(
                    designator=raw_pin.pin_number,
                    name=pin_name,
                    component=comp,
                    net=net,
                    no_connect=is_nc,
                )
                comp.pins.append(pin)
                if net is not None:
                    net.pins.append(pin)

            page.components.append(comp)

        # Collect ports (RECORD=18) for cross-page bridging
        for rec in records:
            if rec.get("RECORD") != "18":
                continue
            port_name = rec.get("Name", "")
            if not port_name:
                continue
            px = _int(rec, "Location.X")
            py = _int(rec, "Location.Y")
            net_name = coord_to_net_name.get((px, py))
            if net_name and net_name in nets_by_name:
                port = Port(
                    name=port_name,
                    page=page,
                    net=nets_by_name[net_name],
                    harness=rec.get("HarnessType"),
                )
                page.ports.append(port)

        pages.append(page)

    return merge_pages(name, pages)
