"""Parse a KiCad .kicad_pcb file into the PCB domain model.

Uses sexpdata and the same helper pattern as to_schematic.py.
Handles both KiCad 6 (fp_text reference) and KiCad 8 (property
"Reference") formats.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

import sexpdata

from phosphor_eda.kicad import sexp
from phosphor_eda.pcb import (
    LayerRole,
    Pcb,
    PcbArcGeometry,
    PcbCircleGeometry,
    PcbClosedPath,
    PcbFootprint,
    PcbFootprintMetadata,
    PcbGeometry,
    PcbGeometryMetadata,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbKeepout,
    PcbKeepoutMetadata,
    PcbKeepoutPermission,
    PcbKeepoutRules,
    PcbLayer,
    PcbLayerMetadata,
    PcbLineGeometry,
    PcbMetadata,
    PcbModel3DGeometry,
    PcbNet,
    PcbPadGeometry,
    PcbPolygonGeometry,
    PcbPour,
    PcbPourFillMode,
    PcbPourMetadata,
    PcbPourSettings,
    PcbTextGeometry,
    PcbViaGeometry,
    normalize_geometry_roles,
)
from phosphor_eda.project import Stackup, StackupLayer

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.kicad.sexp import SExpNode


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------


def _xy(item: SExpNode) -> tuple[float, float]:
    """Extract (x, y) from an S-expression like (start 1.0 2.0)."""
    return (sexp.num(item, 1), sexp.num(item, 2))


def _float_val(item: SExpNode) -> float:
    """Extract a single float from item[1]."""
    return sexp.num(item, 1)


def _at(item: SExpNode) -> tuple[float, float, float]:
    """Extract (x, y, rotation) from (at X Y [ROT]).

    The rotation field may be absent, or followed by keywords like
    ``unlocked`` which must be skipped.
    """
    x = sexp.num(item, 1)
    y = sexp.num(item, 2)
    rot = 0.0
    if len(item) > 3:
        v = item[3]
        if isinstance(v, (int, float)):
            rot = float(v)
    return (x, y, rot)


def _transform_point(
    local_x: float, local_y: float, fp_x: float, fp_y: float, fp_rot_deg: float
) -> tuple[float, float]:
    """Transform footprint-local coords to absolute board coords."""
    rad = math.radians(-fp_rot_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    abs_x = fp_x + local_x * cos_r - local_y * sin_r
    abs_y = fp_y + local_x * sin_r + local_y * cos_r
    return (abs_x, abs_y)


def _layers(item: SExpNode) -> list[str]:
    """Extract layer names from (layers "F.Cu" "B.Cu" ...)."""
    result: list[str] = []
    for v in item[1:]:
        if isinstance(v, sexpdata.Symbol):
            result.append(v.value())
        elif isinstance(v, str):
            result.append(v)
    return result


# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------


def _kicad_type_roles(native_type: str) -> tuple[LayerRole, ...]:
    """Return roles carried by KiCad's board-layer type field."""
    normalized = native_type.strip().lower()
    if normalized == "signal":
        return (LayerRole.COPPER, LayerRole.SIGNAL)
    if normalized == "power":
        return (LayerRole.COPPER, LayerRole.POWER)
    if normalized == "mixed":
        return (LayerRole.COPPER, LayerRole.MIXED)
    if normalized == "jumper":
        return (LayerRole.COPPER, LayerRole.JUMPER)
    if normalized == "front":
        return (LayerRole.FRONT,)
    if normalized == "back":
        return (LayerRole.BACK,)
    return ()


def _kicad_name_roles(name: str) -> tuple[LayerRole, ...]:
    """Return roles implied by KiCad canonical layer names."""
    if name == "F.Cu":
        return (LayerRole.COPPER, LayerRole.FRONT, LayerRole.OUTER)
    if name == "B.Cu":
        return (LayerRole.COPPER, LayerRole.BACK, LayerRole.OUTER)
    if name.startswith("In") and name.endswith(".Cu"):
        return (LayerRole.COPPER, LayerRole.INNER)
    if name == "F.Mask":
        return (LayerRole.SOLDER_MASK, LayerRole.FRONT)
    if name == "B.Mask":
        return (LayerRole.SOLDER_MASK, LayerRole.BACK)
    if name == "F.Paste":
        return (LayerRole.SOLDER_PASTE, LayerRole.FRONT)
    if name == "B.Paste":
        return (LayerRole.SOLDER_PASTE, LayerRole.BACK)
    if name == "F.SilkS":
        return (LayerRole.SILKSCREEN, LayerRole.FRONT)
    if name == "B.SilkS":
        return (LayerRole.SILKSCREEN, LayerRole.BACK)
    if name == "F.Adhes":
        return (LayerRole.ADHESIVE, LayerRole.FRONT)
    if name == "B.Adhes":
        return (LayerRole.ADHESIVE, LayerRole.BACK)
    if name == "F.Fab":
        return (LayerRole.FABRICATION, LayerRole.FRONT)
    if name == "B.Fab":
        return (LayerRole.FABRICATION, LayerRole.BACK)
    if name == "F.CrtYd":
        return (LayerRole.FABRICATION, LayerRole.COURTYARD, LayerRole.FRONT)
    if name == "B.CrtYd":
        return (LayerRole.FABRICATION, LayerRole.COURTYARD, LayerRole.BACK)
    if name == "Edge.Cuts":
        return (LayerRole.EDGE,)
    if name == "Margin":
        return (LayerRole.MARGIN,)
    if name == "Dwgs.User":
        return (LayerRole.DRAWING,)
    if name == "Cmts.User":
        return (LayerRole.COMMENT,)
    if name in {"Eco1.User", "Eco2.User"} or name.startswith("User."):
        return (LayerRole.USER,)
    return ()


def _parse_layer_defs(sexpr: SExpNode) -> list[PcbLayer]:
    """Parse the board-level ``(layers ...)`` section into PcbLayer objects."""
    layers_section = sexp.find(sexpr, "layers")
    if not layers_section:
        return []
    result: list[PcbLayer] = []
    for item in layers_section[1:]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        raw_num = item[0]
        num = int(raw_num) if isinstance(raw_num, (int, float)) else 0
        raw_name = item[1]
        name = raw_name.value() if isinstance(raw_name, sexpdata.Symbol) else str(raw_name)
        raw_type = item[2]
        native_type = raw_type.value() if isinstance(raw_type, sexpdata.Symbol) else str(raw_type)
        native_user_name = str(item[3]) if len(item) > 3 else ""
        result.append(
            PcbLayer(
                name=name,
                roles=(*_kicad_type_roles(native_type), *_kicad_name_roles(name)),
                number=num,
                metadata=PcbLayerMetadata(
                    source_format="kicad",
                    native_type=native_type,
                    native_user_name=native_user_name,
                ),
            )
        )
    return result


_LAYER_TO_GEOMETRY_ROLES: dict[LayerRole, PcbGeometryRole] = {
    LayerRole.COPPER: PcbGeometryRole.COPPER,
    LayerRole.SOLDER_MASK: PcbGeometryRole.SOLDER_MASK,
    LayerRole.SOLDER_PASTE: PcbGeometryRole.SOLDER_PASTE,
    LayerRole.SILKSCREEN: PcbGeometryRole.SILKSCREEN,
    LayerRole.FABRICATION: PcbGeometryRole.FABRICATION,
    LayerRole.ASSEMBLY: PcbGeometryRole.ASSEMBLY,
    LayerRole.COURTYARD: PcbGeometryRole.COURTYARD,
    LayerRole.DESIGNATOR: PcbGeometryRole.DESIGNATOR,
    LayerRole.VALUE: PcbGeometryRole.VALUE,
    LayerRole.COMMENT: PcbGeometryRole.COMMENT,
    LayerRole.EDGE: PcbGeometryRole.EDGE,
    LayerRole.MECHANICAL: PcbGeometryRole.MECHANICAL,
    LayerRole.ROUTE_TOOL_PATH: PcbGeometryRole.ROUTE_TOOL_PATH,
    LayerRole.V_CUT: PcbGeometryRole.V_CUT,
    LayerRole.USER: PcbGeometryRole.USER,
}


