"""Altium-native schematic source extraction.

Source extraction keeps Altium records in their own connectivity vocabulary.
It does not construct the public ``Schematic``/``Page``/``Net`` model.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.altium.errors import ParseContext
from phosphor_eda.altium.project import AltiumProject, parse_prjpcb_file
from phosphor_eda.altium.records import (
    AltiumRecord,
    ComponentRec,
    HarnessConnectorRec,
    HarnessEntryRec,
    HarnessTypeRec,
    PinRec,
    SignalHarnessRec,
)
from phosphor_eda.altium.sheet_builder import (
    LocalNetResolution,
    SheetRecords,
    compute_harness_entry_coords,
    load_sheet,
    resolve_local_net_groups,
)
from phosphor_eda.schematic import ScopeId

if TYPE_CHECKING:
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
    no_connect: bool = False


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


def _pin_is_visible(pin: PinRec, components_by_owner: dict[int, ComponentRec]) -> bool:
    component = components_by_owner.get(pin.owner_index)
    return component is None or pin.owner_part_display_mode == component.display_mode


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
) -> AltiumSheetSource:
    sheet_id = f"sheet:{sheet.name}"
    scope_id = ScopeId(path=(sheet.name,))
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
        pin_id = _source_id(sheet_id, "pin", pin.index)
        occurrence = AltiumPinOccurrence(
            id=pin_id,
            scope_id=scope_id,
            source_index=pin.index,
            local_net_id=local_net_id,
            component_source_id=_source_id(sheet_id, "component", pin.owner_index + 1),
            component_reference=designator_by_owner.get(pin.owner_index, ""),
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
        for rel_path in project.schematic_paths:
            schdoc = project_dir / rel_path.replace("\\", "/")
            if schdoc.exists():
                sheet_records = load_sheet(str(schdoc), ctx=ctx)
                sheets[sheet_records.name] = _source_sheet(sheet_records, rel_path)
            else:
                ctx.warn(
                    "missing_sheet",
                    f"Schematic sheet not found: {rel_path} (resolved to {schdoc})",
                )
                print(
                    f"Warning: schematic sheet not found: {rel_path} (resolved to {schdoc})",
                    file=sys.stderr,
                )
        return project, sheets

    sheet_records = load_sheet(str(path), ctx=ctx)
    project = AltiumProject(schematic_paths=[path.name])
    sheets[sheet_records.name] = _source_sheet(sheet_records, path.name)
    return project, sheets


def altium_to_source(path: Path, name: str = "") -> AltiumSourceDesign:
    """Extract Altium-native source connectivity from a project or sheet."""
    ctx = ParseContext()
    project, sheets = load_project_source_sheets(path, ctx=ctx)
    root_sheet_name = next(iter(sheets), "")
    return AltiumSourceDesign(
        name=name or path.stem,
        project=project,
        sheets=sheets,
        root_sheet_name=root_sheet_name,
    )
