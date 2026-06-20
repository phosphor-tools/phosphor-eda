from __future__ import annotations

from pathlib import Path

from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.oracle import parse_packaged_netlist_summary
from phosphor_eda.formats.allegro.parser import parse_allegro_records

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BREAKOUT_BOARD = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
BREAKOUT_NETLIST = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "orcad/OpenCellular/electronics/breakout/schematic/Netlist"
)


def test_allegro_board_assembly_emits_connectivity_padstacks_and_drills() -> None:
    """Proves native Allegro records assemble into strict board-domain objects.

    Packaged netlist sidecars prove component, net, and pin counts. They cannot
    prove physical padstack geometry, routed copper, board profile, or pours.
    """
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    oracle = parse_packaged_netlist_summary(BREAKOUT_NETLIST)

    board = build_allegro_board(record_set, name=BREAKOUT_BOARD.stem)

    assert len(board.footprints) == oracle.unique_refdes_count
    assert (
        len({footprint.reference for footprint in board.footprints}) == oracle.unique_refdes_count
    )
    assert (
        len({footprint.reference for footprint in board.footprints} & oracle.component_refs) >= 40
    )
    assert len(board.nets) >= oracle.net_count - 1
    assert 0 not in board.nets

    footprint_pads = [pad for pad in board.pads if pad.footprint is not None]
    connected_footprint_pads = [pad for pad in footprint_pads if pad.net is not None]
    assert len(connected_footprint_pads) >= oracle.node_count - oracle.no_connect_node_count
    assert footprint_pads[0].number == "1"
    assert all(pad.drill is None or pad.drill.owner is pad for pad in board.pads)
    assert all(pad.footprint in board.footprints for pad in footprint_pads)

    assert board.vias
    assert board.drills
    assert all(via.drill.owner is via for via in board.vias)
    assert all(drill.layers for drill in board.drills)