def _layer_geometry_roles(
    layer_name: str,
    layer_lookup: dict[str, PcbLayer],
) -> tuple[PcbGeometryRole, ...]:
    layer = layer_lookup.get(layer_name)
    if layer is None:
        return (PcbGeometryRole.UNKNOWN,)
    return tuple(
        geometry_role
        for role in layer.roles
        if (geometry_role := _LAYER_TO_GEOMETRY_ROLES.get(role)) is not None
    )


def _geometry_metadata(
    *,
    native_type: str,
    source_collection: str,
    native_kind: str = "",
    native_id: str = "",
    locked: bool = False,
    hidden: bool = False,
    properties: dict[str, str] | None = None,
) -> PcbGeometryMetadata:
    return PcbGeometryMetadata(
        source_format="kicad",
        native_type=native_type,
        native_kind=native_kind,
        native_id=native_id,
        source_collection=source_collection,
        locked=locked,
        hidden=hidden,
        properties=properties or {},
    )


def _item_uuid(item: SExpNode) -> str:
    uuid_node = sexp.find(item, "uuid") or sexp.find(item, "tstamp")
    return sexp.val(uuid_node) if uuid_node else ""


def _item_locked(item: SExpNode) -> bool:
    return any(isinstance(node, sexpdata.Symbol) and node.value() == "locked" for node in item)


def _layered_geometry_roles(
    layer_name: str,
    layer_lookup: dict[str, PcbLayer],
    *roles: PcbGeometryRole,
) -> tuple[PcbGeometryRole, ...]:
    return normalize_geometry_roles(*_layer_geometry_roles(layer_name, layer_lookup), *roles)


# ---------------------------------------------------------------------------
# Net parsing
# ---------------------------------------------------------------------------


def _parse_nets(sexpr: SExpNode) -> dict[int, PcbNet]:
    """Parse top-level (net N "name") entries."""
    nets: dict[int, PcbNet] = {}
    for item in sexp.find_all(sexpr, "net"):
        if len(item) >= 3:
            num = int(sexp.num(item, 1))
            name = str(item[2])
            nets[num] = PcbNet(number=num, name=name)
    return nets


# ---------------------------------------------------------------------------
# Footprint / pad parsing
# ---------------------------------------------------------------------------


def _extract_reference(fp_sexpr: SExpNode) -> str:
    """Get reference designator, handling both KiCad 6 and 8 formats."""
    # KiCad 8: (property "Reference" "R1" ...)
    ref = sexp.find_property(fp_sexpr, "Reference")
    if ref:
        return ref
    # KiCad 6: (fp_text reference "R1" ...)
    for item in fp_sexpr:
        if isinstance(item, list) and sexp.tag(item) == "fp_text" and len(item) > 2:
            v = item[1]
            if isinstance(v, sexpdata.Symbol) and v.value() == "reference":
                return str(item[2])
    return "?"


def _extract_value(fp_sexpr: SExpNode) -> str:
    """Get component value, handling both KiCad 6 and 8 formats."""
    # KiCad 8: (property "Value" "100nF" ...)
    val = sexp.find_property(fp_sexpr, "Value")
    if val:
        return val
    # KiCad 6: (fp_text value "100nF" ...)
    for item in fp_sexpr:
        if isinstance(item, list) and sexp.tag(item) == "fp_text" and len(item) > 2:
            v = item[1]
            if isinstance(v, sexpdata.Symbol) and v.value() == "value":
                return str(item[2])
    return ""


def _parse_pad(
    pad_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    fp_ref: str,
    layer_lookup: dict[str, PcbLayer],
) -> PcbGeometry:
    """Parse a (pad ...) S-expression into normalized pad geometry."""
    number = str(pad_sexpr[1])
    # pad_sexpr[2] = type (smd/thru_hole), pad_sexpr[3] = shape
    pad_type_sym = pad_sexpr[2]
    pad_type = (
        pad_type_sym.value() if isinstance(pad_type_sym, sexpdata.Symbol) else str(pad_type_sym)
    )
    shape_sym = pad_sexpr[3]
    shape = shape_sym.value() if isinstance(shape_sym, sexpdata.Symbol) else str(shape_sym)

    at_node = sexp.find(pad_sexpr, "at")
    local_x, local_y, pad_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

    size_node = sexp.find(pad_sexpr, "size")
    width = sexp.num(size_node, 1) if size_node else 0.0
    height = sexp.num(size_node, 2) if size_node and len(size_node) > 2 else width

    layers_node = sexp.find(pad_sexpr, "layers")
    pad_layers = _layers(layers_node) if layers_node else []

    net_node = sexp.find(pad_sexpr, "net")
    net_num = int(sexp.num(net_node, 1)) if net_node and len(net_node) > 1 else 0
    net_name = str(net_node[2]) if net_node and len(net_node) > 2 else ""

    drill_node = sexp.find(pad_sexpr, "drill")
    drill = 0.0
    drill_shape = "circle"
    drill_width = 0.0
    drill_height = 0.0
    if drill_node and len(drill_node) > 1:
        drill_values = [float(v) for v in drill_node[1:] if isinstance(v, (int, float))]
        if drill_values:
            if (
                len(drill_node) > 1
                and isinstance(drill_node[1], sexpdata.Symbol)
                and drill_node[1].value() == "oval"
            ):
                drill_shape = "oval"
                drill_width = drill_values[0]
                drill_height = drill_values[1] if len(drill_values) > 1 else drill_values[0]
                drill = min(drill_width, drill_height)
            else:
                drill = drill_values[0]
                drill_width = drill
                drill_height = drill

    # Roundrect corner ratio
    rratio_node = sexp.find(pad_sexpr, "roundrect_rratio")
    roundrect_rratio = _float_val(rratio_node) if rratio_node else 0.0

    # Pin function and type (KiCad 8+)
    pinfunc_node = sexp.find(pad_sexpr, "pinfunction")
    pin_function = sexp.val(pinfunc_node) if pinfunc_node else ""
    pintype_node = sexp.find(pad_sexpr, "pintype")
    pin_type = sexp.val(pintype_node) if pintype_node else ""

    abs_x, abs_y = _transform_point(local_x, local_y, fp_x, fp_y, fp_rot)
    roles: list[PcbGeometryRole] = [
        PcbGeometryRole.COPPER,
        PcbGeometryRole.CONDUCTOR,
        PcbGeometryRole.FOOTPRINT_MEMBER,
    ]
    if pad_type == "smd":
        roles.append(PcbGeometryRole.SMD)
    elif pad_type in {"thru_hole", "np_thru_hole"}:
        roles.extend((PcbGeometryRole.THROUGH_HOLE, PcbGeometryRole.DRILL))
        roles.append(
            PcbGeometryRole.NON_PLATED_HOLE
            if pad_type == "np_thru_hole"
            else PcbGeometryRole.PLATED_HOLE
        )
    if shape == "custom":
        roles.append(PcbGeometryRole.CUSTOM_PAD)

    geometry_shape = PcbGeometryShape.UNKNOWN
    if shape in {"rect", "roundrect"}:
        geometry_shape = PcbGeometryShape.RECTANGLE
    elif shape == "circle":
        geometry_shape = PcbGeometryShape.CIRCLE
    elif shape in {"oval", "trapezoid", "custom"}:
        geometry_shape = PcbGeometryShape.POLYGON

    data = PcbPadGeometry(
        number=number,
        x=abs_x,
        y=abs_y,
        width=width,
        height=height,
        shape=shape,
        rotation=pad_rot,
        drill=drill,
        drill_shape=drill_shape,
        drill_width=drill_width,
        drill_height=drill_height,
        roundrect_rratio=roundrect_rratio,
        pin_function=pin_function,
        pin_type=pin_type,
    )
    role_set = list(roles)
    for layer_name in pad_layers:
        role_set.extend(_layer_geometry_roles(layer_name, layer_lookup))
    return PcbGeometry(
        id=f"pad:{fp_ref}:{number}",
        object_type=PcbGeometryObject.PAD,
        shape=geometry_shape,
        roles=tuple(role_set),
        data=data,
        layers=tuple(pad_layers),
        net_number=net_num,
        net_name=net_name,
        footprint_ref=fp_ref,
        metadata=_geometry_metadata(
            native_type="pad",
            native_id=_item_uuid(pad_sexpr),
            source_collection="pads",
            locked=_item_locked(pad_sexpr),
            properties={"pad_type": pad_type},
        ),
    )


