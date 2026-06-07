"""Load a Project into an in-memory DuckDB database.

Orchestrates geometry construction and table population. Each table
has a private loader function that handles the domain model → SQL mapping.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING

import duckdb
from shapely import Point

from phosphor_eda.pcb import (
    LayerRole,
    PcbArcGeometry,
    PcbCircleGeometry,
    PcbDimensionGeometry,
    PcbGeometry,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbLayer,
    PcbLineGeometry,
    PcbPadGeometry,
    PcbPolygonGeometry,
    PcbTextGeometry,
    PcbViaGeometry,
    normalize_roles,
)
from phosphor_eda.sql.geometry import (
    arc_center_from_three_points,
    arc_sweep_angle,
    board_outline_polygon,
    closed_path_geometry,
    footprint_bbox_polygon,
    footprint_side,
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

    from phosphor_eda.pcb import Pcb
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
        _load_geometry(con, project.pcb)
        _load_footprints(con, project.pcb)
        _load_pads(con, project.pcb)
        _load_segments(con, project.pcb)
        _load_vias(con, project.pcb)
        _load_polygons(con, project.pcb)
        _load_pours(con, project.pcb)
        _load_keepouts(con, project.pcb)
        _load_footprint_graphics(con, project.pcb)
        _load_board_graphics(con, project.pcb)
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


def _geometry_side(pcb: Pcb, item: PcbGeometry) -> str:
    sides = {layer.side for name in item.layers if (layer := pcb.layer_for(name)) is not None}
    sides.discard("")
    if len(sides) == 1:
        return next(iter(sides))
    if len(sides) > 1:
        return "through"
    return ""


def _geometry_metadata_json(item: PcbGeometry) -> str:
    return json.dumps(asdict(item.metadata), separators=(",", ":"), sort_keys=True)


def _geometry_to_shape(item: PcbGeometry) -> BaseGeometry | None:
    data = item.data
    if isinstance(data, PcbPadGeometry):
        return pad_polygon(data)
    if isinstance(data, PcbViaGeometry):
        return via_geometry(data)[0]
    if isinstance(data, PcbLineGeometry):
        return segment_geometry(data)[1]
    if isinstance(data, PcbArcGeometry):
        return trace_arc_geometry(data)[1]
    if isinstance(data, PcbPolygonGeometry):
        return polygon_geometry(data)
    if isinstance(data, PcbCircleGeometry):
        geom = Point(data.cx, data.cy).buffer(data.radius, quad_segs=16)
        return geom if data.fill else geom.boundary.buffer(max(data.width, 0.01) / 2)
    if isinstance(data, PcbTextGeometry | PcbDimensionGeometry):
        return None
    return None


def _load_geometry(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for item in pcb.geometry:
        geom = _geometry_to_shape(item)
        _ = con.execute(
            """INSERT INTO geometry VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))""",
            [
                item.id,
                item.object_type.value,
                item.shape.value,
                item.display_role,
                item.primary_role.value,
                list(item.role_values),
                item.primary_layer,
                list(item.layers),
                _geometry_side(pcb, item),
                item.net_name or _net_name(pcb, item.net_number),
                item.net_number,
                item.footprint_ref,
                item.pour_id,
                item.metadata.source_format,
                item.metadata.native_type,
                item.metadata.native_kind,
                item.metadata.native_id,
                item.metadata.native_index,
                _geometry_metadata_json(item),
                _wkb(geom) if geom else None,
            ],
        )


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
    for item in pcb.geometry_by_object_type(PcbGeometryObject.PAD):
        if not isinstance(item.data, PcbPadGeometry):
            continue
        pad = item.data
        geom = pad_polygon(pad)
        _ = con.execute(
            """INSERT INTO pads VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))""",
            [
                item.footprint_ref,
                pad.number,
                item.net_name or _net_name(pcb, item.net_number),
                item.net_number,
                pad.x,
                pad.y,
                pad.width,
                pad.height,
                pad.shape,
                pad.drill,
                pad_side(item.layers),
                _first_copper_layer(item.layers),
                pad.pin_function,
                pad.pin_type,
                pad.mask_aperture_width,
                pad.mask_aperture_height,
                pad.mask_aperture_source or None,
                _wkb(geom),
            ],
        )


def _first_copper_layer(layers: tuple[str, ...]) -> str:
    """Pick the first copper layer name from a pad's layer list."""
    normalized_layers = [str(layer) for layer in layers]
    for ly in normalized_layers:
        if "Cu" in ly or "Layer" in ly or "Top" in ly or "Bottom" in ly:
            return ly
    return normalized_layers[0] if normalized_layers else ""


