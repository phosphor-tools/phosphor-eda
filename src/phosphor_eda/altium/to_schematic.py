"""Convert raw Altium parse results into the schematic domain model."""

from __future__ import annotations

from ecad_tools.altium.netlist import _point_on_segment, _resolve_sheet_nets
from ecad_tools.altium.record_parser import read_schematic_records
from ecad_tools.models import ParsedDesign as RawDesign
from ecad_tools.schematic import Component, Design, Net, Page, Pin, Port, merge_pages

# DistanceFromTop fractional properties use 1/100000 resolution.
_FRAC_DENOM = 100_000


def _int(props: dict[str, str], key: str, default: int = 0) -> int:
    val = props.get(key, "")
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _distance_from_top(rec: dict[str, str]) -> int:
    """Compute DistanceFromTop in standard Altium units (1/100 inch).

    DistanceFromTop is stored in x10 encoding (mils, i.e. 1/1000 inch).
    The _FRAC1 suffix adds sub-unit precision at 1/100000 resolution.
    """
    dist = _int(rec, "DistanceFromTop")
    frac = _int(rec, "DistanceFromTop_Frac1")
    return round(dist * 10 + frac / _FRAC_DENOM)


def _entry_coord(
    parent: dict[str, str], entry: dict[str, str]
) -> tuple[int, int]:
    """Compute the wire-side coordinate for a sheet or harness entry.

    Works for both RECORD=16 (sheet entries inside RECORD=15 sheet symbols)
    and RECORD=216 (harness entries inside RECORD=215 harness connectors).
    Parent Location.Y is the top of the box; DistanceFromTop measures downward.
    """
    sx = _int(parent, "Location.X")
    sy = _int(parent, "Location.Y")
    xsize = _int(parent, "XSize")
    side = entry.get("Side", "0")
    dist = _distance_from_top(entry)

    if side == "1":  # Right
        ex = sx + xsize
    else:  # Left (default)
        ex = sx
    ey = sy - dist
    return (ex, ey)


# ---------------------------------------------------------------------------
# Harness connector parsing
# ---------------------------------------------------------------------------

def _parse_harness_connectors(
    records: list[dict[str, str]],
) -> list[tuple[str, str, list[tuple[str, tuple[int, int]]]]]:
    """Parse harness connectors from the Additional stream records.

    Returns list of (harness_type, port_name, [(member_name, coord), ...]).
    """
    # Collect Additional stream records (RECORD 215-218)
    additional: list[dict[str, str]] = []
    for rec in records:
        if rec.get("RECORD") in ("215", "216", "217", "218"):
            additional.append(rec)

    # Index by Additional stream position
    connectors: dict[int, dict[str, str]] = {}
    entries_by_owner: dict[int, list[dict[str, str]]] = {}
    types_by_owner: dict[int, str] = {}

    for ai, rec in enumerate(additional):
        rid = rec.get("RECORD", "")
        if rid == "215":
            connectors[ai] = rec
        elif rid == "216":
            owner = _int(rec, "OwnerIndex", 0)
            entries_by_owner.setdefault(owner, []).append(rec)
        elif rid == "217":
            owner = _int(rec, "OwnerIndex", 0)
            types_by_owner[owner] = rec.get("Text", "")

    # Map harness_type -> port_name from RECORD=18 ports
    port_names: dict[str, str] = {}
    for rec in records:
        if rec.get("RECORD") == "18":
            ht = rec.get("HarnessType", "")
            if ht:
                port_names[ht] = rec.get("Name", "")

    result: list[tuple[str, str, list[tuple[str, tuple[int, int]]]]] = []
    for ai, conn in connectors.items():
        harness_type = types_by_owner.get(ai, "")
        if not harness_type:
            continue
        port_name = port_names.get(harness_type, harness_type)

        members: list[tuple[str, tuple[int, int]]] = []
        for entry in entries_by_owner.get(ai, []):
            entry_name = entry.get("Name", "")
            if not entry_name:
                continue
            coord = _entry_coord(conn, entry)
            members.append((entry_name, coord))

        if members:
            result.append((harness_type, port_name, members))

    return result