def _parse_fp_lines(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    layer_lookup: dict[str, PcbLayer],
    fp_ref: str = "",
) -> list[PcbGeometry]:
    """Parse fp_line elements matching layer_filter, transform to absolute."""
    lines: list[PcbGeometry] = []
    for index, item in enumerate(sexp.find_all(fp_sexpr, "fp_line")):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        start_node = sexp.find(item, "start")
        end_node = sexp.find(item, "end")
        if not start_node or not end_node:
            continue
        sx, sy = _xy(start_node)
        ex, ey = _xy(end_node)
        abs_s = _transform_point(sx, sy, fp_x, fp_y, fp_rot)
        abs_e = _transform_point(ex, ey, fp_x, fp_y, fp_rot)
        width_node = sexp.find(item, "width")
        stroke_node = sexp.find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = sexp.find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        lines.append(
            PcbGeometry(
                id=f"fp_line:{fp_ref}:{index}:{layer}",
                object_type=PcbGeometryObject.GRAPHIC,
                shape=PcbGeometryShape.LINE,
                roles=_graphic_roles(layer, layer_lookup, footprint_ref=fp_ref),
                data=PcbLineGeometry(abs_s[0], abs_s[1], abs_e[0], abs_e[1], w),
                layers=(layer,),
                footprint_ref=fp_ref,
                metadata=_geometry_metadata(
                    native_type="fp_line",
                    native_id=_item_uuid(item),
                    source_collection="footprint_graphics",
                    locked=_item_locked(item),
                ),
            )
        )
    return lines


def _parse_fp_circles(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    layer_lookup: dict[str, PcbLayer],
    fp_ref: str = "",
) -> list[PcbGeometry]:
    """Parse fp_circle elements matching layer_filter, transform to absolute."""
    circles: list[PcbGeometry] = []
    for index, item in enumerate(sexp.find_all(fp_sexpr, "fp_circle")):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        center_node = sexp.find(item, "center")
        end_node = sexp.find(item, "end")
        if not center_node or not end_node:
            continue
        cx, cy = _xy(center_node)
        ex, ey = _xy(end_node)
        radius = math.hypot(ex - cx, ey - cy)
        abs_c = _transform_point(cx, cy, fp_x, fp_y, fp_rot)
        width_node = sexp.find(item, "width")
        stroke_node = sexp.find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = sexp.find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        fill_node = sexp.find(item, "fill")
        filled = fill_node is not None and sexp.val(fill_node) == "solid"
        circles.append(
            PcbGeometry(
                id=f"fp_circle:{fp_ref}:{index}:{layer}",
                object_type=PcbGeometryObject.GRAPHIC,
                shape=PcbGeometryShape.CIRCLE,
                roles=_graphic_roles(layer, layer_lookup, footprint_ref=fp_ref),
                data=PcbCircleGeometry(abs_c[0], abs_c[1], radius, w, filled),
                layers=(layer,),
                footprint_ref=fp_ref,
                metadata=_geometry_metadata(
                    native_type="fp_circle",
                    native_id=_item_uuid(item),
                    source_collection="footprint_graphics",
                    locked=_item_locked(item),
                ),
            )
        )
    return circles


def _parse_fp_rects_as_lines(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    layer_lookup: dict[str, PcbLayer],
    fp_ref: str = "",
) -> list[PcbGeometry]:
    """Parse fp_rect elements as four line geometry rows."""
    lines: list[PcbGeometry] = []
    for index, item in enumerate(sexp.find_all(fp_sexpr, "fp_rect")):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        start_node = sexp.find(item, "start")
        end_node = sexp.find(item, "end")
        if not start_node or not end_node:
            continue
        sx, sy = _xy(start_node)
        ex, ey = _xy(end_node)
        width_node = sexp.find(item, "width")
        stroke_node = sexp.find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = sexp.find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        # Four corners
        corners = [(sx, sy), (ex, sy), (ex, ey), (sx, ey)]
        abs_corners = [_transform_point(cx, cy, fp_x, fp_y, fp_rot) for cx, cy in corners]
        for i in range(4):
            j = (i + 1) % 4
            lines.append(
                PcbGeometry(
                    id=f"fp_rect:{fp_ref}:{index}:{i}:{layer}",
                    object_type=PcbGeometryObject.GRAPHIC,
                    shape=PcbGeometryShape.LINE,
                    roles=_graphic_roles(layer, layer_lookup, footprint_ref=fp_ref),
                    data=PcbLineGeometry(
                        abs_corners[i][0],
                        abs_corners[i][1],
                        abs_corners[j][0],
                        abs_corners[j][1],
                        w,
                    ),
                    layers=(layer,),
                    footprint_ref=fp_ref,
                    metadata=_geometry_metadata(
                        native_type="fp_rect",
                        native_id=_item_uuid(item),
                        source_collection="footprint_graphics",
                        locked=_item_locked(item),
                    ),
                )
            )
    return lines


def _parse_fp_arcs(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    layer_lookup: dict[str, PcbLayer],
    fp_ref: str = "",
) -> list[PcbGeometry]:
    """Parse fp_arc elements matching layer_filter, transform to absolute."""
    arcs: list[PcbGeometry] = []
    for index, item in enumerate(sexp.find_all(fp_sexpr, "fp_arc")):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        start_node = sexp.find(item, "start")
        mid_node = sexp.find(item, "mid")
        end_node = sexp.find(item, "end")
        if not start_node or not mid_node or not end_node:
            continue
        sx, sy = _xy(start_node)
        mx, my = _xy(mid_node)
        ex, ey = _xy(end_node)
        abs_s = _transform_point(sx, sy, fp_x, fp_y, fp_rot)
        abs_m = _transform_point(mx, my, fp_x, fp_y, fp_rot)
        abs_e = _transform_point(ex, ey, fp_x, fp_y, fp_rot)
        width_node = sexp.find(item, "width")
        stroke_node = sexp.find(item, "stroke")
        if width_node:
            w = _float_val(width_node)
        elif stroke_node:
            sw = sexp.find(stroke_node, "width")
            w = _float_val(sw) if sw else 0.1
        else:
            w = 0.1
        arcs.append(
            PcbGeometry(
                id=f"fp_arc:{fp_ref}:{index}:{layer}",
                object_type=PcbGeometryObject.GRAPHIC,
                shape=PcbGeometryShape.ARC,
                roles=_graphic_roles(layer, layer_lookup, footprint_ref=fp_ref),
                data=PcbArcGeometry(
                    abs_s[0],
                    abs_s[1],
                    abs_m[0],
                    abs_m[1],
                    abs_e[0],
                    abs_e[1],
                    w,
                ),
                layers=(layer,),
                footprint_ref=fp_ref,
                metadata=_geometry_metadata(
                    native_type="fp_arc",
                    native_id=_item_uuid(item),
                    source_collection="footprint_graphics",
                    locked=_item_locked(item),
                ),
            )
        )
    return arcs


_SILK_LAYERS = {"F.SilkS", "B.SilkS", "F.Silkscreen", "B.Silkscreen"}
_COURTYARD_LAYERS = {"F.CrtYd", "B.CrtYd"}
_FAB_LAYERS = {"F.Fab", "B.Fab"}
_MASK_LAYERS = {"F.Mask", "B.Mask"}
_PASTE_LAYERS = {"F.Paste", "B.Paste"}
_EDGE_LAYERS = {"Edge.Cuts"}


def _graphic_roles(
    layer: str,
    layer_lookup: dict[str, PcbLayer],
    *,
    footprint_ref: str = "",
) -> tuple[PcbGeometryRole, ...]:
    roles = list(_layer_geometry_roles(layer, layer_lookup))
    if layer == "Edge.Cuts":
        roles.extend((PcbGeometryRole.BOARD_OUTLINE, PcbGeometryRole.BOARD_LEVEL))
    elif footprint_ref:
        roles.append(PcbGeometryRole.FOOTPRINT_MEMBER)
    else:
        roles.append(PcbGeometryRole.BOARD_LEVEL)
    return normalize_geometry_roles(*roles)


