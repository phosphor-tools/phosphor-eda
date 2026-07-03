"""Padstack expansion helpers for Allegro board records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    PadStack,
    PcbDrillPlating,
    PcbDrillShape,
    PcbPadType,
)
from phosphor_eda.formats.allegro.records import AllegroPadstackComponent, payload_int

if TYPE_CHECKING:
    from phosphor_eda.formats.allegro.records import AllegroRecord
    from phosphor_eda.formats.allegro.sidecars import AllegroPadstackSidecar

_PADSTACK_TYPE_VIA = 0x10
_PADSTACK_TYPE_SMD = {0x20, 0xA0}
_PADSTACK_TYPE_SLOT = 0x30
_PADSTACK_TYPE_NPTH = 0x80

_PAD_COMPONENT_SLOT = 2

_SHAPES = {
    0x02: "circle",
    0x03: "octagon",
    0x05: "rect",
    0x06: "rect",
    0x07: "diamond",
    0x0B: "oval",
    0x0C: "oval",
    0x1B: "roundrect",
    0x1C: "rect",
}


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


def expand_allegro_padstack(
    record: AllegroRecord,
    *,
    name: str,
    unit_to_mm: float,
    sidecar: AllegroPadstackSidecar | None = None,
) -> AllegroExpandedPadstack:
    """Convert a decoded 0x1C source record to reusable pad/via geometry."""
    components = _pad_components(record)
    copper_component = _first_copper_component(record, components)
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
    if sidecar is not None:
        metadata.update(
            _sidecar_metadata(
                sidecar,
                pad_width,
                pad_height,
                shape=_component_shape(copper_component),
            )
        )

    return AllegroExpandedPadstack(
        name=name,
        stack=PadStack.simple(
            _component_shape(copper_component),
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
    return _SHAPES.get(component.component_type, "custom")


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
