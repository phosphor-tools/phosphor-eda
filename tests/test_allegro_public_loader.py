from __future__ import annotations

from pathlib import Path

import pytest

from phosphor_eda.domain.pcb import PcbBuildError
from phosphor_eda.formats.allegro import parse_allegro_pcb
from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.parser import parse_allegro_records
from phosphor_eda.formats.allegro.project_loader import load_allegro_pcb_project
from phosphor_eda.formats.allegro.records import AllegroRecordSet
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.query.project_loader import PCB_EXTENSIONS, load_pcb

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BREAKOUT_BOARD = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)


def test_load_pcb_dispatches_public_brd_files_to_native_allegro_parser() -> None:
    """Proves public PCB loading reaches the native Allegro parser.

    The fixture proves strict domain assembly through the public loader. It
    cannot prove package sidecar enrichment, which belongs to a later slice.
    """
    board = load_pcb(BREAKOUT_BOARD)

    assert ".brd" in PCB_EXTENSIONS
    assert board.name == BREAKOUT_BOARD.stem
    assert board.metadata.source_format == "allegro"
    assert board.board_profile is not None
    assert board.board_profile.elements
    assert board.footprints
    assert board.pads
    assert board.vias
    assert board.drills


def test_parse_allegro_pcb_public_wrapper_accepts_parse_context_for_loader_parity() -> None:
    ctx = ParseContext()

    board = parse_allegro_pcb(BREAKOUT_BOARD, ctx)

    assert board.name == BREAKOUT_BOARD.stem
    assert board.metadata.source_format == "allegro"
    assert ctx.issues == []


def test_load_allegro_pcb_project_returns_board_and_board_side_enrichment() -> None:
    """Proves a standalone Allegro board can be loaded as a project.

    Board-side constraints prove project enrichment. They cannot prove OPJ
    schematic enrichment or package sidecar enrichment.
    """
    project = load_allegro_pcb_project(BREAKOUT_BOARD)

    assert project.name == BREAKOUT_BOARD.stem
    assert project.metadata.format == "allegro"
    assert project.metadata.source_paths == [str(BREAKOUT_BOARD)]
    assert len(project.boards) == 1
    assert project.board is project.boards[0]
    assert project.board is not None
    assert project.board.metadata.source_format == "allegro"
    assert project.board.board_profile is not None
    assert project.net_classes


def test_public_allegro_board_build_requires_board_profile_for_real_boards() -> None:
    source = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    without_profile = AllegroRecordSet(
        header=source.header,
        string_table=source.string_table,
        records=tuple(
            record
            for record in source.records
            if not (
                record.payload.get("layer_class_id") == 1
                and record.payload.get("layer_subclass_id") in {0xEA, 0xFD}
            )
        ),
        end_offset=source.end_offset,
    )

    with pytest.raises(PcbBuildError, match="board profile is required"):
        build_allegro_board(
            without_profile,
            name=BREAKOUT_BOARD.stem,
            require_board_profile=True,
        )
