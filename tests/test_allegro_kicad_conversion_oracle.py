from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

import pytest
from allegro_oracle_helpers import OPENCELLULAR_BREAKOUT_ROOT

from phosphor_eda.formats.allegro.oracle import run_kicad_allegro_conversion_report

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.allegro_external_oracle
def test_kicad_conversion_oracle_is_opt_in_and_reports_converted_board(
    tmp_path: Path,
) -> None:
    """Proves KiCad can independently convert an Allegro board when local tools exist.

    Cannot prove Cadence-native intent, Constraint Manager semantics, or source
    object identity. This is intentionally opt-in because it shells out to KiCad.
    """
    if os.environ.get("PHOSPHOR_RUN_ALLEGRO_EXTERNAL_ORACLES") != "1":
        pytest.skip(
            "KiCad conversion oracle is opt-in; set PHOSPHOR_RUN_ALLEGRO_EXTERNAL_ORACLES=1"
        )
    if shutil.which("kicad-cli") is None:
        pytest.skip("kicad-cli is not installed")

    board = OPENCELLULAR_BREAKOUT_ROOT / "board/OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
    report = run_kicad_allegro_conversion_report(board, tmp_path / "converted.kicad_pcb")

    assert report.source_board == board
    assert report.output_board.exists()
    assert report.output_size_bytes > 0
    assert report.layer_count > 0
