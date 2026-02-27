"""Net resolution for Altium schematics.

Altium resolves nets by:
1. Building connected wire groups (wires sharing endpoints or via T-junctions)
2. Naming groups from net labels and power ports
3. Merging groups that share the same net label name on the same sheet
4. Assigning pins to groups when pin coordinates match wire/label/port coordinates
"""

from ecad_tools.altium.record_parser import read_schematic_records
from ecad_tools.models import NetlistEntry, PageNetEntry, ParsedDesign


class _UnionFind:
    """Union-Find for grouping connected wire segments."""

    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, p: tuple[int, int]) -> tuple[int, int]:
        if p not in self._parent:
            self._parent[p] = p
        while self._parent[p] != p:
            self._parent[p] = self._parent[self._parent[p]]
            p = self._parent[p]
        return p

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _point_on_segment(
    px: int, py: int, x1: int, y1: int, x2: int, y2: int
) -> bool:
    """Check if point (px,py) lies on the axis-aligned segment (x1,y1)-(x2,y2)."""
    if y1 == y2 == py:
        return min(x1, x2) <= px <= max(x1, x2)
    if x1 == x2 == px:
        return min(y1, y2) <= py <= max(y1, y2)
    return False


def _resolve_sheet_nets(
    records: list[dict[str, str]],
    extra_named_coords: dict[tuple[int, int], str] | None = None,
) -> dict[tuple[int, int], str]:
    """Build a coordinate -> net name map from one sheet's records.

    Returns a dict mapping (x, y) coordinates to their net name.

    extra_named_coords: optional fallback names for coordinates that should be
    named even if no net label/power port/port is present. These have the
    LOWEST priority — they only name a wire group if nothing else does.
    """
    uf = _UnionFind()

    # --- Step 1: Collect wire segments and union consecutive points ---
    wire_segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    all_wire_points: set[tuple[int, int]] = set()

    for rec in records:
        if rec.get("RECORD") != "27":
            continue
        loc_count = int(rec.get("LocationCount", "2"))
        points = []
        for i in range(1, loc_count + 1):
            x = int(rec.get(f"X{i}", "0"))
            y = int(rec.get(f"Y{i}", "0"))
            points.append((x, y))
        all_wire_points.update(points)
        for j in range(len(points) - 1):
            uf.union(points[j], points[j + 1])
            wire_segments.append((points[j], points[j + 1]))

    # --- Step 2: T-junction detection ---
    # Check every wire endpoint against every wire segment. If an endpoint
    # lands on another segment (not just at its endpoints), union them.
    # This implements Altium's "Compiler-Generated Junction" rule.
    for pt in list(all_wire_points):
        for (x1, y1), (x2, y2) in wire_segments:
            # Skip if pt is already an endpoint of this segment
            if pt == (x1, y1) or pt == (x2, y2):
                continue
            if _point_on_segment(pt[0], pt[1], x1, y1, x2, y2):
                uf.union(pt, (x1, y1))
                break  # Only need to connect to one segment

    # --- Step 3: Add junctions (explicit connection markers) ---
    for rec in records:
        if rec.get("RECORD") == "29":
            jx = int(rec.get("Location.X", "0"))
            jy = int(rec.get("Location.Y", "0"))
            jp = (jx, jy)
            # Union junction with any wire segment it touches
            for (x1, y1), (x2, y2) in wire_segments:
                if _point_on_segment(jx, jy, x1, y1, x2, y2):
                    uf.union(jp, (x1, y1))
                    break
            all_wire_points.add(jp)

    # --- Step 4: Connect net labels and power ports to wire groups ---
    # Also collect coordinates that should be in the output map even
    # if not on a wire (for direct pin-to-label/port connections).
    all_named_points: set[tuple[int, int]] = set()
    group_names: dict[tuple[int, int], str] = {}

    # Collect net labels, connecting them to wire segments
    label_groups: dict[str, list[tuple[int, int]]] = {}
    for rec in records:
        if rec.get("RECORD") != "25":
            continue
        lx = int(rec.get("Location.X", "0"))
        ly = int(rec.get("Location.Y", "0"))
        name = rec.get("Text", "")
        if not name:
            continue
        lp = (lx, ly)
        all_named_points.add(lp)
        # Try to connect label to a wire segment
        for (x1, y1), (x2, y2) in wire_segments:
            if _point_on_segment(lx, ly, x1, y1, x2, y2):
                uf.union(lp, (x1, y1))
                break
        root = uf.find(lp)
        group_names[root] = name
        label_groups.setdefault(name, []).append(lp)

    # Same-name net labels on the same sheet merge their groups
    for name, points in label_groups.items():
        if len(points) > 1:
            for p in points[1:]:
                uf.union(points[0], p)

    # Connect power ports to wire segments
    for rec in records:
        if rec.get("RECORD") != "17":
            continue
        px = int(rec.get("Location.X", "0"))
        py = int(rec.get("Location.Y", "0"))
        name = rec.get("Text", "")
        if not name:
            continue
        pp = (px, py)
        all_named_points.add(pp)
        for (x1, y1), (x2, y2) in wire_segments:
            if _point_on_segment(px, py, x1, y1, x2, y2):
                uf.union(pp, (x1, y1))
                break
        root = uf.find(pp)
        group_names[root] = name

    # Connect ports to wire segments (skip harness-type ports — those connect
    # to signal harness wires, not regular wires, and are expanded separately)
    for rec in records:
        if rec.get("RECORD") != "18":
            continue
        if rec.get("HarnessType"):
            continue
        px = int(rec.get("Location.X", "0"))
        py = int(rec.get("Location.Y", "0"))
        name = rec.get("Name", "")
        if not name:
            continue
        pp = (px, py)
        all_named_points.add(pp)
        for (x1, y1), (x2, y2) in wire_segments:
            if _point_on_segment(px, py, x1, y1, x2, y2):
                uf.union(pp, (x1, y1))
                break
        root = uf.find(pp)
        group_names[root] = name

    # --- Step 4.5: Fallback names for extra coordinates (lowest priority) ---
    # Used for harness entry coordinates that name otherwise-unnamed wire groups.
    if extra_named_coords:
        for (ex, ey), ename in extra_named_coords.items():
            ep = (ex, ey)
            all_named_points.add(ep)
            for (x1, y1), (x2, y2) in wire_segments:
                if _point_on_segment(ex, ey, x1, y1, x2, y2):
                    uf.union(ep, (x1, y1))
                    break
            root = uf.find(ep)
            if root not in group_names:
                group_names[root] = ename

    # --- Step 5: Rebuild group_names after all unions ---
    # Re-resolve roots since unions in step 4 may have changed them
    final_names: dict[tuple[int, int], str] = {}
    for root, name in group_names.items():
        final_root = uf.find(root)
        # If multiple names map to same root, last wins (power ports
        # and net labels shouldn't conflict on the same wire group)
        final_names[final_root] = name

    # --- Step 6: Build coord -> net name for all relevant points ---
    coord_to_net: dict[tuple[int, int], str] = {}
    for pt in all_wire_points | all_named_points:
        root = uf.find(pt)
        if root in final_names:
            coord_to_net[pt] = final_names[root]

    return coord_to_net


def build_netlist(design: ParsedDesign) -> dict[str, list[NetlistEntry]]:
    """Build a netlist from an Altium ParsedDesign.

    For each page, resolves wire connectivity using the raw records,
    then matches pin coordinates to named wire groups.
    """
    netlist: dict[str, list[NetlistEntry]] = {}

    for page in design.pages:
        schdoc_path = getattr(page, "_schdoc_path", None)
        if schdoc_path is None:
            continue

        records = read_schematic_records(str(schdoc_path))
        coord_to_net = _resolve_sheet_nets(records)

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
