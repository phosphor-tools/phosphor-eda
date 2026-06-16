"""Tests for KiCad-native schematic source extraction."""

from pathlib import Path

from phosphor_eda.domain.schematic import BusKind, ScopeId
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.kicad.resolver import resolve_kicad_source
from phosphor_eda.formats.kicad.source import (
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadPowerSymbol,
    KiCadSheetPin,
)
from phosphor_eda.formats.kicad.source_extractor import _generated_local_net_name
from phosphor_eda.formats.kicad.to_schematic import kicad_to_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HIERARCHY_ROOT = FIXTURES / "kicad-hierarchy" / "root.kicad_sch"
REPEATED_ROOT = FIXTURES / "kicad-repeated-sheet" / "root.kicad_sch"


def test_source_keeps_kicad_identifier_kinds_distinct() -> None:
    source = kicad_to_source(HIERARCHY_ROOT)

    assert source.schematic_version == 20230121
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

    child_pins = [pin for pin in source.pin_occurrences if pin.component_reference == "R1"]
    child_pin_scopes = {pin.scope_id for pin in child_pins}

    assert len(child_pins) == 4
    assert child_pin_scopes == {
        ScopeId(path=("aaaaaaaa-1111-2222-3333-444444444444",)),
        ScopeId(path=("bbbbbbbb-1111-2222-3333-444444444444",)),
    }
    for pin in child_pins:
        assert pin.local_net_id
        assert local_nets_by_id[pin.local_net_id].scope_id == pin.scope_id


def test_generated_local_net_name_keeps_full_anonymous_source_key() -> None:
    assert (
        _generated_local_net_name(
            "root:local_net:0001:12.0000:34.0000",
            [],
            [],
            [],
            [],
            [],
        )
        == "0001:12.0000:34.0000"
    )


