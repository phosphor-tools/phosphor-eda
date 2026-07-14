"""High-level sheet loading and net resolution using typed records.

Replaces the raw-dict iteration in ``netlist.py`` and the inner loop of
``to_schematic.py`` with a structured pipeline:

1. ``load_sheet()`` — parse + materialize + link + index
2. ``resolve_local_net_groups()`` — wire connectivity → Altium source local nets
3. ``resolve_nets()`` — legacy coordinate → generated net-name map
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from phosphor_eda.formats.altium._helpers import parse_bus_notation
from phosphor_eda.formats.altium.record_factory import (
    compute_entry_coord,
    link_children,
    materialize_records,
)
from phosphor_eda.formats.altium.record_parser import read_schematic_records
from phosphor_eda.formats.altium.records import (
    AltiumRecord,
    BusEntryRec,
    BusRec,
    HarnessConnectorRec,
    HarnessEntryRec,
    HarnessTypeRec,
    JunctionRec,
    NetLabelRec,
    NoConnectRec,
    ParameterRec,
    PortRec,
    PowerPortRec,
    SheetEntryRec,
    SheetRec,
    SignalHarnessRec,
    WireRec,
)
from phosphor_eda.formats.altium.wire_index import WireIndex
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.spatial import UnionFind, point_on_segment

if TYPE_CHECKING:
    from collections.abc import Iterator


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
    bus_index: WireIndex = field(default_factory=lambda: WireIndex([]))
    name: str = ""

    def by_type[R: AltiumRecord](self, cls: type[R]) -> Iterator[R]:
        """Yield every record that is an instance of *cls*, narrowed to ``R``."""
        for rec in self.records:
            if isinstance(rec, cls):
                yield rec

    @property
    def sheet_rec(self) -> SheetRec | None:
        """Return the single RECORD=31 sheet properties record, if present."""
        return next(self.by_type(SheetRec), None)

    @property
    def sheet_level_parameters(self) -> Iterator[ParameterRec]:
        """RECORD=41 parameters with no owner (sheet-level title block data)."""
        for rec in self.by_type(ParameterRec):
            if rec.owner_index == -1:
                yield rec


@dataclass(slots=True)
class LocalNetRecordGroup:
    """One Altium sheet-local connectivity group before public net resolution."""

    root: tuple[int, int]
    wire_points: set[tuple[int, int]]
    named_points: set[tuple[int, int]]
    net_labels: list[NetLabelRec]
    power_ports: list[PowerPortRec]
    ports: list[tuple[PortRec, tuple[int, int]]]
    sheet_entries: list[SheetEntryRec]
    extra_named_coords: dict[tuple[int, int], str]
    generated_name: str


@dataclass(slots=True)
class GenericBusRecordGroup:
    """One generic Altium bus backbone with aggregate name evidence."""

    root: tuple[int, int]
    name: str
    source_index: int
    location: tuple[int, int]
    member_roots_by_name: dict[str, tuple[int, int]]


@dataclass(slots=True)
class LocalNetResolution:
    """Sheet-local net grouping plus coordinate lookup evidence."""

    groups: list[LocalNetRecordGroup]
    coord_to_root: dict[tuple[int, int], tuple[int, int]]
    no_connect_wire_coords: set[tuple[int, int]]
    generic_bus_groups: list[GenericBusRecordGroup]


# ---------------------------------------------------------------------------
# load_sheet
# ---------------------------------------------------------------------------


def load_sheet(
    schdoc_path: str,
    ctx: ParseContext | None = None,
) -> SheetRecords:
    """Parse a .SchDoc file into typed records with spatial indices."""
    if ctx is None:
        ctx = ParseContext()
    raw_records = read_schematic_records(schdoc_path)
    records = materialize_records(raw_records, ctx=ctx)
    children = link_children(records)

    wire_recs = [r for r in records if isinstance(r, WireRec)]
    bus_recs = [r for r in records if isinstance(r, BusRec)]
    wire_index = WireIndex(wire_recs)
    bus_index = WireIndex(bus_recs)

    # Derive sheet name from path
    name = Path(schdoc_path).stem

    return SheetRecords(
        records=records,
        children=children,
        wire_index=wire_index,
        bus_index=bus_index,
        name=name,
    )


# ---------------------------------------------------------------------------
# resolve_local_net_groups — wire connectivity before public net resolution
# ---------------------------------------------------------------------------


def _connect_point_to_wire_group(
    point: tuple[int, int],
    sheet: SheetRecords,
    uf: UnionFind[tuple[int, int]],
) -> None:
    touches = sheet.wire_index.segments_touching(point[0], point[1])
    for wire, seg_idx in touches:
        uf.union(point, wire.segments[seg_idx][0])
        break


def _segment_start_touching(
    point: tuple[int, int],
    index: WireIndex,
) -> tuple[int, int] | None:
    touches = index.segments_touching(point[0], point[1])
    for wire, seg_idx in touches:
        return wire.segments[seg_idx][0]
    return None


def _connect_point_to_signal_harness(
    point: tuple[int, int],
    harness_segments: list[tuple[tuple[int, int], tuple[int, int]]],
    uf: UnionFind[tuple[int, int]],
) -> None:
    for seg in harness_segments:
        if point_on_segment(point, seg[0], seg[1]):
            uf.union(point, seg[0])
            break


def _harness_port_ends(port: PortRec) -> tuple[tuple[int, int], tuple[int, int]]:
    """Both candidate wire-attachment points of a port shape.

    Altium stores ``location`` as one corner; the wire can attach there or
    at the opposite end of the shape (right for horizontal styles 0-3,
    above for vertical styles 4-7).
    """
    base = port.location
    alt = (base[0], base[1] + port.width) if port.style >= 4 else (base[0] + port.width, base[1])
    return base, alt


def _first_generated_name(group: LocalNetRecordGroup, sheet_name: str, ordinal: int) -> str:
    for label in group.net_labels:
        if label.text:
            return label.text
    for power_port in group.power_ports:
        if power_port.text:
            return power_port.text
    for port, _coord in group.ports:
        if port.name and not port.harness_type and parse_bus_notation(port.name) is None:
            return port.name
    for entry in group.sheet_entries:
        if entry.name and not entry.harness_type and parse_bus_notation(entry.name) is None:
            return entry.name
    for _coord, name in group.extra_named_coords.items():
        if name:
            return name
    return f"__auto_{sheet_name}_{ordinal}"


def resolve_local_net_groups(
    sheet: SheetRecords,
    extra_named_coords: dict[tuple[int, int], str] | None = None,
) -> LocalNetResolution:
    """Build Altium-native sheet-local connectivity groups.

    The returned groups preserve the distinct source record categories that
    will later drive Altium project-level resolution. Group IDs are assigned
    by the source extractor, not here.
    """
    uf: UnionFind[tuple[int, int]] = UnionFind()
    bus_uf: UnionFind[tuple[int, int]] = UnionFind()

    # --- Step 1: Collect wire segments and union consecutive points ---
    all_wire_points: set[tuple[int, int]] = set()
    all_bus_points: set[tuple[int, int]] = set()

    for wire in sheet.by_type(WireRec):
        all_wire_points.update(wire.points)
        for p1, p2 in wire.segments:
            uf.union(p1, p2)

    for bus in sheet.by_type(BusRec):
        all_bus_points.update(bus.points)
        for p1, p2 in bus.segments:
            bus_uf.union(p1, p2)

    # --- Step 1.5: Signal harness wires ---
    # Signal harnesses carry whole harness bundles between harness-typed
    # sheet entries and ports. Union their segments so those endpoints
    # share a local net; pins never attach to them, so this cannot merge
    # ordinary signal nets.
    harness_segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for signal_harness in sheet.by_type(SignalHarnessRec):
        for p1, p2 in signal_harness.segments:
            uf.union(p1, p2)
            harness_segments.append((p1, p2))
    # T-junctions between signal harness wires: an endpoint of one wire
    # landing mid-segment on another.
    for seg_a in harness_segments:
        for pt in seg_a:
            for seg_b in harness_segments:
                if pt == seg_b[0] or pt == seg_b[1]:
                    continue
                if point_on_segment(pt, seg_b[0], seg_b[1]):
                    uf.union(pt, seg_b[0])

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

    for pt in list(all_bus_points):
        touches = sheet.bus_index.segments_touching(pt[0], pt[1])
        for bus, seg_idx in touches:
            seg = bus.segments[seg_idx]
            if pt == seg[0] or pt == seg[1]:
                continue
            bus_uf.union(pt, seg[0])
            break

    # --- Step 3: Add junctions (explicit connection markers) ---
    for junc in sheet.by_type(JunctionRec):
        jp = junc.location
        touches = sheet.wire_index.segments_touching(jp[0], jp[1])
        for wire, seg_idx in touches:
            uf.union(jp, wire.segments[seg_idx][0])
            break
        all_wire_points.add(jp)

    # --- Step 3.5: Connect no-connect markers to wire groups ---
    # NC markers are placed at wire endpoints; unioning them here lets us
    # later identify which pin coordinates share a wire group with an NC.
    for nc in sheet.by_type(NoConnectRec):
        nc_loc = nc.location
        _connect_point_to_wire_group(nc_loc, sheet, uf)
        all_wire_points.add(nc_loc)

    # --- Step 4: Connect net identifiers to wire groups ---
    all_named_points: set[tuple[int, int]] = set()
    label_points: list[tuple[NetLabelRec, tuple[int, int]]] = []
    power_port_points: list[tuple[PowerPortRec, tuple[int, int]]] = []
    port_points: list[tuple[PortRec, tuple[int, int]]] = []
    sheet_entry_points: list[tuple[SheetEntryRec, tuple[int, int]]] = []
    extra_points: dict[tuple[int, int], str] = {}
    bus_label_points: list[tuple[NetLabelRec, tuple[int, int]]] = []
    bus_entry_points: list[tuple[tuple[int, int], tuple[int, int]]] = []

    # Net labels
    label_groups: dict[tuple[str, bool], list[tuple[int, int]]] = {}
    for label in sheet.by_type(NetLabelRec):
        if not label.text:
            continue
        lp = label.location
        bus_segment_start = _segment_start_touching(lp, sheet.bus_index)
        if bus_segment_start is not None and parse_bus_notation(label.text) is not None:
            bus_uf.union(lp, bus_segment_start)
            all_bus_points.add(lp)
            bus_label_points.append((label, lp))
            continue
        all_named_points.add(lp)
        _connect_point_to_wire_group(lp, sheet, uf)
        label_points.append((label, lp))
        label_groups.setdefault((label.text, label.has_overline), []).append(lp)

    # Same-name net labels on the same sheet merge their groups
    for _name, points in label_groups.items():
        if len(points) > 1:
            for p in points[1:]:
                uf.union(points[0], p)

    # Power ports
    for pp in sheet.by_type(PowerPortRec):
        if not pp.text:
            continue
        loc = pp.location
        all_named_points.add(loc)
        _connect_point_to_wire_group(loc, sheet, uf)
        power_port_points.append((pp, loc))

    # Ports.
    for port in sheet.by_type(PortRec):
        if not port.name:
            continue
        loc = _port_wire_coord(port, sheet.wire_index)
        all_named_points.add(loc)
        _connect_point_to_wire_group(loc, sheet, uf)
        if port.harness_type:
            # Harness ports attach to signal harness wires at either end
            # of the port shape.
            for probe in _harness_port_ends(port):
                uf.union(loc, probe)
                _connect_point_to_signal_harness(probe, harness_segments, uf)
        port_points.append((port, loc))

    # Sheet entries.
    for entry in sheet.by_type(SheetEntryRec):
        if not entry.name:
            continue
        ep = entry.coord
        all_named_points.add(ep)
        _connect_point_to_wire_group(ep, sheet, uf)
        if entry.harness_type:
            _connect_point_to_signal_harness(ep, harness_segments, uf)
        sheet_entry_points.append((entry, ep))

    # Fallback source coordinates, primarily harness connector entries.
    if extra_named_coords:
        for (ex, ey), ename in extra_named_coords.items():
            ep = (ex, ey)
            all_named_points.add(ep)
            _connect_point_to_wire_group(ep, sheet, uf)
            extra_points[ep] = ename

    # Generic bus entries connect one endpoint to a bus backbone and the
    # other endpoint to a scalar member wire. Keep that as bus membership
    # evidence; do not union the bus backbone into scalar connectivity.
    for entry in sheet.by_type(BusEntryRec):
        endpoints = (entry.location, entry.corner)
        first_bus_start = _segment_start_touching(endpoints[0], sheet.bus_index)
        second_bus_start = _segment_start_touching(endpoints[1], sheet.bus_index)
        if first_bus_start is not None and second_bus_start is None:
            bus_point, member_point = endpoints[0], endpoints[1]
            bus_uf.union(bus_point, first_bus_start)
        elif second_bus_start is not None and first_bus_start is None:
            bus_point, member_point = endpoints[1], endpoints[0]
            bus_uf.union(bus_point, second_bus_start)
        else:
            continue
        _connect_point_to_wire_group(member_point, sheet, uf)
        all_bus_points.add(bus_point)
        all_wire_points.add(member_point)
        bus_entry_points.append((bus_point, member_point))

    # --- Step 5: Build group records after all unions ---
    coord_to_root = {pt: uf.find(pt) for pt in all_wire_points | all_named_points}
    groups_by_root: dict[tuple[int, int], LocalNetRecordGroup] = {}
    for root in sorted(set(coord_to_root.values())):
        groups_by_root[root] = LocalNetRecordGroup(
            root=root,
            wire_points=set(),
            named_points=set(),
            net_labels=[],
            power_ports=[],
            ports=[],
            sheet_entries=[],
            extra_named_coords={},
            generated_name="",
        )

    for point in all_wire_points:
        groups_by_root[uf.find(point)].wire_points.add(point)
    for point in all_named_points:
        groups_by_root[uf.find(point)].named_points.add(point)
    for label, point in label_points:
        groups_by_root[uf.find(point)].net_labels.append(label)
    for power_port, point in power_port_points:
        groups_by_root[uf.find(point)].power_ports.append(power_port)
    for port, point in port_points:
        groups_by_root[uf.find(point)].ports.append((port, point))
    for entry, point in sheet_entry_points:
        groups_by_root[uf.find(point)].sheet_entries.append(entry)
    for point, name in extra_points.items():
        groups_by_root[uf.find(point)].extra_named_coords[point] = name

    # --- Step 7: Compute no-connect wire group coordinates ---
    # NC markers propagate through wire groups: any pin on the same wire
    # group as an NC marker should be flagged as intentionally unconnected.
    nc_wire_coords: set[tuple[int, int]] = set()
    nc_roots: set[tuple[int, int]] = set()
    for nc in sheet.by_type(NoConnectRec):
        nc_roots.add(uf.find(nc.location))
    if nc_roots:
        for pt in all_wire_points | all_named_points:
            if uf.find(pt) in nc_roots:
                nc_wire_coords.add(pt)

    groups = list(groups_by_root.values())
    for ordinal, group in enumerate(groups):
        group.generated_name = _first_generated_name(group, sheet.name, ordinal)

    bus_entries_by_root: dict[tuple[int, int], list[tuple[tuple[int, int], tuple[int, int]]]] = {}
    for bus_point, member_point in bus_entry_points:
        bus_root = bus_uf.find(bus_point)
        bus_entries_by_root.setdefault(bus_root, []).append((bus_point, member_point))

    generic_bus_groups: list[GenericBusRecordGroup] = []
    for label, point in bus_label_points:
        member_names = parse_bus_notation(label.text)
        if not member_names:
            continue
        bus_root = bus_uf.find(point)
        entries = sorted(
            bus_entries_by_root.get(bus_root, []),
            key=lambda item: (item[0][1], item[0][0], item[1][1], item[1][0]),
        )
        member_roots_by_name: dict[str, tuple[int, int]] = {}
        for member_name, (_bus_point, member_point) in zip(member_names, entries, strict=False):
            member_root = coord_to_root.get(member_point)
            if member_root is None:
                continue
            _ = member_roots_by_name.setdefault(member_name, member_root)
        generic_bus_groups.append(
            GenericBusRecordGroup(
                root=bus_root,
                name=label.text,
                source_index=label.index,
                location=label.location,
                member_roots_by_name=member_roots_by_name,
            )
        )

    return LocalNetResolution(
        groups=groups,
        coord_to_root=coord_to_root,
        no_connect_wire_coords=nc_wire_coords,
        generic_bus_groups=generic_bus_groups,
    )


def resolve_nets(
    sheet: SheetRecords,
    extra_named_coords: dict[tuple[int, int], str] | None = None,
) -> tuple[dict[tuple[int, int], str], set[tuple[int, int]]]:
    """Build the legacy coordinate → generated net name map for one sheet."""
    resolution = resolve_local_net_groups(sheet, extra_named_coords=extra_named_coords)
    name_by_root = {group.root: group.generated_name for group in resolution.groups}
    coord_to_net = {
        coord: name_by_root[root]
        for coord, root in resolution.coord_to_root.items()
        if root in name_by_root
    }
    return coord_to_net, resolution.no_connect_wire_coords


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HarnessGroup:
    """A harness connector, its matched harness port, and its member entries.

    ``port_name`` is the harness port the connector feeds (empty when no port
    matches). ``members`` pair each harness entry with its wire-side
    coordinate on the connector box.
    """

    connector: HarnessConnectorRec
    harness_type: str
    port_name: str
    members: list[tuple[HarnessEntryRec, tuple[int, int]]]


def parse_harness_groups(sheet: SheetRecords) -> list[HarnessGroup]:
    """Parse harness connectors into :class:`HarnessGroup` entries.

    Works with typed records from the Additional stream (215-218). Owner
    indices in that stream are relative to the Additional records, so the
    records are re-indexed here rather than relying on ``link_children``.

    Each harness connector is matched to its specific port by tracing
    signal harness wire connectivity rather than relying solely on the
    harness type string.  This handles the case where multiple ports
    share the same harness type (e.g. two I2C ports on one page).
    """
    # Connectors and their owned entry/type records, keyed by the connector's
    # position among the harness records below.
    connectors: dict[int, HarnessConnectorRec] = {}
    entries_by_owner: dict[int, list[HarnessEntryRec]] = {}
    types_by_owner: dict[int, str] = {}

    # Entries/types are grouped to their connector by document-order contiguity:
    # a connector owns the harness entry/type records that follow it until the
    # next connector, matching Altium's serialization (connector, then its
    # entries, then its type). This is used in preference to the records'
    # OwnerIndex, which counts Additional-stream records the harness subset omits
    # (a stream header, blankets) and so only aligns when nothing else is
    # interspersed — a connector preceded by a blanket would otherwise lose all
    # its entries. Signal-harness *wires* are read separately (by_type) for port
    # matching below, so they are not collected here.
    harness_records = [
        rec
        for rec in sheet.records
        if isinstance(rec, (HarnessConnectorRec, HarnessEntryRec, HarnessTypeRec))
    ]
    current_ai: int | None = None
    for ai, rec in enumerate(harness_records):
        if isinstance(rec, HarnessConnectorRec):
            connectors[ai] = rec
            current_ai = ai
        elif current_ai is None:
            # A well-formed stream never places an entry/type before its
            # connector; any that appear are from a malformed/reordered stream
            # and are skipped rather than misattributed.
            continue
        elif isinstance(rec, HarnessEntryRec):
            entries_by_owner.setdefault(current_ai, []).append(rec)
        else:  # HarnessTypeRec — the only remaining harness record type
            types_by_owner[current_ai] = rec.text

    # --- Match each connector to its port via signal harness wires ---
    # Build union-find over signal harness wire endpoints so we can trace
    # which connector is spatially connected to which port.
    uf: UnionFind[tuple[int, int]] = UnionFind()
    harness_wire_segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for sh in sheet.by_type(SignalHarnessRec):
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
            if point_on_segment(wire_pt, seg[0], seg[1]):
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

    # Connect each harness port to signal harness wires; the wire can
    # attach at either end of the port shape.
    harness_ports: list[PortRec] = [p for p in sheet.by_type(PortRec) if p.harness_type]
    for port in harness_ports:
        for probe in _harness_port_ends(port):
            seg = next(
                (s for s in harness_wire_segments if point_on_segment(probe, s[0], s[1])),
                None,
            )
            if seg is not None:
                uf.union(port.location, probe)
                uf.union(probe, seg[0])
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
    # Only unambiguous types qualify — with several same-type ports the
    # choice would be arbitrary and could name the wrong interface.
    port_names_by_type: dict[str, str] = {}
    ambiguous_types: set[str] = set()
    for port in harness_ports:
        if port.harness_type in port_names_by_type:
            ambiguous_types.add(port.harness_type)
        port_names_by_type[port.harness_type] = port.name
    for harness_type in ambiguous_types:
        del port_names_by_type[harness_type]

    result: list[HarnessGroup] = []
    for ai, conn in connectors.items():
        harness_type = types_by_owner.get(ai, "")
        port_name = port_name_for_connector.get(
            ai,
            port_names_by_type.get(harness_type, ""),
        )

        members: list[tuple[HarnessEntryRec, tuple[int, int]]] = []
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
            members.append((entry, coord))

        result.append(
            HarnessGroup(
                connector=conn,
                harness_type=harness_type,
                port_name=port_name,
                members=members,
            )
        )

    return result


def compute_harness_entry_coords(
    sheet: SheetRecords,
) -> dict[tuple[int, int], str]:
    """Compute wire-side coordinates for harness entries.

    Returns (x, y) → synthetic_net_name (``portName:memberName``).
    """
    result: dict[tuple[int, int], str] = {}
    for group in parse_harness_groups(sheet):
        if not group.harness_type:
            continue
        prefix = group.port_name or group.harness_type
        for entry, coord in group.members:
            result[coord] = f"{prefix}:{entry.name}"
    return result
