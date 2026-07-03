"""Allegro class/subclass layer mapping."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeGuard

from phosphor_eda.domain.pcb import LayerRole, PcbLayer, PcbLayerMetadata
from phosphor_eda.domain.project import Stackup, StackupLayer
from phosphor_eda.formats.allegro.records import AllegroLayerListEntry, AllegroRecordDiagnostic

if TYPE_CHECKING:
    from collections.abc import Mapping

    from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet

_CLASS_BOARD_GEOMETRY = 0x01
_CLASS_COMPONENT_VALUE = 0x02
_CLASS_DEVICE_TYPE = 0x03
_CLASS_DRAWING_FORMAT = 0x04
_CLASS_ETCH = 0x06
_CLASS_MANUFACTURING = 0x07
_CLASS_ANALYSIS = 0x08
_CLASS_PACKAGE_GEOMETRY = 0x09
_CLASS_PACKAGE_KEEPIN = 0x0A
_CLASS_PACKAGE_KEEPOUT = 0x0B
_CLASS_REF_DES = 0x0D
_CLASS_ROUTE_KEEPIN = 0x0E
_CLASS_ROUTE_KEEPOUT = 0x0F
_CLASS_TOLERANCE = 0x10
_CLASS_USER_PART_NUMBER = 0x11
_CLASS_VIA_KEEPOUT = 0x13
_CLASS_ANTI_ETCH = 0x14
_CLASS_BOUNDARY = 0x15
_CLASS_CONSTRAINTS_REGION = 0x16

_CLASS_NAMES: dict[int, str] = {
    _CLASS_BOARD_GEOMETRY: "Board Geometry",
    _CLASS_COMPONENT_VALUE: "Component Value",
    _CLASS_DEVICE_TYPE: "Device Type",
    _CLASS_DRAWING_FORMAT: "Drawing Format",
    0x05: "DRC Error",
    _CLASS_ETCH: "Etch",
    _CLASS_MANUFACTURING: "Manufacturing",
    _CLASS_ANALYSIS: "Analysis",
    _CLASS_PACKAGE_GEOMETRY: "Package Geometry",
    _CLASS_PACKAGE_KEEPIN: "Package Keepin",
    _CLASS_PACKAGE_KEEPOUT: "Package Keepout",
    0x0C: "Pin",
    _CLASS_REF_DES: "Ref Des",
    _CLASS_ROUTE_KEEPIN: "Route Keepin",
    _CLASS_ROUTE_KEEPOUT: "Route Keepout",
    _CLASS_TOLERANCE: "Tolerance",
    _CLASS_USER_PART_NUMBER: "User Part Number",
    0x12: "Via Class",
    _CLASS_VIA_KEEPOUT: "Via Keepout",
    _CLASS_ANTI_ETCH: "Anti Etch",
    _CLASS_BOUNDARY: "Boundary",
    _CLASS_CONSTRAINTS_REGION: "Constraints Region",
}

_SILKSCREEN_COMPONENT_TEXT_CLASSES = {
    _CLASS_COMPONENT_VALUE,
    _CLASS_REF_DES,
}
_ASSEMBLY_COMPONENT_TEXT_CLASSES = {
    _CLASS_COMPONENT_VALUE,
    _CLASS_DEVICE_TYPE,
    _CLASS_REF_DES,
    _CLASS_TOLERANCE,
    _CLASS_USER_PART_NUMBER,
}

_FIXED_ROLES: dict[tuple[int, int], tuple[LayerRole, ...]] = {
    (_CLASS_BOARD_GEOMETRY, 0xEA): (
        LayerRole.MECHANICAL,
        LayerRole.BOARD,
        LayerRole.BOARD_SHAPE,
        LayerRole.EDGE,
    ),
    (_CLASS_BOARD_GEOMETRY, 0xED): (LayerRole.SOLDER_MASK, LayerRole.BACK),
    (_CLASS_BOARD_GEOMETRY, 0xEE): (LayerRole.SOLDER_MASK, LayerRole.FRONT),
    (_CLASS_BOARD_GEOMETRY, 0xF0): (LayerRole.SILKSCREEN, LayerRole.BACK),
    (_CLASS_BOARD_GEOMETRY, 0xF1): (LayerRole.SILKSCREEN, LayerRole.FRONT),
    (_CLASS_BOARD_GEOMETRY, 0xF9): (LayerRole.MECHANICAL, LayerRole.DIMENSION),
    (_CLASS_BOARD_GEOMETRY, 0xFB): (
        LayerRole.MECHANICAL,
        LayerRole.ASSEMBLY,
        LayerRole.ASSEMBLY_NOTES,
    ),
    (_CLASS_BOARD_GEOMETRY, 0xFD): (
        LayerRole.MECHANICAL,
        LayerRole.BOARD,
        LayerRole.BOARD_SHAPE,
        LayerRole.EDGE,
    ),
    (_CLASS_DRAWING_FORMAT, 0xFD): (LayerRole.MECHANICAL, LayerRole.DRAWING, LayerRole.EDGE),
    (_CLASS_PACKAGE_GEOMETRY, 0xEC): (LayerRole.SOLDER_PASTE, LayerRole.BACK),
    (_CLASS_PACKAGE_GEOMETRY, 0xED): (LayerRole.SOLDER_PASTE, LayerRole.FRONT),
    (_CLASS_PACKAGE_GEOMETRY, 0xEE): (
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.BACK,
    ),
    (_CLASS_PACKAGE_GEOMETRY, 0xEF): (
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.FRONT,
    ),
    (_CLASS_PACKAGE_GEOMETRY, 0xF3): (LayerRole.SOLDER_MASK, LayerRole.BACK),
    (_CLASS_PACKAGE_GEOMETRY, 0xF4): (LayerRole.SOLDER_MASK, LayerRole.FRONT),
    (_CLASS_PACKAGE_GEOMETRY, 0xF5): (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_CENTER,
    ),
    (_CLASS_PACKAGE_GEOMETRY, 0xF6): (LayerRole.SILKSCREEN, LayerRole.BACK),
    (_CLASS_PACKAGE_GEOMETRY, 0xF7): (LayerRole.SILKSCREEN, LayerRole.FRONT),
    (_CLASS_PACKAGE_GEOMETRY, 0xF8): (
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_OUTLINE,
        LayerRole.USER,
    ),
    (_CLASS_PACKAGE_GEOMETRY, 0xF9): (
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_OUTLINE,
        LayerRole.USER,
    ),
    (_CLASS_PACKAGE_GEOMETRY, 0xFA): (
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.BACK,
    ),
    (_CLASS_PACKAGE_GEOMETRY, 0xFB): (
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.FRONT,
    ),
    (_CLASS_PACKAGE_GEOMETRY, 0xFC): (
        LayerRole.MECHANICAL,
        LayerRole.ASSEMBLY,
        LayerRole.BACK,
    ),
    (_CLASS_PACKAGE_GEOMETRY, 0xFD): (
        LayerRole.MECHANICAL,
        LayerRole.ASSEMBLY,
        LayerRole.FRONT,
    ),
    (_CLASS_MANUFACTURING, 0xF3): (LayerRole.SILKSCREEN, LayerRole.BACK),
    (_CLASS_MANUFACTURING, 0xF4): (LayerRole.SILKSCREEN, LayerRole.FRONT),
    (_CLASS_MANUFACTURING, 0xF7): (LayerRole.DRILL, LayerRole.DRILL_DRAWING),
    (_CLASS_MANUFACTURING, 0xF8): (LayerRole.DRILL, LayerRole.DRILL_GUIDE),
    (_CLASS_MANUFACTURING, 0xFD): (LayerRole.FABRICATION, LayerRole.EDGE),
    (_CLASS_PACKAGE_KEEPIN, 0xFD): (LayerRole.KEEPOUT,),
    (_CLASS_ROUTE_KEEPIN, 0xFD): (LayerRole.KEEPOUT,),
    (_CLASS_PACKAGE_KEEPOUT, 0xFB): (LayerRole.KEEPOUT, LayerRole.BACK),
    (_CLASS_PACKAGE_KEEPOUT, 0xFC): (LayerRole.KEEPOUT, LayerRole.FRONT),
    (_CLASS_PACKAGE_KEEPOUT, 0xFD): (LayerRole.KEEPOUT,),
    (_CLASS_ROUTE_KEEPOUT, 0xFB): (LayerRole.KEEPOUT, LayerRole.BACK),
    (_CLASS_ROUTE_KEEPOUT, 0xFC): (LayerRole.KEEPOUT, LayerRole.FRONT),
    (_CLASS_ROUTE_KEEPOUT, 0xFD): (LayerRole.KEEPOUT,),
    (_CLASS_VIA_KEEPOUT, 0xFB): (LayerRole.KEEPOUT, LayerRole.BACK),
    (_CLASS_VIA_KEEPOUT, 0xFC): (LayerRole.KEEPOUT, LayerRole.FRONT),
    (_CLASS_VIA_KEEPOUT, 0xFD): (LayerRole.KEEPOUT,),
}


@dataclass(frozen=True)
class AllegroLayerMap:
    layers: tuple[PcbLayer, ...]
    stackup: Stackup | None
    by_class_subclass: Mapping[tuple[int, int], PcbLayer]
    diagnostics: tuple[AllegroRecordDiagnostic, ...] = ()

    def layers_by_role(self, role: LayerRole | str) -> list[PcbLayer]:
        return [layer for layer in self.layers if layer.has_role(role)]

    def layer_for_class_subclass(self, class_id: int, subclass_id: int) -> PcbLayer | None:
        return self.by_class_subclass.get((class_id, subclass_id))


def build_allegro_layers(record_set: AllegroRecordSet) -> AllegroLayerMap:
    empty_string_table: Mapping[int, str] = {}
    string_table: Mapping[int, str] = (
        record_set.string_table.by_id if record_set.string_table is not None else empty_string_table
    )
    layer_list_records = _layer_list_records_by_key(record_set)
    etch_record = _layer_list_for_class(record_set, _CLASS_ETCH, layer_list_records)
    etch_entries = _layer_entries(etch_record)

    layers: list[PcbLayer] = []
    diagnostics: list[AllegroRecordDiagnostic] = []
    by_class_subclass: dict[tuple[int, int], PcbLayer] = {}
    copper_layers: list[PcbLayer] = []

    for entry in etch_entries:
        name = _resolved_entry_name(
            entry,
            string_table,
            fallback=f"ETCH_{entry.index + 1}",
            class_id=_CLASS_ETCH,
            record=etch_record,
            diagnostics=diagnostics,
        )
        layer = PcbLayer(
            name=name,
            roles=_copper_roles(entry.index, len(etch_entries), name),
            number=entry.index + 1,
            stack_index=entry.index,
            metadata=_metadata(
                native_class_id=_CLASS_ETCH,
                native_subclass_id=entry.index,
                native_layer_name=name,
                layer_list_key=etch_record.key,
                entry=entry,
            ),
        )
        layers.append(layer)
        copper_layers.append(layer)
        by_class_subclass[(_CLASS_ETCH, entry.index)] = layer

    for class_id, entry in enumerate(record_set.header.layer_map if record_set.header else ()):
        if class_id == _CLASS_ETCH or entry.layer_list_key == 0:
            continue
        record = layer_list_records.get(entry.layer_list_key)
        if record is None:
            continue
        if record is etch_record:
            for copper in copper_layers:
                if copper.stack_index is not None:
                    by_class_subclass[(class_id, copper.stack_index)] = copper
            continue
        for layer_entry in _layer_entries(record):
            name = _resolved_entry_name(
                layer_entry,
                string_table,
                fallback=_display_name(class_id, layer_entry.index),
                class_id=class_id,
                record=record,
                diagnostics=diagnostics,
            )
            layer = PcbLayer(
                name=name,
                roles=_class_subclass_roles(class_id, layer_entry.index, name),
                number=len(layers) + 1,
                metadata=_metadata(
                    native_class_id=class_id,
                    native_subclass_id=layer_entry.index,
                    native_layer_name=name,
                    layer_list_key=record.key,
                    entry=layer_entry,
                ),
            )
            layers.append(layer)
            by_class_subclass[(class_id, layer_entry.index)] = layer

    for (class_id, subclass_id), roles in _static_layer_roles():
        if (class_id, subclass_id) in by_class_subclass:
            continue
        name = _display_name(class_id, subclass_id)
        layer = PcbLayer(
            name=name,
            roles=roles,
            number=len(layers) + 1,
            metadata=_metadata(
                native_class_id=class_id,
                native_subclass_id=subclass_id,
                native_layer_name=name,
                layer_list_key=None,
                entry=AllegroLayerListEntry(index=subclass_id),
            ),
        )
        layers.append(layer)
        by_class_subclass[(class_id, subclass_id)] = layer

    stackup = _stackup_from_copper_layers(copper_layers)
    return AllegroLayerMap(
        layers=tuple(layers),
        stackup=stackup,
        by_class_subclass=MappingProxyType(by_class_subclass),
        diagnostics=tuple(diagnostics),
    )


def _layer_list_records_by_key(record_set: AllegroRecordSet) -> dict[int, AllegroRecord]:
    return {
        record.key: record
        for record in record_set.records
        if record.tag == 0x2A and record.key is not None
    }


def _layer_list_for_class(
    record_set: AllegroRecordSet,
    class_id: int,
    layer_list_records: Mapping[int, AllegroRecord],
) -> AllegroRecord:
    if record_set.header is None or class_id >= len(record_set.header.layer_map):
        msg = f"Allegro header has no layer-map entry for class 0x{class_id:02X}"
        raise ValueError(msg)
    key = record_set.header.layer_map[class_id].layer_list_key
    record = layer_list_records.get(key)
    if record is None:
        msg = f"Allegro class 0x{class_id:02X} references missing 0x2A layer list {key}"
        raise ValueError(msg)
    return record


def _layer_entries(record: AllegroRecord) -> tuple[AllegroLayerListEntry, ...]:
    entries = record.payload.get("layer_entries", ())
    if not _is_layer_entries(entries):
        msg = f"Allegro 0x2A record {record.key} has malformed layer entries"
        raise ValueError(msg)
    return entries


def _is_layer_entries(value: object) -> TypeGuard[tuple[AllegroLayerListEntry, ...]]:
    if not _is_object_tuple(value):
        return False
    return all(isinstance(entry, AllegroLayerListEntry) for entry in value)


def _is_object_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    return isinstance(value, tuple)


def _entry_name(entry: AllegroLayerListEntry, string_table: Mapping[int, str]) -> str:
    if entry.name:
        return entry.name
    if entry.name_string_key is None:
        return ""
    return string_table.get(entry.name_string_key, "")


def _resolved_entry_name(
    entry: AllegroLayerListEntry,
    string_table: Mapping[int, str],
    *,
    fallback: str,
    class_id: int,
    record: AllegroRecord,
    diagnostics: list[AllegroRecordDiagnostic],
) -> str:
    name = _entry_name(entry, string_table)
    if name:
        return name
    if entry.name_string_key is not None:
        diagnostics.append(
            AllegroRecordDiagnostic(
                code="unresolved-layer-name",
                message=(
                    f"layer list {record.key} class 0x{class_id:02X} subclass "
                    f"{entry.index} references missing string key {entry.name_string_key}"
                ),
                offset=record.offset,
                tag=record.tag,
                key=record.key,
                reference_key=entry.name_string_key,
            )
        )
    return fallback


def _copper_roles(index: int, count: int, name: str) -> tuple[LayerRole, ...]:
    roles: list[LayerRole] = [LayerRole.COPPER]
    if index == 0:
        roles.extend([LayerRole.FRONT, LayerRole.OUTER])
    elif index == count - 1:
        roles.extend([LayerRole.BACK, LayerRole.OUTER])
    else:
        roles.append(LayerRole.INNER)
    roles.append(_copper_electrical_role(name))
    return tuple(roles)


def _copper_electrical_role(name: str) -> LayerRole:
    normalized = name.lower()
    if "plane" in normalized:
        return LayerRole.PLANE
    if any(token in normalized for token in ("gnd", "ground", "pwr", "power", "vcc", "vdd")):
        return LayerRole.POWER
    return LayerRole.SIGNAL


def _class_subclass_roles(class_id: int, subclass_id: int, name: str) -> tuple[LayerRole, ...]:
    fixed = _fixed_roles(class_id, subclass_id)
    if fixed:
        return fixed
    roles = list(_class_roles(class_id))
    roles.extend(_name_roles(name))
    return tuple(roles)


def _class_roles(class_id: int) -> tuple[LayerRole, ...]:
    if class_id in {_CLASS_PACKAGE_KEEPIN, _CLASS_ROUTE_KEEPIN}:
        return (LayerRole.KEEPOUT,)
    if class_id in {_CLASS_PACKAGE_KEEPOUT, _CLASS_ROUTE_KEEPOUT, _CLASS_VIA_KEEPOUT}:
        return (LayerRole.KEEPOUT,)
    if class_id == _CLASS_PACKAGE_GEOMETRY:
        return (LayerRole.MECHANICAL, LayerRole.FABRICATION, LayerRole.COMPONENT_OUTLINE)
    if class_id == _CLASS_BOARD_GEOMETRY:
        return (LayerRole.MECHANICAL, LayerRole.BOARD)
    if class_id == _CLASS_MANUFACTURING:
        return (LayerRole.FABRICATION,)
    if class_id == _CLASS_REF_DES:
        return (LayerRole.FABRICATION, LayerRole.DESIGNATOR)
    if class_id == _CLASS_COMPONENT_VALUE:
        return (LayerRole.FABRICATION, LayerRole.VALUE)
    if class_id == _CLASS_DEVICE_TYPE:
        return (LayerRole.FABRICATION,)
    if class_id == _CLASS_DRAWING_FORMAT:
        return (LayerRole.MECHANICAL, LayerRole.DRAWING)
    if class_id in {_CLASS_ANTI_ETCH, _CLASS_BOUNDARY}:
        return (LayerRole.COPPER,)
    if class_id == _CLASS_CONSTRAINTS_REGION:
        return (LayerRole.KEEPOUT,)
    return (LayerRole.USER,)


def _fixed_roles(class_id: int, subclass_id: int) -> tuple[LayerRole, ...]:
    if class_id in {
        _CLASS_COMPONENT_VALUE,
        _CLASS_DEVICE_TYPE,
        _CLASS_REF_DES,
        _CLASS_TOLERANCE,
        _CLASS_USER_PART_NUMBER,
    }:
        common = _component_text_roles(class_id, subclass_id)
        if common:
            return common
    return _FIXED_ROLES.get((class_id, subclass_id), ())


def _static_layer_roles() -> tuple[tuple[tuple[int, int], tuple[LayerRole, ...]], ...]:
    roles = dict(_FIXED_ROLES)
    for class_id in (
        _CLASS_COMPONENT_VALUE,
        _CLASS_DEVICE_TYPE,
        _CLASS_REF_DES,
        _CLASS_TOLERANCE,
        _CLASS_USER_PART_NUMBER,
    ):
        for subclass_id in range(0xF8, 0xFE):
            component_roles = _component_text_roles(class_id, subclass_id)
            if component_roles:
                roles[(class_id, subclass_id)] = component_roles
    return tuple(roles.items())


def _component_text_roles(class_id: int, subclass_id: int) -> tuple[LayerRole, ...]:
    if subclass_id not in {0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD}:
        return ()
    base = [LayerRole.FABRICATION]
    if class_id == _CLASS_COMPONENT_VALUE:
        base.append(LayerRole.VALUE)
    elif class_id == _CLASS_REF_DES:
        base.append(LayerRole.DESIGNATOR)
    if subclass_id in {0xF8, 0xFA, 0xFC}:
        base.append(LayerRole.BACK)
    elif subclass_id in {0xF9, 0xFB, 0xFD}:
        base.append(LayerRole.FRONT)
    if subclass_id in {0xFA, 0xFB} and class_id in _SILKSCREEN_COMPONENT_TEXT_CLASSES:
        return tuple([LayerRole.SILKSCREEN, *base])
    if subclass_id in {0xFC, 0xFD} and class_id in _ASSEMBLY_COMPONENT_TEXT_CLASSES:
        return tuple([LayerRole.MECHANICAL, LayerRole.ASSEMBLY, *base])
    return tuple(base)


def _name_roles(name: str) -> tuple[LayerRole, ...]:
    normalized = name.lower().replace("-", "_").replace(" ", "_")
    roles: list[LayerRole] = []
    if any(token in normalized for token in ("soldermask", "solder_mask", "solder", "smask")):
        roles.append(LayerRole.SOLDER_MASK)
    if "paste" in normalized:
        roles.append(LayerRole.SOLDER_PASTE)
    if "silk" in normalized:
        roles.append(LayerRole.SILKSCREEN)
    if "assembly" in normalized or normalized.startswith("ass"):
        roles.append(LayerRole.ASSEMBLY)
    if "fab" in normalized:
        roles.append(LayerRole.FABRICATION)
    if "drill" in normalized:
        roles.extend([LayerRole.DRILL, LayerRole.DRILL_DRAWING])
    if "outline" in normalized:
        roles.append(LayerRole.EDGE)
    if "top" in normalized or normalized.endswith("_t") or normalized.endswith("top.gbr"):
        roles.append(LayerRole.FRONT)
    if "bottom" in normalized or normalized.endswith("_b") or normalized.endswith("bot.gbr"):
        roles.append(LayerRole.BACK)
    return tuple(roles)


def _display_name(class_id: int, subclass_id: int) -> str:
    class_name = _CLASS_NAMES.get(class_id, f"Class {class_id:02X}")
    return f"{class_name} Subclass {subclass_id:02X}"


def _metadata(
    *,
    native_class_id: int,
    native_subclass_id: int,
    native_layer_name: str,
    layer_list_key: int | None,
    entry: AllegroLayerListEntry,
) -> PcbLayerMetadata:
    properties = {
        "native_class_id": str(native_class_id),
        "native_class_name": _CLASS_NAMES.get(native_class_id, f"Class {native_class_id:02X}"),
        "native_subclass_id": str(native_subclass_id),
        "native_layer_name": native_layer_name,
    }
    if layer_list_key is not None:
        properties["native_layer_list_key"] = str(layer_list_key)
    if entry.name_string_key is not None:
        properties["native_name_string_key"] = str(entry.name_string_key)
    if entry.properties is not None:
        properties["native_layer_properties"] = str(entry.properties)
    if entry.unidentified_word is not None:
        properties["native_layer_unidentified_word"] = str(entry.unidentified_word)
    return PcbLayerMetadata(
        source_format="allegro",
        native_type="class_subclass",
        native_kind=properties["native_class_name"],
        native_id=f"{native_class_id}:{native_subclass_id}",
        native_index=native_subclass_id,
        native_user_name=native_layer_name,
        properties=properties,
    )


def _stackup_from_copper_layers(copper_layers: list[PcbLayer]) -> Stackup | None:
    if not copper_layers:
        return None
    return Stackup(
        layers=[
            StackupLayer(name=layer.name, layer_type="copper", side=layer.side)
            for layer in copper_layers
        ]
    )