def _compute_bbox(
    pads: list[PcbGeometry], courtyard_lines: list[PcbGeometry]
) -> tuple[float, float, float, float] | None:
    """Compute bounding box from courtyard lines, or pad extents + margin."""
    xs: list[float] = []
    ys: list[float] = []
    if courtyard_lines:
        for item in courtyard_lines:
            if isinstance(item.data, PcbLineGeometry):
                xs.extend([item.data.start_x, item.data.end_x])
                ys.extend([item.data.start_y, item.data.end_y])
    elif pads:
        margin = 0.5
        for item in pads:
            if isinstance(item.data, PcbPadGeometry):
                p = item.data
                xs.extend([p.x - p.width / 2 - margin, p.x + p.width / 2 + margin])
                ys.extend([p.y - p.height / 2 - margin, p.y + p.height / 2 + margin])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _parse_fp_texts(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    fp_ref: str,
    layer_lookup: dict[str, PcbLayer],
) -> list[PcbGeometry]:
    """Parse fp_text elements into normalized text geometry."""
    texts: list[PcbGeometry] = []
    for index, item in enumerate(sexp.find_all(fp_sexpr, "fp_text")):
        if len(item) < 3:
            continue
        kind_sym = item[1]
        kind = kind_sym.value() if isinstance(kind_sym, sexpdata.Symbol) else str(kind_sym)
        raw_text = str(item[2])

        # Resolve ${REFERENCE} placeholder
        if "${REFERENCE}" in raw_text:
            raw_text = raw_text.replace("${REFERENCE}", fp_ref)

        # Check hidden flag
        hidden = any(isinstance(x, sexpdata.Symbol) and x.value() == "hide" for x in item)

        layer_node = sexp.find(item, "layer")
        layer = sexp.val(layer_node) if layer_node else ""

        at_node = sexp.find(item, "at")
        local_x, local_y, text_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

        # Font size
        effects = sexp.find(item, "effects")
        font = sexp.find(effects, "font") if effects else None
        size_node = sexp.find(font, "size") if font else None
        font_size = sexp.num(size_node, 1) if size_node else 1.0

        abs_x, abs_y = _transform_point(local_x, local_y, fp_x, fp_y, fp_rot)
        abs_rot = fp_rot + text_rot

        roles = list(
            _layered_geometry_roles(
                layer,
                layer_lookup,
                PcbGeometryRole.TEXT,
                PcbGeometryRole.FOOTPRINT_MEMBER,
            )
        )
        if kind == "reference":
            roles.append(PcbGeometryRole.DESIGNATOR)
        elif kind == "value":
            roles.append(PcbGeometryRole.VALUE)
        else:
            roles.append(PcbGeometryRole.USER_TEXT)

        texts.append(
            PcbGeometry(
                id=f"fp_text:{fp_ref}:{index}:{kind}",
                object_type=PcbGeometryObject.TEXT,
                shape=PcbGeometryShape.TEXT,
                roles=tuple(roles),
                data=PcbTextGeometry(raw_text, abs_x, abs_y, abs_rot, font_size),
                layers=(layer,),
                footprint_ref=fp_ref,
                metadata=_geometry_metadata(
                    native_type="fp_text",
                    native_kind=kind,
                    native_id=_item_uuid(item),
                    source_collection="footprint_texts",
                    hidden=hidden,
                    locked=_item_locked(item),
                ),
            )
        )
    return texts


_BUILTIN_PROPERTIES = {"Reference", "Value", "Footprint", "Datasheet", "Description"}


def _parse_fp_properties(fp_sexpr: SExpNode) -> dict[str, str]:
    """Extract custom properties beyond Reference/Value from a footprint.

    KiCad 8 stores footprint properties as (property "Key" "Value" ...).
    Builtin keys (Reference, Value, Footprint, Datasheet, Description) are
    skipped since they're already captured in dedicated fields.
    """
    props: dict[str, str] = {}
    for item in sexp.find_all(fp_sexpr, "property"):
        if len(item) < 3:
            continue
        key = str(item[1])
        if key in _BUILTIN_PROPERTIES:
            continue
        value = str(item[2])
        props[key] = value
    return props


def _parse_fp_models(fp_sexpr: SExpNode, fp_ref: str) -> list[PcbGeometry]:
    """Parse all (model ...) entries from a footprint s-expression."""
    models: list[PcbGeometry] = []
    for index, node in enumerate(sexp.find_all(fp_sexpr, "model")):
        if len(node) < 2:
            continue
        raw_path = node[1]
        source = raw_path.value() if isinstance(raw_path, sexpdata.Symbol) else str(raw_path)

        # KiCad 6+ uses (offset (xyz ...)), KiCad 5 uses (at (xyz ...))
        offset_node = sexp.find(node, "offset") or sexp.find(node, "at")
        scale_node = sexp.find(node, "scale")
        rotate_node = sexp.find(node, "rotate")

        def _xyz(parent: SExpNode | None) -> tuple[float, float, float]:
            if not parent:
                return (0.0, 0.0, 0.0)
            xyz = sexp.find(parent, "xyz")
            if not xyz or len(xyz) < 4:
                return (0.0, 0.0, 0.0)
            return (sexp.num(xyz, 1), sexp.num(xyz, 2), sexp.num(xyz, 3))

        offset = _xyz(offset_node)
        scale = _xyz(scale_node)
        rotation = _xyz(rotate_node)

        # Default scale to (1, 1, 1) if all zeros (missing node)
        if scale == (0.0, 0.0, 0.0) and not scale_node:
            scale = (1.0, 1.0, 1.0)

        models.append(
            PcbGeometry(
                id=f"model_3d:{fp_ref}:{index}",
                object_type=PcbGeometryObject.MODEL_3D,
                shape=PcbGeometryShape.MODEL,
                roles=(
                    PcbGeometryRole.COMPONENT_BODY,
                    PcbGeometryRole.FOOTPRINT_MEMBER,
                ),
                data=PcbModel3DGeometry(
                    source=source,
                    offset=offset,
                    rotation=rotation,
                    scale=scale,
                ),
                footprint_ref=fp_ref,
                metadata=_geometry_metadata(
                    native_type="model",
                    source_collection="models_3d",
                    native_id=_item_uuid(node),
                ),
            )
        )
    return models