def _load_segments(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for item in pcb.geometry_by_object_type(PcbGeometryObject.TRACK):
        if isinstance(item.data, PcbLineGeometry):
            segment = item.data
            centerline, corridor = segment_geometry(segment)
            is_arc = False
            cx = cy = angle = None
        elif isinstance(item.data, PcbArcGeometry):
            segment = item.data
            centerline, corridor = trace_arc_geometry(segment)
            cx, cy, _radius = arc_center_from_three_points(
                segment.start_x,
                segment.start_y,
                segment.mid_x,
                segment.mid_y,
                segment.end_x,
                segment.end_y,
            )
            angle = arc_sweep_angle(
                segment.start_x,
                segment.start_y,
                segment.mid_x,
                segment.mid_y,
                segment.end_x,
                segment.end_y,
                cx,
                cy,
            )
            is_arc = True
        else:
            continue
        _ = con.execute(
            """INSERT INTO segments VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?), ST_GeomFromWKB(?))""",
            [
                item.net_name or _net_name(pcb, item.net_number),
                item.net_number,
                item.primary_layer,
                segment.width,
                segment.start_x,
                segment.start_y,
                segment.end_x,
                segment.end_y,
                is_arc,
                cx,
                cy,
                angle,
                centerline.length,
                _wkb(centerline),
                _wkb(corridor),
            ],
        )


def _load_vias(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for item in pcb.geometry_by_object_type(PcbGeometryObject.VIA):
        if not isinstance(item.data, PcbViaGeometry):
            continue
        via = item.data
        copper, drill = via_geometry(via)
        start_layer = item.layers[0] if item.layers else ""
        end_layer = item.layers[-1] if len(item.layers) > 1 else start_layer
        _ = con.execute(
            """INSERT INTO vias VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?), ST_GeomFromWKB(?))""",
            [
                item.net_name or _net_name(pcb, item.net_number),
                item.net_number,
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
    for item in pcb.geometry_by_shape(PcbGeometryShape.POLYGON):
        if not isinstance(item.data, PcbPolygonGeometry):
            continue
        geom = polygon_geometry(item.data)
        if geom is None:
            continue
        _ = con.execute(
            "INSERT INTO polygons VALUES (?, ?, ?, ST_GeomFromWKB(?))",
            [
                item.net_name or _net_name(pcb, item.net_number),
                item.net_number,
                item.primary_layer,
                _wkb(geom),
            ],
        )


def _load_pours(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for pour in pcb.pours:
        boundary = closed_path_geometry(pour.boundary)
        _ = con.execute(
            """INSERT INTO pours VALUES
            (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ST_GeomFromWKB(?)
            )""",
            [
                pour.id,
                pour.name,
                pour.net_name or _net_name(pcb, pour.net_number),
                pour.net_number,
                pour.layers[0] if pour.layers else "",
                list(pour.layers),
                pour.priority,
                pour.settings.fill_mode.value,
                pour.settings.hatch_style,
                pour.settings.grid_mm,
                pour.settings.track_width_mm,
                pour.settings.min_thickness_mm,
                pour.settings.thermal_gap_mm,
                pour.settings.thermal_bridge_width_mm,
                pour.settings.connect_pads_clearance_mm,
                list(pour.fill_geometry_ids),
                list(pour.cutout_geometry_ids),
                pour.footprint_ref,
                pour.metadata.source_format,
                pour.metadata.native_type,
                pour.metadata.native_kind,
                pour.metadata.native_id,
                pour.metadata.native_index,
                json.dumps(asdict(pour.metadata), separators=(",", ":"), sort_keys=True),
                _wkb(boundary) if boundary else None,
            ],
        )


def _load_keepouts(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for keepout in pcb.keepouts:
        boundary = closed_path_geometry(keepout.boundary)
        _ = con.execute(
            """INSERT INTO keepouts VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))""",
            [
                keepout.id,
                keepout.name,
                keepout.footprint_ref,
                keepout.layers[0] if keepout.layers else "",
                list(keepout.layers),
                keepout.rules.tracks.value,
                keepout.rules.vias.value,
                keepout.rules.pads.value,
                keepout.rules.copper_pours.value,
                keepout.rules.footprints.value,
                keepout.metadata.source_format,
                keepout.metadata.native_type,
                keepout.metadata.native_kind,
                keepout.metadata.native_id,
                keepout.metadata.native_index,
                json.dumps(asdict(keepout.metadata), separators=(",", ":"), sort_keys=True),
                _wkb(boundary) if boundary else None,
            ],
        )


def _load_footprint_graphics(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for item in pcb.geometry_by_object_type(PcbGeometryObject.GRAPHIC):
        if not item.footprint_ref:
            continue
        geom = _geometry_to_shape(item)
        if geom is None:
            continue
        _ = con.execute(
            "INSERT INTO footprint_graphics VALUES (?, ?, ?, ST_GeomFromWKB(?))",
            [item.footprint_ref, item.primary_layer, item.display_role, _wkb(geom)],
        )


def _load_board_graphics(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for item in pcb.geometry_by_object_type(PcbGeometryObject.GRAPHIC):
        if item.footprint_ref:
            continue
        if item.has_role(PcbGeometryRole.BOARD_OUTLINE):
            continue
        geom = _geometry_to_shape(item)
        if geom is None:
            continue
        _ = con.execute(
            "INSERT INTO board_graphics VALUES (?, ?, ?, ST_GeomFromWKB(?))",
            ["", item.primary_layer, item.shape.value, _wkb(geom)],
        )


def _load_graphic_texts(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for item in pcb.geometry_by_object_type(PcbGeometryObject.TEXT):
        if item.footprint_ref:
            continue
        if not isinstance(item.data, PcbTextGeometry):
            continue
        gt = item.data
        _ = con.execute(
            "INSERT INTO graphic_texts VALUES (?, ?, ?, ?, ?, ?, ?)",
            [gt.text, gt.x, gt.y, gt.rotation, item.primary_layer, gt.font_size, gt.justify],
        )


def _load_dimensions(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for item in pcb.geometry_by_object_type(PcbGeometryObject.DIMENSION):
        if not isinstance(item.data, PcbDimensionGeometry):
            continue
        dim = item.data
        _ = con.execute(
            "INSERT INTO dimensions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                dim.kind,
                dim.value_mm,
                item.primary_layer,
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
            # Find matching PCB layer for normalized role/side info
            pcb_layer = pcb.layer_for(sl.name)
            layer_info = pcb_layer or _stackup_layer_as_pcb_layer(sl.layer_type, sl.side)
            _ = con.execute(
                "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    i,
                    sl.name,
                    layer_info.primary_role.value,
                    list(layer_info.role_values),
                    layer_info.side,
                    pcb_layer.number if pcb_layer else None,
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
            "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                None,
                pl.name,
                pl.primary_role.value,
                list(pl.role_values),
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


def _stackup_layer_as_pcb_layer(layer_type: str, side: str) -> PcbLayer:
    roles: list[LayerRole] = []
    if layer_type == "copper":
        roles.append(LayerRole.COPPER)
    elif layer_type in {"core", "prepreg", "dielectric"}:
        roles.append(LayerRole.DIELECTRIC)
    elif layer_type == "solder_mask":
        roles.append(LayerRole.SOLDER_MASK)
    else:
        roles.append(LayerRole.UNKNOWN)
    if side == "front":
        roles.append(LayerRole.FRONT)
    elif side == "back":
        roles.append(LayerRole.BACK)
    elif side == "inner":
        roles.append(LayerRole.INNER)
    return PcbLayer(name="", roles=normalize_roles(*roles))


def _load_board(con: duckdb.DuckDBPyConnection, pcb: Pcb, stackup: Stackup | None) -> None:
    outline = board_outline_polygon(pcb.board_profile_geometry())
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
