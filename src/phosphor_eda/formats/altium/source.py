"""Altium-native schematic source extraction.

Source extraction keeps Altium records in their own connectivity vocabulary.
It does not construct the public ``Schematic``/``Page``/``Net`` model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import ScopeId
from phosphor_eda.formats.altium.project import AltiumProject, parse_prjpcb_file
from phosphor_eda.formats.altium.records import (
    AltiumRecord,
    ComponentRec,
    HarnessConnectorRec,
    HarnessEntryRec,
    HarnessTypeRec,
    PinRec,
    SignalHarnessRec,
)
from phosphor_eda.formats.altium.sheet_builder import (
    LocalNetResolution,
    SheetRecords,
    compute_harness_entry_coords,
    load_sheet,
    resolve_local_net_groups,
)
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.paths import resolve_document_reference

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@dataclass(slots=True)
class AltiumNetLabel:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: tuple[int, int]
    has_overline: bool = False
    kind: str = field(default="net_label", init=False)


@dataclass(slots=True)
class AltiumPowerPort:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: tuple[int, int]
    style: int
    orientation: int
    show_net_name: bool
    has_overline: bool = False
    kind: str = field(default="power_port", init=False)


@dataclass(slots=True)
class AltiumPort:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: tuple[int, int]
    wire_coord: tuple[int, int]
    harness_type: str
    io_type: int
    style: int
    has_overline: bool = False
    kind: str = field(default="port", init=False)


@dataclass(slots=True)
class AltiumSheetSymbol:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    child_source_file: str
    location: tuple[int, int]
    x_size: int
    y_size: int


@dataclass(slots=True)
class AltiumSheetEntry:
    id: str
    scope_id: ScopeId
    source_index: int
    sheet_symbol_id: str
    name: str
    coord: tuple[int, int]
    side: int
    distance_from_top: int
    harness_type: str
    io_type: int
    has_overline: bool = False
    kind: str = field(default="sheet_entry", init=False)


@dataclass(slots=True)
class AltiumHarnessConnector:
    id: str
    scope_id: ScopeId
    source_index: int
    harness_type: str
    location: tuple[int, int]
    x_size: int
    y_size: int


@dataclass(slots=True)
class AltiumHarnessMember:
    id: str
    scope_id: ScopeId
    source_index: int
    connector_id: str
    name: str
    coord: tuple[int, int]
    side: int
    distance_from_top: int
    has_overline: bool = False


@dataclass(slots=True)
class AltiumPinOccurrence:
    id: str
    scope_id: ScopeId
    source_index: int
    local_net_id: str
    component_source_id: str
    component_reference: str
    pin_designator: str
    pin_name: str
    location: tuple[int, int]
    tip: tuple[int, int]
    component_occurrence_source_id: str = ""
    no_connect: bool = False
    component_part_id: str = ""
    component_part: str = ""
    component_description: str = ""
    component_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AltiumLocalNet:
    id: str
    scope_id: ScopeId
    wire_points: set[tuple[int, int]]
    pin_ids: list[str]
    net_labels: list[AltiumNetLabel]
    power_ports: list[AltiumPowerPort]
    ports: list[AltiumPort]
    sheet_entries: list[AltiumSheetEntry]
    harness_members: list[AltiumHarnessMember]
    generated_name: str


@dataclass(slots=True)
class AltiumSheetSource:
    id: str
    name: str
    source_file: str
    scope_id: ScopeId
    local_nets: list[AltiumLocalNet]
    sheet_symbols: list[AltiumSheetSymbol]
    sheet_entries: list[AltiumSheetEntry]
    harness_connectors: list[AltiumHarnessConnector]
    harness_members: list[AltiumHarnessMember]
    pin_occurrences: list[AltiumPinOccurrence]


@dataclass(slots=True)
class AltiumSourceDesign:
    name: str
    project: AltiumProject
    sheets: dict[str, AltiumSheetSource]
    root_sheet_name: str = ""


def _source_id(sheet_id: str, kind: str, source_index: int) -> str:
    return f"{sheet_id}:{kind}:{source_index}"


def _source_file_key(source_file: str) -> str:
    return source_file.replace("\\", "/")


def _parent_dir(source_file_key: str) -> str:
    """Return the directory portion of a normalized source-file key, or ``""``."""
    head, sep, _ = source_file_key.rpartition("/")
    return head if sep else ""


def _default_sheet_id(sheet: SheetRecords, source_file: str) -> str:
    source_key = _source_file_key(source_file)
    if source_key:
        return f"sheet:{source_key}"
    return f"sheet:{sheet.name}"


def _sheet_symbol_sources(
    sheet: SheetRecords,
    sheet_id: str,
    scope_id: ScopeId,
) -> tuple[list[AltiumSheetSymbol], dict[int, str]]:
    names_by_owner: dict[int, str] = {}
    files_by_owner: dict[int, str] = {}
    for rec in sheet.sheet_names:
        names_by_owner[rec.owner_index] = rec.text
    for rec in sheet.file_names:
        files_by_owner[rec.owner_index] = rec.text

    symbols: list[AltiumSheetSymbol] = []
    symbol_ids_by_owner: dict[int, str] = {}
    for symbol in sheet.sheet_symbols:
        owner_key = symbol.index - 1
        symbol_id = _source_id(sheet_id, "sheet_symbol", symbol.index)
        symbol_ids_by_owner[owner_key] = symbol_id
        symbols.append(
            AltiumSheetSymbol(
                id=symbol_id,
                scope_id=scope_id,
                source_index=symbol.index,
                name=names_by_owner.get(owner_key, ""),
                child_source_file=files_by_owner.get(owner_key, ""),
                location=symbol.location,
                x_size=symbol.x_size,
                y_size=symbol.y_size,
            ),
        )

    return symbols, symbol_ids_by_owner


def _sheet_entry_sources(
    sheet: SheetRecords,
    sheet_id: str,
    scope_id: ScopeId,
    symbol_ids_by_owner: dict[int, str],
) -> dict[int, AltiumSheetEntry]:
    entries: dict[int, AltiumSheetEntry] = {}
    for entry in sheet.sheet_entries:
        entries[entry.index] = AltiumSheetEntry(
            id=_source_id(sheet_id, "sheet_entry", entry.index),
            scope_id=scope_id,
            source_index=entry.index,
            sheet_symbol_id=symbol_ids_by_owner.get(entry.owner_index, ""),
            name=entry.name,
            coord=entry.coord,
            side=int(entry.side),
            distance_from_top=entry.distance_from_top,
            harness_type=entry.harness_type,
            io_type=int(entry.io_type),
            has_overline=entry.has_overline,
        )
    return entries


def _harness_sources(
    sheet: SheetRecords,
    sheet_id: str,
    scope_id: ScopeId,
) -> tuple[list[AltiumHarnessConnector], dict[int, AltiumHarnessMember]]:
    additional_records: list[AltiumRecord] = []
    for rec in sheet.records:
        if isinstance(
            rec,
            (HarnessConnectorRec, HarnessEntryRec, HarnessTypeRec, SignalHarnessRec),
        ):
            additional_records.append(rec)

    connectors_by_additional_index: dict[int, HarnessConnectorRec] = {}
    harness_types_by_owner: dict[int, str] = {}
    entries_by_owner: dict[int, list[HarnessEntryRec]] = {}
    for additional_index, rec in enumerate(additional_records):
        if isinstance(rec, HarnessConnectorRec):
            connectors_by_additional_index[additional_index] = rec
        elif isinstance(rec, HarnessTypeRec):
            harness_types_by_owner[rec.owner_index] = rec.text
        elif isinstance(rec, HarnessEntryRec):
            entries_by_owner.setdefault(rec.owner_index, []).append(rec)

    connectors: list[AltiumHarnessConnector] = []
    members: dict[int, AltiumHarnessMember] = {}
    connector_ids_by_additional_index: dict[int, str] = {}
    for additional_index, connector in connectors_by_additional_index.items():
        connector_id = _source_id(sheet_id, "harness_connector", connector.index)
        connector_ids_by_additional_index[additional_index] = connector_id
        connectors.append(
            AltiumHarnessConnector(
                id=connector_id,
                scope_id=scope_id,
                source_index=connector.index,
                harness_type=harness_types_by_owner.get(additional_index, ""),
                location=connector.location,
                x_size=connector.x_size,
                y_size=connector.y_size,
            ),
        )

    for owner_index, entries in entries_by_owner.items():
        connector_id = connector_ids_by_additional_index.get(owner_index, "")
        for entry in entries:
            members[entry.index] = AltiumHarnessMember(
                id=_source_id(sheet_id, "harness_member", entry.index),
                scope_id=scope_id,
                source_index=entry.index,
                connector_id=connector_id,
                name=entry.name,
                coord=entry.coord,
                side=entry.side,
                distance_from_top=entry.distance_from_top,
                has_overline=entry.has_overline,
            )

    return connectors, members


def _component_records(sheet: SheetRecords) -> dict[int, ComponentRec]:
    return {component.index - 1: component for component in sheet.components}


def _designators_by_owner(sheet: SheetRecords) -> dict[int, str]:
    result: dict[int, str] = {}
    for designator in sheet.designators:
        if designator.owner_index >= 0:
            result[designator.owner_index] = designator.text
    return result


def _component_parameters_by_owner(sheet: SheetRecords) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for parameter in sheet.parameters:
        if parameter.owner_index < 0 or not parameter.name or not parameter.text:
            continue
        result.setdefault(parameter.owner_index, {})[parameter.name] = parameter.text
    return result


def _component_source_id(
    sheet_id: str,
    scope_id: ScopeId,
    owner_index: int,
    component: ComponentRec | None,
) -> str:
    occurrence_source_id = _component_occurrence_source_id(sheet_id, owner_index)
    if component is not None and component.unique_id:
        return f"altium:component:{scope_id}:uid:{component.unique_id}"
    return occurrence_source_id


def _multipart_component_scope_key(scope_id: ScopeId) -> str:
    parent_path = scope_id.path[:-1]
    return "root" if not parent_path else "/".join(parent_path)


def _component_occurrence_source_id(sheet_id: str, owner_index: int) -> str:
    return _source_id(sheet_id, "component", owner_index + 1)


def _component_part_id(component: ComponentRec | None) -> str:
    if component is None:
        return ""
    return str(component.current_part_id)


def _component_part_count(pin: AltiumPinOccurrence) -> int:
    value = pin.component_metadata.get("altium_part_count", "1")
    try:
        return int(value)
    except ValueError:
        return 1


def _apply_multipart_component_identities(sheets: Iterable[AltiumSheetSource]) -> None:
    groups: dict[tuple[str, str, str, int], list[AltiumPinOccurrence]] = {}
    for sheet in sheets:
        for pin in sheet.pin_occurrences:
            part_count = _component_part_count(pin)
            if part_count <= 1 or not pin.component_reference:
                continue
            key = (
                _multipart_component_scope_key(pin.scope_id),
                pin.component_reference,
                pin.component_part,
                part_count,
            )
            groups.setdefault(key, []).append(pin)

    for (scope_key, reference, part, part_count), pins in groups.items():
        occurrence_ids = {pin.component_occurrence_source_id for pin in pins}
        part_ids = {pin.component_part_id for pin in pins if pin.component_part_id}
        if len(occurrence_ids) <= 1 or len(part_ids) <= 1:
            continue
        component_source_id = (
            f"altium:component:{scope_key}:multipart:{reference}:{part}:{part_count}"
        )
        for pin in pins:
            pin.component_source_id = component_source_id


def _component_metadata(
    component: ComponentRec | None,
    parameters: dict[str, str],
) -> dict[str, str]:
    metadata = dict(parameters)
    if component is None:
        return metadata
    if component.unique_id:
        metadata["altium_component_unique_id"] = metadata.get(
            "altium_component_unique_id",
            component.unique_id,
        )
    metadata["altium_current_part_id"] = metadata.get(
        "altium_current_part_id",
        str(component.current_part_id),
    )
    metadata["altium_part_count"] = metadata.get("altium_part_count", str(component.part_count))
    metadata["altium_display_mode"] = metadata.get(
        "altium_display_mode",
        str(component.display_mode),
    )
    return metadata


def _pin_is_visible(pin: PinRec, components_by_owner: dict[int, ComponentRec]) -> bool:
    component = components_by_owner.get(pin.owner_index)
    if component is None:
        return True
    part_matches = pin.owner_part_id == 0 or pin.owner_part_id == component.current_part_id
    return part_matches and pin.owner_part_display_mode == component.display_mode


def _root_for_point(
    point: tuple[int, int],
    sheet: SheetRecords,
    resolution: LocalNetResolution,
) -> tuple[int, int] | None:
    root = resolution.coord_to_root.get(point)
    if root is not None:
        return root
    touches = sheet.wire_index.segments_touching(point[0], point[1])
    for wire, segment_index in touches:
        return resolution.coord_to_root.get(wire.segments[segment_index][0])
    return None


def _source_sheet(
    sheet: SheetRecords,
    source_file: str,
    *,
    sheet_id: str = "",
    scope_id: ScopeId | None = None,
) -> AltiumSheetSource:
    sheet_id = sheet_id or _default_sheet_id(sheet, source_file)
    scope_id = scope_id or ScopeId(path=(sheet.name,))
    symbols, symbol_ids_by_owner = _sheet_symbol_sources(sheet, sheet_id, scope_id)
    sheet_entries_by_index = _sheet_entry_sources(
        sheet,
        sheet_id,
        scope_id,
        symbol_ids_by_owner,
    )
    harness_connectors, harness_members_by_index = _harness_sources(sheet, sheet_id, scope_id)

    resolution = resolve_local_net_groups(
        sheet,
        extra_named_coords=compute_harness_entry_coords(sheet),
    )
    root_to_net_id: dict[tuple[int, int], str] = {}
    local_nets: list[AltiumLocalNet] = []
    for ordinal, group in enumerate(resolution.groups):
        local_net_id = f"{sheet_id}:local:{ordinal:04d}:{group.root[0]}:{group.root[1]}"
        root_to_net_id[group.root] = local_net_id
        local_nets.append(
            AltiumLocalNet(
                id=local_net_id,
                scope_id=scope_id,
                wire_points=set(group.wire_points),
                pin_ids=[],
                net_labels=[
                    AltiumNetLabel(
                        id=_source_id(sheet_id, "net_label", label.index),
                        scope_id=scope_id,
                        source_index=label.index,
                        name=label.text,
                        location=label.location,
                        has_overline=label.has_overline,
                    )
                    for label in group.net_labels
                ],
                power_ports=[
                    AltiumPowerPort(
                        id=_source_id(sheet_id, "power_port", port.index),
                        scope_id=scope_id,
                        source_index=port.index,
                        name=port.text,
                        location=port.location,
                        style=int(port.style),
                        orientation=int(port.orientation),
                        show_net_name=port.show_net_name,
                        has_overline=port.has_overline,
                    )
                    for port in group.power_ports
                ],
                ports=[
                    AltiumPort(
                        id=_source_id(sheet_id, "port", port.index),
                        scope_id=scope_id,
                        source_index=port.index,
                        name=port.name,
                        location=port.location,
                        wire_coord=wire_coord,
                        harness_type=port.harness_type,
                        io_type=int(port.io_type),
                        style=int(port.style),
                        has_overline=port.has_overline,
                    )
                    for port, wire_coord in group.ports
                ],
                sheet_entries=[
                    sheet_entries_by_index[entry.index]
                    for entry in group.sheet_entries
                    if entry.index in sheet_entries_by_index
                ],
                harness_members=[
                    harness_members_by_index[member_index]
                    for member_index in sorted(harness_members_by_index)
                    if harness_members_by_index[member_index].coord in group.extra_named_coords
                ],
                generated_name=group.generated_name,
            ),
        )

    local_nets_by_id = {local_net.id: local_net for local_net in local_nets}
    components_by_owner = _component_records(sheet)
    component_parameters_by_owner = _component_parameters_by_owner(sheet)
    designator_by_owner = _designators_by_owner(sheet)
    no_connect_roots = {
        root
        for point in resolution.no_connect_wire_coords
        if (root := resolution.coord_to_root.get(point)) is not None
    }

    pin_occurrences: list[AltiumPinOccurrence] = []
    for pin in sheet.pins:
        if pin.owner_index < 0 or not _pin_is_visible(pin, components_by_owner):
            continue
        root = _root_for_point(pin.tip, sheet, resolution)
        if root is None:
            continue
        local_net_id = root_to_net_id.get(root, "")
        component = components_by_owner.get(pin.owner_index)
        component_reference = designator_by_owner.get(pin.owner_index, "")
        pin_id = _source_id(sheet_id, "pin", pin.index)
        occurrence = AltiumPinOccurrence(
            id=pin_id,
            scope_id=scope_id,
            source_index=pin.index,
            local_net_id=local_net_id,
            component_source_id=_component_source_id(
                sheet_id,
                scope_id,
                pin.owner_index,
                component,
            ),
            component_occurrence_source_id=_component_occurrence_source_id(
                sheet_id,
                pin.owner_index,
            ),
            component_reference=component_reference,
            component_part_id=_component_part_id(component),
            component_part=component.lib_reference if component is not None else "",
            component_description=component.description if component is not None else "",
            component_metadata=_component_metadata(
                component,
                component_parameters_by_owner.get(pin.owner_index, {}),
            ),
            pin_designator=pin.designator,
            pin_name=pin.name,
            location=pin.location,
            tip=pin.tip,
            no_connect=pin.tip in resolution.no_connect_wire_coords or root in no_connect_roots,
        )
        pin_occurrences.append(occurrence)
        local_net = local_nets_by_id.get(local_net_id)
        if local_net is not None:
            local_net.pin_ids.append(pin_id)

    return AltiumSheetSource(
        id=sheet_id,
        name=sheet.name,
        source_file=source_file,
        scope_id=scope_id,
        local_nets=local_nets,
        sheet_symbols=symbols,
        sheet_entries=list(sheet_entries_by_index.values()),
        harness_connectors=harness_connectors,
        harness_members=list(harness_members_by_index.values()),
        pin_occurrences=pin_occurrences,
    )


def load_project_source_sheets(
    path: Path,
    ctx: ParseContext | None = None,
) -> tuple[AltiumProject, dict[str, AltiumSheetSource]]:
    """Load all sheets from a project or single sheet into Altium source objects."""
    if ctx is None:
        ctx = ParseContext()

    sheets: dict[str, AltiumSheetSource] = {}
    if path.suffix.lower() == ".prjpcb":
        project = parse_prjpcb_file(str(path))
        project_dir = path.parent
        records_by_source_file: dict[str, SheetRecords] = {}
        for rel_path in project.schematic_paths:
            source_file = _source_file_key(rel_path)
            schdoc = project_dir / source_file
            if schdoc.exists():
                records_by_source_file[source_file] = load_sheet(str(schdoc), ctx=ctx)
            else:
                ctx.warn(
                    "missing_sheet",
                    f"Schematic sheet not found: {rel_path} (resolved to {schdoc})",
                )

        # Sheet-symbol FileName records store a bare filename (e.g.
        # "Microcontroller Pin Choice.SchDoc"), but project document paths may
        # include a subdirectory ("SCH/Microcontroller Pin Choice.SchDoc").
        # Resolve a symbol's child reference to the canonical project source-file
        # key so hierarchy detection and instance expansion match regardless of
        # how the child is spelled.
        def canonical_child(child_source_file: str, referencing_dir: str) -> str:
            if not child_source_file:
                return ""
            resolved = resolve_document_reference(
                child_source_file,
                referencing_dir=referencing_dir,
                known_documents=records_by_source_file.keys(),
                ctx=ctx,
            )
            return resolved if resolved is not None else _source_file_key(child_source_file)

        base_sources_by_file = {
            source_file: _source_sheet(sheet_records, source_file)
            for source_file, sheet_records in records_by_source_file.items()
        }
        child_source_counts: dict[str, int] = {}
        for source_file, source in base_sources_by_file.items():
            referencing_dir = _parent_dir(source_file)
            for symbol in source.sheet_symbols:
                child_source_file = canonical_child(symbol.child_source_file, referencing_dir)
                if child_source_file:
                    child_source_counts[child_source_file] = (
                        child_source_counts.get(child_source_file, 0) + 1
                    )
        repeated_child_files = {
            source_file for source_file, count in child_source_counts.items() if count > 1
        }

        for source_file, source in base_sources_by_file.items():
            if source_file not in repeated_child_files:
                sheets[source.id] = source

        base_sheet_ids = {source.id for source in base_sources_by_file.values()}
        parents: list[tuple[AltiumSheetSource, tuple[str, ...]]] = [
            (source, (_source_file_key(source.source_file),)) for source in sheets.values()
        ]
        for parent, lineage in parents:
            referencing_dir = _parent_dir(_source_file_key(parent.source_file))
            for symbol in parent.sheet_symbols:
                child_source_file = canonical_child(symbol.child_source_file, referencing_dir)
                child_records = records_by_source_file.get(child_source_file)
                if child_records is None or child_source_file in lineage:
                    continue
                parent_is_instance = parent.id not in base_sheet_ids
                if not parent_is_instance and child_source_file not in repeated_child_files:
                    continue
                child_base_id = _default_sheet_id(child_records, child_source_file)
                child_sheet_id = f"{parent.id}:{symbol.id}:{child_base_id}"
                if child_sheet_id in sheets:
                    continue
                child_scope_id = ScopeId(
                    path=(*parent.scope_id.path, symbol.id, child_sheet_id),
                )
                child_source = _source_sheet(
                    child_records,
                    child_source_file,
                    sheet_id=child_sheet_id,
                    scope_id=child_scope_id,
                )
                sheets[child_sheet_id] = child_source
                parents.append((child_source, (*lineage, child_source_file)))
        _apply_multipart_component_identities(sheets.values())
        return project, sheets

    sheet_records = load_sheet(str(path), ctx=ctx)
    project = AltiumProject(schematic_paths=[path.name])
    source = _source_sheet(sheet_records, path.name)
    sheets[source.id] = source
    _apply_multipart_component_identities(sheets.values())
    return project, sheets


def altium_to_source(
    path: Path, name: str = "", ctx: ParseContext | None = None
) -> AltiumSourceDesign:
    """Extract Altium-native source connectivity from a project or sheet.

    Missing-sheet and other non-fatal issues are recorded on *ctx* when
    provided, mirroring the other parser pipelines.
    """
    if ctx is None:
        ctx = ParseContext()
    project, sheets = load_project_source_sheets(path, ctx=ctx)
    root_sheet_name = next(iter(sheets), "")
    return AltiumSourceDesign(
        name=name or path.stem,
        project=project,
        sheets=sheets,
        root_sheet_name=root_sheet_name,
    )