def _compute_harness_entry_coords(
    records: list[dict[str, str]],
) -> dict[tuple[int, int], str]:
    """Compute wire-side coordinates for harness connector entries.

    Returns (x, y) -> synthetic_net_name for each harness entry. Used as
    fallback net names for unnamed wire groups in _resolve_sheet_nets.
    """
    result: dict[tuple[int, int], str] = {}
    for _ht, port_name, members in _parse_harness_connectors(records):
        for member_name, coord in members:
            result[coord] = f"{port_name}:{member_name}"
    return result


def _collect_harness_type_members(
    records: list[dict[str, str]],
    members_by_type: dict[str, list[str]],
) -> None:
    """Collect harness member names from harness connectors on this page."""
    for harness_type, _port_name, members in _parse_harness_connectors(records):
        if harness_type not in members_by_type:
            members_by_type[harness_type] = [m[0] for m in members]


def _collect_harness_port_nets(
    records: list[dict[str, str]],
    coord_to_net_name: dict[tuple[int, int], str],
    harness_port_nets: dict[str, list[tuple[str, dict[str, str]]]],
    page_name: str,
) -> None:
    """Collect harness port -> [(page_name, {member -> net_name}), ...].

    Multiple pages may have harness ports with the same name (e.g., "SPI" on
    both ADC and ASIC Interface pages). We store the page name alongside each
    mapping so the bridge code can match entries to the correct child page.
    """
    for _ht, port_name, members in _parse_harness_connectors(records):
        nets: dict[str, str] = {}
        for member_name, coord in members:
            net_name = coord_to_net_name.get(coord)
            if net_name:
                nets[member_name] = net_name
        if nets:
            harness_port_nets.setdefault(port_name, []).append((page_name, nets))


# ---------------------------------------------------------------------------
# Port collection
# ---------------------------------------------------------------------------

def _collect_sheet_entry_ports(
    records: list[dict[str, str]],
    page: Page,
    nets_by_name: dict[str, Net],
    coord_to_net_name: dict[tuple[int, int], str],
) -> None:
    """Add Port objects for non-harness sheet entries (RECORD=16) on this page.

    Sheet symbols (RECORD=15) on a parent page contain sheet entries that
    correspond to ports on child pages. Each entry's coordinate is computed
    from the symbol's position/size and the entry's Side/DistanceFromTop,
    then resolved to a net on this page. The resulting Port bridges the
    parent-page net with the matching child-page port via merge_pages().
    """
    # Index sheet symbols by their OwnerIndex-compatible position
    symbols: dict[int, dict[str, str]] = {}
    for i, rec in enumerate(records):
        if rec.get("RECORD") == "15":
            symbols[i - 1] = rec

    if not symbols:
        return

    # Process each non-harness sheet entry
    for rec in records:
        if rec.get("RECORD") != "16":
            continue
        # Harness sheet entries are handled by _collect_harness_bridge_ports
        if rec.get("HarnessType"):
            continue
        owner = _int(rec, "OwnerIndex", -1)
        sym = symbols.get(owner)
        if sym is None:
            continue

        entry_name = rec.get("Name", "")
        if not entry_name:
            continue

        ex, ey = _entry_coord(sym, rec)
        net_name = coord_to_net_name.get((ex, ey))

        if net_name and net_name in nets_by_name:
            port = Port(
                name=entry_name,
                page=page,
                net=nets_by_name[net_name],
            )
            page.ports.append(port)


def _collect_harness_member_ports(
    records: list[dict[str, str]],
    page: Page,
    nets_by_name: dict[str, Net],
    coord_to_net_name: dict[tuple[int, int], str],
) -> None:
    """Add ports for each harness connector entry member on child pages.

    For each harness connector on this page, creates a port named
    ``portName:memberName`` attached to the resolved local net. These ports
    are matched by merge_pages() with bridge ports on the parent page.
    """
    for _ht, port_name, members in _parse_harness_connectors(records):
        for member_name, coord in members:
            net_name = coord_to_net_name.get(coord)
            if net_name and net_name in nets_by_name:
                port = Port(
                    name=f"{port_name}@{page.name}:{member_name}",
                    page=page,
                    net=nets_by_name[net_name],
                )
                page.ports.append(port)


