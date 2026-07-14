"""Padstack expansion helpers for Allegro board records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.domain.arc_geometry import arc_to_polyline
from phosphor_eda.domain.pcb import (
    PadStack,
    PcbDrillPlating,
    PcbDrillShape,
    PcbPadType,
    PcbPathSegmentKind,
    PcbPolygon,
)
from phosphor_eda.formats.allegro.constants import PAD_COMPONENT_SHAPES
from phosphor_eda.formats.allegro.coords import BoardFrame
from phosphor_eda.formats.allegro.diagnostics import build_diagnostic
from phosphor_eda.formats.allegro.graphics import closed_path_from_segment_chain
from phosphor_eda.formats.allegro.records import AllegroPadstackComponent, payload_int

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import PcbClosedPath
    from phosphor_eda.formats.allegro.graph import AllegroObjectGraph
    from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordDiagnostic
    from phosphor_eda.formats.allegro.sidecars import AllegroPadstackSidecar

# Non-zero string_key on a "custom" (shape-symbol) pad component references a
# 0x28 shape record whose segment chain holds the pad copper in pad-local units.
_SHAPE_SYMBOL_TAG = 0x28
# Points per arc when linearizing a shape-symbol boundary into a fill polygon.
_ARC_LINEARIZE_POINTS = 24
_DIAG_UNRESOLVED_SHAPE_SYMBOL = "unresolved-pad-shape-symbol"
_DIAG_UNMODELED_SHAPE_VOID = "unmodeled-pad-shape-void"
_DIAG_DEGENERATE_PAD_SIZE = "degenerate-pad-size"

_PADSTACK_TYPE_VIA = 0x10
_PADSTACK_TYPE_SMD = {0x20, 0xA0}
_PADSTACK_TYPE_SLOT = 0x30
_PADSTACK_TYPE_NPTH = 0x80

_PAD_COMPONENT_SLOT = 2


@dataclass(frozen=True)
class AllegroExpandedPadstack:
    name: str
    stack: PadStack
    pad_type: PcbPadType
    drill_diameter: float
    drill_shape: PcbDrillShape
    drill_width: float
    drill_height: float
    plating: PcbDrillPlating
    metadata: dict[str, str]
    # Shape-symbol flash geometry in PAD-LOCAL millimeters (Y-down), centered on
    # the pad origin. Placed instances rotate and translate these via
    # ``place_custom_shapes``. Empty unless a "custom" component resolved a 0x28.
    custom_shapes: tuple[PcbPolygon, ...] = ()


def expand_allegro_padstack(
    record: AllegroRecord,
    *,
    name: str,
    unit_to_mm: float,
    sidecar: AllegroPadstackSidecar | None = None,
    graph: AllegroObjectGraph | None = None,
    diagnostics: list[AllegroRecordDiagnostic] | None = None,
) -> AllegroExpandedPadstack:
    """Convert a decoded 0x1C source record to reusable pad/via geometry."""
    components = _pad_components(record)
    copper_component = _first_copper_component(record, components)
    shape = _component_shape(copper_component)
    custom_shapes: tuple[PcbPolygon, ...] = ()
    if shape == "custom" and copper_component is not None:
        custom_shapes = _resolve_shape_symbol(
            record, copper_component, unit_to_mm, graph, diagnostics
        )
    drill_size = payload_int(record, "drill_size")
    slot_x = payload_int(record, "slot_x")
    slot_y = payload_int(record, "slot_y")
    pad_type_code = payload_int(record, "pad_type_code")
    drill_diameter = drill_size * unit_to_mm
    drill_width = slot_x * unit_to_mm if slot_x > 0 else drill_diameter
    drill_height = slot_y * unit_to_mm if slot_y > 0 else drill_diameter
    pad_width = abs(copper_component.width) * unit_to_mm if copper_component else drill_width
    pad_height = abs(copper_component.height) * unit_to_mm if copper_component else drill_height
    if pad_width <= 0:
        if drill_width <= 0 and diagnostics is not None:
            diagnostics.append(
                build_diagnostic(
                    record,
                    code=_DIAG_DEGENERATE_PAD_SIZE,
                    message=(
                        f"padstack {record.key} ({name}) has no copper or drill geometry; "
                        "falling back to a 0.1 mm pad"
                    ),
                )
            )
        pad_width = drill_width if drill_width > 0 else 0.1
    if pad_height <= 0:
        pad_height = drill_height if drill_height > 0 else pad_width

    metadata = {
        "native_padstack_key": "" if record.key is None else str(record.key),
        "native_padstack_name": name,
        "native_pad_type_code": str(pad_type_code),
        "native_layer_count": str(payload_int(record, "layer_count")),
        "native_component_count": str(payload_int(record, "component_count")),
    }
    if copper_component is not None:
        metadata["native_pad_component_type"] = str(copper_component.component_type)
    if custom_shapes and copper_component is not None:
        metadata["native_pad_shape_symbol_key"] = str(copper_component.string_key)
    if sidecar is not None:
        metadata.update(
            _sidecar_metadata(
                sidecar,
                pad_width,
                pad_height,
                shape=shape,
            )
        )

    return AllegroExpandedPadstack(
        name=name,
        stack=PadStack.simple(
            shape,
            pad_width,
            pad_height,
            _corner_radius_ratio(copper_component),
            _component_offset(copper_component.offset_x if copper_component else 0, unit_to_mm),
            _component_offset(copper_component.offset_y if copper_component else 0, unit_to_mm),
        ),
        pad_type=_pad_type(pad_type_code, drill_diameter, drill_width, drill_height),
        drill_diameter=drill_diameter,
        drill_shape=PcbDrillShape.SLOT
        if pad_type_code == _PADSTACK_TYPE_SLOT
        else PcbDrillShape.ROUND,
        drill_width=drill_width,
        drill_height=drill_height,
        plating=_plating(record, pad_type_code),
        metadata=metadata,
        custom_shapes=custom_shapes,
    )


def _resolve_shape_symbol(
    record: AllegroRecord,
    component: AllegroPadstackComponent,
    unit_to_mm: float,
    graph: AllegroObjectGraph | None,
    diagnostics: list[AllegroRecordDiagnostic] | None,
) -> tuple[PcbPolygon, ...]:
    """Resolve a custom pad component's 0x28 flash into a pad-local fill polygon.

    The component's ``string_key`` references a 0x28 shape record owned by the
    footprint definition; its segment chain holds the pad copper boundary in
    pad-local units around the origin. Arcs are linearized into a single filled
    polygon. Anything unresolvable keeps the bounding-rect fallback and records
    a diagnostic instead of degrading silently.
    """
    if graph is None or component.string_key == 0:
        return ()
    shape_record = graph.by_key.get(component.string_key)
    if shape_record is not None and shape_record.tag == _SHAPE_SYMBOL_TAG:
        # Flash symbols must be footprint-definition-owned; a board-level shape
        # here would double-emit (copper already renders it) and its absolute
        # coordinates would be misread as pad-local.
        owner = graph.by_key.get(payload_int(shape_record, "owner_key"))
        if owner is None or owner.tag != 0x2B:
            if diagnostics is not None:
                diagnostics.append(
                    build_diagnostic(
                        record,
                        code=_DIAG_UNRESOLVED_SHAPE_SYMBOL,
                        message=(
                            f"padstack {record.key} custom component references shape "
                            f"{component.string_key} that is not footprint-definition-owned"
                        ),
                        reference_key=component.string_key,
                    )
                )
            return ()
    if shape_record is None or shape_record.tag != _SHAPE_SYMBOL_TAG:
        if diagnostics is not None:
            diagnostics.append(
                build_diagnostic(
                    record,
                    code=_DIAG_UNRESOLVED_SHAPE_SYMBOL,
                    message=(
                        f"padstack {record.key} custom component references shape symbol "
                        f"{component.string_key} that is missing or not a 0x28 shape record"
                    ),
                    reference_key=component.string_key,
                )
            )
        return ()
    boundary = closed_path_from_segment_chain(
        shape_record,
        graph=graph,
        frame=BoardFrame(unit_to_mm),
        head_key=payload_int(shape_record, "first_segment_key"),
        diagnostics=diagnostics if diagnostics is not None else [],
        diagnostic_prefix="pad-shape",
    )
    if boundary is None:
        return ()
    if payload_int(shape_record, "first_keepout_key") and diagnostics is not None:
        diagnostics.append(
            build_diagnostic(
                shape_record,
                code=_DIAG_UNMODELED_SHAPE_VOID,
                message=(
                    f"pad shape symbol {shape_record.key} carries a void chain that is "
                    "not modeled in the flash polygon"
                ),
                reference_key=payload_int(shape_record, "first_keepout_key"),
            )
        )
    points = _linearize_boundary(boundary)
    if len(points) < 3:
        return ()
    return (PcbPolygon(points=points, fill=True),)


def _linearize_boundary(boundary: PcbClosedPath) -> list[tuple[float, float]]:
    """Flatten a closed path (lines + arcs) into a ring of polygon points."""
    points: list[tuple[float, float]] = []
    for segment in boundary.segments:
        if segment.kind is PcbPathSegmentKind.ARC:
            arc_points = arc_to_polyline(
                segment.start_x,
                segment.start_y,
                segment.mid_x,
                segment.mid_y,
                segment.end_x,
                segment.end_y,
                num_points=_ARC_LINEARIZE_POINTS,
            )
            points.extend(arc_points[:-1])
        else:
            points.append((segment.start_x, segment.start_y))
    return points


def place_custom_shapes(
    padstack: AllegroExpandedPadstack,
    x: float,
    y: float,
    rotation_deg: float,
) -> tuple[PcbPolygon, ...]:
    """Rotate and translate a padstack's pad-local flash polygons onto the board.

    The padstack stores flash geometry in pad-local millimeters centered on the
    pad origin; each placed pad bakes its instance rotation and center into that
    shared geometry to produce absolute board coordinates. The ``-rotation`` sign
    matches ``transform_point`` and the renderer's y-down pad rotation.
    """
    if not padstack.custom_shapes:
        return ()
    rad = math.radians(-rotation_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)

    def place(point: tuple[float, float]) -> tuple[float, float]:
        local_x, local_y = point
        return (
            x + local_x * cos_r - local_y * sin_r,
            y + local_x * sin_r + local_y * cos_r,
        )

    return tuple(
        PcbPolygon(
            points=[place(point) for point in polygon.points],
            holes=[[place(point) for point in ring] for ring in polygon.holes],
            width=polygon.width,
            fill=polygon.fill,
        )
        for polygon in padstack.custom_shapes
    )


def _sidecar_metadata(
    sidecar: AllegroPadstackSidecar, pad_width: float, pad_height: float, *, shape: str
) -> dict[str, str]:
    result = {
        "sidecar_padstack_path": str(sidecar.path),
        "sidecar_padstack_name": sidecar.name,
        "sidecar_padstack_units": sidecar.units,
        "sidecar_padstack_shape": sidecar.shape,
        "sidecar_padstack_width_mm": repr(sidecar.width_mm),
        "sidecar_padstack_height_mm": repr(sidecar.height_mm),
    }
    if (
        _nearly_equal(sidecar.width_mm, pad_width)
        and _nearly_equal(sidecar.height_mm, pad_height)
        and sidecar.shape in {"", shape}
    ):
        result["sidecar_padstack_match"] = "geometry_confirmed"
    else:
        result["sidecar_padstack_match"] = "identity_only"
    return result


def _nearly_equal(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-6


def _pad_components(record: AllegroRecord) -> tuple[AllegroPadstackComponent, ...]:
    components = record.payload.get("components", ())
    if not isinstance(components, tuple):
        return ()
    return tuple(
        component for component in components if isinstance(component, AllegroPadstackComponent)
    )


def _first_copper_component(
    record: AllegroRecord, components: tuple[AllegroPadstackComponent, ...]
) -> AllegroPadstackComponent | None:
    layer_count = payload_int(record, "layer_count")
    fixed_count = payload_int(record, "fixed_component_count")
    per_layer = payload_int(record, "components_per_layer")
    if layer_count <= 0 or fixed_count <= 0 or per_layer <= 0:
        candidates = components
    else:
        candidates = tuple(
            components[index]
            for index in range(fixed_count + _PAD_COMPONENT_SLOT, len(components), per_layer)
            if index < len(components)
        )
    for component in candidates:
        if component.component_type != 0 and (component.width != 0 or component.height != 0):
            return component
    return None


def _component_shape(component: AllegroPadstackComponent | None) -> str:
    if component is None:
        return "circle"
    return PAD_COMPONENT_SHAPES.get(component.component_type, "custom")


def _corner_radius_ratio(component: AllegroPadstackComponent | None) -> float:
    if component is None or component.component_type != 0x1B or component.z1 is None:
        return 0.0
    shortest = min(abs(component.width), abs(component.height))
    if shortest <= 0:
        return 0.0
    return max(0.0, min(0.5, abs(component.z1) / shortest))


def _component_offset(value: int, unit_to_mm: float) -> float:
    return value * unit_to_mm


def _pad_type(
    pad_type_code: int,
    drill_diameter: float,
    drill_width: float,
    drill_height: float,
) -> PcbPadType:
    has_hole = drill_diameter > 0.0 or drill_width > 0.0 or drill_height > 0.0
    if pad_type_code in _PADSTACK_TYPE_SMD or not has_hole:
        return PcbPadType.SMD
    return PcbPadType.THROUGH_HOLE


def _plating(record: AllegroRecord, pad_type_code: int) -> PcbDrillPlating:
    if pad_type_code == _PADSTACK_TYPE_NPTH:
        return PcbDrillPlating.NON_PLATED
    plated = payload_int(record, "plated")
    if plated:
        return PcbDrillPlating.PLATED
    if pad_type_code in {_PADSTACK_TYPE_VIA, _PADSTACK_TYPE_SLOT}:
        return PcbDrillPlating.UNKNOWN
    return PcbDrillPlating.NON_PLATED
