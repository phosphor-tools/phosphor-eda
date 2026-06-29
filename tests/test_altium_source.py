"""Tests for Altium-native source connectivity extraction."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import SchematicDirectiveKind
from phosphor_eda.formats.altium.project import AltiumHierarchyMode
from phosphor_eda.formats.altium.records import (
    FileNameRec,
    LabelRec,
    NoteRec,
    ParameterRec,
    ParameterSetRec,
    RecordType,
    SheetNameRec,
    SheetSymbolRec,
    TextFrameRec,
    WireRec,
)
from phosphor_eda.formats.altium.sheet_builder import SheetRecords
from phosphor_eda.formats.altium.source import load_project_source_sheets
from phosphor_eda.formats.altium.to_schematic import altium_to_design, altium_to_source
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.spatial import WireIndex

if TYPE_CHECKING:
    import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
QFSAE_PRJPCB = FIXTURES / "altium/qfsae-debugger/Debugger.PrjPcb"


def _sheet_by_name(source_name: str):
    source = altium_to_source(QFSAE_PRJPCB)
    for sheet in source.sheets.values():
        if sheet.name == source_name:
            return sheet
    raise AssertionError(f"No Altium source sheet named {source_name}")


def _records_sheet(name: str, child_files: list[str]) -> SheetRecords:
    records = []
    for index, child_file in enumerate(child_files, start=1):
        owner_index = index - 1
        records.extend(
            [
                SheetSymbolRec(
                    record_type=RecordType.SHEET_SYMBOL,
                    index=index,
                    owner_index=-1,
                ),
                SheetNameRec(
                    record_type=RecordType.SHEET_NAME,
                    index=100 + index,
                    owner_index=owner_index,
                    text=f"{name}-child-{index}",
                ),
                FileNameRec(
                    record_type=RecordType.FILE_NAME,
                    index=200 + index,
                    owner_index=owner_index,
                    text=child_file,
                ),
            ]
        )
    return SheetRecords(records=records, children={}, wire_index=WireIndex([]), name=name)


def _parameter_set_sheet(location: tuple[int, int] = (50, 0)) -> SheetRecords:
    wire = WireRec(
        record_type=RecordType.WIRE,
        index=1,
        owner_index=-1,
        points=[(0, 0), (100, 0)],
    )
    parameter_set = ParameterSetRec(
        record_type=RecordType.PARAMETER_SET,
        index=2,
        owner_index=-1,
        location=location,
        name="DIFFPAIR",
    )
    differential_pair = ParameterRec(
        record_type=RecordType.PARAMETER,
        index=3,
        owner_index=parameter_set.owner_key,
        name="DifferentialPair",
        text="True",
    )
    differential_pair_class = ParameterRec(
        record_type=RecordType.PARAMETER,
        index=4,
        owner_index=parameter_set.owner_key,
        name="DifferentialPairClassName",
        text="LVDS",
    )
    records = [wire, parameter_set, differential_pair, differential_pair_class]
    return SheetRecords(
        records=records,
        children={parameter_set.owner_key: [differential_pair, differential_pair_class]},
        wire_index=WireIndex([wire]),
        name="Directives",
    )


def _sheet_level_parameter(index: int, name: str, text: str) -> ParameterRec:
    return ParameterRec(
        record_type=RecordType.PARAMETER,
        index=index,
        owner_index=-1,
        name=name,
        text=text,
    )


def _load_records_sheet(
    monkeypatch: pytest.MonkeyPatch,
    records: SheetRecords,
    source_file: str,
    ctx: ParseContext | None = None,
):
    monkeypatch.setattr(
        "phosphor_eda.formats.altium.source.load_sheet",
        lambda _path, ctx: records,
    )
    _project, sheets = load_project_source_sheets(Path(source_file), ctx=ctx)
    [sheet] = sheets.values()
    return sheet


def test_altium_to_source_preserves_project_options():
    source = altium_to_source(QFSAE_PRJPCB, name="QFSAE Debugger")

    assert source.name == "QFSAE Debugger"
    assert source.project.hierarchy_mode is AltiumHierarchyMode.SMART
    assert source.project.allow_port_net_names is False
    assert source.project.allow_sheet_entry_net_names is True
    assert source.project.append_sheet_number_to_local_nets is False
    assert source.project.name_nets_hierarchically is False
    assert source.project.power_port_names_take_priority is False


def test_root_sheet_preserves_sheet_symbols_and_sheet_entries():
    top = _sheet_by_name("TOP")

    assert len(top.sheet_symbols) == 3
    assert len(top.sheet_entries) == 16
    assert {symbol.child_source_file for symbol in top.sheet_symbols} == {
        "MCU.SchDoc",
        "Power.SchDoc",
        "Connectors.SchDoc",
    }


def test_top_sheet_symbol_child_binding_uses_owner_index_convention():
    top = _sheet_by_name("TOP")

    entries_by_symbol = {
        symbol.child_source_file: [
            entry.name for entry in top.sheet_entries if entry.sheet_symbol_id == symbol.id
        ]
        for symbol in top.sheet_symbols
    }

    assert entries_by_symbol["MCU.SchDoc"]
    assert entries_by_symbol["Power.SchDoc"] == []
    assert entries_by_symbol["Connectors.SchDoc"]


def test_source_keeps_distinct_net_identifier_record_lists():
    source = altium_to_source(QFSAE_PRJPCB)
    local_nets = [net for sheet in source.sheets.values() for net in sheet.local_nets]

    assert any(net.net_labels for net in local_nets)
    assert any(net.power_ports for net in local_nets)
    assert any(net.ports for net in local_nets)
    assert any(net.sheet_entries for net in local_nets)

    for local_net in local_nets:
        assert all(label.kind == "net_label" for label in local_net.net_labels)
        assert all(port.kind == "power_port" for port in local_net.power_ports)
        assert all(port.kind == "port" for port in local_net.ports)
        assert all(entry.kind == "sheet_entry" for entry in local_net.sheet_entries)


def test_source_local_net_ids_are_not_final_net_names():
    source = altium_to_source(QFSAE_PRJPCB)
    local_nets = [net for sheet in source.sheets.values() for net in sheet.local_nets]
    source_names = {
        label.name
        for net in local_nets
        for label in [*net.net_labels, *net.power_ports, *net.ports, *net.sheet_entries]
        if label.name
    }

    assert {"GND", "VCC3V3"} & source_names
    for local_net in local_nets:
        assert local_net.id not in source_names
        assert "GND" not in local_net.id.upper()
        assert "VCC3V3" not in local_net.id.upper()


def test_altium_sheet_title_block_maps_typed_fields_and_preserves_raw_metadata(
    monkeypatch: pytest.MonkeyPatch,
):
    records = [
        _sheet_level_parameter(1, "Title", "*"),
        _sheet_level_parameter(2, "CompanyName", "Acme Hardware"),
        _sheet_level_parameter(3, "Address2", "Floor 2"),
        _sheet_level_parameter(4, "Address1", "12 Main St"),
        _sheet_level_parameter(5, "SheetNumber", "3"),
        _sheet_level_parameter(6, "SheetTotal", "9"),
        _sheet_level_parameter(7, "DrawnBy", "Drafter"),
        _sheet_level_parameter(8, "CheckedBy", "Checker"),
        _sheet_level_parameter(9, "ApprovedBy", "Approver"),
        _sheet_level_parameter(10, "ModifiedDate", "2026-06-16"),
        _sheet_level_parameter(11, "Author", "~"),
    ]
    sheet = _load_records_sheet(
        monkeypatch,
        SheetRecords(records=records, children={}, wire_index=WireIndex([]), name="Sheet"),
        "Sheet.SchDoc",
    )

    block = sheet.title_block

    assert block is not None
    assert block.title == ""
    assert block.organization == "Acme Hardware"
    assert block.org_address == "12 Main St\nFloor 2"
    assert block.sheet_number == "3"
    assert block.sheet_total == "9"
    assert block.drawn_by == "Drafter"
    assert block.checked_by == "Checker"
    assert block.approved_by == "Approver"
    assert block.modified_date == "2026-06-16"
    assert block.author == ""
    assert block.metadata["Title"] == "*"
    assert block.metadata["Author"] == "~"


def test_altium_text_frames_become_page_annotations(monkeypatch: pytest.MonkeyPatch):
    records = SheetRecords(
        records=[
            TextFrameRec(
                record_type=RecordType.TEXT_FRAME,
                index=1,
                owner_index=-1,
                text="Initial release~1Changed power sequencing",
            ),
            TextFrameRec(
                record_type=RecordType.TEXT_FRAME,
                index=2,
                owner_index=-1,
                text="   ",
            ),
        ],
        children={},
        wire_index=WireIndex([]),
        name="Notes",
    )
    sheet = _load_records_sheet(monkeypatch, records, "Notes.SchDoc")

    assert sheet.annotations == ["Initial release\nChanged power sequencing"]
    assert sheet.title_block is None


def test_altium_notes_and_ownerless_labels_become_page_annotations_in_record_order(
    monkeypatch: pytest.MonkeyPatch,
):
    records = SheetRecords(
        records=[
            LabelRec(
                record_type=RecordType.LABEL,
                index=1,
                owner_index=-1,
                text="Assembly note",
            ),
            NoteRec(
                record_type=RecordType.NOTE,
                index=2,
                owner_index=-1,
                text='@{"Id":"abc","ObjectType":2} Route USB as differential pair',
            ),
            TextFrameRec(
                record_type=RecordType.TEXT_FRAME,
                index=3,
                owner_index=-1,
                text="First line~1Second line",
            ),
            LabelRec(
                record_type=RecordType.LABEL,
                index=4,
                owner_index=-1,
                text="=SheetNumber",
            ),
            LabelRec(
                record_type=RecordType.LABEL,
                index=5,
                owner_index=10,
                text="Owned component text",
            ),
            NoteRec(
                record_type=RecordType.NOTE,
                index=6,
                owner_index=10,
                text="Owned note",
            ),
            TextFrameRec(
                record_type=RecordType.TEXT_FRAME,
                index=7,
                owner_index=-1,
                text="*",
            ),
        ],
        children={},
        wire_index=WireIndex([]),
        name="Notes",
    )
    sheet = _load_records_sheet(monkeypatch, records, "Notes.SchDoc")

    assert sheet.annotations == [
        "Assembly note",
        "Route USB as differential pair",
        "First line\nSecond line",
    ]


def test_altium_text_frames_reach_public_page_annotations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    schdoc = tmp_path / "Notes.SchDoc"
    schdoc.write_text("", encoding="utf-8")
    records = SheetRecords(
        records=[
            TextFrameRec(
                record_type=RecordType.TEXT_FRAME,
                index=1,
                owner_index=-1,
                text="First line~1Second line",
            )
        ],
        children={},
        wire_index=WireIndex([]),
        name="Notes",
    )
    monkeypatch.setattr(
        "phosphor_eda.formats.altium.source.load_sheet",
        lambda _path, ctx: records,
    )

    design = altium_to_design(schdoc)

    assert design.pages[0].annotations == ["First line\nSecond line"]


def test_parameter_set_diff_pair_directives_attach_to_touched_local_net(
    monkeypatch: pytest.MonkeyPatch,
):
    ctx = ParseContext()
    sheet = _load_records_sheet(
        monkeypatch,
        _parameter_set_sheet(),
        "Directives.SchDoc",
        ctx,
    )

    [local_net] = sheet.local_nets
    assert [
        (directive.kind, directive.value, directive.native_name)
        for directive in local_net.directives
    ] == [
        (SchematicDirectiveKind.DIFF_PAIR, "true", "DifferentialPair"),
        (SchematicDirectiveKind.DIFF_PAIR_CLASS, "LVDS", "DifferentialPairClassName"),
    ]
    assert {directive.source_id for directive in local_net.directives} == {
        f"{sheet.id}:parameter_set:2",
    }
    assert all(directive.source == "altium" for directive in local_net.directives)
    assert local_net.directives[0].metadata["DifferentialPair"] == "True"
    assert ctx.issues == []


def test_unresolved_parameter_set_diff_pair_records_diagnostic_without_attaching_directive(
    monkeypatch: pytest.MonkeyPatch,
):
    ctx = ParseContext()
    sheet = _load_records_sheet(
        monkeypatch,
        _parameter_set_sheet(location=(50, 50)),
        "Directives.SchDoc",
        ctx,
    )

    assert sheet.local_nets
    assert all(not local_net.directives for local_net in sheet.local_nets)
    assert [issue.category for issue in ctx.issues] == ["altium_unresolved_directive_anchor"]


def test_multipart_component_source_identity_uses_component_not_part_record():
    sheet = _sheet_by_name("MCU")
    u1_pins = [
        pin
        for pin in sheet.pin_occurrences
        if pin.component_reference == "U1" and pin.component_source_id
    ]

    assert u1_pins
    assert {pin.component_source_id for pin in u1_pins} == {
        "altium:component:root:multipart:U1:STM32F103CBT6:3"
    }
    assert len({pin.component_occurrence_source_id for pin in u1_pins}) > 1


def test_project_source_expands_nested_repeated_sheet_instances(tmp_path, monkeypatch):
    project_path = tmp_path / "Nested.PrjPcb"
    project_path.write_text("", encoding="utf-8")
    for sheet_name in ("Top.SchDoc", "Child.SchDoc", "Leaf.SchDoc"):
        (tmp_path / sheet_name).write_text("", encoding="utf-8")

    records_by_file = {
        "Top.SchDoc": _records_sheet("Top", ["Child.SchDoc", "Child.SchDoc"]),
        "Child.SchDoc": _records_sheet("Child", ["Leaf.SchDoc", "Leaf.SchDoc"]),
        "Leaf.SchDoc": _records_sheet("Leaf", []),
    }

    monkeypatch.setattr(
        "phosphor_eda.formats.altium.source.parse_prjpcb_file",
        lambda _path: type(
            "Project",
            (),
            {
                "schematic_paths": ["Top.SchDoc", "Child.SchDoc", "Leaf.SchDoc"],
                "hierarchy_mode": AltiumHierarchyMode.SMART,
            },
        )(),
    )
    monkeypatch.setattr(
        "phosphor_eda.formats.altium.source.load_sheet",
        lambda path, ctx: records_by_file[Path(path).name],
    )

    _project, sheets = load_project_source_sheets(project_path)

    child_sheets = [sheet for sheet in sheets.values() if sheet.source_file == "Child.SchDoc"]
    leaf_sheets = [sheet for sheet in sheets.values() if sheet.source_file == "Leaf.SchDoc"]
    child_scope_paths = {child.scope_id.path for child in child_sheets}
    assert len(child_sheets) == 2
    assert len(leaf_sheets) == 4
    assert {
        leaf.scope_id.path[: len(child_scope)]
        for leaf in leaf_sheets
        for child_scope in child_scope_paths
        if leaf.scope_id.path[: len(child_scope)] == child_scope
    } == child_scope_paths