def _parse_footprint(
    fp_sexpr: SExpNode,
    layer_lookup: dict[str, PcbLayer],
) -> tuple[PcbFootprint, list[PcbGeometry], list[PcbKeepout]]:
    """Parse a footprint plus all placed geometry authored inside it."""
    lib_name = str(fp_sexpr[1])

    layer_node = sexp.find(fp_sexpr, "layer")
    layer = sexp.val(layer_node) if layer_node else "F.Cu"

    at_node = sexp.find(fp_sexpr, "at")
    fp_x, fp_y, fp_rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

    ref = _extract_reference(fp_sexpr)
    value = _extract_value(fp_sexpr)

    pads = [
        _parse_pad(p, fp_x, fp_y, fp_rot, ref, layer_lookup) for p in sexp.find_all(fp_sexpr, "pad")
    ]

    silk_lines = _parse_fp_lines(
        fp_sexpr, fp_x, fp_y, fp_rot, _SILK_LAYERS, layer_lookup, fp_ref=ref
    )
    court_lines = _parse_fp_lines(
        fp_sexpr, fp_x, fp_y, fp_rot, _COURTYARD_LAYERS, layer_lookup, fp_ref=ref
    )
    fab_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS, layer_lookup, fp_ref=ref)
    fab_lines.extend(
        _parse_fp_rects_as_lines(
            fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS, layer_lookup, fp_ref=ref
        )
    )
    fab_circles = _parse_fp_circles(
        fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS, layer_lookup, fp_ref=ref
    )
    fab_arcs = _parse_fp_arcs(fp_sexpr, fp_x, fp_y, fp_rot, _FAB_LAYERS, layer_lookup, fp_ref=ref)
    edge_lines = _parse_fp_lines(fp_sexpr, fp_x, fp_y, fp_rot, _EDGE_LAYERS, layer_lookup)
    edge_arcs = _parse_fp_arcs(fp_sexpr, fp_x, fp_y, fp_rot, _EDGE_LAYERS, layer_lookup)
    graphic_layers = _MASK_LAYERS | _PASTE_LAYERS
    graphic_lines = _parse_fp_lines(
        fp_sexpr, fp_x, fp_y, fp_rot, graphic_layers, layer_lookup, fp_ref=ref
    )
    graphic_arcs = _parse_fp_arcs(
        fp_sexpr, fp_x, fp_y, fp_rot, graphic_layers, layer_lookup, fp_ref=ref
    )
    fp_polys = _parse_fp_polys(
        fp_sexpr,
        fp_x,
        fp_y,
        fp_rot,
        _FAB_LAYERS | _SILK_LAYERS | _MASK_LAYERS,
        layer_lookup,
        fp_ref=ref,
    )
    fp_keepouts = _parse_fp_keepouts(fp_sexpr, fp_x, fp_y, fp_rot, layer_lookup, fp_ref=ref)

    texts = _parse_fp_texts(fp_sexpr, fp_x, fp_y, fp_rot, ref, layer_lookup)

    models = _parse_fp_models(fp_sexpr, ref)

    bbox = _compute_bbox(pads, court_lines)

    # Custom properties beyond Reference/Value (KiCad 8 format)
    properties = _parse_fp_properties(fp_sexpr)

    fp = PcbFootprint(
        reference=ref,
        footprint_lib=lib_name,
        x=fp_x,
        y=fp_y,
        rotation=fp_rot,
        layer=layer,
        value=value,
        bbox=bbox,
        properties=properties,
        metadata=PcbFootprintMetadata(source_format="kicad", native_type="footprint"),
    )
    geometry = [
        *pads,
        *silk_lines,
        *court_lines,
        *fab_lines,
        *fab_circles,
        *fab_arcs,
        *edge_lines,
        *edge_arcs,
        *graphic_lines,
        *graphic_arcs,
        *fp_polys,
        *texts,
        *models,
    ]
    return fp, geometry, fp_keepouts


# ---------------------------------------------------------------------------
# Segment / via parsing
# ---------------------------------------------------------------------------


def _parse_segment(
    seg_sexpr: SExpNode, layer_lookup: dict[str, PcbLayer], index: int
) -> PcbGeometry:
    start_node = sexp.find(seg_sexpr, "start")
    end_node = sexp.find(seg_sexpr, "end")
    width_node = sexp.find(seg_sexpr, "width")
    layer_node = sexp.find(seg_sexpr, "layer")
    if not start_node or not end_node or not width_node or not layer_node:
        msg = "Segment missing required start/end/width/layer"
        raise ValueError(msg)
    start = _xy(start_node)
    end = _xy(end_node)
    width = _float_val(width_node)
    layer = sexp.val(layer_node)
    net_node = sexp.find(seg_sexpr, "net")
    net = int(sexp.num(net_node, 1)) if net_node else 0
    return PcbGeometry(
        id=f"segment:{layer}:{index}",
        object_type=PcbGeometryObject.TRACK,
        shape=PcbGeometryShape.LINE,
        roles=_layered_geometry_roles(
            layer,
            layer_lookup,
            PcbGeometryRole.CONDUCTOR,
            PcbGeometryRole.ROUTE,
            PcbGeometryRole.TRACE,
            PcbGeometryRole.BOARD_LEVEL,
        ),
        data=PcbLineGeometry(start[0], start[1], end[0], end[1], width),
        layers=(layer,),
        net_number=net,
        metadata=_geometry_metadata(
            native_type="segment",
            native_id=_item_uuid(seg_sexpr),
            source_collection="segments",
            locked=_item_locked(seg_sexpr),
        ),
    )


def _parse_via(via_sexpr: SExpNode, _layer_lookup: dict[str, PcbLayer], index: int) -> PcbGeometry:
    at_node = sexp.find(via_sexpr, "at")
    size_node = sexp.find(via_sexpr, "size")
    drill_node = sexp.find(via_sexpr, "drill")
    if not at_node or not size_node or not drill_node:
        msg = "Via missing required at/size/drill"
        raise ValueError(msg)
    x, y = sexp.num(at_node, 1), sexp.num(at_node, 2)
    size = _float_val(size_node)
    drill = _float_val(drill_node)
    layers_node = sexp.find(via_sexpr, "layers")
    via_layers = _layers(layers_node) if layers_node else []
    net_node = sexp.find(via_sexpr, "net")
    net = int(sexp.num(net_node, 1)) if net_node else 0
    via_kind = ""
    if len(via_sexpr) > 1 and isinstance(via_sexpr[1], sexpdata.Symbol):
        via_kind = via_sexpr[1].value()
    roles = [
        PcbGeometryRole.COPPER,
        PcbGeometryRole.CONDUCTOR,
        PcbGeometryRole.DRILL,
        PcbGeometryRole.BOARD_LEVEL,
    ]
    if via_kind == "blind":
        roles.append(PcbGeometryRole.BLIND_VIA)
    elif via_kind == "micro":
        roles.append(PcbGeometryRole.MICROVIA)
    elif via_kind == "free":
        roles.append(PcbGeometryRole.FREE_VIA)
    else:
        roles.append(PcbGeometryRole.THROUGH_HOLE)
    return PcbGeometry(
        id=f"via:{index}",
        object_type=PcbGeometryObject.VIA,
        shape=PcbGeometryShape.CIRCLE,
        roles=tuple(roles),
        data=PcbViaGeometry(x, y, size, drill, via_kind),
        layers=tuple(via_layers),
        net_number=net,
        metadata=_geometry_metadata(
            native_type="via",
            native_kind=via_kind,
            native_id=_item_uuid(via_sexpr),
            source_collection="vias",
            locked=_item_locked(via_sexpr),
        ),
    )


# ---------------------------------------------------------------------------
# Board outline parsing
# ---------------------------------------------------------------------------


def _parse_gr_line_any_layer(
    item: SExpNode, layer_lookup: dict[str, PcbLayer], index: int
) -> PcbGeometry | None:
    """Parse a top-level (gr_line ...) on any layer."""
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        return None
    layer = sexp.val(layer_node)
    start_node = sexp.find(item, "start")
    end_node = sexp.find(item, "end")
    if not start_node or not end_node:
        return None
    start = _xy(start_node)
    end = _xy(end_node)
    width_node = sexp.find(item, "width")
    stroke_node = sexp.find(item, "stroke")
    if width_node:
        w = _float_val(width_node)
    elif stroke_node:
        sw = sexp.find(stroke_node, "width")
        w = _float_val(sw) if sw else 0.1
    else:
        w = 0.1
    return PcbGeometry(
        id=f"gr_line:{layer}:{index}",
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.LINE,
        roles=_graphic_roles(layer, layer_lookup),
        data=PcbLineGeometry(start[0], start[1], end[0], end[1], w),
        layers=(layer,),
        metadata=_geometry_metadata(
            native_type="gr_line",
            native_id=_item_uuid(item),
            source_collection="graphic_lines",
            locked=_item_locked(item),
        ),
    )


