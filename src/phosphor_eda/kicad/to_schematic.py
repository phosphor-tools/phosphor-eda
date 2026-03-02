"""Convert a KiCad .kicad_sch schematic to the domain model.

Parses the S-expression file directly using sexpdata, extracting
embedded lib_symbols for pin definitions and computing wire
connectivity via union-find — same approach as the Altium parser.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import sexpdata

from ecad_tools.schematic import Component, Design, Net, Page, Pin, Port, merge_pages

# KiCad overline: ~{TEXT} means TEXT with overline bar.
# Bare ~ means "no name" (unnamed pin).
_OVERLINE_RE = re.compile(r"~\{([^}]+)\}")


# ---------------------------------------------------------------------------
# S-expression helpers
# ---------------------------------------------------------------------------


def _tag(item: list | object) -> str | None:
    """Return the tag name of an S-expression list, or None."""
    if isinstance(item, list) and item and isinstance(item[0], sexpdata.Symbol):
        return item[0].value()
    return None


def _find(items: list, tag_name: str) -> list | None:
    """Find the first child with the given tag."""
    for item in items:
        if _tag(item) == tag_name:
            return item
    return None


def _find_all(items: list, tag_name: str) -> list[list]:
    """Find all children with the given tag."""
    return [item for item in items if _tag(item) == tag_name]


def _val(item: list) -> str:
    """Return the string value of item[1]."""
    if len(item) > 1:
        v = item[1]
        return v.value() if isinstance(v, sexpdata.Symbol) else str(v)
    return ""


def _property(items: list, name: str) -> str:
    """Get a named property value from S-expression children."""
    for item in items:
        if _tag(item) == "property" and len(item) > 2 and str(item[1]) == name:
            return str(item[2])
    return ""


# ---------------------------------------------------------------------------
# Pin electrical type mapping
# ---------------------------------------------------------------------------

def _strip_kicad_markup(name: str) -> str:
    """Strip KiCad text markup from a name.

    - ``~{TEXT}`` → ``TEXT`` (overline notation)
    - Bare ``~`` → ``""`` (unnamed pin placeholder)
    """
    if not name or name == "~":
        return ""
    return _OVERLINE_RE.sub(r"\1", name)


_ELECTRICAL_MAP = {
    "input": "input",
    "output": "output",
    "bidirectional": "IO",
    "tri_state": "hi-Z",
    "passive": "passive",
    "free": "unspecified",
    "unspecified": "unspecified",
    "power_in": "power",
    "power_out": "power",
    "open_collector": "open-collector",
    "open_emitter": "open-emitter",
    "no_connect": "no-connect",
}


# ---------------------------------------------------------------------------
# Lib symbol pin extraction
# ---------------------------------------------------------------------------


def _parse_lib_symbols(
    lib_syms: list,
) -> tuple[
    dict[str, list[tuple[str, str, str, float, float]]],
    dict[str, str],
]:
    """Parse embedded lib_symbols into pin definitions and descriptions.

    Returns:
        (pins_by_lib_id, descriptions_by_lib_id)
        pins: {lib_id: [(pin_number, pin_name, electrical_type, x, y), ...]}
              where x, y are in library coordinates (Y-up).
        descriptions: {lib_id: description_text}
    """
    pins_result: dict[str, list[tuple[str, str, str, float, float]]] = {}
    desc_result: dict[str, str] = {}
    for sym in lib_syms[1:]:
        if _tag(sym) != "symbol":
            continue
        lib_id = str(sym[1])
        desc = _property(sym[2:], "ki_description")
        if desc:
            desc_result[lib_id] = desc
        pins: list[tuple[str, str, str, float, float]] = []
        # Pins live in sub-symbol units (e.g., "RP2040_0_1", "RP2040_1_1")
        for child in sym[2:]:
            if _tag(child) == "symbol":
                for elem in child[1:]:
                    if _tag(elem) != "pin":
                        continue
                    pin_type = str(elem[1])
                    pnum = pname = ""
                    px = py = 0.0
                    for pe in elem[3:]:
                        t = _tag(pe)
                        if t == "number":
                            pnum = _val(pe)
                        elif t == "name":
                            pname = _strip_kicad_markup(_val(pe))
                        elif t == "at":
                            px = float(pe[1])
                            py = float(pe[2])
                    pins.append((pnum, pname, pin_type, px, py))
        pins_result[lib_id] = pins
    return pins_result, desc_result


# ---------------------------------------------------------------------------
# Pin position transform
# ---------------------------------------------------------------------------


def _transform_pin(
    lib_x: float,
    lib_y: float,
    comp_x: float,
    comp_y: float,
    comp_rot: float,
    mirror: str | None = None,
) -> tuple[float, float]:
    """Transform a pin from library coordinates to schematic coordinates.

    KiCad lib symbols use Y-up; schematics use Y-down.
    """
    lx, ly = lib_x, lib_y
    if mirror == "y":
        lx = -lx
    elif mirror == "x":
        ly = -ly
    # Flip Y from lib coords (Y-up) to schematic (Y-down)
    ly = -ly
    # Rotate by component rotation (degrees, CCW in schematic)
    rad = math.radians(comp_rot)
    rx = lx * math.cos(rad) - ly * math.sin(rad)
    ry = lx * math.sin(rad) + ly * math.cos(rad)
    return round(comp_x + rx, 4), round(comp_y + ry, 4)


# ---------------------------------------------------------------------------
# Union-Find for wire connectivity
# ---------------------------------------------------------------------------


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[tuple[float, float], tuple[float, float]] = {}

    def find(self, p: tuple[float, float]) -> tuple[float, float]:
        if p not in self._parent:
            self._parent[p] = p
        while self._parent[p] != p:
            self._parent[p] = self._parent[self._parent[p]]
            p = self._parent[p]
        return p

    def union(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------


def kicad_to_design(path: Path, name: str = "") -> Design:
    """Parse a KiCad .kicad_sch file and return a Design."""
    if not name:
        name = path.stem

    with open(path) as f:
        data = sexpdata.loads(f.read())

    # Parse embedded symbol library
    lib_syms_node = _find(data[1:], "lib_symbols")
    if lib_syms_node:
        lib_pins, lib_descs = _parse_lib_symbols(lib_syms_node)
    else:
        lib_pins, lib_descs = {}, {}

    # Discover child sheets
    child_sheets = _find_all(data[1:], "sheet")
    pages: list[Page] = []
    design_meta: dict[str, str] = {}

    # Build root page
    root_page, root_meta = _build_page(data, name, lib_pins, lib_descs)
    pages.append(root_page)
    design_meta.update(root_meta)

    # Build child pages
    for sheet_node in child_sheets:
        sheet_name, sheet_file = _parse_sheet_info(sheet_node)
        child_path = path.parent / sheet_file
        if not child_path.exists():
            continue
        with open(child_path) as f:
            child_data = sexpdata.loads(f.read())
        child_lib_node = _find(child_data[1:], "lib_symbols")
        if child_lib_node:
            child_lib_pins, child_lib_descs = _parse_lib_symbols(child_lib_node)
        else:
            child_lib_pins, child_lib_descs = {}, {}
        child_page, _ = _build_page(
            child_data, sheet_name, child_lib_pins, child_lib_descs,
        )

        # Hierarchical labels in the child become Ports
        for hlabel in _find_all(child_data[1:], "hierarchical_label"):
            label_name = _strip_kicad_markup(str(hlabel[1]))
            # Find or create the net for this label
            matching_net = None
            for n in child_page.nets:
                if n.name == label_name:
                    matching_net = n
                    break
            if matching_net is None:
                matching_net = Net(name=label_name)
                child_page.nets.append(matching_net)
            child_page.ports.append(
                Port(name=label_name, page=child_page, net=matching_net)
            )

        pages.append(child_page)

        # Sheet pins on the root page become Ports
        for pin_node in _find_all(sheet_node[1:], "pin"):
            pin_name = str(pin_node[1])
            matching_net = None
            for n in root_page.nets:
                if n.name == pin_name:
                    matching_net = n
                    break
            if matching_net is None:
                matching_net = Net(name=pin_name)
                root_page.nets.append(matching_net)
            root_page.ports.append(
                Port(name=pin_name, page=root_page, net=matching_net)
            )

    return merge_pages(name, pages, metadata=design_meta)


def _parse_sheet_info(sheet_node: list) -> tuple[str, str]:
    """Extract name and filename from a sheet S-expression node."""
    sheet_name = ""
    sheet_file = ""
    for sub in sheet_node[1:]:
        if _tag(sub) == "property":
            prop_name = str(sub[1])
            prop_val = str(sub[2]) if len(sub) > 2 else ""
            if prop_name == "Sheetname":
                sheet_name = prop_val
            elif prop_name == "Sheetfile":
                sheet_file = prop_val
    return sheet_name, sheet_file


def _build_page(
    data: list,
    page_name: str,
    lib_pins: dict[str, list[tuple[str, str, str, float, float]]],
    lib_descs: dict[str, str] | None = None,
) -> tuple[Page, dict[str, str]]:
    """Build a Page from parsed S-expression data.

    Returns (page, design_metadata).
    """
    if lib_descs is None:
        lib_descs = {}
    page = Page(name=page_name)
    design_meta: dict[str, str] = {}
    nets_by_name: dict[str, Net] = {}

    # --- Title block ---
    title_block = _find(data[1:], "title_block")
    if title_block:
        for sub in title_block[1:]:
            t = _tag(sub)
            if t == "title":
                page.metadata["PageTitle"] = _val(sub)
                design_meta["Title"] = _val(sub)
            elif t == "date":
                design_meta["Date"] = _val(sub)
            elif t == "rev":
                design_meta["Revision"] = _val(sub)
            elif t == "company":
                design_meta["Organization"] = _val(sub)

    # --- Paper size ---
    paper = _find(data[1:], "paper")
    if paper:
        page.metadata["SheetSize"] = _val(paper)

    # --- Wire connectivity via union-find ---
    uf = _UnionFind()
    wire_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    wire_points: set[tuple[float, float]] = set()

    for wire_node in _find_all(data[1:], "wire"):
        pts_node = _find(wire_node[1:], "pts")
        if not pts_node:
            continue
        points = []
        for xy in _find_all(pts_node[1:], "xy"):
            points.append((round(float(xy[1]), 4), round(float(xy[2]), 4)))
        for i in range(len(points) - 1):
            uf.union(points[i], points[i + 1])
            wire_segments.append((points[i], points[i + 1]))
            wire_points.add(points[i])
            wire_points.add(points[i + 1])

    # Junctions merge wire groups that cross
    for junc in _find_all(data[1:], "junction"):
        at_node = _find(junc[1:], "at")
        if at_node:
            jp = (round(float(at_node[1]), 4), round(float(at_node[2]), 4))
            _connect_point(uf, jp, wire_segments, wire_points)

    # --- Labels assign net names to wire groups ---
    group_names: dict[tuple[float, float], str] = {}

    for label in _find_all(data[1:], "label"):
        label_name = _strip_kicad_markup(str(label[1]))
        at_node = _find(label[2:], "at")
        if not at_node:
            continue
        lp = (round(float(at_node[1]), 4), round(float(at_node[2]), 4))
        _connect_point(uf, lp, wire_segments, wire_points)
        root = uf.find(lp)
        group_names[root] = label_name

    # Global labels work the same way — their names are globally unique
    for glabel in _find_all(data[1:], "global_label"):
        label_name = _strip_kicad_markup(str(glabel[1]))
        at_node = _find(glabel[2:], "at")
        if not at_node:
            continue
        lp = (round(float(at_node[1]), 4), round(float(at_node[2]), 4))
        _connect_point(uf, lp, wire_segments, wire_points)
        root = uf.find(lp)
        group_names[root] = label_name

    # Hierarchical labels also name wire groups on their page
    for hlabel in _find_all(data[1:], "hierarchical_label"):
        label_name = _strip_kicad_markup(str(hlabel[1]))
        at_node = _find(hlabel[2:], "at")
        if not at_node:
            continue
        lp = (round(float(at_node[1]), 4), round(float(at_node[2]), 4))
        _connect_point(uf, lp, wire_segments, wire_points)
        root = uf.find(lp)
        group_names[root] = label_name

    # --- Power symbols create globally-named nets ---
    for sym_node in _find_all(data[1:], "symbol"):
        ref = _property(sym_node[1:], "Reference")
        if not ref.startswith("#PWR") and not ref.startswith("#FLG"):
            continue
        value = _property(sym_node[1:], "Value")
        if not value:
            continue
        # Get the power symbol's pin position
        lib_id_node = _find(sym_node[1:], "lib_id")
        if not lib_id_node:
            continue
        lib_id = _val(lib_id_node)
        at_node = _find(sym_node[1:], "at")
        if not at_node:
            continue
        comp_x = float(at_node[1])
        comp_y = float(at_node[2])
        comp_rot = float(at_node[3]) if len(at_node) > 3 else 0.0

        mirror = None
        mirror_node = _find(sym_node[1:], "mirror")
        if mirror_node:
            mirror = _val(mirror_node)

        # Get pin positions from lib_symbols
        sym_pins = lib_pins.get(lib_id, [])
        for _pnum, _pname, _ptype, px, py in sym_pins:
            abs_pos = _transform_pin(px, py, comp_x, comp_y, comp_rot, mirror)
            _connect_point(uf, abs_pos, wire_segments, wire_points)
            root = uf.find(abs_pos)
            # Power symbols always name their wire group
            group_names[root] = value

    # --- No-connect markers ---
    nc_positions: set[tuple[float, float]] = set()
    for nc_node in _find_all(data[1:], "no_connect"):
        at_node = _find(nc_node[1:], "at")
        if at_node:
            nc_positions.add(
                (round(float(at_node[1]), 4), round(float(at_node[2]), 4))
            )

    # --- Resolve wire group → net name mapping ---
    def _get_net(pos: tuple[float, float]) -> Net | None:
        root = uf.find(pos)
        name = group_names.get(root)
        if name is None:
            return None
        if name not in nets_by_name:
            net = Net(name=name)
            nets_by_name[name] = net
            page.nets.append(net)
        return nets_by_name[name]

    # --- Build components ---
    auto_net_id = 0

    for sym_node in _find_all(data[1:], "symbol"):
        ref = _property(sym_node[1:], "Reference")
        # Skip power symbols and power flags
        if ref.startswith("#"):
            continue

        lib_id_node = _find(sym_node[1:], "lib_id")
        lib_id = _val(lib_id_node) if lib_id_node else ""
        value = _property(sym_node[1:], "Value")
        description = lib_descs.get(lib_id, "")
        footprint = _property(sym_node[1:], "Footprint")

        # DNP (Do Not Place) — KiCad 7+
        dnp_node = _find(sym_node[1:], "dnp")
        is_dnp = dnp_node is not None and _val(dnp_node) == "yes"

        at_node = _find(sym_node[1:], "at")
        comp_x = float(at_node[1]) if at_node else 0.0
        comp_y = float(at_node[2]) if at_node else 0.0
        comp_rot = float(at_node[3]) if at_node and len(at_node) > 3 else 0.0

        mirror = None
        mirror_node = _find(sym_node[1:], "mirror")
        if mirror_node:
            mirror = _val(mirror_node)

        comp = Component(
            reference=ref,
            part=lib_id,
            description=description,
            pages=[page],
            metadata={},
        )
        if value:
            comp.metadata["Value"] = value
        if footprint:
            comp.metadata["Footprint"] = footprint
        if is_dnp:
            comp.metadata["dni"] = "yes"

        # Get pin definitions from lib_symbols
        sym_pins = lib_pins.get(lib_id, [])
        # Get pin UUIDs from the placed instance to know which pins exist
        inst_pin_uuids = {}
        for pin_node in _find_all(sym_node[1:], "pin"):
            pnum = str(pin_node[1])
            uuid_node = _find(pin_node[2:], "uuid")
            if uuid_node:
                inst_pin_uuids[pnum] = _val(uuid_node)

        for pnum, pname, ptype, px, py in sym_pins:
            # Only include pins that belong to this unit (or unit 0 = shared)
            # Pin unit is encoded in the sub-symbol name: "SymName_U_V"
            # where U=unit (0=all), V=variant. We track this via the parent
            # sub-symbol index. For simplicity, include if pin_uuid exists.
            if inst_pin_uuids and pnum not in inst_pin_uuids:
                continue

            abs_pos = _transform_pin(px, py, comp_x, comp_y, comp_rot, mirror)

            # Connect pin to wire network (pin may touch wire midpoint)
            _connect_point(uf, abs_pos, wire_segments, wire_points)

            # Look up net at pin position
            net = _get_net(abs_pos)
            if net is None and abs_pos in wire_points:
                # Pin is on a wire but no net name assigned yet — auto-name
                root = uf.find(abs_pos)
                if root not in group_names:
                    auto_name = f"__auto_{page_name}_{auto_net_id}"
                    auto_net_id += 1
                    group_names[root] = auto_name
                net = _get_net(abs_pos)

            is_nc = (
                ptype == "no_connect"
                or abs_pos in nc_positions
                or any(
                    abs(nc[0] - abs_pos[0]) < 0.01 and abs(nc[1] - abs_pos[1]) < 0.01
                    for nc in nc_positions
                )
            )

            electrical = _ELECTRICAL_MAP.get(ptype, "")
            pin_meta: dict[str, str] = {}
            if electrical and electrical != "passive":
                pin_meta["electrical"] = electrical

            pin = Pin(
                designator=pnum,
                name=pname,
                component=comp,
                net=net,
                no_connect=is_nc,
                metadata=pin_meta,
            )
            comp.pins.append(pin)
            if net is not None:
                net.pins.append(pin)

        page.components.append(comp)

    return page, design_meta


def _point_on_segment(
    point: tuple[float, float],
    seg_start: tuple[float, float],
    seg_end: tuple[float, float],
    tol: float = 0.01,
) -> bool:
    """Check if a point lies on a line segment (within tolerance).

    Only handles horizontal and vertical segments (Manhattan wires).
    """
    px, py = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    # Horizontal segment
    if abs(y1 - y2) < tol and abs(py - y1) < tol:
        lo, hi = (min(x1, x2) - tol, max(x1, x2) + tol)
        return lo <= px <= hi
    # Vertical segment
    if abs(x1 - x2) < tol and abs(px - x1) < tol:
        lo, hi = (min(y1, y2) - tol, max(y1, y2) + tol)
        return lo <= py <= hi
    return False


def _connect_point(
    uf: _UnionFind,
    point: tuple[float, float],
    wire_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    wire_points: set[tuple[float, float]],
) -> None:
    """Connect a point to the wire network.

    Checks both exact endpoint match and whether the point lies on a segment.
    """
    wire_points.add(point)
    # First try exact endpoint match
    for wp in wire_points:
        if wp != point and abs(wp[0] - point[0]) < 0.01 and abs(wp[1] - point[1]) < 0.01:
            uf.union(point, wp)
            return
    # Then check if point lies on a wire segment
    for seg_start, seg_end in wire_segments:
        if _point_on_segment(point, seg_start, seg_end):
            uf.union(point, seg_start)
            return
