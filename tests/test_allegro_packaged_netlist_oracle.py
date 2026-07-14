from __future__ import annotations

from fixture_paths import UPSTREAM_FIXTURES

from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.oracle import (
    compare_board_to_packaged_netlist,
    parse_packaged_netlist_summary,
)
from phosphor_eda.formats.allegro.parser import parse_allegro_records

BREAKOUT_BOARD = (
    UPSTREAM_FIXTURES
    / "opencellular/electronics/breakout"
    / "board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
BREAKOUT_NETLIST = UPSTREAM_FIXTURES / "opencellular/electronics/breakout" / "schematic/Netlist"


def test_packaged_netlist_comparison_reports_board_component_net_and_pin_counts() -> None:
    """Proves Cadence sidecars validate board component, net, and connected-pin counts.

    Cannot prove padstack copper geometry, drills, placement transforms, routed
    copper, board profile, or dynamic shapes.
    """
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)
    summary = parse_packaged_netlist_summary(BREAKOUT_NETLIST)

    comparison = compare_board_to_packaged_netlist(board, summary)

    assert comparison.board_component_count == summary.unique_refdes_count
    assert comparison.sidecar_component_count == summary.unique_refdes_count
    assert comparison.board_connected_pin_count >= comparison.sidecar_connected_pin_count
    assert comparison.board_net_count >= comparison.sidecar_net_count - 1
    assert comparison.unresolved_component_refs