def _parse_gr_arc_any_layer(
    item: SExpNode, layer_lookup: dict[str, PcbLayer], index: int
) -> PcbGeometry | None:
    """Parse a top-level (gr_arc ...) on any layer."""
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        return None
    layer = sexp.val(layer_node)
    mid_node = sexp.find(item, "mid")
    start_node = sexp.find(item, "start")
    end_node = sexp.find(item, "end")
    if not start_node or not end_node:
        return None
    width_node = sexp.find(item, "width")
    stroke_node = sexp.find(item, "stroke")
    if width_node:
        w = _float_val(width_node)
    elif stroke_node:
        sw = sexp.find(stroke_node, "width")
        w = _float_val(sw) if sw else 0.1
    else:
        w = 0.1
    if mid_node:
        # KiCad 6+: start/mid/end are three points on the arc
        start = _xy(start_node)
        mid = _xy(mid_node)
        end = _xy(end_node)
        return PcbGeometry(
            id=f"gr_arc:{layer}:{index}",
            object_type=PcbGeometryObject.GRAPHIC,
            shape=PcbGeometryShape.ARC,
            roles=_graphic_roles(layer, layer_lookup),
            data=PcbArcGeometry(start[0], start[1], mid[0], mid[1], end[0], end[1], w),
            layers=(layer,),
            metadata=_geometry_metadata(
                native_type="gr_arc",
                native_id=_item_uuid(item),
                source_collection="graphic_arcs",
                locked=_item_locked(item),
            ),
        )
    else:
        # KiCad 5: start=centre, end=one endpoint, angle=sweep
        angle_node = sexp.find(item, "angle")
        if not angle_node:
            return None
        cx, cy = _xy(start_node)
        ex, ey = _xy(end_node)
        angle_deg = _float_val(angle_node)
        # Compute the other endpoint and midpoint
        rad = math.radians(angle_deg)
        half_rad = rad / 2
        dx, dy = ex - cx, ey - cy
        # Midpoint of the arc
        cos_h, sin_h = math.cos(half_rad), math.sin(half_rad)
        mx = cx + dx * cos_h - dy * sin_h
        my = cy + dx * sin_h + dy * cos_h
        # Far endpoint
        cos_f, sin_f = math.cos(rad), math.sin(rad)
        fx = cx + dx * cos_f - dy * sin_f
        fy = cy + dx * sin_f + dy * cos_f
        return PcbGeometry(
            id=f"gr_arc:{layer}:{index}",
            object_type=PcbGeometryObject.GRAPHIC,
            shape=PcbGeometryShape.ARC,
            roles=_graphic_roles(layer, layer_lookup),
            data=PcbArcGeometry(ex, ey, mx, my, fx, fy, w),
            layers=(layer,),
            metadata=_geometry_metadata(
                native_type="gr_arc",
                native_id=_item_uuid(item),
                source_collection="graphic_arcs",
                locked=_item_locked(item),
            ),
        )


# ---------------------------------------------------------------------------
# Zone / polygon / trace-arc parsing
# ---------------------------------------------------------------------------


def _parse_zone_polygons(
    zone_sexpr: SExpNode,
    layer_lookup: dict[str, PcbLayer],
    zone_index: int,
    *,
    pour_id: str,
) -> list[PcbGeometry]:
    """Extract filled_polygon entries from a zone as normalized geometry."""
    net_node = sexp.find(zone_sexpr, "net")
    net_num = int(sexp.num(net_node, 1)) if net_node and len(net_node) > 1 else 0
    net_name_node = sexp.find(zone_sexpr, "net_name")
    net_name = sexp.val(net_name_node) if net_name_node else ""

    # Zone-level layer (KiCad 5 filled_polygons inherit this)
    zone_layer_node = sexp.find(zone_sexpr, "layer")
    zone_layer = sexp.val(zone_layer_node) if zone_layer_node else ""

    polygons: list[PcbGeometry] = []
    for index, fp_node in enumerate(sexp.find_all(zone_sexpr, "filled_polygon")):
        # KiCad 6+ has per-filled_polygon layer; KiCad 5 inherits from zone
        layer_node = sexp.find(fp_node, "layer")
        layer = sexp.val(layer_node) if layer_node else zone_layer
        pts_node = sexp.find(fp_node, "pts")
        if not pts_node:
            continue
        points: list[tuple[float, float]] = []
        for xy_node in sexp.find_all(pts_node, "xy"):
            points.append((sexp.num(xy_node, 1), sexp.num(xy_node, 2)))
        if points:
            polygons.append(
                PcbGeometry(
                    id=f"pour_fill:{zone_index}:{index}:{layer}",
                    object_type=PcbGeometryObject.REGION,
                    shape=PcbGeometryShape.POLYGON,
                    roles=_layered_geometry_roles(
                        layer,
                        layer_lookup,
                        PcbGeometryRole.CONDUCTOR,
                        PcbGeometryRole.POUR,
                        PcbGeometryRole.POUR_FILL,
                        PcbGeometryRole.BOARD_LEVEL,
                    ),
                    data=PcbPolygonGeometry(points=points),
                    layers=(layer,),
                    net_number=net_num,
                    net_name=net_name,
                    pour_id=pour_id,
                    metadata=_geometry_metadata(
                        native_type="filled_polygon",
                        native_id=_item_uuid(fp_node),
                        source_collection="pour_fills",
                    ),
                )
            )
    return polygons


def _parse_zone_keepout(
    zone_sexpr: SExpNode,
    layer_lookup: dict[str, PcbLayer],
    *,
    index: int,
) -> PcbKeepout | None:
    """Parse a KiCad keepout/rule-area zone, if present."""
    _ = layer_lookup
    keepout_node = sexp.find(zone_sexpr, "keepout")
    if not keepout_node:
        return None
    boundary = _parse_zone_polygon_points(zone_sexpr)
    if not boundary:
        return None
    layers = tuple(_zone_layer_names(zone_sexpr))
    rules = _parse_keepout_rules(keepout_node)
    return PcbKeepout(
        id=f"keepout:{index}",
        boundary=PcbClosedPath.from_points(boundary),
        layers=layers,
        rules=rules,
        metadata=PcbKeepoutMetadata(
            source_format="kicad",
            native_type="zone",
            native_kind="keepout",
            native_id=_item_uuid(zone_sexpr),
            properties={"locked": str(_item_locked(zone_sexpr)).lower()},
        ),
    )


def _parse_keepout_rules(keepout_node: SExpNode) -> PcbKeepoutRules:
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


def _zone_layer_names(zone_sexpr: SExpNode) -> list[str]:
    layer_node = sexp.find(zone_sexpr, "layer")
    if layer_node:
        return [sexp.val(layer_node)]
    layers_node = sexp.find(zone_sexpr, "layers")
    return _layers(layers_node) if layers_node else []


def _parse_zone_polygon_points(zone_sexpr: SExpNode) -> list[tuple[float, float]]:
    polygon_node = sexp.find(zone_sexpr, "polygon")
    if not polygon_node:
        return []
    pts_node = sexp.find(polygon_node, "pts")
    if not pts_node:
        return []
    return [
        (sexp.num(xy_node, 1), sexp.num(xy_node, 2)) for xy_node in sexp.find_all(pts_node, "xy")
    ]


def _parse_gr_poly(
    item: SExpNode, layer_lookup: dict[str, PcbLayer], index: int
) -> PcbGeometry | None:
    """Parse a (gr_poly ...) as normalized polygon geometry."""
    layer_node = sexp.find(item, "layer")
    if not layer_node:
        return None
    layer = sexp.val(layer_node)
    pts_node = sexp.find(item, "pts")
    if not pts_node:
        return None
    points: list[tuple[float, float]] = []
    for xy_node in sexp.find_all(pts_node, "xy"):
        points.append((sexp.num(xy_node, 1), sexp.num(xy_node, 2)))
    if not points:
        return None
    return PcbGeometry(
        id=f"gr_poly:{layer}:{index}",
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.POLYGON,
        roles=_graphic_roles(layer, layer_lookup),
        data=PcbPolygonGeometry(points=points),
        layers=(layer,),
        metadata=_geometry_metadata(
            native_type="gr_poly",
            native_id=_item_uuid(item),
            source_collection="polygons",
            locked=_item_locked(item),
        ),
    )


def _parse_fp_polys(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_filter: set[str],
    layer_lookup: dict[str, PcbLayer],
    fp_ref: str = "",
) -> list[PcbGeometry]:
    """Parse fp_poly elements matching layer_filter, transform to absolute."""
    polys: list[PcbGeometry] = []
    for index, item in enumerate(sexp.find_all(fp_sexpr, "fp_poly")):
        layer_node = sexp.find(item, "layer")
        if not layer_node:
            continue
        layer = sexp.val(layer_node)
        if layer not in layer_filter:
            continue
        pts_node = sexp.find(item, "pts")
        if not pts_node:
            continue
        points: list[tuple[float, float]] = []
        for xy_node in sexp.find_all(pts_node, "xy"):
            lx, ly = sexp.num(xy_node, 1), sexp.num(xy_node, 2)
            ax, ay = _transform_point(lx, ly, fp_x, fp_y, fp_rot)
            points.append((ax, ay))
        if points:
            polys.append(
                PcbGeometry(
                    id=f"fp_poly:{fp_ref}:{index}:{layer}",
                    object_type=PcbGeometryObject.GRAPHIC,
                    shape=PcbGeometryShape.POLYGON,
                    roles=_graphic_roles(layer, layer_lookup, footprint_ref=fp_ref),
                    data=PcbPolygonGeometry(points=points),
                    layers=(layer,),
                    footprint_ref=fp_ref,
                    metadata=_geometry_metadata(
                        native_type="fp_poly",
                        native_id=_item_uuid(item),
                        source_collection="footprint_graphics",
                        locked=_item_locked(item),
                    ),
                )
            )
    return polys


