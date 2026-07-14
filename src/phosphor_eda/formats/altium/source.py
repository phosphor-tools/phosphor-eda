"""Altium-native schematic source extraction.

Source extraction keeps Altium records in their own connectivity vocabulary.
It does not construct the public ``Schematic``/``Page``/``Net`` model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import (
    ComponentKind,
    FootprintModel,
    LibraryLink,
    Parameter,
    SchematicDirective,
    SchematicDirectiveKind,
    ScopeId,
    TitleBlock,
)
from phosphor_eda.formats.altium.annotation import (
    AnnotationDesignator,
    load_annotation_designators,
)
from phosphor_eda.formats.altium.project import AltiumProject, parse_prjpcb_file
from phosphor_eda.formats.altium.records import (
    ComponentRec,
    DesignatorRec,
    FileNameRec,
    ImplementationListRec,
    ImplementationRec,
    LabelRec,
    NoteRec,
    ParameterRec,
    ParameterSetRec,
    PinRec,
    SheetEntryRec,
    SheetNameRec,
    SheetSymbolRec,
    TextFrameRec,
)
from phosphor_eda.formats.altium.sheet_builder import (
    LocalNetResolution,
    SheetRecords,
    compute_harness_entry_coords,
    load_sheet,
    parse_harness_groups,
    resolve_local_net_groups,
)
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.paths import resolve_document_reference
from phosphor_eda.formats.common.resolved_graph import ResolvedComponentInfo
from phosphor_eda.formats.common.text import render_annotation_table

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
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
    unique_id: str = ""


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
    port_name: str
    location: tuple[int, int]
    x_size: int
    y_size: int


@dataclass(slots=True)
class AltiumHarnessMember:
    id: str
    scope_id: ScopeId
    source_index: int
    connector_id: str
    port_name: str
    name: str
    coord: tuple[int, int]
    side: int
    distance_from_top: int
    has_overline: bool = False


@dataclass(slots=True)
class AltiumGenericBusLine:
    id: str
    scope_id: ScopeId
    source_index: int
    name: str
    location: tuple[int, int]
    member_local_net_ids: dict[str, str]
    kind: str = field(default="generic_bus", init=False)


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
    pin_unique_id: str = ""
    component_part_id: str = ""
    component_part: str = ""
    component_description: str = ""
    component_metadata: dict[str, str] = field(default_factory=dict)
    component_info: ResolvedComponentInfo | None = None


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
    generic_bus_members: list[str] = field(default_factory=list)
    directives: list[SchematicDirective] = field(default_factory=list)


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
    annotations: list[str] = field(default_factory=list)
    title_block: TitleBlock | None = None
    generic_bus_lines: list[AltiumGenericBusLine] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _AnnotationEvent:
    source_index: int
    text: str


@dataclass(frozen=True, slots=True)
class _TextFrameCell:
    source_index: int
    text: str
    standalone_text: str
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1


@dataclass(frozen=True, slots=True)
class _GroupedTextFrameTable:
    event: _AnnotationEvent
    cell_source_indices: set[int]


@dataclass(slots=True)
class AltiumSourceDesign:
    name: str
    project: AltiumProject
    sheets: dict[str, AltiumSheetSource]
    # Key into ``sheets`` for the design's root sheet.
    root_sheet_id: str = ""
    # Hierarchical unique-id path -> per-instance designator entry, from the
    # project's ``.Annotation`` file. Empty for single sheets and un-annotated
    # projects.
    physical_designators: dict[str, AnnotationDesignator] = field(default_factory=dict)


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
    for rec in sheet.by_type(SheetNameRec):
        names_by_owner[rec.owner_index] = rec.text
    for rec in sheet.by_type(FileNameRec):
        files_by_owner[rec.owner_index] = rec.text

    symbols: list[AltiumSheetSymbol] = []
    symbol_ids_by_owner: dict[int, str] = {}
    for symbol in sheet.by_type(SheetSymbolRec):
        owner_key = symbol.owner_key
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
                unique_id=symbol.unique_id,
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
    for entry in sheet.by_type(SheetEntryRec):
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
    connectors: list[AltiumHarnessConnector] = []
    members: dict[int, AltiumHarnessMember] = {}
    for group in parse_harness_groups(sheet):
        connector_id = _source_id(sheet_id, "harness_connector", group.connector.index)
        connectors.append(
            AltiumHarnessConnector(
                id=connector_id,
                scope_id=scope_id,
                source_index=group.connector.index,
                harness_type=group.harness_type,
                port_name=group.port_name,
                location=group.connector.location,
                x_size=group.connector.x_size,
                y_size=group.connector.y_size,
            ),
        )
        for entry, coord in group.members:
            members[entry.index] = AltiumHarnessMember(
                id=_source_id(sheet_id, "harness_member", entry.index),
                scope_id=scope_id,
                source_index=entry.index,
                connector_id=connector_id,
                port_name=group.port_name,
                name=entry.name,
                coord=coord,
                side=entry.side,
                distance_from_top=entry.distance_from_top,
                has_overline=entry.has_overline,
            )

    return connectors, members


def _component_records(sheet: SheetRecords) -> dict[int, ComponentRec]:
    return {component.owner_key: component for component in sheet.by_type(ComponentRec)}


def _designators_by_owner(sheet: SheetRecords) -> dict[int, str]:
    result: dict[int, str] = {}
    for designator in sheet.by_type(DesignatorRec):
        if designator.owner_index >= 0:
            result[designator.owner_index] = designator.text
    return result


def _resolve_indirect_text(text: str, texts_by_name: dict[str, str]) -> str:
    """Chase an ``=Name`` reference through same-component parameter texts.

    Returns the referenced parameter's text, following chained references.
    The literal *text* is kept when the reference is missing or cyclic.
    """
    seen: set[str] = set()
    current = text
    while current.startswith("="):
        key = current[1:].strip().lower()
        if key in seen or key not in texts_by_name:
            return text
        seen.add(key)
        current = texts_by_name[key]
    return current


def component_parameters(records: Sequence[ParameterRec]) -> tuple[Parameter, ...]:
    """Ordered parameters from one owner's RECORD=41 children, in document order.

    ``=Name`` texts are indirect references to a sibling parameter
    (case-insensitive); the resolved text is stored as the value with
    ``indirect=True``.
    """
    texts_by_name: dict[str, str] = {}
    for record in records:
        if record.name:
            _ = texts_by_name.setdefault(record.name.lower(), record.text)
    parameters: list[Parameter] = []
    for record in records:
        if not record.name:
            continue
        indirect = record.text.startswith("=")
        value = _resolve_indirect_text(record.text, texts_by_name) if indirect else record.text
        parameters.append(
            Parameter(
                name=record.name,
                value=value,
                visible=not record.is_hidden,
                indirect=indirect,
            )
        )
    return tuple(parameters)


def _component_parameters_by_owner(sheet: SheetRecords) -> dict[int, tuple[Parameter, ...]]:
    records_by_owner: dict[int, list[ParameterRec]] = {}
    component_owner_keys = {component.owner_key for component in sheet.by_type(ComponentRec)}
    for parameter in sheet.by_type(ParameterRec):
        if parameter.owner_index not in component_owner_keys or not parameter.name:
            continue
        records_by_owner.setdefault(parameter.owner_index, []).append(parameter)
    return {owner: component_parameters(records) for owner, records in records_by_owner.items()}


def _pin_unique_ids_by_owner(sheet: SheetRecords) -> dict[int, str]:
    result: dict[int, str] = {}
    for pin in sheet.by_type(PinRec):
        for child in sheet.children.get(pin.owner_key, []):
            if not isinstance(child, ParameterRec):
                continue
            if child.name.casefold() != "pinuniqueid" or not child.text:
                continue
            result[pin.owner_key] = child.text
            break
    return result


def _component_footprints_by_owner(sheet: SheetRecords) -> dict[int, tuple[FootprintModel, ...]]:
    """PCBLIB implementation models per component owner key, in document order.

    Footprints hang off a component as RECORD=44 (implementation list) →
    RECORD=45 (implementation) children.
    """
    models_by_owner: dict[int, list[FootprintModel]] = {}
    for impl_list in sheet.by_type(ImplementationListRec):
        if impl_list.owner_index < 0:
            continue
        for child in sheet.children.get(impl_list.owner_key, []):
            if not isinstance(child, ImplementationRec):
                continue
            if child.model_type.upper() != "PCBLIB" or not child.model_name:
                continue
            models_by_owner.setdefault(impl_list.owner_index, []).append(
                FootprintModel(
                    name=child.model_name,
                    library=child.model_library,
                    is_current=child.is_current,
                    description=child.description,
                )
            )
    return {owner: tuple(models) for owner, models in models_by_owner.items()}


def _library_link(component: ComponentRec) -> LibraryLink | None:
    link = LibraryLink(
        symbol=component.lib_reference,
        library=component.source_library_name,
        design_item_id=component.design_item_id,
        source="database" if component.database_table else "",
    )
    if not (link.symbol or link.library or link.design_item_id):
        return None
    return link


# Altium COMPONENTKIND values, per the Altium Designer SDK ``TComponentKind``
# enum: eComponentKind_Standard=0, _Mechanical=1, _Graphical=2,
# _NetTie_InBOM=3, _NetTie_NoBOM=4, _Standard_NoBOM=5, _Jumper=6. Fixture
# evidence agrees: pi-mx8 title-block drawing components carry 2 and its
# NetTie_0.2mm components carry 4. Absent → standard.
_COMPONENT_KINDS: dict[int, ComponentKind] = {
    0: ComponentKind.STANDARD,
    1: ComponentKind.MECHANICAL,
    2: ComponentKind.GRAPHICAL,
    3: ComponentKind.NET_TIE,
    4: ComponentKind.NET_TIE,
    5: ComponentKind.STANDARD,  # standard part excluded from the BOM
}

# Kind value for a standard part excluded from the BOM (eComponentKind_Standard_NoBOM).
_KIND_STANDARD_NO_BOM = 5


def component_kind(component: ComponentRec) -> ComponentKind:
    return _COMPONENT_KINDS.get(component.component_kind, ComponentKind.OTHER)


def component_info(
    component: ComponentRec | None,
    parameters: tuple[Parameter, ...],
    footprints: tuple[FootprintModel, ...],
) -> ResolvedComponentInfo:
    if component is None:
        return ResolvedComponentInfo(parameters=parameters, footprints=footprints)
    return ResolvedComponentInfo(
        parameters=parameters,
        lib=_library_link(component),
        footprints=footprints,
        kind=component_kind(component),
        # Altium has no native DNP flag — the shared convention matcher decides.
        explicit_dnp=None,
        exclude_from_bom=component.component_kind == _KIND_STANDARD_NO_BOM,
    )


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
    parameters: tuple[Parameter, ...],
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    # First occurrence of a parameter name wins (cross-format collision rule);
    # empty values never land in the convenience dict.
    for parameter in parameters:
        if parameter.value:
            _ = metadata.setdefault(parameter.name, parameter.value)
    if component is None:
        return metadata
    if component.unique_id:
        _ = metadata.setdefault("altium_component_unique_id", component.unique_id)
    _ = metadata.setdefault("altium_current_part_id", str(component.current_part_id))
    _ = metadata.setdefault("altium_part_count", str(component.part_count))
    _ = metadata.setdefault("altium_display_mode", str(component.display_mode))
    return metadata


# Sheet-level parameter names that map onto typed TitleBlock fields
# (case-insensitive); all raw non-empty fields also land in TitleBlock.metadata.
_TITLE_BLOCK_PLACEHOLDERS = frozenset({"", "*", "~"})
_TITLE_BLOCK_FIELD_BY_NAME = {
    "approvedby": "approved_by",
    "approved by": "approved_by",
    "author": "author",
    "cage code": "cage_code",
    "cagecode": "cage_code",
    "checkedby": "checked_by",
    "checked by": "checked_by",
    "checkeddate": "modified_date",
    "checked date": "modified_date",
    "company": "organization",
    "companyname": "organization",
    "created": "created_date",
    "createddate": "created_date",
    "date": "date",
    "documentnumber": "document_number",
    "document number": "document_number",
    "drawnby": "drawn_by",
    "drawn by": "drawn_by",
    "modifieddate": "modified_date",
    "modified date": "modified_date",
    "orgname": "organization",
    "organization": "organization",
    "revision": "revision",
    "sheetnumber": "sheet_number",
    "sheet number": "sheet_number",
    "sheettotal": "sheet_total",
    "sheet total": "sheet_total",
    "title": "title",
}


def _title_value(value: str) -> str:
    text = value.strip()
    return "" if text in _TITLE_BLOCK_PLACEHOLDERS else text


def _address_line_number(name: str) -> int | None:
    lowered = name.casefold()
    for prefix in ("address", "orgaddr"):
        if not lowered.startswith(prefix):
            continue
        suffix = lowered[len(prefix) :]
        if suffix.isdigit():
            return int(suffix)
    return None


def _sheet_title_block(sheet: SheetRecords) -> TitleBlock | None:
    """Title block from sheet-level (ownerless) RECORD=41 parameters.

    Altium writes ``*`` for sheet parameters left at their print-time
    placeholder (Time, Date, DocumentFullPathAndName, ...); those count as
    unset. Returns ``None`` when no parameter carries a real value.
    """
    block = TitleBlock()
    populated = False
    address_lines: dict[int, str] = {}
    for record in sheet.sheet_level_parameters:
        value = record.text
        if not record.name or not value:
            continue
        populated = True
        _ = block.metadata.setdefault(record.name, value)
        typed_value = _title_value(value)
        if not typed_value:
            continue
        address_number = _address_line_number(record.name)
        if address_number is not None:
            address_lines.setdefault(address_number, typed_value)
            continue
        field_name = _TITLE_BLOCK_FIELD_BY_NAME.get(record.name.casefold())
        if field_name is None:
            continue
        if not getattr(block, field_name):
            setattr(block, field_name, typed_value)
    if address_lines:
        block.org_address = "\n".join(value for _line_no, value in sorted(address_lines.items()))
    return block if populated else None


def _sheet_annotations(sheet: SheetRecords) -> list[str]:
    events: list[_AnnotationEvent] = []
    text_frame_cells: list[_TextFrameCell] = []
    for record in sheet.records:
        if record.owner_index != -1:
            continue
        if isinstance(record, LabelRec):
            text = _annotation_text(record.text)
            if text.startswith("="):
                continue
        elif isinstance(record, (NoteRec, TextFrameRec)):
            text = _strip_active_link_prefix(_annotation_text(record.text))
        else:
            continue
        if isinstance(record, TextFrameRec):
            cell_text = "" if text in _TITLE_BLOCK_PLACEHOLDERS else text
            cell = _text_frame_cell(record, cell_text, text)
            if cell is not None:
                text_frame_cells.append(cell)
                continue
        if not text:
            continue
        events.append(_AnnotationEvent(source_index=record.index, text=text))
    grouped_cell_indices: set[int] = set()
    for table in _grouped_text_frame_tables(text_frame_cells):
        events.append(table.event)
        grouped_cell_indices.update(table.cell_source_indices)
    for cell in text_frame_cells:
        if cell.source_index not in grouped_cell_indices and cell.standalone_text:
            events.append(
                _AnnotationEvent(source_index=cell.source_index, text=cell.standalone_text)
            )
    return [event.text for event in sorted(events, key=lambda event: event.source_index)]


def _text_frame_cell(
    record: TextFrameRec, table_text: str, standalone_text: str
) -> _TextFrameCell | None:
    x1, y1 = record.location
    x2, y2 = record.corner
    if x1 == x2 or y1 == y2:
        return None
    return _TextFrameCell(
        source_index=record.index,
        text=table_text,
        standalone_text=standalone_text,
        x1=min(x1, x2),
        y1=min(y1, y2),
        x2=max(x1, x2),
        y2=max(y1, y2),
    )


def _grouped_text_frame_tables(cells: list[_TextFrameCell]) -> list[_GroupedTextFrameTable]:
    tables: list[_GroupedTextFrameTable] = []
    for component in _text_frame_components(cells):
        rows = _table_rows_for_component(component)
        if rows is None:
            continue
        table_text = render_annotation_table([[cell.text for cell in row] for row in rows])
        if table_text:
            tables.append(
                _GroupedTextFrameTable(
                    event=_AnnotationEvent(
                        source_index=min(cell.source_index for row in rows for cell in row),
                        text=table_text,
                    ),
                    cell_source_indices={cell.source_index for row in rows for cell in row},
                )
            )
    return tables


def _text_frame_components(cells: list[_TextFrameCell]) -> list[list[_TextFrameCell]]:
    components: list[list[_TextFrameCell]] = []
    seen: set[int] = set()
    for index, _cell in enumerate(cells):
        if index in seen:
            continue
        stack = [index]
        seen.add(index)
        component: list[_TextFrameCell] = []
        while stack:
            current_index = stack.pop()
            current = cells[current_index]
            component.append(current)
            for candidate_index, candidate in enumerate(cells):
                if candidate_index in seen:
                    continue
                if _rectangles_adjacent(current, candidate):
                    seen.add(candidate_index)
                    stack.append(candidate_index)
        components.append(component)
    return components


def _rectangles_adjacent(first: _TextFrameCell, second: _TextFrameCell) -> bool:
    tolerance = 2
    x_overlap = min(first.x2, second.x2) - max(first.x1, second.x1)
    y_overlap = min(first.y2, second.y2) - max(first.y1, second.y1)
    x_touch = abs(first.x2 - second.x1) <= tolerance or abs(second.x2 - first.x1) <= tolerance
    y_touch = abs(first.y2 - second.y1) <= tolerance or abs(second.y2 - first.y1) <= tolerance
    return (y_overlap >= -tolerance and x_touch) or (x_overlap >= -tolerance and y_touch)


def _table_rows_for_component(
    component: list[_TextFrameCell],
) -> list[list[_TextFrameCell]] | None:
    rows = _horizontal_text_frame_rows(component)
    strong_rows = [row for row in rows if _is_strong_table_row(row)]
    if not strong_rows:
        return None
    return sorted(
        rows, key=lambda row: (min(cell.y1 for cell in row), min(cell.x1 for cell in row))
    )


def _horizontal_text_frame_rows(
    cells: list[_TextFrameCell],
) -> list[list[_TextFrameCell]]:
    rows: list[list[_TextFrameCell]] = []
    seen: set[int] = set()
    for index, _cell in enumerate(cells):
        if index in seen:
            continue
        stack = [index]
        seen.add(index)
        row: list[_TextFrameCell] = []
        while stack:
            current_index = stack.pop()
            current = cells[current_index]
            row.append(current)
            for candidate_index, candidate in enumerate(cells):
                if candidate_index in seen:
                    continue
                if _same_table_row(current, candidate):
                    seen.add(candidate_index)
                    stack.append(candidate_index)
        rows.append(sorted(row, key=lambda cell: cell.x1))
    return rows


def _same_table_row(first: _TextFrameCell, second: _TextFrameCell) -> bool:
    tolerance = 2
    y_overlap = min(first.y2, second.y2) - max(first.y1, second.y1)
    x_touch = abs(first.x2 - second.x1) <= tolerance or abs(second.x2 - first.x1) <= tolerance
    return y_overlap > 0 and x_touch


def _is_strong_table_row(row: list[_TextFrameCell]) -> bool:
    if len(row) < 2:
        return False
    tolerance = 2
    top = min(cell.y1 for cell in row)
    bottom = max(cell.y2 for cell in row)
    return all(
        abs(cell.y1 - top) <= tolerance and abs(cell.y2 - bottom) <= tolerance for cell in row
    )


def _annotation_text(text: str) -> str:
    return text.replace("~1", "\n").strip()


def _strip_active_link_prefix(text: str) -> str:
    if not text.startswith("@{"):
        return text
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[index + 1 :].strip()
    return text


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


def _parameter_set_directives_by_root(
    sheet: SheetRecords,
    sheet_id: str,
    resolution: LocalNetResolution,
    ctx: ParseContext | None,
) -> dict[tuple[int, int], list[SchematicDirective]]:
    directives_by_root: dict[tuple[int, int], list[SchematicDirective]] = {}
    for parameter_set in sheet.by_type(ParameterSetRec):
        directives = _parameter_set_directives(parameter_set, sheet, sheet_id)
        if not directives:
            continue
        root = _root_for_point(parameter_set.location, sheet, resolution)
        if root is None:
            if ctx is not None:
                ctx.warn(
                    "altium_unresolved_directive_anchor",
                    (
                        "Altium parameter set directive did not touch a local net: "
                        f"{_source_id(sheet_id, 'parameter_set', parameter_set.index)}"
                    ),
                    record_index=parameter_set.index,
                )
            continue
        directives_by_root.setdefault(root, []).extend(directives)
    return directives_by_root


def _parameter_set_directives(
    parameter_set: ParameterSetRec,
    sheet: SheetRecords,
    sheet_id: str,
) -> list[SchematicDirective]:
    if parameter_set.name != "DIFFPAIR":
        return []
    child_parameters = [
        child
        for child in sheet.children.get(parameter_set.owner_key, [])
        if isinstance(child, ParameterRec)
    ]
    metadata = _parameter_set_metadata(parameter_set, child_parameters)
    directives: list[SchematicDirective] = []
    differential_pair = _child_parameter_text(child_parameters, "DifferentialPair")
    if _truthy_altium_text(differential_pair):
        directives.append(
            _parameter_set_directive(
                parameter_set,
                sheet_id,
                kind=SchematicDirectiveKind.DIFF_PAIR,
                native_name="DifferentialPair",
                value="true",
                metadata=metadata,
            )
        )
    class_name = _child_parameter_text(child_parameters, "DifferentialPairClassName").strip()
    if class_name:
        directives.append(
            _parameter_set_directive(
                parameter_set,
                sheet_id,
                kind=SchematicDirectiveKind.DIFF_PAIR_CLASS,
                native_name="DifferentialPairClassName",
                value=class_name,
                metadata=metadata,
            )
        )
    return directives


def _parameter_set_directive(
    parameter_set: ParameterSetRec,
    sheet_id: str,
    *,
    kind: SchematicDirectiveKind,
    native_name: str,
    value: str,
    metadata: dict[str, str],
) -> SchematicDirective:
    return SchematicDirective(
        kind=kind,
        value=value,
        source="altium",
        source_id=_source_id(sheet_id, "parameter_set", parameter_set.index),
        native_name=native_name,
        x=float(parameter_set.location[0]),
        y=float(parameter_set.location[1]),
        metadata=dict(metadata),
    )


def _parameter_set_metadata(
    parameter_set: ParameterSetRec,
    child_parameters: list[ParameterRec],
) -> dict[str, str]:
    metadata = {
        "ParameterSetName": parameter_set.name,
        "Style": str(parameter_set.style),
        "Orientation": str(parameter_set.orientation),
    }
    for parameter in child_parameters:
        if parameter.name:
            metadata[parameter.name] = parameter.text
    return metadata


def _child_parameter_text(parameters: list[ParameterRec], name: str) -> str:
    for parameter in parameters:
        if parameter.name == name:
            return parameter.text
    return ""


def _truthy_altium_text(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "t", "yes", "y"}


def _source_sheet(
    sheet: SheetRecords,
    source_file: str,
    *,
    sheet_id: str = "",
    scope_id: ScopeId | None = None,
    ctx: ParseContext | None = None,
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
    directives_by_root = _parameter_set_directives_by_root(sheet, sheet_id, resolution, ctx)
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
                generic_bus_members=[],
                generated_name=group.generated_name,
                directives=directives_by_root.get(group.root, []),
            ),
        )

    local_nets_by_id = {local_net.id: local_net for local_net in local_nets}
    generic_bus_lines: list[AltiumGenericBusLine] = []
    for ordinal, bus_group in enumerate(resolution.generic_bus_groups):
        member_local_net_ids: dict[str, str] = {}
        for member_name, member_root in bus_group.member_roots_by_name.items():
            member_local_net_id = root_to_net_id.get(member_root)
            if member_local_net_id is None:
                continue
            member_local_net_ids[member_name] = member_local_net_id
            local_net = local_nets_by_id.get(member_local_net_id)
            if local_net is not None and member_name not in local_net.generic_bus_members:
                local_net.generic_bus_members.append(member_name)
        generic_bus_lines.append(
            AltiumGenericBusLine(
                id=f"{sheet_id}:generic_bus:{ordinal:04d}:{bus_group.source_index}",
                scope_id=scope_id,
                source_index=bus_group.source_index,
                name=bus_group.name,
                location=bus_group.location,
                member_local_net_ids=member_local_net_ids,
            )
        )

    components_by_owner = _component_records(sheet)
    component_parameters_by_owner = _component_parameters_by_owner(sheet)
    component_footprints_by_owner = _component_footprints_by_owner(sheet)
    component_info_by_owner: dict[int, ResolvedComponentInfo] = {}
    pin_unique_ids_by_owner = _pin_unique_ids_by_owner(sheet)
    designator_by_owner = _designators_by_owner(sheet)
    no_connect_roots = {
        root
        for point in resolution.no_connect_wire_coords
        if (root := resolution.coord_to_root.get(point)) is not None
    }

    pin_occurrences: list[AltiumPinOccurrence] = []
    for pin in sheet.by_type(PinRec):
        if pin.owner_index < 0 or not _pin_is_visible(pin, components_by_owner):
            continue
        root = _root_for_point(pin.tip, sheet, resolution)
        if root is None:
            continue
        local_net_id = root_to_net_id.get(root, "")
        component = components_by_owner.get(pin.owner_index)
        component_reference = designator_by_owner.get(pin.owner_index, "")
        component_parameters = component_parameters_by_owner.get(pin.owner_index, ())
        resolved_component_info = component_info_by_owner.get(pin.owner_index)
        if resolved_component_info is None:
            resolved_component_info = component_info(
                component,
                component_parameters,
                component_footprints_by_owner.get(pin.owner_index, ()),
            )
            component_info_by_owner[pin.owner_index] = resolved_component_info
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
            component_metadata=_component_metadata(component, component_parameters),
            component_info=resolved_component_info,
            pin_designator=pin.designator,
            pin_name=pin.name,
            location=pin.location,
            tip=pin.tip,
            pin_unique_id=pin_unique_ids_by_owner.get(pin.owner_key, ""),
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
        generic_bus_lines=generic_bus_lines,
        harness_connectors=harness_connectors,
        harness_members=list(harness_members_by_index.values()),
        pin_occurrences=pin_occurrences,
        annotations=_sheet_annotations(sheet),
        title_block=_sheet_title_block(sheet),
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
            source_file: _source_sheet(sheet_records, source_file, ctx=ctx)
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
                    ctx=ctx,
                )
                sheets[child_sheet_id] = child_source
                parents.append((child_source, (*lineage, child_source_file)))
        _apply_multipart_component_identities(sheets.values())
        return project, sheets

    sheet_records = load_sheet(str(path), ctx=ctx)
    project = AltiumProject(schematic_paths=[path.name])
    source = _source_sheet(sheet_records, path.name, ctx=ctx)
    sheets[source.id] = source
    _apply_multipart_component_identities(sheets.values())
    return project, sheets


def structural_root_sheet_id(sheets: dict[str, AltiumSheetSource]) -> str:
    """Pick the root sheet id: the sheet no sheet symbol references.

    Project files list documents in arbitrary order, so document order alone
    cannot identify the top sheet. The first-listed candidate wins ties
    (several unreferenced sheets), and the first-listed sheet overall is the
    fallback for reference cycles and empty projects.
    """
    known = {_source_file_key(sheet.source_file) for sheet in sheets.values()}
    referenced: set[str] = set()
    for sheet in sheets.values():
        referencing_dir = _parent_dir(_source_file_key(sheet.source_file))
        for symbol in sheet.sheet_symbols:
            if not symbol.child_source_file:
                continue
            # No ctx here: the loader already warned once about ambiguous
            # child references when it expanded the hierarchy.
            resolved = resolve_document_reference(
                symbol.child_source_file,
                referencing_dir=referencing_dir,
                known_documents=known,
            )
            referenced.add(
                resolved if resolved is not None else _source_file_key(symbol.child_source_file)
            )
    for sheet_id, sheet in sheets.items():
        if _source_file_key(sheet.source_file) not in referenced:
            return sheet_id
    return next(iter(sheets), "")


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
    return AltiumSourceDesign(
        name=name or path.stem,
        project=project,
        sheets=sheets,
        root_sheet_id=structural_root_sheet_id(sheets),
        physical_designators=load_annotation_designators(path, ctx=ctx),
    )
