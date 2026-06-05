"""Load a Project into an in-memory DuckDB database.

Orchestrates geometry construction and table population. Each table
has a private loader function that handles the domain model → SQL mapping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb
from shapely import LineString, Point

from phosphor_eda.pcb import PcbPolygon
from phosphor_eda.sql.geometry import (
    arc_center_from_three_points,
    arc_sweep_angle,
    board_outline_polygon,
    footprint_bbox_polygon,
    footprint_side,
    keepout_geometry,
    pad_polygon,
    pad_side,
    polygon_geometry,
    segment_geometry,
    trace_arc_geometry,
    via_geometry,
)
from phosphor_eda.sql.schema import create_tables, create_views

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb import Pcb, PcbLine
    from phosphor_eda.project import Project, Stackup
    from phosphor_eda.schematic import Schematic

# Net name patterns that indicate power nets
_POWER_PREFIXES = ("VCC", "VDD", "GND", "VSS", "VBUS", "V3P3", "V1P8", "V5P0")
_POWER_CHARS = ("+", "-")


def load_database(project: Project) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB with spatial extension and load all project data."""
    con = duckdb.connect(":memory:")
    create_tables(con)

    if project.pcb:
        _load_footprints(con, project.pcb)
        _load_pads(con, project.pcb)
        _load_segments(con, project.pcb)
        _load_vias(con, project.pcb)
        _load_polygons(con, project.pcb)
        _load_zones(con, project.pcb)
        _load_keepouts(con, project.pcb)
        _load_footprint_graphics(con, project.pcb)
        _load_graphic_texts(con, project.pcb)
        _load_dimensions(con, project.pcb)
        _load_layers(con, project.pcb, project.stackup)
        _load_board(con, project.pcb, project.stackup)

    _load_net_classes(con, project)
    _load_design_rules(con, project)

    if project.schematic:
        _load_components(con, project.schematic)
        _load_component_metadata(con, project.schematic)
        _load_pins(con, project.schematic)
        _load_nets(con, project.schematic, project)
        _load_pages(con, project.schematic)

    _load_project_metadata(con, project)

    create_views(con)
    return con


def _wkb(geom: BaseGeometry) -> bytes:
    """Serialize a Shapely geometry to WKB."""
    return geom.wkb


def _net_name(pcb: Pcb, net_number: int) -> str:
    """Resolve net number to name, empty string for unconnected."""
    net = pcb.nets.get(net_number)
    return net.name if net else ""


# ---------------------------------------------------------------------------
# PCB table loaders
# ---------------------------------------------------------------------------


