"""KiCad PCB zone parsing: copper pours, keepouts, and zone fills."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sexpdata

from phosphor_eda.domain.pcb import (
    PcbClosedPath,
    PcbConductor,
    PcbConductorKind,
    PcbKeepout,
    PcbKeepoutPermission,
    PcbKeepoutRules,
    PcbPolygon,
    PcbPour,
    PcbPourFillMode,
    PcbPourSettings,
)
from phosphor_eda.formats.common import sexp
from phosphor_eda.formats.kicad import pcb_common
from phosphor_eda.formats.kicad.layers import resolve_layers

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import PcbFootprint
    from phosphor_eda.domain.pcb_builder import PcbBuilder
    from phosphor_eda.formats.common.sexp import SExpNode


def parse_zone_keepout(
    builder: PcbBuilder,
    zone_sexpr: SExpNode,
    *,
    index: int,
    footprint: PcbFootprint | None = None,
    transform: tuple[float, float, float] | None = None,
) -> PcbKeepout | None:
    keepout_node = sexp.find(zone_sexpr, "keepout")
    if not keepout_node:
        return None
    boundary_points = parse_zone_polygon_points(zone_sexpr)
    if not boundary_points:
        return None
    points = [pcb_common.maybe_transform(point, transform) for point in boundary_points]
    layers = resolve_layers(builder, zone_layer_names(zone_sexpr), source="keepout")
    prefix = f"fp_keepout:{footprint.reference}" if footprint is not None else "keepout"
    return PcbKeepout(
        id=f"{prefix}:{index}",
        boundary=PcbClosedPath.from_points(points),
        layers=layers,
        rules=parse_keepout_rules(keepout_node),
        footprint=footprint,
        metadata=pcb_common.object_metadata(
            native_type="zone",
            source_collection="keepouts",
            native_kind="footprint_keepout" if footprint is not None else "keepout",
            native_id=pcb_common.item_uuid(zone_sexpr),
            native_index=index,
            locked=pcb_common.item_locked(zone_sexpr),
        ),
    )


def parse_keepout_rules(keepout_node: SExpNode) -> PcbKeepoutRules:
    return PcbKeepoutRules(
        tracks=_keepout_rule_value(keepout_node, "tracks"),
        vias=_keepout_rule_value(keepout_node, "vias"),
        pads=_keepout_rule_value(keepout_node, "pads"),
        copper_pours=_keepout_rule_value(keepout_node, "copperpour"),
        footprints=_keepout_rule_value(keepout_node, "footprints"),
    )


def _keepout_rule_value(keepout_node: SExpNode, name: str) -> PcbKeepoutPermission:
    rule_node = sexp.find(keepout_node, name)
    if not rule_node:
        return PcbKeepoutPermission.UNKNOWN
    raw = sexp.val(rule_node)
    if raw == "allowed":
        return PcbKeepoutPermission.ALLOWED
    if raw == "not_allowed":
        return PcbKeepoutPermission.NOT_ALLOWED
    return PcbKeepoutPermission.UNKNOWN


def zone_layer_names(zone_sexpr: SExpNode) -> list[str]:
    layer_node = sexp.find(zone_sexpr, "layer")
    if layer_node:
        return [sexp.val(layer_node)]
    return pcb_common.layer_names(sexp.find(zone_sexpr, "layers"))


def parse_zone_polygon_points(zone_sexpr: SExpNode) -> list[tuple[float, float]]:
    polygon_node = sexp.find(zone_sexpr, "polygon")
    pts_node = sexp.find(polygon_node, "pts") if polygon_node else None
    if not pts_node:
        return []
    return [pcb_common.xy(xy_node) for xy_node in sexp.find_all(pts_node, "xy")]


def parse_zone(builder: PcbBuilder, zone_sexpr: SExpNode, index: int) -> None:
    keepout = parse_zone_keepout(builder, zone_sexpr, index=index)
    if keepout is not None:
        builder.add_keepout_object(keepout, source="zone keepout")
        return
    boundary_points = parse_zone_polygon_points(zone_sexpr)
    if not boundary_points:
        return
    layers = resolve_layers(builder, zone_layer_names(zone_sexpr), source="zone")
    fill_node = sexp.find(zone_sexpr, "fill")
    connect_node = sexp.find(zone_sexpr, "connect_pads")
    priority_node = sexp.find(zone_sexpr, "priority")
    layer_name = layers[0].name if layers else "unknown"
    pour = builder.add_pour_object(
        PcbPour(
            id=f"zone:{index}:{layer_name}",
            boundary=PcbClosedPath.from_points(boundary_points),
            layers=layers,
            net=pcb_common.resolve_net_node(builder, zone_sexpr, source="zone"),
            priority=int(sexp.num(priority_node, 1))
            if priority_node and len(priority_node) > 1
            else 0,
            settings=PcbPourSettings(
                fill_mode=_kicad_fill_mode(fill_node) if fill_node else PcbPourFillMode.UNKNOWN,
                min_thickness_mm=sexp.find_num(zone_sexpr, "min_thickness"),
                thermal_gap_mm=sexp.find_num(fill_node, "thermal_gap") if fill_node else 0.0,
                thermal_bridge_width_mm=(
                    sexp.find_num(fill_node, "thermal_bridge_width") if fill_node else 0.0
                ),
                connect_pads_clearance_mm=(
                    sexp.find_num(connect_node, "clearance") if connect_node else 0.0
                ),
            ),
            metadata=pcb_common.object_metadata(
                native_type="zone",
                source_collection="pours",
                native_id=pcb_common.item_uuid(zone_sexpr),
                native_index=index,
                locked=pcb_common.item_locked(zone_sexpr),
            ),
        ),
        source="zone",
    )
    fills = _parse_zone_fills(builder, zone_sexpr, zone_index=index, pour=pour)
    pour.fills = tuple(fills)


def _parse_zone_fills(
    builder: PcbBuilder,
    zone_sexpr: SExpNode,
    *,
    zone_index: int,
    pour: PcbPour,
) -> list[PcbConductor]:
    zone_layers = zone_layer_names(zone_sexpr)
    zone_layer = zone_layers[0] if zone_layers else ""
    conductors: list[PcbConductor] = []
    for index, fill_node in enumerate(sexp.find_all(zone_sexpr, "filled_polygon")):
        layer_node = sexp.find(fill_node, "layer")
        layer = builder.resolve_layer(
            sexp.val(layer_node) if layer_node else zone_layer,
            source="filled_polygon",
        )
        pts_node = sexp.find(fill_node, "pts")
        if not pts_node:
            continue
        points = [pcb_common.xy(xy_node) for xy_node in sexp.find_all(pts_node, "xy")]
        if not points:
            continue
        conductor = builder.add_conductor_object(
            PcbConductor(
                id=f"pour_fill:{zone_index}:{index}:{layer.name}",
                kind=PcbConductorKind.POUR_FILL,
                layer=layer,
                data=PcbPolygon(points=points),
                net=pour.net,
                pour=pour,
                metadata=pcb_common.object_metadata(
                    native_type="filled_polygon",
                    source_collection="conductors",
                    native_id=pcb_common.item_uuid(fill_node),
                    native_index=index,
                ),
            ),
            source="filled_polygon",
        )
        conductors.append(conductor)
    return conductors


def _kicad_fill_mode(fill_node: SExpNode) -> PcbPourFillMode:
    if len(fill_node) > 1:
        raw = fill_node[1]
        if isinstance(raw, sexpdata.Symbol):
            value = raw.value()
            if value == "yes":
                return PcbPourFillMode.SOLID
            if value == "no":
                return PcbPourFillMode.NONE
    return PcbPourFillMode.UNKNOWN