def _parse_fp_keepouts(
    fp_sexpr: SExpNode,
    fp_x: float,
    fp_y: float,
    fp_rot: float,
    layer_lookup: dict[str, PcbLayer],
    fp_ref: str,
) -> list[PcbKeepout]:
    """Parse footprint-local keepout/rule-area zones and transform them to board space."""
    keepouts: list[PcbKeepout] = []
    for index, zone_sexpr in enumerate(sexp.find_all(fp_sexpr, "zone")):
        keepout = _parse_zone_keepout(zone_sexpr, layer_lookup, index=index)
        if keepout is None:
            continue
        points = keepout.boundary.points
        keepouts.append(
            replace(
                keepout,
                id=f"fp_keepout:{fp_ref}:{index}",
                boundary=PcbClosedPath.from_points(
                    [_transform_point(x, y, fp_x, fp_y, fp_rot) for x, y in points],
                ),
                footprint_ref=fp_ref,
                metadata=replace(
                    keepout.metadata,
                    native_kind="footprint_keepout",
                ),
            )
        )
    return keepouts


def _parse_trace_arc(
    arc_sexpr: SExpNode, layer_lookup: dict[str, PcbLayer], index: int
) -> PcbGeometry | None:
    """Parse a top-level (arc ...) copper trace arc."""
    start_node = sexp.find(arc_sexpr, "start")
    mid_node = sexp.find(arc_sexpr, "mid")
    end_node = sexp.find(arc_sexpr, "end")
    if not start_node or not mid_node or not end_node:
        return None
    sx, sy = _xy(start_node)
    mx, my = _xy(mid_node)
    ex, ey = _xy(end_node)
    width_node = sexp.find(arc_sexpr, "width")
    w = _float_val(width_node) if width_node else 0.1
    layer_node = sexp.find(arc_sexpr, "layer")
    layer = sexp.val(layer_node) if layer_node else ""
    net_node = sexp.find(arc_sexpr, "net")
    net = int(sexp.num(net_node, 1)) if net_node and len(net_node) > 1 else 0
    return PcbGeometry(
        id=f"trace_arc:{layer}:{index}",
        object_type=PcbGeometryObject.TRACK,
        shape=PcbGeometryShape.ARC,
        roles=_layered_geometry_roles(
            layer,
            layer_lookup,
            PcbGeometryRole.CONDUCTOR,
            PcbGeometryRole.ROUTE,
            PcbGeometryRole.TRACE,
            PcbGeometryRole.BOARD_LEVEL,
        ),
        data=PcbArcGeometry(sx, sy, mx, my, ex, ey, w),
        layers=(layer,),
        net_number=net,
        metadata=_geometry_metadata(
            native_type="arc",
            native_id=_item_uuid(arc_sexpr),
            source_collection="trace_arcs",
            locked=_item_locked(arc_sexpr),
        ),
    )


# ---------------------------------------------------------------------------
# Graphic text parsing
# ---------------------------------------------------------------------------


def _parse_gr_text(
    item: SExpNode, layer_lookup: dict[str, PcbLayer], index: int
) -> PcbGeometry | None:
    """Parse a (gr_text ...) into normalized text geometry."""
    if len(item) < 2:
        return None
    raw_text = str(item[1])

    layer_node = sexp.find(item, "layer")
    layer = sexp.val(layer_node) if layer_node else ""

    at_node = sexp.find(item, "at")
    x, y, rot = _at(at_node) if at_node else (0.0, 0.0, 0.0)

    effects = sexp.find(item, "effects")
    font = sexp.find(effects, "font") if effects else None
    size_node = sexp.find(font, "size") if font else None
    font_size = sexp.num(size_node, 1) if size_node else 1.0

    # Justify
    justify_node = sexp.find(effects, "justify") if effects else None
    justify = ""
    if justify_node and len(justify_node) > 1:
        justify = (
            justify_node[1].value()
            if isinstance(justify_node[1], sexpdata.Symbol)
            else str(justify_node[1])
        )

    roles = list(
        _layered_geometry_roles(
            layer,
            layer_lookup,
            PcbGeometryRole.TEXT,
            PcbGeometryRole.BOARD_LEVEL,
        )
    )
    if layer == "Cmts.User":
        roles.append(PcbGeometryRole.COMMENT)
    return PcbGeometry(
        id=f"gr_text:{layer}:{index}",
        object_type=PcbGeometryObject.TEXT,
        shape=PcbGeometryShape.TEXT,
        roles=tuple(roles),
        data=PcbTextGeometry(raw_text, x, y, rot, font_size, justify),
        layers=(layer,),
        metadata=_geometry_metadata(
            native_type="gr_text",
            native_id=_item_uuid(item),
            source_collection="graphic_texts",
            locked=_item_locked(item),
        ),
    )


# ---------------------------------------------------------------------------
# Zone boundary parsing
# ---------------------------------------------------------------------------


def _parse_zone_boundary(
    zone_sexpr: SExpNode, layer_lookup: dict[str, PcbLayer], index: int
) -> PcbPour | None:
    """Parse a zone's boundary polygon and properties into copper-pour intent."""
    _ = layer_lookup
    net_node = sexp.find(zone_sexpr, "net")
    net_num = int(sexp.num(net_node, 1)) if net_node and len(net_node) > 1 else 0
    net_name_node = sexp.find(zone_sexpr, "net_name")
    net_name = sexp.val(net_name_node) if net_name_node else ""

    layers = _zone_layer_names(zone_sexpr)
    layer = layers[0] if layers else ""

    # Priority
    priority_node = sexp.find(zone_sexpr, "priority")
    priority = int(sexp.num(priority_node, 1)) if priority_node and len(priority_node) > 1 else 0

    # Boundary polygon
    boundary = _parse_zone_polygon_points(zone_sexpr)
    if not boundary:
        return None

    # Fill settings
    fill_node = sexp.find(zone_sexpr, "fill")
    fill_mode = PcbPourFillMode.UNKNOWN
    thermal_gap = 0.0
    thermal_bridge = 0.0
    if fill_node:
        fill_mode = _kicad_fill_mode(fill_node)
        # (fill yes) or (fill (thermal_gap 0.5) (thermal_bridge_width 0.25))
        thermal_gap_node = sexp.find(fill_node, "thermal_gap")
        thermal_gap = _float_val(thermal_gap_node) if thermal_gap_node else 0.0
        bridge_node = sexp.find(fill_node, "thermal_bridge_width")
        thermal_bridge = _float_val(bridge_node) if bridge_node else 0.0

    # Min thickness
    min_thick_node = sexp.find(zone_sexpr, "min_thickness")
    min_thickness = _float_val(min_thick_node) if min_thick_node else 0.0

    # Connect pads clearance
    connect_node = sexp.find(zone_sexpr, "connect_pads")
    connect_clearance = 0.0
    if connect_node:
        clr_node = sexp.find(connect_node, "clearance")
        connect_clearance = _float_val(clr_node) if clr_node else 0.0

    return PcbPour(
        id=f"zone:{index}:{layer}",
        boundary=PcbClosedPath.from_points(boundary),
        layers=tuple(layers),
        net_number=net_num,
        net_name=net_name,
        priority=priority,
        settings=PcbPourSettings(
            fill_mode=fill_mode,
            min_thickness_mm=min_thickness,
            thermal_gap_mm=thermal_gap,
            thermal_bridge_width_mm=thermal_bridge,
            connect_pads_clearance_mm=connect_clearance,
        ),
        metadata=PcbPourMetadata(
            source_format="kicad",
            native_type="zone",
            native_id=_item_uuid(zone_sexpr),
            native_index=index,
            properties={"locked": str(_item_locked(zone_sexpr)).lower()},
        ),
    )


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