def _collect_harness_bridge_ports(
    records: list[dict[str, str]],
    page: Page,
    nets_by_name: dict[str, Net],
    harness_port_nets: dict[str, list[tuple[str, dict[str, str]]]],
    harness_members_by_type: dict[str, list[str]],
) -> None:
    """Create bridge ports for harness sheet entries connected by signal harness wires.

    On a parent page, harness-type sheet entries (RECORD=16 with HarnessType)
    are connected by signal harness wires (RECORD=218). For each group of
    connected entries, one entry's nets are chosen as canonical and ports are
    created for each other entry to bridge through.

    Entries are identified by (entry_name, child_page) since multiple sheet
    symbols can have entries with the same name (e.g., "SPI" on both the ADC
    and ASIC Interface sheet symbols).
    """
    # Build signal harness wire connectivity
    parent_map: dict[tuple[int, int], tuple[int, int]] = {}

    def find(p: tuple[int, int]) -> tuple[int, int]:
        if p not in parent_map:
            parent_map[p] = p
        while parent_map[p] != p:
            parent_map[p] = parent_map[parent_map[p]]
            p = parent_map[p]
        return p

    def union(a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent_map[ra] = rb

    harness_wire_segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for rec in records:
        if rec.get("RECORD") != "218":
            continue
        loc_count = int(rec.get("LocationCount", "2"))
        points: list[tuple[int, int]] = []
        for i in range(1, loc_count + 1):
            x = int(rec.get(f"X{i}", "0"))
            y = int(rec.get(f"Y{i}", "0"))
            points.append((x, y))
        for j in range(len(points) - 1):
            union(points[j], points[j + 1])
            harness_wire_segments.append((points[j], points[j + 1]))

    if not harness_wire_segments:
        return

    # Index sheet symbols
    symbols: dict[int, dict[str, str]] = {}
    for i, rec in enumerate(records):
        if rec.get("RECORD") == "15":
            symbols[i - 1] = rec

    # Map sheet symbol key -> child page name from RECORD=33 (FileName)
    child_page_for_symbol: dict[int, str] = {}
    for rec in records:
        if rec.get("RECORD") == "33":
            owner = _int(rec, "OwnerIndex", -1)
            text = rec.get("Text", "")
            if owner >= 0 and text.endswith(".SchDoc"):
                child_page_for_symbol[owner] = text.removesuffix(".SchDoc")

    # Find harness-type sheet entries and compute their coordinates.
    # Each entry is (entry_name, harness_type, child_page, coord).
    harness_entries: list[tuple[str, str, str, tuple[int, int]]] = []
    for rec in records:
        if rec.get("RECORD") != "16":
            continue
        ht = rec.get("HarnessType", "")
        if not ht:
            continue
        owner = _int(rec, "OwnerIndex", -1)
        sym = symbols.get(owner)
        if sym is None:
            continue
        entry_name = rec.get("Name", "")
        if not entry_name:
            continue

        child_page = child_page_for_symbol.get(owner, "")
        coord = _entry_coord(sym, rec)

        # Connect to signal harness wire
        for seg in harness_wire_segments:
            if _point_on_segment(
                coord[0], coord[1], seg[0][0], seg[0][1], seg[1][0], seg[1][1]
            ):
                union(coord, seg[0])
                break

        harness_entries.append((entry_name, ht, child_page, coord))

    # Group connected entries
    groups: dict[tuple[int, int], list[tuple[str, str, str]]] = {}
    for entry_name, ht, child_page, coord in harness_entries:
        root = find(coord)
        groups.setdefault(root, []).append((entry_name, ht, child_page))

    # For each group with 2+ entries, create bridge ports
    for group in groups.values():
        if len(group) < 2:
            continue

        ht = group[0][1]
        members = harness_members_by_type.get(ht, [])
        if not members:
            continue

        # Look up port-net mapping for each entry, disambiguating by child page
        entry_nets: dict[tuple[str, str], dict[str, str]] = {}
        for entry_name, _, child_page in group:
            port_net_list = harness_port_nets.get(entry_name, [])
            for page_name, nets in port_net_list:
                if page_name == child_page:
                    entry_nets[(entry_name, child_page)] = nets
                    break
            else:
                # Fallback: if only one mapping exists, use it
                if len(port_net_list) == 1:
                    entry_nets[(entry_name, child_page)] = port_net_list[0][1]

        # Pick the entry with real net names (no ":" in name) as canonical.
        # This ensures the real net name survives merging.
        canonical_key: tuple[str, str] | None = None
        canonical_nets: dict[str, str] | None = None

        for entry_name, _, child_page in group:
            nets = entry_nets.get((entry_name, child_page))
            if nets:
                has_real = any(":" not in n for n in nets.values())
                if has_real and canonical_key is None:
                    canonical_key = (entry_name, child_page)
                    canonical_nets = nets

        # Fallback to first available
        if canonical_nets is None:
            for entry_name, _, child_page in group:
                nets = entry_nets.get((entry_name, child_page))
                if nets:
                    canonical_key = (entry_name, child_page)
                    canonical_nets = nets
                    break

        if canonical_nets is None:
            continue

        # Create nets with canonical names and bridge ports for all entries.
        # Every entry (including canonical) gets bridge ports using the
        # ``entry_name@child_page:member_name`` naming scheme to match the
        # corresponding harness member ports on child pages.
        for member_name in members:
            net_name = canonical_nets.get(member_name)
            if not net_name:
                continue

            if net_name not in nets_by_name:
                net = Net(name=net_name)
                nets_by_name[net_name] = net
                page.nets.append(net)
            net = nets_by_name[net_name]

            for entry_name, _, child_page in group:
                port = Port(
                    name=f"{entry_name}@{child_page}:{member_name}",
                    page=page,
                    net=net,
                )
                page.ports.append(port)


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def altium_to_design(raw: RawDesign, name: str = "") -> Design:
    """Convert a raw Altium ParsedDesign to a schematic Design."""
    pages: list[Page] = []

    # Pre-scan: read records, resolve nets, collect harness info across pages.
    # We need harness member names and port-to-net mappings from child pages
    # before we can create bridge ports on parent pages.
    cached_data: dict[str, tuple[list[dict[str, str]], dict[tuple[int, int], str]]] = {}
    harness_port_nets: dict[str, list[tuple[str, dict[str, str]]]] = {}
    harness_members_by_type: dict[str, list[str]] = {}

    for raw_page in raw.pages:
        schdoc_path = getattr(raw_page, "_schdoc_path", None)
        if schdoc_path is None:
            continue
        records = read_schematic_records(str(schdoc_path))
        harness_entry_coords = _compute_harness_entry_coords(records)
        coord_to_net_name = _resolve_sheet_nets(
            records, extra_named_coords=harness_entry_coords
        )
        cached_data[raw_page.name] = (records, coord_to_net_name)
        _collect_harness_type_members(records, harness_members_by_type)
        _collect_harness_port_nets(
            records, coord_to_net_name, harness_port_nets, raw_page.name
        )

    # Main pass: build Page/Net/Component/Pin/Port objects
    for raw_page in raw.pages:
        data = cached_data.get(raw_page.name)
        if data is None:
            continue
        records, coord_to_net_name = data

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

        # Collect non-harness ports (RECORD=18) for cross-page bridging.
        # Harness-type ports are expanded by _collect_harness_member_ports.
        for rec in records:
            if rec.get("RECORD") != "18":
                continue
            if rec.get("HarnessType"):
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

        # Non-harness sheet entries as ports for hierarchical bridging
        _collect_sheet_entry_ports(records, page, nets_by_name, coord_to_net_name)

        # Harness member ports on child pages (pages with harness connectors)
        _collect_harness_member_ports(
            records, page, nets_by_name, coord_to_net_name
        )

        # Harness bridge ports on parent pages (pages with harness sheet entries)
        _collect_harness_bridge_ports(
            records, page, nets_by_name,
            harness_port_nets, harness_members_by_type,
        )

        pages.append(page)

    return merge_pages(name, pages)