def test_multi_pin_power_symbol_attaches_evidence_to_each_local_net(tmp_path: Path) -> None:
    schematic_path = tmp_path / "multi_pin_power.kicad_sch"
    schematic_path.write_text(
        """
(kicad_sch (version 20230121) (generator eeschema)
  (uuid 10000000-0000-0000-0000-000000000001)
  (paper "A4")
  (lib_symbols
    (symbol "Test:OnePin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "OnePin" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "OnePin_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
    (symbol "power:DUAL_VCC" (power) (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Value" "VCC" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "DUAL_VCC_1_1"
        (pin power_in line (at 0 0 0) (length 0)
          (name "VCC" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
        (pin power_in line (at 20 0 0) (length 0)
          (name "VCC" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "Test:OnePin") (at 10 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000011)
    (property "Reference" "J1" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Probe" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000021))
  )
  (symbol (lib_id "Test:OnePin") (at 30 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000012)
    (property "Reference" "J2" (at 30 10 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Probe" (at 30 10 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000022))
  )
  (symbol (lib_id "power:DUAL_VCC") (at 10 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000031)
    (property "Reference" "#PWR_MULTI" (at 10 10 0) (effects (font (size 1.27 1.27)) hide))
    (property "Value" "VCC" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000041))
    (pin "2" (uuid 10000000-0000-0000-0000-000000000042))
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )

    source = kicad_to_source(schematic_path)

    power_symbols = [symbol for symbol in source.power_symbols if symbol.reference == "#PWR_MULTI"]
    assert [(symbol.name, symbol.location) for symbol in power_symbols] == [
        ("VCC", (10.0, 10.0)),
        ("VCC", (30.0, 10.0)),
    ]
    assert [symbol.id for symbol in power_symbols] == [
        "root:power_symbol:10000000-0000-0000-0000-000000000031:pin:0001",
        "root:power_symbol:10000000-0000-0000-0000-000000000031:pin:0002",
    ]
    assert len({symbol.local_net_id for symbol in power_symbols}) == 2

    design = resolve_kicad_source(source)

    [vcc_net] = [net for net in design.nets if net.name == "VCC"]
    assert {pin.component.reference for pin in vcc_net.pins} == {"J1", "J2"}
    assert len(vcc_net.occurrences) == 2
    assert all("VCC" in occurrence.source_names for occurrence in vcc_net.occurrences)


def test_bus_entries_connect_vector_bus_members(tmp_path: Path) -> None:
    schematic_path = tmp_path / "bus_entries.kicad_sch"
    schematic_path.write_text(
        """
(kicad_sch (version 20231120) (generator eeschema)
  (uuid 10000000-0000-0000-0000-000000000001)
  (paper "A4")
  (lib_symbols
    (symbol "Test:OnePin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "OnePin" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "OnePin_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "Test:OnePin") (at 10 20 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000011)
    (property "Reference" "J1" (at 10 20 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Probe" (at 10 20 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000021))
  )
  (symbol (lib_id "Test:OnePin") (at 10 30 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000012)
    (property "Reference" "J2" (at 10 30 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Probe" (at 10 30 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000022))
  )
  (wire (pts (xy 10 20) (xy 20 20)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000031))
  (wire (pts (xy 10 30) (xy 20 30)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000032))
  (bus (pts (xy 25 15) (xy 25 25)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000041))
  (bus_entry (pts (xy 20 20) (xy 25 15)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000051))
  (bus_entry (pts (xy 20 30) (xy 25 25)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000052))
  (label "DATA[0..1]" (at 25 15 0)
    (effects (font (size 1.27 1.27)))
    (uuid 10000000-0000-0000-0000-000000000061)
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )

    source = kicad_to_source(schematic_path)

    assert [(entry.start, entry.end, entry.member_name) for entry in source.bus_entries] == [
        ((20.0, 20.0), (25.0, 15.0), "DATA0"),
        ((20.0, 30.0), (25.0, 25.0), "DATA1"),
    ]

    design = resolve_kicad_source(source)

    assert {net.name for net in design.nets} == {"/DATA0", "/DATA1"}
    bus = next(bus for bus in design.buses if bus.name == "DATA[0..1]")
    assert bus.kind is BusKind.VECTOR
    assert {net.name for net in bus.members} == {"/DATA0", "/DATA1"}


def test_bus_entries_use_bus_junctions_to_find_labels(tmp_path: Path) -> None:
    schematic_path = tmp_path / "bus_entry_junction.kicad_sch"
    schematic_path.write_text(
        """
(kicad_sch (version 20231120) (generator eeschema)
  (uuid 10000000-0000-0000-0000-000000000001)
  (paper "A4")
  (lib_symbols
    (symbol "Test:OnePin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "OnePin" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "OnePin_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "Test:OnePin") (at 10 20 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000011)
    (property "Reference" "J1" (at 10 20 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Probe" (at 10 20 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000021))
  )
  (wire (pts (xy 10 20) (xy 20 20)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000031))
  (bus (pts (xy 25 15) (xy 25 25)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000041))
  (bus (pts (xy 25 20) (xy 30 20)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000042))
  (junction (at 25 20) (diameter 0) (color 0 0 0 0)
    (uuid 10000000-0000-0000-0000-000000000043))
  (bus_entry (pts (xy 20 20) (xy 25 15)) (stroke (width 0) (type default))
    (uuid 10000000-0000-0000-0000-000000000051))
  (label "DATA[0..0]" (at 30 20 0)
    (effects (font (size 1.27 1.27)))
    (uuid 10000000-0000-0000-0000-000000000061)
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )

    source = kicad_to_source(schematic_path)

    [entry] = source.bus_entries
    assert entry.member_name == "DATA0"

    design = resolve_kicad_source(source)

    bus = next(bus for bus in design.buses if bus.name == "DATA[0..0]")
    assert {net.name for net in bus.members} == {"/DATA0"}


def _write_single_pin_label_schematic(path: Path, label_name: str) -> None:
    path.write_text(
        f"""
(kicad_sch (version 20231120) (generator eeschema)
  (uuid 10000000-0000-0000-0000-000000000001)
  (paper "A4")
  (lib_symbols
    (symbol "Test:OnePin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "OnePin" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "OnePin_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "Test:OnePin") (at 10 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000011)
    (property "Reference" "J1" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Probe" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000021))
  )
  (label "{label_name}" (at 10 10 0)
    (effects (font (size 1.27 1.27)))
    (uuid 10000000-0000-0000-0000-000000000031)
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )


def test_project_text_variables_are_resolved_in_kicad_net_names(tmp_path: Path) -> None:
    schematic_path = tmp_path / "textvars.kicad_sch"
    project_path = tmp_path / "textvars.kicad_pro"
    _write_single_pin_label_schematic(schematic_path, "${NET}")
    project_path.write_text(
        '{"text_variables": {"NET": "USB/DP"}, "net_settings": {}}',
        encoding="utf-8",
    )

    source = kicad_to_source(schematic_path)

    assert [label.name for label in source.local_labels] == ["USB/DP"]
    [net] = resolve_kicad_source(source).nets
    assert net.name == "/USB{slash}DP"


def test_unresolved_project_text_variables_are_reported(tmp_path: Path) -> None:
    schematic_path = tmp_path / "missing_textvar.kicad_sch"
    _write_single_pin_label_schematic(schematic_path, "${MISSING}")
    ctx = ParseContext()

    source = kicad_to_source(schematic_path, ctx=ctx)

    assert [label.name for label in source.local_labels] == ["${MISSING}"]
    assert len(ctx.issues) == 1
    assert ctx.issues[0].category == "kicad_unresolved_text_variable"
    assert "MISSING" in ctx.issues[0].message


def test_local_power_symbol_kind_is_extracted_from_kicad_lib_symbol(tmp_path: Path) -> None:
    schematic_path = tmp_path / "local_power.kicad_sch"
    schematic_path.write_text(
        """
(kicad_sch (version 20250227) (generator eeschema)
  (uuid 10000000-0000-0000-0000-000000000001)
  (paper "A4")
  (lib_symbols
    (symbol "Test:OnePin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "OnePin" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "OnePin_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
    (symbol "power:VLOCAL" (power local) (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "VLOCAL" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "VLOCAL_1_1"
        (pin power_in line (at 0 0 90) (length 0) hide
          (name "VLOCAL" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "Test:OnePin") (at 10 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000011)
    (property "Reference" "J1" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Probe" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000021))
  )
  (symbol (lib_id "power:VLOCAL") (at 10 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000012)
    (property "Reference" "#PWR01" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (property "Value" "VLOCAL" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000022))
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )

    source = kicad_to_source(schematic_path)

    [symbol] = source.power_symbols
    assert symbol.power_kind == "local"
    [net] = resolve_kicad_source(source).nets
    assert net.name == "/VLOCAL"


def test_overline_label_markup_is_preserved_in_kicad_net_name(tmp_path: Path) -> None:
    schematic_path = tmp_path / "overline_label.kicad_sch"
    schematic_path.write_text(
        """
(kicad_sch (version 20231120) (generator eeschema)
  (uuid 10000000-0000-0000-0000-000000000001)
  (paper "A4")
  (lib_symbols
    (symbol "Test:OnePin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "OnePin" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "OnePin_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "Test:OnePin") (at 10 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000011)
    (property "Reference" "J1" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Probe" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000021))
  )
  (label "~{CS}" (at 10 10 0)
    (effects (font (size 1.27 1.27)))
    (uuid 10000000-0000-0000-0000-000000000031)
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )

    design = resolve_kicad_source(kicad_to_source(schematic_path))

    [net] = design.nets
    assert net.name == "/~{CS}"
    assert net.names[0].name == "/~{CS}"


def test_overline_pin_markup_is_preserved_for_kicad_auto_net_name(tmp_path: Path) -> None:
    schematic_path = tmp_path / "overline_pin.kicad_sch"
    schematic_path.write_text(
        """
(kicad_sch (version 20231120) (generator eeschema)
  (uuid 10000000-0000-0000-0000-000000000001)
  (paper "A4")
  (lib_symbols
    (symbol "Test:RawPin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "RawPin" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "RawPin_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "~{RESET}" (effects (font (size 1.27 1.27))))
          (number "23" (effects (font (size 1.27 1.27))))
        )
      )
    )
    (symbol "Test:PadPin" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "TP" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "PadPin" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "PadPin_1_1"
        (pin passive line (at 0 0 0) (length 0)
          (name "1" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "Test:RawPin") (at 10 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000011)
    (property "Reference" "U5" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (property "Value" "RawPin" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (pin "23" (uuid 10000000-0000-0000-0000-000000000021))
  )
  (symbol (lib_id "Test:PadPin") (at 10 10 0) (unit 1)
    (uuid 10000000-0000-0000-0000-000000000012)
    (property "Reference" "TP1" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (property "Value" "PadPin" (at 10 10 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid 10000000-0000-0000-0000-000000000022))
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )

    source = kicad_to_source(schematic_path)

    raw_pin = next(pin for pin in source.pin_occurrences if pin.component_reference == "U5")
    assert raw_pin.pin_name == "RESET"
    assert raw_pin.pin_net_name == "~{RESET}"

    design = resolve_kicad_source(source)
    [net] = design.nets
    assert net.name == "Net-(U5-~{RESET})"
    assert {pin.name for pin in net.pins} >= {"RESET", "1"}


def test_sheet_traversal_skips_ancestor_file_cycles(
    tmp_path: Path,
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

    ctx = ParseContext()
    source = kicad_to_source(root, ctx=ctx)

    assert [instance.sheet_name for instance in source.sheet_instances] == [
        "root",
        "Child",
    ]
    assert any("cycle" in issue.message for issue in ctx.issues)