# ---------------------------------------------------------------------------
# Stackup parsing
# ---------------------------------------------------------------------------


def parse_kicad_stackup(sexpr: SExpNode) -> Stackup | None:
    """Parse stackup from the (setup (stackup ...)) section of a .kicad_pcb.

    Returns None if the file has no stackup section.
    """
    setup_node = sexp.find(sexpr, "setup")
    if not setup_node:
        return None
    stackup_node = sexp.find(setup_node, "stackup")
    if not stackup_node:
        return None

    layers: list[StackupLayer] = []
    copper_finish = ""

    for item in stackup_node[1:]:
        if not isinstance(item, list) or not item:
            continue
        tag = item[0].value() if isinstance(item[0], sexpdata.Symbol) else str(item[0])

        if tag == "copper_finish":
            copper_finish = str(item[1]) if len(item) > 1 else ""
            continue

        if tag != "layer":
            continue

        # (layer "F.Cu" (type "copper") (thickness 0.035) ...)
        name = str(item[1]) if len(item) > 1 else ""
        type_node = sexp.find(item, "type")
        layer_type = sexp.val(type_node) if type_node else ""
        thickness_node = sexp.find(item, "thickness")
        thickness = _float_val(thickness_node) if thickness_node else 0.0
        material_node = sexp.find(item, "material")
        material = sexp.val(material_node) if material_node else ""
        epsilon_node = sexp.find(item, "epsilon_r")
        epsilon_r = _float_val(epsilon_node) if epsilon_node else 0.0
        loss_node = sexp.find(item, "loss_tangent")
        loss_tangent = _float_val(loss_node) if loss_node else 0.0

        # Determine side from layer name convention
        side = ""
        if name.startswith("F.") or name == "Top":
            side = "front"
        elif name.startswith("B.") or name == "Bottom":
            side = "back"

        layers.append(
            StackupLayer(
                name=name,
                layer_type=layer_type,
                thickness_mm=thickness,
                material=material,
                epsilon_r=epsilon_r,
                loss_tangent=loss_tangent,
                side=side,
            )
        )

        # Handle sublayers (addsublayer) — appears inside the parent layer node
        for sub_item in item[2:]:
            if not isinstance(sub_item, list) or not sub_item:
                continue
            first = sub_item[0]
            sub_tag = first.value() if isinstance(first, sexpdata.Symbol) else str(first)
            if sub_tag != "addsublayer":
                continue
            sub_thickness_node = sexp.find(sub_item, "thickness")
            sub_thickness = _float_val(sub_thickness_node) if sub_thickness_node else 0.0
            sub_material_node = sexp.find(sub_item, "material")
            sub_material = sexp.val(sub_material_node) if sub_material_node else ""
            sub_epsilon_node = sexp.find(sub_item, "epsilon_r")
            sub_epsilon = _float_val(sub_epsilon_node) if sub_epsilon_node else 0.0
            sub_loss_node = sexp.find(sub_item, "loss_tangent")
            sub_loss = _float_val(sub_loss_node) if sub_loss_node else 0.0
            layers.append(
                StackupLayer(
                    name=f"{name} (sublayer)",
                    layer_type=layer_type,
                    thickness_mm=sub_thickness,
                    material=sub_material,
                    epsilon_r=sub_epsilon,
                    loss_tangent=sub_loss,
                    side=side,
                )
            )

    if not layers:
        return None

    total = sum(ly.thickness_mm for ly in layers)
    return Stackup(layers=layers, total_thickness_mm=total, copper_finish=copper_finish)


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------


def load_kicad_stackup(path: Path) -> Stackup | None:
    """Parse stackup from a .kicad_pcb file on disk.

    Convenience wrapper around parse_kicad_stackup() that handles file I/O.
    """
    text = path.read_text(encoding="utf-8")
    data: SExpNode = sexpdata.loads(text)
    sexpr: SExpNode = list(data[1:]) if data else []
    return parse_kicad_stackup(sexpr)


def parse_kicad_pcb(path: Path) -> Pcb:
    """Parse a .kicad_pcb file into the PCB domain model."""
    sexpr = read_kicad_pcb_sexpr(path)
    return parse_kicad_pcb_from_sexpr(sexpr, default_name=path.stem)


def read_kicad_pcb_sexpr(path: Path) -> SExpNode:
    """Read a .kicad_pcb file and return the top-level S-expression list."""
    text = path.read_text(encoding="utf-8")
    data: SExpNode = sexpdata.loads(text)
    return list(data[1:]) if data else []


def parse_kicad_pcb_from_sexpr(sexpr: SExpNode, *, default_name: str = "") -> Pcb:
    """Parse a PCB from an already-loaded S-expression list."""
    # Layer definitions
    layer_defs = _parse_layer_defs(sexpr)
    layer_lookup = {layer.name: layer for layer in layer_defs}

    # Title for the board name
    title_block = sexp.find(sexpr, "title_block")
    title_node = sexp.find(title_block, "title") if title_block else None
    name = sexp.val(title_node) if title_node else default_name

    nets = _parse_nets(sexpr)

    footprints: list[PcbFootprint] = []
    pours: list[PcbPour] = []
    keepouts: list[PcbKeepout] = []
    geometry: list[PcbGeometry] = []
    # KiCad 6+ uses "footprint", KiCad 5 uses "module"
    for tag in ("footprint", "module"):
        for fp_sexpr in sexp.find_all(sexpr, tag):
            fp, footprint_geometry, footprint_keepouts = _parse_footprint(fp_sexpr, layer_lookup)
            footprints.append(fp)
            geometry.extend(footprint_geometry)
            keepouts.extend(footprint_keepouts)

    geometry.extend(
        _parse_segment(item, layer_lookup, index)
        for index, item in enumerate(sexp.find_all(sexpr, "segment"))
    )
    geometry.extend(
        _parse_via(item, layer_lookup, index)
        for index, item in enumerate(sexp.find_all(sexpr, "via"))
    )

    # Zones — extract filled_polygon geometry + zone boundaries
    for zone_index, zone_sexpr in enumerate(sexp.find_all(sexpr, "zone")):
        keepout = _parse_zone_keepout(zone_sexpr, layer_lookup, index=zone_index)
        if keepout:
            keepouts.append(keepout)
            continue
        pour = _parse_zone_boundary(zone_sexpr, layer_lookup, zone_index)
        if pour is None:
            continue
        fill_geometry = _parse_zone_polygons(
            zone_sexpr,
            layer_lookup,
            zone_index,
            pour_id=pour.id,
        )
        geometry.extend(fill_geometry)
        pours.append(replace(pour, fill_geometry_ids=tuple(item.id for item in fill_geometry)))
    # Top-level graphic polygons
    for index, item in enumerate(sexp.find_all(sexpr, "gr_poly")):
        p = _parse_gr_poly(item, layer_lookup, index)
        if p:
            geometry.append(p)

    # Trace arcs (curved copper traces)
    for index, item in enumerate(sexp.find_all(sexpr, "arc")):
        ta = _parse_trace_arc(item, layer_lookup, index)
        if ta:
            geometry.append(ta)

    # Board outline: top-level gr_line/gr_arc on Edge.Cuts + fp-internal ones
    for index, item in enumerate(sexp.find_all(sexpr, "gr_line")):
        ln = _parse_gr_line_any_layer(item, layer_lookup, index)
        if ln:
            geometry.append(ln)
    for index, item in enumerate(sexp.find_all(sexpr, "gr_arc")):
        arc = _parse_gr_arc_any_layer(item, layer_lookup, index)
        if arc:
            geometry.append(arc)

    # Graphic texts (board-level, not inside footprints)
    for index, item in enumerate(sexp.find_all(sexpr, "gr_text")):
        gt = _parse_gr_text(item, layer_lookup, index)
        if gt:
            geometry.append(gt)

    return Pcb(
        name=name,
        nets=nets,
        footprints=footprints,
        pours=pours,
        keepouts=keepouts,
        geometry=geometry,
        layers=layer_defs,
        metadata=PcbMetadata(source_format="kicad"),
    )
