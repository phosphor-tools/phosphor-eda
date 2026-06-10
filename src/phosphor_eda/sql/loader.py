"""Load a Project into an in-memory DuckDB database."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import TYPE_CHECKING

import duckdb
from shapely import LineString, Point
from shapely.affinity import rotate

from phosphor_eda.pcb import (
    LayerRole,
    PcbArc,
    PcbArtwork,
    PcbCircle,
    PcbClosedPath,
    PcbDimension,
    PcbDrill,
    PcbDrillShape,
    PcbLayer,
    PcbLine,
    PcbMetadata,
    PcbModel3D,
    PcbPad,
    PcbPolygon,
    PcbText,
    PcbVia,
    normalize_roles,
)
from phosphor_eda.sql.geometry import (
    arc_center_from_three_points,
    arc_sweep_angle,
    arc_to_polyline,
    board_outline_polygon,
    closed_path_geometry,
    footprint_bbox_polygon,
    footprint_side,
    pad_polygon,
    polygon_geometry,
    segment_geometry,
    trace_arc_geometry,
    via_geometry,
)
from phosphor_eda.sql.schema import create_tables, create_views
from phosphor_eda.text_outlines import text_outline_geometry

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb import Pcb, PcbNet
    from phosphor_eda.project import Project, Stackup
    from phosphor_eda.schematic import Schematic

_POWER_PREFIXES = ("VCC", "VDD", "GND", "VSS", "VBUS", "V3P3", "V1P8", "V5P0")
_POWER_CHARS = ("+", "-")


def load_database(project: Project) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB with spatial extension and load project data."""
    con = duckdb.connect(":memory:")
    create_tables(con)

    if project.pcb:
        _load_footprints(con, project.pcb)
        _load_pads(con, project.pcb)
        _load_vias(con, project.pcb)
        _load_drills(con, project.pcb)
        _load_conductors(con, project.pcb)
        _load_artwork(con, project.pcb)
        _load_board_profile(con, project.pcb)
        _load_pours(con, project.pcb)
        _load_keepouts(con, project.pcb)
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


def _wkb(geom: BaseGeometry | None) -> bytes | None:
    return None if geom is None or geom.is_empty else geom.wkb


def _metadata_json(metadata: PcbMetadata) -> str:
    return json.dumps(asdict(metadata), separators=(",", ":"), sort_keys=True)


def _net_fields(net: PcbNet | None) -> tuple[str | None, int | None]:
    if net is None:
        return None, None
    return net.name, net.number


def _layer_names(layers: tuple[PcbLayer, ...]) -> list[str]:
    return [layer.name for layer in layers]


def _primary_layer(layers: tuple[PcbLayer, ...]) -> str:
    return layers[0].name if layers else ""


def _pad_side(pad: PcbPad) -> str:
    sides = {layer.side for layer in pad.layers if layer.side}
    if len(sides) > 1:
        return "through"
    return next(iter(sides), "")


