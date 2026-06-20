"""Assemble decoded Allegro source records into the PCB domain model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    Board,
    LayerRole,
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbCircle,
    PcbClosedPath,
    PcbConductor,
    PcbDrill,
    PcbFootprint,
    PcbFootprintMetadata,
    PcbKeepout,
    PcbLayer,
    PcbLine,
    PcbMetadata,
    PcbNet,
    PcbObjectMetadata,
    PcbPad,
    PcbPolygon,
    PcbPour,
    PcbPourFillMode,
    PcbPourSettings,
    PcbVia,
    PcbViaType,
)
from phosphor_eda.domain.pcb_builder import PcbBuilder
from phosphor_eda.formats.allegro.constants import AllegroBoardUnits
from phosphor_eda.formats.allegro.graph import AllegroObjectGraph, build_allegro_object_graph
from phosphor_eda.formats.allegro.graphics import extract_allegro_copper, extract_allegro_graphics
from phosphor_eda.formats.allegro.layers import build_allegro_layers
from phosphor_eda.formats.allegro.padstacks import AllegroExpandedPadstack, expand_allegro_padstack
from phosphor_eda.formats.allegro.primitives import (
    AllegroConductorPrimitive,
    AllegroCopper,
    AllegroGraphicPrimitive,
    AllegroGraphics,
    AllegroPourPrimitive,
    AllegroPrimitiveKind,
    AllegroPrimitiveRole,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from phosphor_eda.formats.allegro.layers import AllegroLayerMap
    from phosphor_eda.formats.allegro.records import AllegroRecord, AllegroRecordSet
    from phosphor_eda.formats.allegro.sidecars import (
        AllegroPackageSymbolSidecar,
        AllegroSidecarSet,
    )

_REFDES_RE = re.compile(r"^[A-Z]+[A-Z0-9]*\d+[A-Z0-9]*$")


@dataclass(frozen=True)
class _FootprintSource:
    record: AllegroRecord
    package_name: str


def build_allegro_board(
    record_set: AllegroRecordSet,
    *,
    name: str = "Allegro Board",
    require_board_profile: bool = False,
    sidecars: AllegroSidecarSet | None = None,
) -> Board:
    layer_map = build_allegro_layers(record_set)
    graph = build_allegro_object_graph(record_set)
    graphics = extract_allegro_graphics(record_set, layer_map)
    unit_to_mm = _unit_to_mm(record_set)
    builder = PcbBuilder(
        name,
        metadata=PcbMetadata(
            source_format="allegro",
            native_type="board",
            properties={
                "allegro_version": record_set.header.version.value if record_set.header else "",
            },
        ),
    )
    for layer in layer_map.layers:
        builder.add_layer(layer, source=_source("layer", None))
    copper_layers = tuple(layer for layer in builder.layers if layer.has_role(LayerRole.COPPER))

    nets_by_key = _add_nets(builder, record_set)
    matched_padstack_sidecars: set[Path] = set()
    padstacks = _padstacks(
        record_set,
        unit_to_mm,
        sidecars=sidecars,
        matched_sidecars=matched_padstack_sidecars,
    )
    text_by_wrapper = _text_by_wrapper(record_set)
    pad_definitions = {
        record.key: record
        for record in record_set.records
        if record.tag == 0x0D and record.key is not None
    }
    footprint_sources = _footprint_sources(record_set, graph)
    matched_package_sidecars: set[Path] = set()
    footprints_by_instance_key = _add_footprints(
        builder,
        record_set,
        footprint_sources=footprint_sources,
        unit_to_mm=unit_to_mm,
        text_by_wrapper=text_by_wrapper,
        sidecars=sidecars,
        matched_sidecars=matched_package_sidecars,
    )

    for component in (record for record in record_set.records if record.tag == 0x07):
        footprint_key = _payload_int(component, "footprint_instance_key")
        footprint = footprints_by_instance_key.get(footprint_key)
        if footprint is None:
            continue
        first_pad_key = _payload_int(component, "first_pad_key")
        current_key = first_pad_key
        seen: set[int] = set()
        while current_key and current_key not in seen:
            seen.add(current_key)
            pad_record = graph.by_key.get(current_key)
            if pad_record is None or pad_record.tag != 0x32:
                break
            _add_pad(
                builder,
                pad_record,
                footprint=footprint,
                pad_definitions=pad_definitions,
                padstacks=padstacks,
                nets_by_key=nets_by_key,
                unit_to_mm=unit_to_mm,
                copper_layers=copper_layers,
                text_by_wrapper=text_by_wrapper,
            )
            current_key = _payload_int(pad_record, "next_in_component_key")

    _add_vias(
        builder,
        record_set,
        padstacks=padstacks,
        nets_by_key=nets_by_key,
        unit_to_mm=unit_to_mm,
        copper_layers=copper_layers,
    )
    copper = _add_conductors(
        builder,
        record_set,
        layer_map=layer_map,
        graph=graph,
        nets_by_key=nets_by_key,
        footprints_by_instance_key=footprints_by_instance_key,
    )
    _add_graphics(builder, graphics, include_copper_artwork=False)
    diagnostic_count = (
        len(layer_map.diagnostics) + len(graphics.diagnostics) + len(copper.diagnostics)
    )
    if diagnostic_count:
        builder.metadata.properties["parse_diagnostic_count"] = str(diagnostic_count)
    _add_sidecar_board_metadata(
        builder,
        sidecars,
        matched_padstack_sidecars=matched_padstack_sidecars,
        matched_package_sidecars=matched_package_sidecars,
    )

    board = builder.build(require_board_profile=require_board_profile)
    board.stackup = layer_map.stackup
    return board


def build_allegro_graphics_board(record_set: AllegroRecordSet, *, name: str) -> Board:
    """Assemble the PR04 graphics subset into a strict PCB ``Board``."""
    layer_map = build_allegro_layers(record_set)
    graphics = extract_allegro_graphics(record_set, layer_map)
    metadata = PcbMetadata(source_format="allegro")
    diagnostic_count = len(layer_map.diagnostics) + len(graphics.diagnostics)
    if diagnostic_count:
        metadata.properties["parse_diagnostic_count"] = str(diagnostic_count)

    builder = PcbBuilder(name, metadata=metadata)
    for layer in layer_map.layers:
        builder.add_layer(layer, source="allegro layers")

    _add_graphics(builder, graphics, include_copper_artwork=True)

    board = builder.build(require_board_profile=True)
    board.stackup = layer_map.stackup
    return board


def _add_nets(builder: PcbBuilder, record_set: AllegroRecordSet) -> dict[int, PcbNet]:
    by_key: dict[int, PcbNet] = {}
    string_table = _strings(record_set)
    next_number = 1
    for record in record_set.records:
        if record.tag != 0x1B or record.key is None:
            continue
        native_name = _string(string_table, _payload_int(record, "net_name_key"))
        name = native_name or f"NET_{record.key}"
        net = builder.add_net(
            PcbNet(
                number=next_number,
                name=name,
                metadata=PcbMetadata(
                    source_format="allegro",
                    native_type="net",
                    native_id=str(record.key),
                    properties={
                        "native_name_string_key": str(_payload_int(record, "net_name_key")),
                        "native_assignment_key": str(_payload_int(record, "assignment_key")),
                    },
                ),
            ),
            source=_source("net", record),
        )
        by_key[record.key] = net
        next_number += 1
    for record in record_set.records:
        if record.tag != 0x04 or record.key is None:
            continue
        net = by_key.get(_payload_int(record, "net_key"))
        if net is not None:
            by_key[record.key] = net
    return by_key


def _padstacks(
    record_set: AllegroRecordSet,
    unit_to_mm: float,
    *,
    sidecars: AllegroSidecarSet | None = None,
    matched_sidecars: set[Path] | None = None,
) -> dict[int, AllegroExpandedPadstack]:
    string_table = _strings(record_set)
    sidecars_by_name = sidecars.padstacks_by_name if sidecars is not None else {}
    result: dict[int, AllegroExpandedPadstack] = {}
    for record in record_set.records:
        if record.tag != 0x1C or record.key is None:
            continue
        padstack_name = _string(string_table, _payload_int(record, "pad_name_key"))
        if not padstack_name:
            padstack_name = f"PADSTACK_{record.key}"
        sidecar = sidecars_by_name.get(padstack_name.casefold())
        if sidecar is not None and matched_sidecars is not None:
            matched_sidecars.add(sidecar.path)
        result[record.key] = expand_allegro_padstack(
            record,
            name=padstack_name,
            unit_to_mm=unit_to_mm,
            sidecar=sidecar,
        )
    return result


def _footprint_sources(
    record_set: AllegroRecordSet,
    graph: AllegroObjectGraph,
) -> dict[int, _FootprintSource]:
    string_table = _strings(record_set)
    result: dict[int, _FootprintSource] = {}
    for definition in (record for record in record_set.records if record.tag == 0x2B):
        package_name = _string(string_table, _payload_int(definition, "footprint_name_key"))
        if not package_name:
            package_name = f"PACKAGE_{definition.key or 'unknown'}"
        walk = graph.walk_key_chain(head_key=_payload_int(definition, "first_instance_key"))
        for instance in walk.records:
            if instance.tag == 0x2D and instance.key is not None:
                result[instance.key] = _FootprintSource(
                    record=definition,
                    package_name=package_name,
                )
    return result


def _add_footprints(
    builder: PcbBuilder,
    record_set: AllegroRecordSet,
    *,
    footprint_sources: Mapping[int, _FootprintSource],
    unit_to_mm: float,
    text_by_wrapper: Mapping[int, str],
    sidecars: AllegroSidecarSet | None = None,
    matched_sidecars: set[Path] | None = None,
) -> dict[int, PcbFootprint]:
    string_table = _strings(record_set)
    copper_front = _front_copper(builder.layers)
    copper_back = _back_copper(builder.layers)
    package_symbols_by_name = sidecars.package_symbols_by_name if sidecars is not None else {}
    used_refs: set[str] = set()
    result: dict[int, PcbFootprint] = {}
    for component in (record for record in record_set.records if record.tag == 0x07):
        instance_key = _payload_int(component, "footprint_instance_key")
        instance = record_set.by_key.get(instance_key)
        if instance is None or instance.tag != 0x2D:
            continue
        package = footprint_sources.get(instance_key)
        refdes = _string(string_table, _payload_int(component, "refdes_string_key"))
        if not _looks_like_refdes(refdes):
            text = text_by_wrapper.get(_payload_int(instance, "text_key"), "")
            refdes = text if _looks_like_refdes(text) else ""
        if not refdes:
            refdes = f"REF_{component.key or instance_key}"
        refdes = _unique_ref(refdes, used_refs)
        side = _payload_int(instance, "placement_side")
        layer = copper_back if side == 1 else copper_front
        package_name = package.package_name if package else ""
        footprint_metadata = {
            "native_component_instance_key": str(component.key or ""),
            "native_refdes_string_key": str(_payload_int(component, "refdes_string_key")),
            "native_placement_side": str(side),
        }
        if package_name:
            package_sidecar = package_symbols_by_name.get(package_name.casefold())
            if package_sidecar is not None:
                if matched_sidecars is not None:
                    matched_sidecars.add(package_sidecar.path)
                footprint_metadata.update(_package_symbol_metadata(package_sidecar))
        footprint = builder.add_footprint(
            PcbFootprint(
                reference=refdes,
                footprint_lib=package_name,
                x=_coord(instance, "coord_x", unit_to_mm),
                y=_coord(instance, "coord_y", unit_to_mm),
                rotation=_payload_int(instance, "rotation_mdeg") / 1000.0,
                layer=layer,
                metadata=PcbFootprintMetadata(
                    source_format="allegro",
                    native_type="footprint_instance",
                    native_id=str(instance.key),
                    source_designator=refdes,
                    source_footprint_library=package_name,
                    properties=footprint_metadata,
                ),
            ),
            source=_source("footprint", instance),
        )
        result[instance_key] = footprint
    return result


def _add_sidecar_board_metadata(
    builder: PcbBuilder,
    sidecars: AllegroSidecarSet | None,
    *,
    matched_padstack_sidecars: set[Path],
    matched_package_sidecars: set[Path],
) -> None:
    if sidecars is None or not sidecars.has_evidence:
        return
    builder.metadata.properties["allegro_sidecar_root"] = str(sidecars.root)
    builder.metadata.properties["allegro_sidecar_count"] = str(
        len(sidecars.padstacks) + len(sidecars.package_symbols)
    )
    if sidecars.padstacks:
        builder.metadata.properties["allegro_padstack_sidecar_count"] = str(len(sidecars.padstacks))
        unmatched_padstacks = sorted(
            sidecar.name
            for sidecar in sidecars.padstacks
            if sidecar.path not in matched_padstack_sidecars
        )
        if unmatched_padstacks:
            builder.metadata.properties["allegro_unmatched_padstack_sidecars"] = ";".join(
                unmatched_padstacks
            )
    if sidecars.package_symbols:
        builder.metadata.properties["allegro_package_symbol_sidecar_count"] = str(
            len(sidecars.package_symbols)
        )
        unmatched_package_symbols = sorted(
            sidecar.name
            for sidecar in sidecars.package_symbols
            if sidecar.path not in matched_package_sidecars
        )
        if unmatched_package_symbols:
            builder.metadata.properties["allegro_unmatched_package_symbol_sidecars"] = ";".join(
                unmatched_package_symbols
            )
    if sidecars.diagnostics:
        builder.metadata.properties["allegro_sidecar_diagnostic_count"] = str(
            len(sidecars.diagnostics)
        )
        builder.metadata.properties["allegro_sidecar_diagnostic_codes"] = ";".join(
            diagnostic.code for diagnostic in sidecars.diagnostics
        )
        builder.metadata.properties["allegro_sidecar_diagnostic_paths"] = ";".join(
            str(diagnostic.path) for diagnostic in sidecars.diagnostics
        )


def _package_symbol_metadata(
    package_sidecar: AllegroPackageSymbolSidecar,
) -> dict[str, str]:
    return {
        "sidecar_package_symbol_path": str(package_sidecar.path),
        "sidecar_package_symbol_name": package_sidecar.name,
        "sidecar_package_symbol_kind": package_sidecar.kind,
        "sidecar_package_symbol_byte_size": str(package_sidecar.byte_size),
        "sidecar_package_symbol_signature": package_sidecar.signature_hex,
    }


def _add_pad(
    builder: PcbBuilder,
    record: AllegroRecord,
    *,
    footprint: PcbFootprint,
    pad_definitions: Mapping[int, AllegroRecord],
    padstacks: Mapping[int, AllegroExpandedPadstack],
    nets_by_key: Mapping[int, PcbNet],
    unit_to_mm: float,
    copper_layers: tuple[PcbLayer, ...],
    text_by_wrapper: Mapping[int, str],
) -> None:
    pad_definition = pad_definitions.get(_payload_int(record, "pad_definition_key"))
    if pad_definition is None:
        return
    padstack = padstacks.get(_payload_int(pad_definition, "padstack_key"))
    if padstack is None:
        return
    layers = _pad_layers(copper_layers, footprint, padstack)
    drill = (
        _add_drill(
            builder,
            record,
            padstack,
            layers,
            unit_to_mm,
            owner_prefix="pad",
        )
        if _has_drill(padstack)
        else None
    )
    metadata = _object_metadata(
        record,
        "placed_pad",
        {
            **padstack.metadata,
            "native_pad_definition_key": str(pad_definition.key or ""),
            "native_parent_footprint_key": str(_payload_int(record, "parent_footprint_key")),
        },
    )
    builder.add_pad_object(
        PcbPad(
            id=f"pad-{record.key}",
            number=_pad_number(record, text_by_wrapper),
            x=_coord(record, "coord_x", unit_to_mm),
            y=_coord(record, "coord_y", unit_to_mm),
            stack=padstack.stack,
            pad_type=padstack.pad_type,
            layers=layers,
            net=nets_by_key.get(_payload_int(record, "net_key")),
            footprint=footprint,
            drill=drill,
            metadata=metadata,
        ),
        source=_source("pad", record),
    )


def _add_vias(
    builder: PcbBuilder,
    record_set: AllegroRecordSet,
    *,
    padstacks: Mapping[int, AllegroExpandedPadstack],
    nets_by_key: Mapping[int, PcbNet],
    unit_to_mm: float,
    copper_layers: tuple[PcbLayer, ...],
) -> None:
    for record in record_set.records:
        if record.tag != 0x33:
            continue
        padstack = padstacks.get(_payload_int(record, "padstack_key"))
        if padstack is None or not _has_drill(padstack):
            continue
        drill = _add_drill(
            builder,
            record,
            padstack,
            copper_layers,
            unit_to_mm,
            owner_prefix="via",
        )
        builder.add_via_object(
            PcbVia(
                id=f"via-{record.key}",
                x=_coord(record, "coord_x", unit_to_mm),
                y=_coord(record, "coord_y", unit_to_mm),
                stack=padstack.stack,
                layers=copper_layers,
                drill=drill,
                net=nets_by_key.get(_payload_int(record, "net_key")),
                via_type=PcbViaType.THROUGH,
                metadata=_object_metadata(record, "via", padstack.metadata),
            ),
            source=_source("via", record),
        )


def _add_conductors(
    builder: PcbBuilder,
    record_set: AllegroRecordSet,
    *,
    layer_map: AllegroLayerMap,
    graph: AllegroObjectGraph,
    nets_by_key: Mapping[int, PcbNet],
    footprints_by_instance_key: Mapping[int, PcbFootprint],
) -> AllegroCopper:
    copper = extract_allegro_copper(record_set, layer_map, graph)
    pours_by_id: dict[str, PcbPour] = {}
    pour_fills: dict[str, list[PcbConductor]] = {}
    for primitive in copper.pours:
        pour = builder.add_pour_object(
            _pour(
                primitive,
                nets_by_key=nets_by_key,
            ),
            source=primitive.id,
        )
        pours_by_id[pour.id] = pour
    for primitive in copper.conductors:
        conductor = builder.add_conductor_object(
            _conductor(
                primitive,
                nets_by_key=nets_by_key,
                footprints_by_instance_key=footprints_by_instance_key,
                pours_by_id=pours_by_id,
            ),
            source=primitive.id,
        )
        if conductor.pour is not None:
            pour_fills.setdefault(conductor.pour.id, []).append(conductor)
    for pour in pours_by_id.values():
        pour.fills = tuple(pour_fills.get(pour.id, ()))
    return copper


def _add_graphics(
    builder: PcbBuilder,
    graphics: AllegroGraphics,
    *,
    include_copper_artwork: bool,
) -> None:
    profile_elements = tuple(_profile_element(primitive) for primitive in graphics.board_profile)
    if profile_elements or include_copper_artwork:
        builder.set_board_profile(
            PcbBoardProfile(elements=profile_elements),
            source="allegro profile",
        )

    for primitive in graphics.artwork:
        if (
            not include_copper_artwork
            and primitive.layer is not None
            and primitive.layer.has_role(LayerRole.COPPER)
        ):
            # Copper-layer graphics are emitted as conductors in the full board
            # path; adding them as artwork would double-count manufactured copper.
            continue
        builder.add_artwork_object(_artwork(primitive), source=primitive.id)
    for primitive in graphics.keepouts:
        builder.add_keepout_object(_keepout(primitive), source=primitive.id)


def _pour(
    primitive: AllegroPourPrimitive,
    *,
    nets_by_key: Mapping[int, PcbNet],
) -> PcbPour:
    return PcbPour(
        id=primitive.id,
        boundary=PcbClosedPath.from_points(primitive.boundary.points),
        layers=(primitive.layer,),
        net=nets_by_key.get(primitive.net_key) if primitive.net_key is not None else None,
        settings=PcbPourSettings(fill_mode=_pour_fill_mode(primitive)),
        footprint=None,
        metadata=primitive.metadata,
    )


def _pour_fill_mode(primitive: AllegroPourPrimitive) -> PcbPourFillMode:
    properties = primitive.metadata.properties
    if (
        "native_first_keepout_key" in properties
        or properties.get("dynamic_shape_degraded") == "true"
    ):
        return PcbPourFillMode.UNKNOWN
    return PcbPourFillMode.SOLID


def _conductor(
    primitive: AllegroConductorPrimitive,
    *,
    nets_by_key: Mapping[int, PcbNet],
    footprints_by_instance_key: Mapping[int, PcbFootprint],
    pours_by_id: Mapping[str, PcbPour],
) -> PcbConductor:
    return PcbConductor(
        id=primitive.id,
        kind=primitive.kind,
        layer=primitive.layer,
        data=primitive.data,
        net=nets_by_key.get(primitive.net_key) if primitive.net_key is not None else None,
        footprint=(
            footprints_by_instance_key.get(primitive.footprint_key)
            if primitive.footprint_key is not None
            else None
        ),
        pour=pours_by_id.get(primitive.pour_id) if primitive.pour_id is not None else None,
        metadata=primitive.metadata,
    )


def _add_drill(
    builder: PcbBuilder,
    record: AllegroRecord,
    padstack: AllegroExpandedPadstack,
    layers: tuple[PcbLayer, ...],
    unit_to_mm: float,
    *,
    owner_prefix: str,
) -> PcbDrill:
    drill = PcbDrill(
        id=f"{owner_prefix}-drill-{record.key}",
        x=_coord(record, "coord_x", unit_to_mm),
        y=_coord(record, "coord_y", unit_to_mm),
        diameter=_drill_diameter(padstack),
        shape=padstack.drill_shape,
        plating=padstack.plating,
        width=padstack.drill_width,
        height=padstack.drill_height,
        layers=layers,
        metadata=_object_metadata(record, f"{owner_prefix}_drill", padstack.metadata),
    )
    return builder.add_drill_object(drill, source=_source("drill", record))


def _pad_layers(
    copper_layers: tuple[PcbLayer, ...],
    footprint: PcbFootprint,
    padstack: AllegroExpandedPadstack,
) -> tuple[PcbLayer, ...]:
    if _has_drill(padstack):
        return copper_layers
    return (footprint.layer,)


def _has_drill(padstack: AllegroExpandedPadstack) -> bool:
    return (
        padstack.drill_diameter > 0.0 or padstack.drill_width > 0.0 or padstack.drill_height > 0.0
    )


def _drill_diameter(padstack: AllegroExpandedPadstack) -> float:
    if padstack.drill_diameter > 0.0:
        return padstack.drill_diameter
    return max(padstack.drill_width, padstack.drill_height)


def _front_copper(layers: list[PcbLayer]) -> PcbLayer:
    for layer in layers:
        if layer.has_role(LayerRole.COPPER) and layer.has_role(LayerRole.FRONT):
            return layer
    return layers[0]


def _back_copper(layers: list[PcbLayer]) -> PcbLayer:
    for layer in layers:
        if layer.has_role(LayerRole.COPPER) and layer.has_role(LayerRole.BACK):
            return layer
    return _front_copper(layers)


def _unit_to_mm(record_set: AllegroRecordSet) -> float:
    if record_set.header is None:
        return 1.0
    divisor = record_set.header.unit_divisor
    units = record_set.header.board_units
    if units is AllegroBoardUnits.MILS:
        return 0.0254 / divisor
    if units is AllegroBoardUnits.INCHES:
        return 25.4 / divisor
    if units is AllegroBoardUnits.MILLIMETERS:
        return 1.0 / divisor
    if units is AllegroBoardUnits.CENTIMETERS:
        return 10.0 / divisor
    if units is AllegroBoardUnits.MICROMETERS:
        return 0.001 / divisor
    return 1.0 / divisor


def _text_by_wrapper(record_set: AllegroRecordSet) -> dict[int, str]:
    text_records = {
        record.key: str(record.payload.get("text", ""))
        for record in record_set.records
        if record.tag == 0x31 and record.key is not None
    }
    result: dict[int, str] = {}
    for record in record_set.records:
        if record.tag != 0x30 or record.key is None:
            continue
        text = text_records.get(_payload_int(record, "string_graphic_key"), "")
        if text:
            result[record.key] = text
    return result


def _strings(record_set: AllegroRecordSet) -> Mapping[int, str]:
    return record_set.string_table.by_id if record_set.string_table is not None else {}


def _string(string_table: Mapping[int, str], key: int) -> str:
    return string_table.get(key, "") if key else ""


def _payload_int(record: AllegroRecord, key: str) -> int:
    value = record.payload.get(key, 0)
    return value if isinstance(value, int) else 0


def _coord(record: AllegroRecord, key: str, unit_to_mm: float) -> float:
    return _payload_int(record, key) * unit_to_mm


def _pad_number(record: AllegroRecord, text_by_wrapper: Mapping[int, str]) -> str:
    text = text_by_wrapper.get(_payload_int(record, "name_text_key"), "")
    if text:
        return text
    return str(record.key or "")


def _object_metadata(
    record: AllegroRecord, native_type: str, properties: Mapping[str, str]
) -> PcbObjectMetadata:
    return PcbObjectMetadata(
        source_format="allegro",
        native_type=native_type,
        native_id=str(record.key or ""),
        source_collection=f"record_0x{record.tag:02X}",
        native_layer_id=_native_layer_id(record),
        properties=dict(properties),
    )


def _native_layer_id(record: AllegroRecord) -> str:
    class_id = _payload_int(record, "layer_class_id")
    subclass_id = _payload_int(record, "layer_subclass_id")
    if class_id == 0 and subclass_id == 0:
        return ""
    return f"{class_id}:{subclass_id}"


def _looks_like_refdes(value: str) -> bool:
    return bool(_REFDES_RE.match(value))


def _unique_ref(refdes: str, used_refs: set[str]) -> str:
    candidate = refdes
    suffix = 2
    while candidate.upper() in used_refs:
        candidate = f"{refdes}_{suffix}"
        suffix += 1
    used_refs.add(candidate.upper())
    return candidate


def _source(kind: str, record: AllegroRecord | None) -> str:
    if record is None:
        return f"allegro:{kind}"
    return f"allegro:{kind}:0x{record.tag:02X}:{record.key or 'unkeyed'}"


def _profile_element(primitive: AllegroGraphicPrimitive) -> PcbBoardProfileElement:
    if not isinstance(primitive.data, PcbLine | PcbArc | PcbCircle | PcbPolygon):
        msg = f"board profile primitive {primitive.id} has unsupported data"
        raise ValueError(msg)
    return PcbBoardProfileElement(
        id=primitive.id,
        kind=_artwork_kind(primitive.kind),
        layer=primitive.layer,
        data=primitive.data,
        is_cutout=primitive.is_cutout,
        metadata=primitive.metadata,
    )


def _artwork(primitive: AllegroGraphicPrimitive) -> PcbArtwork:
    return PcbArtwork(
        id=primitive.id,
        kind=_artwork_kind(primitive.kind),
        purpose=_artwork_purpose(primitive),
        layer=primitive.layer,
        data=primitive.data,
        metadata=primitive.metadata,
    )


def _keepout(primitive: AllegroGraphicPrimitive) -> PcbKeepout:
    if not isinstance(primitive.data, PcbPolygon):
        msg = f"keepout primitive {primitive.id} has unsupported data"
        raise ValueError(msg)
    if primitive.layer is None:
        msg = f"keepout primitive {primitive.id} has no resolved layer"
        raise ValueError(msg)
    return PcbKeepout(
        id=primitive.id,
        boundary=PcbClosedPath.from_points(primitive.data.points),
        layers=(primitive.layer,),
        metadata=primitive.metadata,
    )


def _artwork_kind(kind: AllegroPrimitiveKind) -> PcbArtworkKind:
    if kind is AllegroPrimitiveKind.LINE:
        return PcbArtworkKind.LINE
    if kind is AllegroPrimitiveKind.ARC:
        return PcbArtworkKind.ARC
    if kind is AllegroPrimitiveKind.TEXT:
        return PcbArtworkKind.TEXT
    return PcbArtworkKind.POLYGON


def _artwork_purpose(primitive: AllegroGraphicPrimitive) -> PcbArtworkPurpose:
    if primitive.has_role(AllegroPrimitiveRole.TEXT):
        return PcbArtworkPurpose.USER_TEXT
    return PcbArtworkPurpose.MECHANICAL
