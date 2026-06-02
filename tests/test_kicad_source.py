"""Tests for KiCad-native schematic source extraction."""

from pathlib import Path

import pytest

from phosphor_eda.kicad.source import (
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadPowerSymbol,
    KiCadSheetPin,
)
from phosphor_eda.kicad.to_schematic import kicad_to_source
from phosphor_eda.schematic import ScopeId

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HIERARCHY_ROOT = FIXTURES / "kicad-hierarchy" / "root.kicad_sch"
REPEATED_ROOT = FIXTURES / "kicad-repeated-sheet" / "root.kicad_sch"


def test_source_keeps_kicad_identifier_kinds_distinct() -> None:
    source = kicad_to_source(HIERARCHY_ROOT)

    assert [label.name for label in source.local_labels] == ["SIG_IN"]
    assert source.global_labels == []
    assert [label.name for label in source.hierarchical_labels] == ["SIG_IN"]
    assert {symbol.name for symbol in source.power_symbols} == {"VCC", "GND"}
    assert [pin.name for pin in source.sheet_pins] == ["SIG_IN"]

    assert all(isinstance(label, KiCadLocalLabel) for label in source.local_labels)
    assert all(isinstance(label, KiCadGlobalLabel) for label in source.global_labels)
    assert all(isinstance(label, KiCadHierarchicalLabel) for label in source.hierarchical_labels)
    assert all(isinstance(symbol, KiCadPowerSymbol) for symbol in source.power_symbols)
    assert all(isinstance(pin, KiCadSheetPin) for pin in source.sheet_pins)


def test_sheet_scope_ids_use_instance_identifier_not_file_path() -> None:
    source = kicad_to_source(HIERARCHY_ROOT)

    child_instance = next(
        instance for instance in source.sheet_instances if instance.sheet_name == "ChildSheet"
    )
    expected_scope = ScopeId(path=("99999999-9999-9999-9999-999999999999",))

    assert child_instance.scope_id == expected_scope
    assert child_instance.source_file.endswith("child.kicad_sch")
    assert expected_scope.path[0] != "child.kicad_sch"
    assert {label.scope_id for label in source.hierarchical_labels} == {expected_scope}


def test_repeated_child_sheet_file_produces_distinct_scopes() -> None:
    source = kicad_to_source(REPEATED_ROOT)

    repeated_instances = [
        instance
        for instance in source.sheet_instances
        if instance.source_file.endswith("kicad-hierarchy/child.kicad_sch")
    ]
    repeated_scopes = {instance.scope_id for instance in repeated_instances}

    assert [label.name for label in source.global_labels] == ["SYNC"]
    assert len(repeated_instances) == 2
    assert repeated_scopes == {
        ScopeId(path=("aaaaaaaa-1111-2222-3333-444444444444",)),
        ScopeId(path=("bbbbbbbb-1111-2222-3333-444444444444",)),
    }


def test_pin_occurrences_keep_scope_and_local_net_id() -> None:
    source = kicad_to_source(REPEATED_ROOT)
    local_nets_by_id = {local_net.id: local_net for local_net in source.local_nets}

    child_pins = [
        pin for pin in source.pin_occurrences if pin.component_reference == "R1"
    ]
    child_pin_scopes = {pin.scope_id for pin in child_pins}

    assert len(child_pins) == 4
    assert child_pin_scopes == {
        ScopeId(path=("aaaaaaaa-1111-2222-3333-444444444444",)),
        ScopeId(path=("bbbbbbbb-1111-2222-3333-444444444444",)),
    }
    for pin in child_pins:
        assert pin.local_net_id
        assert local_nets_by_id[pin.local_net_id].scope_id == pin.scope_id


def test_sheet_traversal_skips_ancestor_file_cycles(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "root.kicad_sch"
    child = tmp_path / "child.kicad_sch"
    root.write_text(
        """
(kicad_sch (version 20230121) (generator eeschema)
  (sheet (at 10 10) (size 10 10)
    (uuid aaaaaaaa-1111-2222-3333-444444444444)
    (property "Sheetname" "Child")
    (property "Sheetfile" "child.kicad_sch"))
)
""",
        encoding="utf-8",
    )
    child.write_text(
        """
(kicad_sch (version 20230121) (generator eeschema)
  (sheet (at 10 10) (size 10 10)
    (uuid bbbbbbbb-1111-2222-3333-444444444444)
    (property "Sheetname" "RootAgain")
    (property "Sheetfile" "root.kicad_sch"))
)
""",
        encoding="utf-8",
    )

    source = kicad_to_source(root)

    assert [instance.sheet_name for instance in source.sheet_instances] == [
        "root",
        "Child",
    ]
    assert "cycle" in capsys.readouterr().err
