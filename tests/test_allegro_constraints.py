from __future__ import annotations

from pathlib import Path

from phosphor_eda.formats.allegro.constants import AllegroBoardUnits, AllegroVersion
from phosphor_eda.formats.allegro.constraints import extract_allegro_constraints
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.project_loader import load_allegro_pcb_project
from phosphor_eda.formats.allegro.records import AllegroHeader, AllegroRecord, AllegroRecordSet

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BREAKOUT_BOARD = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)


def test_physical_constraint_sets_become_net_classes() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    constraints = extract_allegro_constraints(record_set)

    physical_classes = [
        nc
        for nc in constraints.net_classes
        if nc.properties["allegro_constraint_kind"] == "physical_constraint_set"
    ]
    assert [(nc.name, nc.trace_width_mm, nc.clearance_mm) for nc in physical_classes] == [
        ("CS_0", 0.508, 0.1016),
        ("CS_1", 0.14478, 0.1016),
        ("CS_2", 0.12192, 0.1016),
        ("DEFAULT", 0.2032, 0.1016),
    ]
    assert [rule.name for rule in constraints.design_rules] == [
        "Allegro physical constraint set CS_0",
        "Allegro physical constraint set CS_1",
        "Allegro physical constraint set CS_2",
        "Allegro physical constraint set DEFAULT",
    ]


def test_physical_constraint_units_are_exhaustive_and_do_not_fabricate_diff_pair_width() -> None:
    record_set = AllegroRecordSet(
        header=AllegroHeader(
            magic=0,
            version=AllegroVersion.V_172,
            version_string="",
            object_count=1,
            max_key=1,
            record_0x27_end=0,
            string_count=0,
            board_units=AllegroBoardUnits.MICROMETERS,
            unit_divisor=1,
            linked_lists=(),
            layer_map=(),
        ),
        string_table=None,
        records=(
            AllegroRecord(
                tag=0x1D,
                offset=0,
                end_offset=56,
                key=1,
                next_key=None,
                payload={
                    "data_b_fields": ((0, 1_000, 0, 0, 2_000, 0, 0, 3_000, 0, 0, 0, 0, 0, 0),)
                },
            ),
        ),
        end_offset=56,
    )

    constraints = extract_allegro_constraints(record_set)

    net_class = constraints.net_classes[0]
    assert net_class.trace_width_mm == 1.0
    assert net_class.clearance_mm == 2.0
    assert net_class.diff_pair_gap_mm == 3.0
    assert net_class.diff_pair_width_mm == 0.0


def test_nets_without_explicit_constraint_set_inherit_default_net_class() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    constraints = extract_allegro_constraints(record_set)

    classes_by_name = {net_class.name: net_class for net_class in constraints.net_classes}
    assert "pcie0_txn" in classes_by_name["DEFAULT"].members
    assert "irq_int" in classes_by_name["DEFAULT"].members
    assert classes_by_name["CS_0"].members == []


def test_match_groups_emit_diff_pairs_and_match_group_net_classes() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)

    constraints = extract_allegro_constraints(record_set)

    assert [
        (pair.name, pair.positive_net, pair.negative_net) for pair in constraints.diff_pairs
    ] == [
        ("TRXFE_USB3_RX0", "trxfe_usb3_rx0_p", "trxfe_usb3_rx0_n"),
    ]
    classes_by_name = {net_class.name: net_class for net_class in constraints.net_classes}
    assert classes_by_name["MG_50_OHMS"].properties["allegro_constraint_kind"] == "match_group"
    assert classes_by_name["MG_DP2"].properties["allegro_constraint_kind"] == "match_group"
    assert "irq_int" in classes_by_name["MG_50_OHMS"].members
    assert "n370055" in classes_by_name["MG_DP2"].members
    assert [diagnostic.code for diagnostic in constraints.diagnostics] == [
        "allegro-diff-pair-polarity-unknown"
    ]


def test_allegro_pcb_project_loader_returns_constraint_enrichment() -> None:
    project = load_allegro_pcb_project(BREAKOUT_BOARD)

    assert project.name == BREAKOUT_BOARD.stem
    assert project.metadata.format == "allegro"
    assert project.metadata.source_paths == [str(BREAKOUT_BOARD)]
    assert len(project.net_classes) == 8
    assert len(project.design_rules) == 4
    assert len(project.diff_pairs) == 1