def _shape_geometry(payload: object) -> BaseGeometry | None:
    if isinstance(payload, PcbLine):
        return (
            segment_geometry(payload)[1]
            if payload.width > 0.0
            else LineString(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
        )
    if isinstance(payload, PcbArc):
        return (
            trace_arc_geometry(payload)[1]
            if payload.width > 0.0
            else LineString(
                arc_to_polyline(
                    payload.start_x,
                    payload.start_y,
                    payload.mid_x,
                    payload.mid_y,
                    payload.end_x,
                    payload.end_y,
                )
            )
        )
    if isinstance(payload, PcbPolygon):
        return polygon_geometry(payload)
    if isinstance(payload, PcbCircle):
        outer = Point(payload.cx, payload.cy).buffer(payload.radius, quad_segs=16)
        if payload.fill:
            return outer
        return outer.boundary.buffer(max(payload.width, 0.01) / 2.0)
    if isinstance(payload, PcbText):
        return text_outline_geometry(payload)
    if isinstance(payload, PcbDimension):
        return LineString(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
    if isinstance(payload, PcbModel3D):
        return None
    if isinstance(payload, PcbClosedPath):
        return closed_path_geometry(payload)
    return None


def _profile_shape_geometry(payload: object) -> BaseGeometry | None:
    if isinstance(payload, PcbLine):
        return LineString(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
    if isinstance(payload, PcbArc):
        return LineString(
            arc_to_polyline(
                payload.start_x,
                payload.start_y,
                payload.mid_x,
                payload.mid_y,
                payload.end_x,
                payload.end_y,
                num_points=32,
            )
        )
    return _shape_geometry(payload)


def _drill_geometry(drill: PcbDrill) -> BaseGeometry | None:
    width = drill.width if drill.width > 0.0 else drill.diameter
    height = drill.height if drill.height > 0.0 else drill.diameter
    if width <= 0.0 or height <= 0.0:
        return None
    if drill.shape != PcbDrillShape.SLOT or math.isclose(width, height):
        return Point(drill.x, drill.y).buffer(width / 2.0, quad_segs=8)
    radius = min(width, height) / 2.0
    if width > height:
        half_span = (width - height) / 2.0
        line = LineString(((drill.x - half_span, drill.y), (drill.x + half_span, drill.y)))
    else:
        half_span = (height - width) / 2.0
        line = LineString(((drill.x, drill.y - half_span), (drill.x, drill.y + half_span)))
    geometry = line.buffer(radius, quad_segs=8)
    if not math.isclose(drill.rotation % 360.0, 0.0):
        geometry = rotate(geometry, -drill.rotation, origin=(drill.x, drill.y))
    return geometry


def _drill_owner(drill: PcbDrill) -> tuple[str, str]:
    owner = drill.owner
    if isinstance(owner, PcbPad):
        return "pad", owner.id
    if isinstance(owner, PcbVia):
        return "via", owner.id
    if isinstance(owner, PcbArtwork):
        return "artwork", owner.id
    return "mechanical", ""


def _load_footprints(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for fp in pcb.footprints:
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
                _wkb(footprint_bbox_polygon(fp)),
            ],
        )


def _load_pads(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    _ = pcb
    for pad in pcb.pads:
        net_name, net_number = _net_fields(pad.net)
        _ = con.execute(
            """INSERT INTO pads VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))""",
            [
                pad.id,
                None if pad.footprint is None else pad.footprint.reference,
                pad.number,
                net_name,
                net_number,
                pad.x,
                pad.y,
                pad.width,
                pad.height,
                pad.shape,
                pad.pad_type.value,
                None if pad.drill is None else pad.drill.id,
                None if pad.drill is None else pad.drill.diameter,
                _pad_side(pad),
                _primary_layer(pad.layers),
                _layer_names(pad.layers),
                pad.pin_function,
                pad.pin_type,
                pad.mask_aperture_width,
                pad.mask_aperture_height,
                pad.mask_aperture_source or None,
                _wkb(pad_polygon(pad)),
            ],
        )


def _load_vias(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for via in pcb.vias:
        net_name, net_number = _net_fields(via.net)
        start_layer = via.layers[0].name if via.layers else ""
        end_layer = via.layers[-1].name if len(via.layers) > 1 else start_layer
        _ = con.execute(
            """INSERT INTO vias VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))""",
            [
                via.id,
                net_name,
                net_number,
                via.x,
                via.y,
                via.diameter,
                via.drill.id,
                via.via_type.value,
                start_layer,
                end_layer,
                _layer_names(via.layers),
                _wkb(via_geometry(via)[0]),
            ],
        )


def _load_drills(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for drill in pcb.drills:
        owner_kind, owner_id = _drill_owner(drill)
        _ = con.execute(
            """INSERT INTO drills VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))""",
            [
                drill.id,
                owner_kind,
                owner_id,
                drill.plating.value,
                drill.shape.value,
                drill.x,
                drill.y,
                drill.diameter,
                drill.width,
                drill.height,
                drill.rotation,
                _layer_names(drill.layers),
                _wkb(_drill_geometry(drill)),
            ],
        )


def _load_conductors(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for conductor in pcb.conductors:
        net_name, net_number = _net_fields(conductor.net)
        centerline: BaseGeometry | None = None
        geom: BaseGeometry | None = None
        width = None
        start_x = start_y = end_x = end_y = None
        is_arc = False
        arc_center_x = arc_center_y = arc_angle = None
        length = None
        data = conductor.data
        if isinstance(data, PcbLine):
            centerline, geom = segment_geometry(data)
            width = data.width
            start_x, start_y = data.start_x, data.start_y
            end_x, end_y = data.end_x, data.end_y
            length = centerline.length
        elif isinstance(data, PcbArc):
            centerline, geom = trace_arc_geometry(data)
            width = data.width
            start_x, start_y = data.start_x, data.start_y
            end_x, end_y = data.end_x, data.end_y
            arc_center_x, arc_center_y, _radius = arc_center_from_three_points(
                data.start_x,
                data.start_y,
                data.mid_x,
                data.mid_y,
                data.end_x,
                data.end_y,
            )
            arc_angle = arc_sweep_angle(
                data.start_x,
                data.start_y,
                data.mid_x,
                data.mid_y,
                data.end_x,
                data.end_y,
                arc_center_x,
                arc_center_y,
            )
            is_arc = True
            length = centerline.length
        else:
            geom = _shape_geometry(data)
            if geom is None:
                msg = f"unsupported conductor payload type {type(data).__name__}"
                raise TypeError(msg)
            length = 0.0
        _ = con.execute(
            """INSERT INTO conductors VALUES
            (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ST_GeomFromWKB(?), ST_GeomFromWKB(?)
            )""",
            [
                conductor.id,
                conductor.kind.value,
                net_name,
                net_number,
                conductor.layer.name,
                width,
                start_x,
                start_y,
                end_x,
                end_y,
                is_arc,
                arc_center_x,
                arc_center_y,
                arc_angle,
                length,
                None if conductor.footprint is None else conductor.footprint.reference,
                None if conductor.pour is None else conductor.pour.id,
                _wkb(centerline),
                _wkb(geom),
            ],
        )


def _load_artwork(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for artwork in pcb.artwork:
        text = x = y = rotation = font_size = None
        if isinstance(artwork.data, PcbText):
            text = artwork.data.text
            x = artwork.data.x
            y = artwork.data.y
            rotation = artwork.data.rotation
            font_size = artwork.data.font_size
        _ = con.execute(
            """INSERT INTO artwork VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?))""",
            [
                artwork.id,
                artwork.purpose.value,
                artwork.kind.value,
                None if artwork.footprint is None else artwork.footprint.reference,
                None if artwork.layer is None else artwork.layer.name,
                text,
                x,
                y,
                rotation,
                font_size,
                _wkb(_shape_geometry(artwork.data)),
            ],
        )


def _load_board_profile(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    if pcb.board_profile is None:
        return
    for element in pcb.board_profile.elements:
        _ = con.execute(
            "INSERT INTO board_profile VALUES (?, ?, ?, ?, ST_GeomFromWKB(?))",
            [
                element.id,
                element.kind.value,
                None if element.layer is None else element.layer.name,
                element.is_cutout,
                _wkb(_profile_shape_geometry(element.data)),
            ],
        )


def _load_pours(con: duckdb.DuckDBPyConnection, pcb: Pcb) -> None:
    for pour in pcb.pours:
        net_name, net_number = _net_fields(pour.net)
        boundary = closed_path_geometry(pour.boundary)
        _ = con.execute(
            """INSERT INTO pours VALUES
            (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ST_GeomFromWKB(?)
            )""",
            [
                pour.id,
                pour.name,
                net_name,
                net_number,
                _primary_layer(pour.layers),
                _layer_names(pour.layers),
                pour.priority,
                pour.settings.fill_mode.value,
                pour.settings.hatch_style,
                pour.settings.grid_mm,
                pour.settings.track_width_mm,
                pour.settings.min_thickness_mm,
                pour.settings.thermal_gap_mm,
                pour.settings.thermal_bridge_width_mm,
                pour.settings.connect_pads_clearance_mm,
                [fill.id for fill in pour.fills],
                None if pour.footprint is None else pour.footprint.reference,
                pour.metadata.source_format,
                pour.metadata.native_type,
                pour.metadata.native_kind,
                pour.metadata.native_id,
                pour.metadata.native_index,
                _metadata_json(pour.metadata),
                _wkb(boundary),
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
                None if keepout.footprint is None else keepout.footprint.reference,
                _primary_layer(keepout.layers),
                _layer_names(keepout.layers),
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
                _metadata_json(keepout.metadata),
                _wkb(boundary),
            ],
        )


def _load_layers(con: duckdb.DuckDBPyConnection, pcb: Pcb, stackup: Stackup | None) -> None:
    stackup_map: dict[str, int] = {}
    if stackup:
        for index, layer in enumerate(stackup.layers, start=1):
            stackup_map[layer.name] = index
            pcb_layer = pcb.layer_for(layer.name)
            layer_info = pcb_layer or _stackup_layer_as_pcb_layer(layer.layer_type, layer.side)
            _ = con.execute(
                "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    index,
                    layer.name,
                    list(layer_info.role_values),
                    layer_info.side,
                    None if pcb_layer is None else pcb_layer.number,
                    layer.thickness_mm if layer.thickness_mm else None,
                    layer.material or None,
                    layer.epsilon_r if layer.epsilon_r else None,
                    layer.loss_tangent if layer.loss_tangent else None,
                    layer.layer_type,
                    layer.copper_orientation or None,
                ],
            )

    for layer in pcb.layers:
        if layer.name in stackup_map:
            continue
        _ = con.execute(
            "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                None,
                layer.name,
                list(layer.role_values),
                layer.side,
                layer.number,
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
    outline = board_outline_polygon(pcb.board_profile) if pcb.board_profile is not None else None
    total_thickness = stackup.total_thickness_mm if stackup else None
    copper_finish = stackup.copper_finish if stackup else None
    layer_count = 0
    if stackup:
        layer_count = sum(1 for layer in stackup.layers if layer.layer_type == "copper")
    _ = con.execute(
        "INSERT INTO board VALUES (?, ?, ?, ?, ?, ST_GeomFromWKB(?))",
        [pcb.name, total_thickness, copper_finish, None, layer_count, _wkb(outline)],
    )


def _load_net_classes(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    for net_class in project.net_classes:
        _ = con.execute(
            "INSERT INTO net_classes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                net_class.name,
                net_class.kind,
                net_class.trace_width_mm if net_class.trace_width_mm else None,
                net_class.clearance_mm if net_class.clearance_mm else None,
                net_class.via_diameter_mm if net_class.via_diameter_mm else None,
                net_class.via_drill_mm if net_class.via_drill_mm else None,
                net_class.diff_pair_width_mm if net_class.diff_pair_width_mm else None,
                net_class.diff_pair_gap_mm if net_class.diff_pair_gap_mm else None,
            ],
        )
        for member in net_class.members:
            _ = con.execute("INSERT INTO net_class_members VALUES (?, ?)", [member, net_class.name])


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


def _load_components(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    for component in schematic.components:
        page_name = component.pages[0].name if component.pages else ""
        _ = con.execute(
            "INSERT INTO components VALUES (?, ?, ?, ?)",
            [component.reference, component.part, component.description, page_name],
        )


def _load_component_metadata(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    for component in schematic.components:
        for key, value in component.metadata.items():
            _ = con.execute(
                "INSERT INTO component_metadata VALUES (?, ?, ?)", [component.reference, key, value]
            )


def _load_pins(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    for component in schematic.components:
        for pin in component.pins:
            net_name = pin.net.name if pin.net else ""
            electrical = pin.metadata.get("electrical", "")
            _ = con.execute(
                "INSERT INTO pins VALUES (?, ?, ?, ?, ?, ?)",
                [
                    component.reference,
                    pin.designator,
                    pin.name,
                    net_name,
                    electrical,
                    pin.no_connect,
                ],
            )


def _load_nets(con: duckdb.DuckDBPyConnection, schematic: Schematic, project: Project) -> None:
    net_to_class: dict[str, str] = {}
    for net_class in project.net_classes:
        for member in net_class.members:
            net_to_class[member] = net_class.name

    net_to_diff_pair: dict[str, tuple[str, str]] = {}
    for diff_pair in project.diff_pairs:
        net_to_diff_pair[diff_pair.positive_net] = (diff_pair.name, "+")
        net_to_diff_pair[diff_pair.negative_net] = (diff_pair.name, "-")

    for net in schematic.nets:
        diff_pair_info = net_to_diff_pair.get(net.name)
        aliases = ",".join(sorted(net.aliases)) if net.aliases else None
        _ = con.execute(
            "INSERT INTO nets VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                net.name,
                len(net.pins),
                _is_power_net(net.name),
                net_to_class.get(net.name),
                None if diff_pair_info is None else diff_pair_info[0],
                None if diff_pair_info is None else diff_pair_info[1],
                aliases,
            ],
        )


def _is_power_net(name: str) -> bool:
    upper = name.upper()
    if any(upper.startswith(prefix) for prefix in _POWER_PREFIXES):
        return True
    return bool(name and name[0] in _POWER_CHARS)


def _load_pages(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    for page in schematic.pages:
        _ = con.execute(
            "INSERT INTO pages VALUES (?, ?, ?)", [page.name, len(page.components), len(page.nets)]
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