def _load_footprints(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for fp in pcb.footprints:
        geom = footprint_bbox_polygon(fp)
        wkb_val = _wkb(geom) if geom else None
        _ = con.execute(
            "INSERT INTO footprints VALUES (?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))",
            [
                fp.reference,
                fp.footprint_lib,
                fp.x,
                fp.y,
                fp.rotation,
                footprint_side(fp),
                fp.value,
                wkb_val,
            ],
        )


def _load_pads(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for fp in pcb.footprints:
        for pad in fp.pads:
            geom = pad_polygon(pad)
            side = pad_side(pad)
            # Use the first copper layer as the representative layer
            layer = _first_copper_layer(pad.layers)
            _ = con.execute(
                """INSERT INTO pads VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))""",
                [
                    pad.footprint_ref,
                    pad.number,
                    pad.net_name,
                    pad.net_number,
                    pad.x,
                    pad.y,
                    pad.width,
                    pad.height,
                    pad.shape,
                    pad.drill,
                    side,
                    layer,
                    pad.pin_function,
                    pad.pin_type,
                    _wkb(geom),
                ],
            )


def _first_copper_layer(layers: list[str]) -> str:
    """Pick the first copper layer name from a pad's layer list."""
    normalized_layers = [str(layer) for layer in layers]
    for ly in normalized_layers:
        if "Cu" in ly or "Layer" in ly or "Top" in ly or "Bottom" in ly:
            return ly
    return normalized_layers[0] if normalized_layers else ""


def _load_segments(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    # Straight segments
    for seg in pcb.segments:
        centerline, corridor = segment_geometry(seg)
        length = centerline.length
        net_name = _net_name(pcb, seg.net_number)
        _ = con.execute(
            """INSERT INTO segments VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?), ST_GeomFromWKB(?))""",
            [
                net_name,
                seg.net_number,
                seg.layer,
                seg.width,
                seg.start_x,
                seg.start_y,
                seg.end_x,
                seg.end_y,
                False,
                None,
                None,
                None,
                length,
                _wkb(centerline),
                _wkb(corridor),
            ],
        )

    # Arc segments
    for arc in pcb.trace_arcs:
        centerline, corridor = trace_arc_geometry(arc)
        length = centerline.length
        net_name = _net_name(pcb, arc.net_number)
        cx, cy, _ = arc_center_from_three_points(
            arc.start_x, arc.start_y, arc.mid_x, arc.mid_y, arc.end_x, arc.end_y
        )
        angle = arc_sweep_angle(
            arc.start_x, arc.start_y, arc.mid_x, arc.mid_y, arc.end_x, arc.end_y, cx, cy
        )
        _ = con.execute(
            """INSERT INTO segments VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?), ST_GeomFromWKB(?))""",
            [
                net_name,
                arc.net_number,
                arc.layer,
                arc.width,
                arc.start_x,
                arc.start_y,
                arc.end_x,
                arc.end_y,
                True,
                cx,
                cy,
                angle,
                length,
                _wkb(centerline),
                _wkb(corridor),
            ],
        )


def _load_vias(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for via in pcb.vias:
        copper, drill = via_geometry(via)
        net_name = _net_name(pcb, via.net_number)
        # Determine start/end layers from via.layers list
        start_layer = str(via.layers[0]) if via.layers else ""
        end_layer = str(via.layers[-1]) if len(via.layers) > 1 else start_layer
        _ = con.execute(
            """INSERT INTO vias VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?), ST_GeomFromWKB(?))""",
            [
                net_name,
                via.net_number,
                via.x,
                via.y,
                via.size,
                via.drill,
                start_layer,
                end_layer,
                _wkb(copper),
                _wkb(drill),
            ],
        )


def _load_polygons(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for poly in pcb.polygons:
        geom = polygon_geometry(poly)
        if geom is None:
            continue
        _ = con.execute(
            "INSERT INTO polygons VALUES (?, ?, ?, ST_GeomFromWKB(?))",
            [poly.net_name, poly.net_number, poly.layer, _wkb(geom)],
        )


def _load_zones(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for zone in pcb.zones:
        # Build boundary polygon from zone.boundary points
        boundary_poly = polygon_geometry(PcbPolygon(points=zone.boundary, layer=zone.layer))
        wkb_val = _wkb(boundary_poly) if boundary_poly else None
        _ = con.execute(
            "INSERT INTO zones VALUES (?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))",
            [
                zone.net_name,
                zone.net_number,
                zone.layer,
                zone.priority,
                zone.min_thickness_mm,
                zone.thermal_gap_mm,
                zone.thermal_bridge_width_mm,
                zone.fill_type,
                wkb_val,
            ],
        )


def _load_keepouts(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for keepout in pcb.keepouts:
        geom = keepout_geometry(keepout)
        wkb_val = _wkb(geom) if geom else None
        layers = ",".join(keepout.layers)
        for layer in keepout.layers:
            _ = con.execute(
                "INSERT INTO keepouts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))",
                [
                    keepout.footprint_ref,
                    layer,
                    layers,
                    keepout.rules.tracks,
                    keepout.rules.vias,
                    keepout.rules.pads,
                    keepout.rules.copperpour,
                    keepout.rules.footprints,
                    keepout.source,
                    wkb_val,
                ],
            )


def _load_footprint_graphics(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for fp in pcb.footprints:
        _insert_graphic_lines(con, fp.reference, fp.courtyard_lines, "courtyard")
        _insert_graphic_lines(con, fp.reference, fp.silkscreen_lines, "silkscreen")
        _insert_graphic_lines(con, fp.reference, fp.fab_lines, "fab")
        for circle in fp.fab_circles:
            geom = Point(circle.cx, circle.cy).buffer(circle.radius, quad_segs=16)
            _ = con.execute(
                "INSERT INTO footprint_graphics VALUES (?, ?, ?, ST_GeomFromWKB(?))",
                [fp.reference, circle.layer, "fab", _wkb(geom)],
            )
        for poly in fp.fab_polygons:
            geom = polygon_geometry(poly)
            if geom:
                _ = con.execute(
                    "INSERT INTO footprint_graphics VALUES (?, ?, ?, ST_GeomFromWKB(?))",
                    [fp.reference, poly.layer, "fab", _wkb(geom)],
                )


def _insert_graphic_lines(
    con: duckdb.DuckDBPyConnection,
    reference: str,
    lines: list[PcbLine],
    kind: str,
) -> None:
    """Insert a set of graphic lines as LineString geometries."""
    for line in lines:
        geom = LineString([(line.start_x, line.start_y), (line.end_x, line.end_y)])
        _ = con.execute(
            "INSERT INTO footprint_graphics VALUES (?, ?, ?, ST_GeomFromWKB(?))",
            [reference, line.layer, kind, _wkb(geom)],
        )


def _load_graphic_texts(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for gt in pcb.graphic_texts:
        _ = con.execute(
            "INSERT INTO graphic_texts VALUES (?, ?, ?, ?, ?, ?, ?)",
            [gt.text, gt.x, gt.y, gt.rotation, gt.layer, gt.font_size, gt.justify],
        )


def _load_dimensions(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for dim in pcb.dimensions:
        _ = con.execute(
            "INSERT INTO dimensions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                dim.kind,
                dim.value_mm,
                dim.layer,
                dim.start_x,
                dim.start_y,
                dim.end_x,
                dim.end_y,
                dim.text,
            ],
        )


def _load_layers(con: duckdb.DuckDBPyConnection, pcb: Pcb, stackup: Stackup | None) -> None:
    # Build stackup position map (name → position index)
    stackup_map: dict[str, int] = {}
    if stackup:
        for i, layer in enumerate(stackup.layers, start=1):
            stackup_map[layer.name] = i

    # Insert stackup layers first (these have physical properties)
    if stackup:
        for i, sl in enumerate(stackup.layers, start=1):
            # Find matching PCB layer for function/side info
            pcb_layer = pcb.layer_for(sl.name)
            func = pcb_layer.function.value if pcb_layer else sl.layer_type
            side = pcb_layer.side if pcb_layer else sl.side
            number = pcb_layer.number if pcb_layer else None
            _ = con.execute(
                "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    i,
                    sl.name,
                    func,
                    side,
                    number,
                    sl.thickness_mm if sl.thickness_mm else None,
                    sl.material or None,
                    sl.epsilon_r if sl.epsilon_r else None,
                    sl.loss_tangent if sl.loss_tangent else None,
                    sl.layer_type,
                    sl.copper_orientation or None,
                ],
            )

    # Insert non-stackup PCB layers (silkscreen, mask, etc.)
    for pl in pcb.layers:
        if pl.name in stackup_map:
            continue  # Already inserted from stackup
        _ = con.execute(
            "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                None,
                pl.name,
                pl.function.value,
                pl.side,
                pl.number,
                None,
                None,
                None,
                None,
                None,
                None,
            ],
        )


def _load_board(con: duckdb.DuckDBPyConnection, pcb: Pcb, stackup: Stackup | None) -> None:
    outline = board_outline_polygon(pcb.outline_lines, pcb.outline_arcs)
    wkb_val = _wkb(outline) if outline else None
    total_thickness = stackup.total_thickness_mm if stackup else None
    copper_finish = stackup.copper_finish if stackup else None
    # Count copper layers from stackup
    layer_count = 0
    if stackup:
        layer_count = sum(1 for ly in stackup.layers if ly.layer_type == "copper")
    _ = con.execute(
        "INSERT INTO board VALUES (?, ?, ?, ?, ?, ST_GeomFromWKB(?))",
        [pcb.name, total_thickness, copper_finish, None, layer_count, wkb_val],
    )


# ---------------------------------------------------------------------------
# Design rules & net classes
# ---------------------------------------------------------------------------


def _load_net_classes(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    for nc in project.net_classes:
        _ = con.execute(
            "INSERT INTO net_classes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                nc.name,
                nc.kind,
                nc.trace_width_mm if nc.trace_width_mm else None,
                nc.clearance_mm if nc.clearance_mm else None,
                nc.via_diameter_mm if nc.via_diameter_mm else None,
                nc.via_drill_mm if nc.via_drill_mm else None,
                nc.diff_pair_width_mm if nc.diff_pair_width_mm else None,
                nc.diff_pair_gap_mm if nc.diff_pair_gap_mm else None,
            ],
        )
        for member in nc.members:
            _ = con.execute(
                "INSERT INTO net_class_members VALUES (?, ?)",
                [member, nc.name],
            )


def _load_design_rules(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    for rule in project.design_rules:
        _ = con.execute(
            "INSERT INTO design_rules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                rule.name,
                rule.kind,
                rule.enabled,
                rule.priority,
                rule.scope1 or None,
                rule.scope2 or None,
                rule.layer_scope or None,
                rule.min_value_mm,
                rule.max_value_mm,
                rule.preferred_value_mm,
            ],
        )


# ---------------------------------------------------------------------------
# Schematic table loaders
# ---------------------------------------------------------------------------


def _load_components(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    for comp in schematic.components:
        page_name = comp.pages[0].name if comp.pages else ""
        _ = con.execute(
            "INSERT INTO components VALUES (?, ?, ?, ?)",
            [comp.reference, comp.part, comp.description, page_name],
        )


def _load_component_metadata(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    for comp in schematic.components:
        for key, value in comp.metadata.items():
            _ = con.execute(
                "INSERT INTO component_metadata VALUES (?, ?, ?)",
                [comp.reference, key, value],
            )


def _load_pins(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    for comp in schematic.components:
        for pin in comp.pins:
            net_name = pin.net.name if pin.net else ""
            electrical = pin.metadata.get("electrical", "")
            _ = con.execute(
                "INSERT INTO pins VALUES (?, ?, ?, ?, ?, ?)",
                [
                    comp.reference,
                    pin.designator,
                    pin.name,
                    net_name,
                    electrical,
                    pin.no_connect,
                ],
            )


def _load_nets(con: duckdb.DuckDBPyConnection, schematic: Schematic, project: Project) -> None:
    # Build lookup maps for net class membership and diff pairs
    net_to_class: dict[str, str] = {}
    for nc in project.net_classes:
        for member in nc.members:
            net_to_class[member] = nc.name

    net_to_diff_pair: dict[str, tuple[str, str]] = {}
    for dp in project.diff_pairs:
        net_to_diff_pair[dp.positive_net] = (dp.name, "+")
        net_to_diff_pair[dp.negative_net] = (dp.name, "-")

    for net in schematic.nets:
        is_power = _is_power_net(net.name)
        net_class = net_to_class.get(net.name)
        dp_info = net_to_diff_pair.get(net.name)
        diff_pair = dp_info[0] if dp_info else None
        diff_pair_polarity = dp_info[1] if dp_info else None
        aliases = ",".join(sorted(net.aliases)) if net.aliases else None
        _ = con.execute(
            "INSERT INTO nets VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                net.name,
                len(net.pins),
                is_power,
                net_class,
                diff_pair,
                diff_pair_polarity,
                aliases,
            ],
        )


def _is_power_net(name: str) -> bool:
    """Heuristic: detect power/ground nets from name."""
    upper = name.upper()
    if any(upper.startswith(p) for p in _POWER_PREFIXES):
        return True
    return bool(name and name[0] in _POWER_CHARS)


def _load_pages(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    for page in schematic.pages:
        _ = con.execute(
            "INSERT INTO pages VALUES (?, ?, ?)",
            [page.name, len(page.components), len(page.nets)],
        )


def _load_project_metadata(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    meta = project.metadata
    entries = [
        ("name", project.name),
        ("revision", meta.revision),
        ("author", meta.author),
        ("date", meta.date),
        ("organization", meta.organization),
        ("format", meta.format),
    ]
    for key, value in entries:
        if value:
            _ = con.execute("INSERT INTO project VALUES (?, ?)", [key, value])
