"""pstxnet naming oracle over an optional local OrCAD corpus.

Runs only with ``--run-corpus`` or ``PHOSPHOR_RUN_CORPUS=1`` where the corpus
checkout exists. The boards are the corpus designs that ship a
Cadence-packaged netlist and parse cleanly (MicArray-Main carries a PSD-era
stream layout the parser rejects and is excluded).
"""

import os
from pathlib import Path

import pytest
from dsn_oracle_helpers import compare_net_names

CORPUS_ROOT = Path(os.environ.get("PHOSPHOR_EDA_CORPUS_ROOT", "__external_corpus_missing__"))
CORPUS = CORPUS_ROOT / "designs/orcad"

# (board, DSN, pstxnet.dat, min membership-matched nets, min matched autonames)
BOARDS = [
    (
        "OC-sync",
        CORPUS / "OpenCellular/electronics/sync/schematics/dsn/FB_CONNECT1_SYNC_LIFE-3_V1P1.DSN",
        CORPUS / "OpenCellular/electronics/sync/schematics/Netlist/pstxnet.dat",
        120,
        90,
    ),
    (
        "OC-frontend-GSM",
        CORPUS
        / "OpenCellular/electronics/front-end/GSM-900-2W/V1/schematics"
        / "DSN/OC_CONNECT1_FRONTEND_REV_C_V1P1.DSN",
        CORPUS / "OpenCellular/electronics/front-end/GSM-900-2W/V1/schematics/Netlist/pstxnet.dat",
        700,
        400,
    ),
    (
        "OC-breakout",
        CORPUS / "OpenCellular/electronics/breakout/schematic/dsn/OC_CONNECT_1_BRKOUT_BRD.DSN",
        CORPUS / "OpenCellular/electronics/breakout/schematic/Netlist/pstxnet.dat",
        19,
        19,
    ),
    (
        "MicArray-8Mics",
        CORPUS / "Mic_Array/Hardware/8Mics/8MICS.DSN",
        CORPUS / "Mic_Array/Hardware/8Mics/allegro/pstxnet.dat",
        18,
        0,
    ),
]


@pytest.mark.corpus
@pytest.mark.skipif(not CORPUS.exists(), reason="external OrCAD corpus not present")
@pytest.mark.parametrize(
    ("board", "dsn", "pstxnet", "min_matched", "min_autonames"),
    BOARDS,
    ids=[board[0] for board in BOARDS],
)
def test_corpus_net_names_match_pstxnet_oracle(
    board: str,
    dsn: Path,
    pstxnet: Path,
    min_matched: int,
    min_autonames: int,
) -> None:
    if not dsn.exists() or not pstxnet.exists():
        pytest.skip(f"{board} files missing from corpus checkout")

    result = compare_net_names(dsn, pstxnet)

    assert result.mismatched == [], f"{board}: oracle name mismatches {result.mismatched[:10]}"
    assert len(result.matched) >= min_matched
    autonames = result.matched_autonames
    assert len(autonames) >= min_autonames
    # Autonames must be byte-exact (N + zero-padded seed-wire dbid).
    assert all(oracle == ours for oracle, ours in autonames)
