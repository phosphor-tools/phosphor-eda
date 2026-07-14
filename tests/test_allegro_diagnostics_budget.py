"""Diagnostics budget locks for the four Allegro fixture boards.

The parser-accuracy work (arc-true pours/voids/keepouts, ring-terminated chain
walkers, mechanical pads, real text sizes) collapsed per-board parse diagnostics
from tens of thousands of noise records to the low hundreds. Nothing in the type
system stops a future refactor from silently reintroducing that flood — a
regressed walker or a dropped arc branch would re-emit thousands of diagnostics
while the board still "parses", so no other test would fail.

This budget is that guardrail. It pins two things per board:

1. A total-diagnostic ceiling set ~20% above today's measured count (the
   bulk of the remaining diagnostics are ``skipped-footprint-shape`` records:
   package-symbol copper not yet attached to any consumer, surfaced instead
   of silently dropped), so a
   regression that reintroduces bulk noise trips the budget instead of passing
   silently.
2. A hard denylist of noise codes that the accuracy work eliminated. These must
   never reappear in bulk; ``parse_diagnostic_codes`` is a deduplicated set, so
   asserting a code is absent is equivalent to asserting its count is zero.

Parsing all four boards takes only a couple of seconds total, so the coverage is
cheap relative to the regression it prevents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fixture_paths import UPSTREAM_FIXTURES

from phosphor_eda.formats.allegro.build import build_allegro_board
from phosphor_eda.formats.allegro.parser import parse_allegro_records

if TYPE_CHECKING:
    from pathlib import Path

BREAKOUT_BOARD = (
    UPSTREAM_FIXTURES
    / "opencellular/electronics/breakout"
    / "board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
SYNC_BOARD = (
    UPSTREAM_FIXTURES / "opencellular/electronics/sync" / "board" / "Fb_Connect1_SYNC_Life-3.brd"
)
ROHM_BOARD = (
    UPSTREAM_FIXTURES
    / "rohm-stepper-driver"
    / "Design Files for Rev 1.0"
    / "STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd"
)
LAUNCHXL_BOARD = (
    UPSTREAM_FIXTURES
    / "cp-smartgarden"
    / "Document/Hardware/mcu/swrc319/Cadence/Allegro"
    / "LAUNCHXL-CC1310.brd"
)

# Ceilings sit ~20% above the counts measured after the accuracy work landed
# (breakout 259, sync 477, rohm 264, launchxl 91). The headroom absorbs benign
# additions; a bulk-noise regression blows past it.
BOARD_BUDGETS: tuple[tuple[str, Path, int], ...] = (
    ("breakout", BREAKOUT_BOARD, 606),
    ("sync", SYNC_BOARD, 756),
    ("rohm", ROHM_BOARD, 407),
    ("launchxl", LAUNCHXL_BOARD, 730),
)

# Noise codes the parser-accuracy work eliminated. Each was previously emitted in
# bulk (hundreds to tens of thousands per board) for non-anomalies; none must
# reappear.
FORBIDDEN_NOISE_CODES: frozenset[str] = frozenset(
    {
        "segment-owner-mismatch",
        "unresolved-component-pad",
        "unresolved-footprint-instance-chain",
        "approximated-shape-arc",
        "approximated-keepout-arc",
        "approximated-shape-void-arc",
        "invalid-keepout-boundary",
        "invalid-shape-void-boundary",
        "unresolved-text-size",
    }
)


@pytest.mark.parametrize(
    ("board_path", "ceiling"),
    [pytest.param(path, ceiling, id=name) for name, path, ceiling in BOARD_BUDGETS],
)
def test_allegro_board_stays_within_diagnostic_budget(board_path: Path, ceiling: int) -> None:
    record_set = parse_allegro_records(board_path.read_bytes(), source_name=board_path.name)

    board = build_allegro_board(record_set, name=board_path.stem)

    count = int(board.metadata.properties["parse_diagnostic_count"])
    assert count <= ceiling, (
        f"{board_path.stem} emitted {count} parse diagnostics, over the {ceiling} budget"
    )

    codes = set(board.metadata.properties.get("parse_diagnostic_codes", "").split(";"))
    reintroduced = FORBIDDEN_NOISE_CODES & codes
    assert not reintroduced, f"{board_path.stem} reintroduced forbidden noise codes: {reintroduced}"
