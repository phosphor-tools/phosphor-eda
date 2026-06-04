"""Tests for Altium-native source connectivity extraction."""

from pathlib import Path

from phosphor_eda.altium.project import AltiumHierarchyMode
from phosphor_eda.altium.records import FileNameRec, RecordType, SheetNameRec, SheetSymbolRec
from phosphor_eda.altium.sheet_builder import SheetRecords
from phosphor_eda.altium.source import load_project_source_sheets
from phosphor_eda.altium.spatial import WireIndex
from phosphor_eda.altium.to_schematic import altium_to_source

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
        "phosphor_eda.altium.source.parse_prjpcb_file",
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
        "phosphor_eda.altium.source.load_sheet",
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
