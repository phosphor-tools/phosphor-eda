"""High-level sheet loading and net resolution using typed records.

Replaces the raw-dict iteration in ``netlist.py`` and the inner loop of
``to_schematic.py`` with a structured pipeline:

1. ``load_sheet()`` — parse + materialize + link + index
2. ``resolve_nets()`` — wire connectivity → coord-to-net-name map
3. ``build_page()`` — construct domain model objects (Page/Net/Component/Pin/Port)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from phosphor_eda.altium.record_factory import (
    compute_entry_coord,
    link_children,
    materialize_records,
)
from phosphor_eda.altium.record_parser import read_schematic_records
from phosphor_eda.altium.records import (
    AltiumRecord,
    BlanketRec,
    ComponentRec,
    DesignatorRec,
    FileNameRec,
    HarnessConnectorRec,
    HarnessEntryRec,
    HarnessTypeRec,
    ImplementationRec,
    JunctionRec,
    LabelRec,
    NetLabelRec,
    NoConnectRec,
    ParameterRec,
    ParameterSetRec,
    PinRec,
    PortRec,
    PowerPortRec,
    RecordType,
    SheetEntryRec,
    SheetNameRec,
    SheetRec,
    SheetSymbolRec,
    SignalHarnessRec,
    TextFrameRec,
    WireRec,
)
from phosphor_eda.altium.spatial import UnionFind, WireIndex, point_on_segment
from phosphor_eda.schematic import Component, Net, Page, Pin, Port

if TYPE_CHECKING:
    from collections.abc import Iterator

# Pin electrical type names (Altium Electrical field values)
_PIN_ELECTRICAL_NAMES = {
    0: "input",
    1: "IO",
    2: "output",
    3: "open-collector",
    4: "passive",
    5: "hi-Z",
    6: "open-emitter",
    7: "power",
}

# IO type names for ports and sheet entries
_IO_TYPE_NAMES = {
    0: "unspecified",
    1: "output",
    2: "input",
    3: "bidirectional",
}

# Altium standard sheet sizes (SheetStyle → name)
_SHEET_STYLE_NAMES = {
    0: "A4",
    1: "A3",
    2: "A2",
    3: "A1",
    4: "A0",
    5: "A",
    6: "B",
    7: "C",
    8: "D",
    9: "E",
    10: "Letter",
    11: "Legal",
    12: "Tabloid",
    13: "OrCAD-A",
    14: "OrCAD-B",
    15: "OrCAD-C",
    16: "OrCAD-D",
    17: "OrCAD-E",
}


# ---------------------------------------------------------------------------
# Port connection-point helper
# ---------------------------------------------------------------------------


def _port_wire_coord(port: PortRec, wire_index: WireIndex) -> tuple[int, int]:
    """Determine the wire-side coordinate for a port.

    Altium stores ``location`` as one corner of the port shape.  The actual
    wire connection can be at ``location`` or at the opposite end.  For
    horizontal ports (style 0-3) the opposite end is ``(x + width, y)``;
    for vertical ports (style 4-7) it is ``(x, y + width)``.

    We probe both ends and return whichever touches a wire, falling back to
    ``location`` if neither does (e.g. harness ports).
    """
    loc = port.location
    touches = wire_index.segments_touching(loc[0], loc[1])
    if touches:
        return loc

    # Vertical: opposite end above location; horizontal: to the right
    alt = (loc[0], loc[1] + port.width) if port.style >= 4 else (loc[0] + port.width, loc[1])

    touches = wire_index.segments_touching(alt[0], alt[1])
    if touches:
        return alt

    return loc


# ---------------------------------------------------------------------------
# SheetRecords container
# ---------------------------------------------------------------------------


@dataclass
class SheetRecords:
    """All typed records from one .SchDoc sheet, with spatial indices."""

    records: list[AltiumRecord]
    children: dict[int, list[AltiumRecord]]
    wire_index: WireIndex
    name: str = ""

    def by_type(self, cls: type[AltiumRecord]) -> Iterator[AltiumRecord]:
        for rec in self.records:
            if isinstance(rec, cls):
                yield rec

    @property
    def components(self) -> Iterator[ComponentRec]:
        for rec in self.records:
            if isinstance(rec, ComponentRec):
                yield rec

    @property
    def pins(self) -> Iterator[PinRec]:
        for rec in self.records:
            if isinstance(rec, PinRec):
                yield rec

    @property
    def wires(self) -> Iterator[WireRec]:
        for rec in self.records:
            if isinstance(rec, WireRec):
                yield rec

    @property
    def net_labels(self) -> Iterator[NetLabelRec]:
        for rec in self.records:
            if isinstance(rec, NetLabelRec):
                yield rec

    @property
    def power_ports(self) -> Iterator[PowerPortRec]:
        for rec in self.records:
            if isinstance(rec, PowerPortRec):
                yield rec

    @property
    def ports(self) -> Iterator[PortRec]:
        for rec in self.records:
            if isinstance(rec, PortRec):
                yield rec

    @property
    def junctions(self) -> Iterator[JunctionRec]:
        for rec in self.records:
            if isinstance(rec, JunctionRec):
                yield rec

    @property
    def no_connects(self) -> Iterator[NoConnectRec]:
        for rec in self.records:
            if isinstance(rec, NoConnectRec):
                yield rec

    @property
    def sheet_symbols(self) -> Iterator[SheetSymbolRec]:
        for rec in self.records:
            if isinstance(rec, SheetSymbolRec):
                yield rec

    @property
    def sheet_entries(self) -> Iterator[SheetEntryRec]:
        for rec in self.records:
            if isinstance(rec, SheetEntryRec):
                yield rec

    @property
    def designators(self) -> Iterator[DesignatorRec]:
        for rec in self.records:
            if isinstance(rec, DesignatorRec):
                yield rec

    @property
    def parameters(self) -> Iterator[ParameterRec]:
        for rec in self.records:
            if isinstance(rec, ParameterRec):
                yield rec

    @property
    def file_names(self) -> Iterator[FileNameRec]:
        for rec in self.records:
            if isinstance(rec, FileNameRec):
                yield rec

    @property
    def harness_connectors(self) -> Iterator[HarnessConnectorRec]:
        for rec in self.records:
            if isinstance(rec, HarnessConnectorRec):
                yield rec

    @property
    def harness_entries(self) -> Iterator[HarnessEntryRec]:
        for rec in self.records:
            if isinstance(rec, HarnessEntryRec):
                yield rec

    @property
    def harness_types(self) -> Iterator[HarnessTypeRec]:
        for rec in self.records:
            if isinstance(rec, HarnessTypeRec):
                yield rec

    @property
    def signal_harnesses(self) -> Iterator[SignalHarnessRec]:
        for rec in self.records:
            if isinstance(rec, SignalHarnessRec):
                yield rec

    @property
    def sheet_rec(self) -> SheetRec | None:
        """Return the single RECORD=31 sheet properties record, if present."""
        for rec in self.records:
            if isinstance(rec, SheetRec):
                return rec
        return None

    @property
    def sheet_names(self) -> Iterator[SheetNameRec]:
        for rec in self.records:
            if isinstance(rec, SheetNameRec):
                yield rec

    @property
    def implementations(self) -> Iterator[ImplementationRec]:
        for rec in self.records:
            if isinstance(rec, ImplementationRec):
                yield rec

    @property
    def parameter_sets(self) -> Iterator[ParameterSetRec]:
        for rec in self.records:
            if isinstance(rec, ParameterSetRec):
                yield rec

    @property
    def labels(self) -> Iterator[LabelRec]:
        for rec in self.records:
            if isinstance(rec, LabelRec):
                yield rec

    @property
    def text_frames(self) -> Iterator[TextFrameRec]:
        for rec in self.records:
            if isinstance(rec, TextFrameRec):
                yield rec

    @property
    def blankets(self) -> Iterator[BlanketRec]:
        for rec in self.records:
            if isinstance(rec, BlanketRec):
                yield rec

    @property
    def sheet_level_parameters(self) -> Iterator[ParameterRec]:
        """RECORD=41 parameters with no owner (sheet-level title block data)."""
        for rec in self.records:
            if isinstance(rec, ParameterRec) and rec.owner_index == -1:
                yield rec


# ---------------------------------------------------------------------------
# load_sheet
# ---------------------------------------------------------------------------


def load_sheet(schdoc_path: str) -> SheetRecords:
    """Parse a .SchDoc file into typed records with spatial indices."""
    raw_records = read_schematic_records(schdoc_path)
    records = materialize_records(raw_records)
    children = link_children(records)

    wire_recs = [r for r in records if isinstance(r, WireRec)]
    wire_index = WireIndex(wire_recs)

    # Derive sheet name from path
    name = Path(schdoc_path).stem

    return SheetRecords(
        records=records,
        children=children,
        wire_index=wire_index,
        name=name,
    )


# ---------------------------------------------------------------------------
# resolve_nets — wire connectivity → coord-to-net-name
# ---------------------------------------------------------------------------


def resolve_nets(
    sheet: SheetRecords,
    extra_named_coords: dict[tuple[int, int], str] | None = None,
) -> tuple[dict[tuple[int, int], str], set[tuple[int, int]]]:
    """Build a coordinate → net name map from one sheet's typed records.

    Returns ``(coord_to_net, nc_wire_coords)`` where *nc_wire_coords* is
    the set of all wire points reachable from a no-connect marker through
    wire connectivity.

    Same algorithm as the original ``_resolve_sheet_nets`` in netlist.py
    but uses typed records and WireIndex for efficient spatial queries.
    """
    uf: UnionFind[tuple[int, int]] = UnionFind()

    # --- Step 1: Collect wire segments and union consecutive points ---
    all_wire_points: set[tuple[int, int]] = set()

    for wire in sheet.wires:
        all_wire_points.update(wire.points)
        for p1, p2 in wire.segments:
            uf.union(p1, p2)

    # --- Step 2: T-junction detection ---
    # Check every wire endpoint against the wire index.
    for pt in list(all_wire_points):
        touches = sheet.wire_index.segments_touching(pt[0], pt[1])
        for wire, seg_idx in touches:
            seg = wire.segments[seg_idx]
            # Skip if pt is an endpoint of this segment
            if pt == seg[0] or pt == seg[1]:
                continue
            uf.union(pt, seg[0])
            break  # Only need to connect to one segment

    # --- Step 3: Add junctions (explicit connection markers) ---
    for junc in sheet.junctions:
        jp = junc.location
        touches = sheet.wire_index.segments_touching(jp[0], jp[1])
        for wire, seg_idx in touches:
            uf.union(jp, wire.segments[seg_idx][0])
            break
        all_wire_points.add(jp)

    # --- Step 3.5: Connect no-connect markers to wire groups ---
    # NC markers are placed at wire endpoints; unioning them here lets us
    # later identify which pin coordinates share a wire group with an NC.
    for nc in sheet.no_connects:
        nc_loc = nc.location
        touches = sheet.wire_index.segments_touching(nc_loc[0], nc_loc[1])
        for wire, seg_idx in touches:
            uf.union(nc_loc, wire.segments[seg_idx][0])
            break
        all_wire_points.add(nc_loc)

    # --- Step 4: Connect net labels, power ports, ports to wire groups ---
    all_named_points: set[tuple[int, int]] = set()
    group_names: dict[tuple[int, int], str] = {}

    # Net labels
    label_groups: dict[str, list[tuple[int, int]]] = {}
    for label in sheet.net_labels:
        if not label.text:
            continue
        lp = label.location
        all_named_points.add(lp)
        touches = sheet.wire_index.segments_touching(lp[0], lp[1])
        for wire, seg_idx in touches:
            uf.union(lp, wire.segments[seg_idx][0])
            break
        root = uf.find(lp)
        group_names[root] = label.text
        label_groups.setdefault(label.text, []).append(lp)

    # Same-name net labels on the same sheet merge their groups
    for _name, points in label_groups.items():
        if len(points) > 1:
            for p in points[1:]:
                uf.union(points[0], p)

    # Power ports
    for pp in sheet.power_ports:
        if not pp.text:
            continue
        loc = pp.location
        all_named_points.add(loc)
        touches = sheet.wire_index.segments_touching(loc[0], loc[1])
        for wire, seg_idx in touches:
            uf.union(loc, wire.segments[seg_idx][0])
            break
        root = uf.find(loc)
        group_names[root] = pp.text

    # Ports (skip harness-type — those connect to signal harness wires)
    for port in sheet.ports:
        if port.harness_type or not port.name:
            continue
        loc = _port_wire_coord(port, sheet.wire_index)
        all_named_points.add(loc)
        touches = sheet.wire_index.segments_touching(loc[0], loc[1])
        for wire, seg_idx in touches:
            uf.union(loc, wire.segments[seg_idx][0])
            break
        root = uf.find(loc)
        group_names[root] = port.name

    # --- Step 4.6: Sheet entries as net name sources (low priority) ---
    # Sheet entries that touch a wire group contribute their name only if
    # no net label, power port, or port already names the group.  This
    # ensures wire groups connecting only sheet entries (e.g. ADC1_IN3
    # and SLIDE_POS wired together on the Top Level page) get named.
    for entry in sheet.sheet_entries:
        if entry.harness_type or not entry.name:
            continue
        ep = entry.coord
        all_named_points.add(ep)
        touches = sheet.wire_index.segments_touching(ep[0], ep[1])
        for wire, seg_idx in touches:
            uf.union(ep, wire.segments[seg_idx][0])
            break
        root = uf.find(ep)
        if root not in group_names:
            group_names[root] = entry.name

    # --- Step 4.5: Fallback names for extra coordinates (lowest priority) ---
    if extra_named_coords:
        for (ex, ey), ename in extra_named_coords.items():
            ep = (ex, ey)
            all_named_points.add(ep)
            touches = sheet.wire_index.segments_touching(ex, ey)
            for wire, seg_idx in touches:
                uf.union(ep, wire.segments[seg_idx][0])
                break
            root = uf.find(ep)
            if root not in group_names:
                group_names[root] = ename

    # --- Step 5: Rebuild group_names after all unions ---
    final_names: dict[tuple[int, int], str] = {}
    for root, name in group_names.items():
        final_root = uf.find(root)
        final_names[final_root] = name

    # --- Step 5.5: Auto-name remaining unnamed wire groups ---
    # Altium auto-names all wire groups; we assign synthetic names to any
    # group that has wire points but no net label/port/power port/sheet entry.
    named_roots: set[tuple[int, int]] = set(final_names.keys())
    auto_id = 0
    for pt in sorted(all_wire_points):
        root = uf.find(pt)
        if root not in named_roots:
            named_roots.add(root)
            final_names[root] = f"__auto_{sheet.name}_{auto_id}"
            auto_id += 1

    # --- Step 6: Build coord → net name for all relevant points ---
    coord_to_net: dict[tuple[int, int], str] = {}
    for pt in all_wire_points | all_named_points:
        root = uf.find(pt)
        if root in final_names:
            coord_to_net[pt] = final_names[root]

    # --- Step 7: Compute no-connect wire group coordinates ---
    # NC markers propagate through wire groups: any pin on the same wire
    # group as an NC marker should be flagged as intentionally unconnected.
    nc_wire_coords: set[tuple[int, int]] = set()
    nc_roots: set[tuple[int, int]] = set()
    for nc in sheet.no_connects:
        nc_roots.add(uf.find(nc.location))
    if nc_roots:
        for pt in all_wire_points | all_named_points:
            if uf.find(pt) in nc_roots:
                nc_wire_coords.add(pt)

    return coord_to_net, nc_wire_coords


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------


def _parse_harness_groups(
    sheet: SheetRecords,
) -> list[tuple[str, str, list[tuple[str, tuple[int, int]]]]]:
    """Parse harness connectors into (harness_type, port_name, [(member, coord)]).

    Works with typed records from the Additional stream (215-218).

    Each harness connector is matched to its specific port by tracing
    signal harness wire connectivity rather than relying solely on the
    harness type string.  This handles the case where multiple ports
    share the same harness type (e.g. two I2C ports on one page).
    """
    # Index harness connectors by their record index (for OwnerIndex lookup)
    connectors: dict[int, HarnessConnectorRec] = {}
    entries_by_owner: dict[int, list[HarnessEntryRec]] = {}
    types_by_owner: dict[int, str] = {}

    # Additional stream records use their position among Additional records
    # for OwnerIndex, not their position in the full record list. We need
    # to re-index them relative to the Additional stream.
    additional_records: list[AltiumRecord] = []
    for rec in sheet.records:
        if isinstance(
            rec,
            (HarnessConnectorRec, HarnessEntryRec, HarnessTypeRec, SignalHarnessRec),
        ):
            additional_records.append(rec)

    for ai, rec in enumerate(additional_records):
        if isinstance(rec, HarnessConnectorRec):
            connectors[ai] = rec
        elif isinstance(rec, HarnessEntryRec):
            entries_by_owner.setdefault(rec.owner_index, []).append(rec)
        elif isinstance(rec, HarnessTypeRec):
            types_by_owner[rec.owner_index] = rec.text

    # --- Match each connector to its port via signal harness wires ---
    # Build union-find over signal harness wire endpoints so we can trace
    # which connector is spatially connected to which port.
    uf: UnionFind[tuple[int, int]] = UnionFind()
    harness_wire_segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for sh in sheet.signal_harnesses:
        for seg in sh.segments:
            uf.union(seg[0], seg[1])
            harness_wire_segments.append(seg)

    # Connect each connector's wire-side edge to signal harness wires.
    # Entries sit on one side of the connector box; the signal harness
    # wire attaches on the opposite side.
    connector_wire_point: dict[int, tuple[int, int]] = {}
    for ai, conn in connectors.items():
        conn_entries = entries_by_owner.get(ai, [])
        if not conn_entries:
            continue
        entry_side = conn_entries[0].side
        # Compute a representative point on the wire-side edge
        # Entries on left → wire connects on right edge; otherwise left edge
        wire_x = conn.location[0] + conn.x_size if entry_side == 0 else conn.location[0]
        # Mid-y of the connector
        wire_y = conn.location[1] - conn.y_size // 2
        wire_pt = (wire_x, wire_y)
        connector_wire_point[ai] = wire_pt

        # Connect to any signal harness segment touching this edge
        for seg in harness_wire_segments:
            if point_on_segment(
                wire_pt[0],
                wire_pt[1],
                seg[0][0],
                seg[0][1],
                seg[1][0],
                seg[1][1],
            ):
                uf.union(wire_pt, seg[0])
                break
        else:
            # Mid-y didn't land on a segment; try each segment endpoint
            # that shares the wire-side x coordinate and falls within
            # the connector's y range.
            cy_top = conn.location[1]
            cy_bot = conn.location[1] - conn.y_size
            for seg in harness_wire_segments:
                for pt in (seg[0], seg[1]):
                    if pt[0] == wire_x and cy_bot <= pt[1] <= cy_top:
                        uf.union(wire_pt, pt)
                        break

    # Connect each harness port location to signal harness wires
    harness_ports: list[PortRec] = [p for p in sheet.ports if p.harness_type]
    for port in harness_ports:
        for seg in harness_wire_segments:
            if point_on_segment(
                port.location[0],
                port.location[1],
                seg[0][0],
                seg[0][1],
                seg[1][0],
                seg[1][1],
            ):
                uf.union(port.location, seg[0])
                break

    # Map each connector to its port by finding which port shares the
    # same union-find group.
    port_name_for_connector: dict[int, str] = {}
    for ai in connectors:
        wire_pt = connector_wire_point.get(ai)
        if wire_pt is None:
            continue
        for port in harness_ports:
            if uf.find(wire_pt) == uf.find(port.location):
                port_name_for_connector[ai] = port.name
                break

    # Fallback: map harness_type -> port_name for connectors that couldn't
    # be matched spatially (e.g. no signal harness wires on this page).
    port_names_by_type: dict[str, str] = {}
    for port in harness_ports:
        port_names_by_type[port.harness_type] = port.name

    result: list[tuple[str, str, list[tuple[str, tuple[int, int]]]]] = []
    for ai, conn in connectors.items():
        harness_type = types_by_owner.get(ai, "")
        if not harness_type:
            continue
        port_name = port_name_for_connector.get(
            ai,
            port_names_by_type.get(harness_type, harness_type),
        )

        members: list[tuple[str, tuple[int, int]]] = []
        for entry in entries_by_owner.get(ai, []):
            if not entry.name:
                continue
            # Compute coord from parent connector
            coord = compute_entry_coord(
                conn.location,
                conn.x_size,
                entry.side,
                entry.distance_from_top,
                conn.y_size,
            )
            members.append((entry.name, coord))

        if members:
            result.append((harness_type, port_name, members))

    return result


def compute_harness_entry_coords(
    sheet: SheetRecords,
) -> dict[tuple[int, int], str]:
    """Compute wire-side coordinates for harness entries.

    Returns (x, y) → synthetic_net_name (``portName:memberName``).
    """
    result: dict[tuple[int, int], str] = {}
    for _ht, port_name, members in _parse_harness_groups(sheet):
        for member_name, coord in members:
            result[coord] = f"{port_name}:{member_name}"
    return result


def collect_harness_type_members(
    sheet: SheetRecords,
    members_by_type: dict[str, list[str]],
) -> None:
    """Collect harness member names from harness connectors on this page."""
    for harness_type, _port_name, members in _parse_harness_groups(sheet):
        if harness_type not in members_by_type:
            members_by_type[harness_type] = [m[0] for m in members]


def collect_harness_port_nets(
    sheet: SheetRecords,
    coord_to_net_name: dict[tuple[int, int], str],
    harness_port_nets: dict[str, list[tuple[str, dict[str, str]]]],
    page_name: str,
) -> None:
    """Collect harness port → [(page_name, {member → net_name}), ...]."""
    for _ht, port_name, members in _parse_harness_groups(sheet):
        nets: dict[str, str] = {}
        for member_name, coord in members:
            net_name = coord_to_net_name.get(coord)
            if net_name:
                nets[member_name] = net_name
        if nets:
            harness_port_nets.setdefault(port_name, []).append((page_name, nets))


# ---------------------------------------------------------------------------
# build_page — construct domain model from typed records
# ---------------------------------------------------------------------------


def _collect_sheet_entry_ports(
    sheet: SheetRecords,
    page: Page,
    nets_by_name: dict[str, Net],
    coord_to_net_name: dict[tuple[int, int], str],
) -> None:
    """Add Port objects for non-harness sheet entries on this page."""
    for entry in sheet.sheet_entries:
        if entry.harness_type:
            continue
        if not entry.name:
            continue
        net_name = coord_to_net_name.get(entry.coord)
        if net_name and net_name in nets_by_name:
            port_meta: dict[str, str] = {}
            if entry.io_type:
                port_meta["io_type"] = _IO_TYPE_NAMES.get(
                    entry.io_type,
                    str(entry.io_type),
                )
            if entry.has_overline:
                port_meta["active_low"] = "true"
            port = Port(
                name=entry.name,
                page=page,
                net=nets_by_name[net_name],
                metadata=port_meta,
            )
            page.ports.append(port)


def _collect_harness_member_ports(
    sheet: SheetRecords,
    page: Page,
    nets_by_name: dict[str, Net],
    coord_to_net_name: dict[tuple[int, int], str],
) -> None:
    """Add ports for each harness connector entry member on child pages."""
    for _ht, port_name, members in _parse_harness_groups(sheet):
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
    sheet: SheetRecords,
    page: Page,
    nets_by_name: dict[str, Net],
    harness_port_nets: dict[str, list[tuple[str, dict[str, str]]]],
    harness_members_by_type: dict[str, list[str]],
) -> None:
    """Create bridge ports for harness sheet entries connected by signal harness
    wires."""
    # Build signal harness wire connectivity
    uf: UnionFind[tuple[int, int]] = UnionFind()
    harness_wire_segments: list[tuple[tuple[int, int], tuple[int, int]]] = []

    for sh in sheet.signal_harnesses:
        for seg in sh.segments:
            uf.union(seg[0], seg[1])
            harness_wire_segments.append(seg)

    if not harness_wire_segments:
        return

    # Map sheet symbol key → child page name from FileNameRec
    child_page_for_symbol: dict[int, str] = {}
    for fn in sheet.file_names:
        if fn.owner_index >= 0 and fn.text.lower().endswith(".schdoc"):
            child_page_for_symbol[fn.owner_index] = fn.text[: -len(".SchDoc")]

    # Find harness-type sheet entries and compute their coordinates
    harness_entries: list[tuple[str, str, str, tuple[int, int]]] = []
    for entry in sheet.sheet_entries:
        if not entry.harness_type or not entry.name:
            continue

        # Find parent sheet symbol
        parent_key = entry.owner_index
        child_page = child_page_for_symbol.get(parent_key, "")

        # Connect to signal harness wire
        for seg in harness_wire_segments:
            if point_on_segment(
                entry.coord[0],
                entry.coord[1],
                seg[0][0],
                seg[0][1],
                seg[1][0],
                seg[1][1],
            ):
                uf.union(entry.coord, seg[0])
                break

        harness_entries.append((entry.name, entry.harness_type, child_page, entry.coord))

    # Group connected entries
    groups: dict[tuple[int, int], list[tuple[str, str, str]]] = {}
    for entry_name, ht, child_page, coord in harness_entries:
        root = uf.find(coord)
        groups.setdefault(root, []).append((entry_name, ht, child_page))

    # For each group with 2+ entries, create bridge ports
    for group in groups.values():
        if len(group) < 2:
            continue

        ht = group[0][1]
        members = harness_members_by_type.get(ht, [])
        if not members:
            continue

        # Look up port-net mapping, disambiguating by child page
        entry_nets: dict[tuple[str, str], dict[str, str]] = {}
        for entry_name, _, child_page in group:
            port_net_list = harness_port_nets.get(entry_name, [])
            for page_name, nets in port_net_list:
                if page_name == child_page:
                    entry_nets[(entry_name, child_page)] = nets
                    break
            else:
                if len(port_net_list) == 1:
                    entry_nets[(entry_name, child_page)] = port_net_list[0][1]

        # Pick entry with real net names as canonical
        canonical_nets: dict[str, str] | None = None
        for entry_name, _, child_page in group:
            nets = entry_nets.get((entry_name, child_page))
            if nets:
                has_real = any(":" not in n for n in nets.values())
                if has_real and canonical_nets is None:
                    canonical_nets = nets

        if canonical_nets is None:
            for entry_name, _, child_page in group:
                nets = entry_nets.get((entry_name, child_page))
                if nets:
                    canonical_nets = nets
                    break

        if canonical_nets is None:
            continue

        # Create nets with canonical names and bridge ports for all entries
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


def _find_footprint(
    comp_key: int,
    children: dict[int, list[AltiumRecord]],
) -> str:
    """Walk Component → ImplementationList → Implementation to find footprint."""
    for child in children.get(comp_key, []):
        if child.record_type == RecordType.IMPLEMENTATION_LIST:
            impl_list_key = child.index - 1
            for impl_child in children.get(impl_list_key, []):
                if isinstance(impl_child, ImplementationRec) and impl_child.model_name:
                    return impl_child.model_name
    return ""


def _resolve_net_metadata(
    sheet: SheetRecords,
    coord_to_net_name: dict[tuple[int, int], str],
    nets_by_name: dict[str, Net],
) -> None:
    """Attach ParameterSet metadata to the nets they sit on."""
    params_by_owner: dict[int, dict[str, str]] = {}
    for param in sheet.parameters:
        if param.owner_index >= 0 and param.name and param.text and param.text != "*":
            params_by_owner.setdefault(param.owner_index, {})[param.name] = param.text

    for pset in sheet.parameter_sets:
        x, y = pset.location
        touches = sheet.wire_index.segments_touching(x, y)
        if not touches:
            continue
        # Find the net at this point
        wire, seg_idx = touches[0]
        seg = wire.segments[seg_idx]
        net_name = coord_to_net_name.get(seg[0])
        if not net_name:
            net_name = coord_to_net_name.get(seg[1])
        if not net_name:
            continue
        net = nets_by_name.get(net_name)
        if net is None:
            continue
        # Attach child RECORD=41 params to net metadata
        pset_key = pset.index - 1
        pset_params = params_by_owner.get(pset_key, {})
        net.metadata.update(pset_params)


def build_page(
    sheet: SheetRecords,
    coord_to_net_name: dict[tuple[int, int], str],
    harness_port_nets: dict[str, list[tuple[str, dict[str, str]]]],
    harness_members_by_type: dict[str, list[str]],
    nc_wire_coords: set[tuple[int, int]] | None = None,
) -> Page:
    """Build a domain model Page from a sheet's typed records."""
    page = Page(name=sheet.name)

    # --- Page metadata from RECORD=31 (sheet properties) ---
    sr = sheet.sheet_rec
    if sr is not None:
        if sr.use_custom_sheet:
            page.metadata["SheetSize"] = f"Custom ({sr.custom_x}x{sr.custom_y})"
        else:
            style_name = _SHEET_STYLE_NAMES.get(sr.sheet_style, str(sr.sheet_style))
            page.metadata["SheetSize"] = style_name
        if sr.template_file_name:
            page.metadata["TemplateFile"] = sr.template_file_name

    # --- Page metadata from sheet-level RECORD=41 parameters ---
    for param in sheet.sheet_level_parameters:
        if param.name and param.text and param.text != "*":
            page.metadata[param.name] = param.text

    # --- Text annotations from RECORD=28 text frames ---
    # Text frames carry revision notes, design rationale, and change history.
    for frame in sheet.text_frames:
        text = frame.text.replace("~1", "\n").strip()
        if text:
            page.annotations.append(text)

    # Build Net objects
    nets_by_name: dict[str, Net] = {}
    for nname in sorted(set(coord_to_net_name.values())):
        net = Net(name=nname)
        nets_by_name[nname] = net
        page.nets.append(net)

    # Mark active-low nets (those whose name came from an overline source)
    for label in sheet.net_labels:
        if label.has_overline and label.text in nets_by_name:
            nets_by_name[label.text].metadata["active_low"] = "true"
    for pp in sheet.power_ports:
        if pp.has_overline and pp.text in nets_by_name:
            nets_by_name[pp.text].metadata["active_low"] = "true"
    for port_rec in sheet.ports:
        if port_rec.has_overline and not port_rec.harness_type and port_rec.name:
            wire_coord = _port_wire_coord(port_rec, sheet.wire_index)
            net_name = coord_to_net_name.get(wire_coord)
            if net_name and net_name in nets_by_name:
                nets_by_name[net_name].metadata["active_low"] = "true"

    # No-connect marker coordinates — expanded through wire groups so
    # that NC markers at wire endpoints propagate to connected pins.
    nc_coords: set[tuple[int, int]] = set()
    for nc in sheet.no_connects:
        nc_coords.add(nc.location)
    if nc_wire_coords:
        nc_coords |= nc_wire_coords

    # Index components by OwnerIndex-compatible key (index - 1)
    comp_record_keys: dict[int, ComponentRec] = {}
    for comp_rec in sheet.components:
        comp_record_keys[comp_rec.index - 1] = comp_rec

    # Designator text keyed by OwnerIndex
    designator_by_owner: dict[int, str] = {}
    for desig in sheet.designators:
        if desig.owner_index >= 0:
            designator_by_owner[desig.owner_index] = desig.text

    # PinRec keyed by (OwnerIndex, Designator).
    # Filter by display mode: components can have alternate visual variants
    # (e.g. Normal + Small) with separate pin records per variant.  Only
    # pins matching the owning component's active DisplayMode are kept.
    pin_rec_by_key: dict[tuple[int, str], PinRec] = {}
    for pin in sheet.pins:
        if pin.owner_index >= 0 and pin.designator:
            comp = comp_record_keys.get(pin.owner_index)
            if comp is not None and pin.owner_part_display_mode != comp.display_mode:
                continue
            pin_rec_by_key[(pin.owner_index, pin.designator)] = pin

    # Parameters keyed by OwnerIndex
    params_by_owner: dict[int, dict[str, str]] = {}
    for param in sheet.parameters:
        if param.owner_index >= 0 and param.name and param.text and param.text != "*":
            params_by_owner.setdefault(param.owner_index, {})[param.name] = param.text

    # Group pin records by owner index for efficient per-component lookup
    pins_by_owner: dict[int, list[PinRec]] = {}
    for key, prec in pin_rec_by_key.items():
        pins_by_owner.setdefault(key[0], []).append(prec)

    # Build Component and Pin objects directly from typed records
    for comp_owner_idx in sorted(comp_record_keys):
        comp_rec = comp_record_keys[comp_owner_idx]
        reference = designator_by_owner.get(comp_owner_idx, "")
        if not reference:
            continue

        comp = Component(
            reference=reference,
            part=comp_rec.lib_reference,
            description="",
            pages=[page],
        )

        # Apply parameters as metadata
        if comp_owner_idx in params_by_owner:
            comp.metadata.update(params_by_owner[comp_owner_idx])

        if "Description" in comp.metadata:
            comp.description = comp.metadata.pop("Description")

        # Description from ComponentRec (fallback if not from parameter)
        if not comp.description and comp_rec.description:
            comp.description = comp_rec.description

        if comp_rec.unique_id:
            comp.metadata["UniqueId"] = comp_rec.unique_id
        if comp_rec.database_table:
            comp.metadata["DatabaseTable"] = comp_rec.database_table
        if comp_rec.design_item_id:
            comp.metadata["DesignItemId"] = comp_rec.design_item_id
        if comp_rec.part_count > 2:
            comp.metadata["PartCount"] = str(comp_rec.part_count)
            comp.metadata["CurrentPartId"] = str(comp_rec.current_part_id)
        if comp_rec.display_mode_count > 1:
            comp.metadata["DisplayModeCount"] = str(comp_rec.display_mode_count)
            comp.metadata["DisplayMode"] = str(comp_rec.display_mode)
        if comp_rec.orientation:
            comp.metadata["Orientation"] = str(comp_rec.orientation)
        if comp_rec.is_mirrored:
            comp.metadata["IsMirrored"] = "True"

        # Footprint via Implementation chain
        footprint = _find_footprint(comp_owner_idx, sheet.children)
        if footprint:
            comp.metadata["Footprint"] = footprint

        # Altium's PartCount is always actual_electrical_parts + 1.
        # PartCount=2 means 1 part (every simple passive); true
        # multi-part components (e.g. dual opamp, MCU sections) have
        # PartCount > 2.
        is_multipart = comp_rec.part_count > 2

        for prec in pins_by_owner.get(comp_owner_idx, []):
            # For multi-part components, skip pins belonging to
            # other parts.  owner_part_id==0 means shared (e.g.
            # power pins), which we keep on every part.
            if (
                is_multipart
                and prec.owner_part_id != 0
                and prec.owner_part_id != comp_rec.current_part_id
            ):
                continue

            coord = prec.tip
            net_name = coord_to_net_name.get(coord)
            net = nets_by_name.get(net_name) if net_name else None
            is_nc = coord in nc_coords

            pin_meta: dict[str, str] = {}
            if prec.electrical is not None:
                pin_meta["electrical"] = _PIN_ELECTRICAL_NAMES.get(
                    prec.electrical,
                    str(prec.electrical),
                )
            if prec.has_overline:
                pin_meta["active_low"] = "true"
            if is_multipart and prec.owner_part_id:
                pin_meta["owner_part_id"] = str(prec.owner_part_id)

            pin = Pin(
                designator=prec.designator,
                name=prec.name,
                component=comp,
                net=net,
                no_connect=is_nc,
                metadata=pin_meta,
            )
            comp.pins.append(pin)
            if net is not None:
                net.pins.append(pin)

        page.components.append(comp)

    # --- Net metadata from ParameterSets (RECORD=43) ---
    _resolve_net_metadata(sheet, coord_to_net_name, nets_by_name)

    # Collect non-harness ports (RECORD=18) with io_type metadata
    for port_rec in sheet.ports:
        if port_rec.harness_type or not port_rec.name:
            continue
        wire_coord = _port_wire_coord(port_rec, sheet.wire_index)
        net_name = coord_to_net_name.get(wire_coord)
        if net_name and net_name in nets_by_name:
            port_meta: dict[str, str] = {}
            if port_rec.io_type:
                port_meta["io_type"] = _IO_TYPE_NAMES.get(
                    port_rec.io_type,
                    str(port_rec.io_type),
                )
            if port_rec.has_overline:
                port_meta["active_low"] = "true"
            port = Port(
                name=port_rec.name,
                page=page,
                net=nets_by_name[net_name],
                harness=port_rec.harness_type or None,
                metadata=port_meta,
            )
            page.ports.append(port)

    # Sheet entry ports (hierarchical bridging)
    _collect_sheet_entry_ports(sheet, page, nets_by_name, coord_to_net_name)

    # Harness member ports on child pages
    _collect_harness_member_ports(sheet, page, nets_by_name, coord_to_net_name)

    # Harness bridge ports on parent pages
    _collect_harness_bridge_ports(
        sheet,
        page,
        nets_by_name,
        harness_port_nets,
        harness_members_by_type,
    )

    return page
