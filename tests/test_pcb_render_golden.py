"""Golden-file snapshot tests for PCB SVG rendering.

These lock the SVG output for one KiCad and one Altium fixture so that
deliberate output changes (plan 11 native curves) are reviewed via a visible
golden diff, and unintended changes are caught.

The KiCad fixture is small enough to commit its full SVG as a byte-exact
golden. The Altium fixture renders to tens of megabytes (the very bloat plan
11 removes), so committing the raw SVG would be hostile to the repo. Instead
we commit a manifest (SHA-256 + byte size + path-command counts); the byte
size is the per-step shrink record, and the hash makes any unintended change
fail loudly.

Regenerate goldens after an intentional change:

    PHOSPHOR_UPDATE_GOLDENS=1 uv run pytest cli/tests/test_pcb_render_golden.py

Review the resulting diff before committing. The companion path-data
assertions guard the specific primitive shapes (native arcs vs polygons).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from phosphor_eda.formats.allegro import parse_allegro_pcb
from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.formats.kicad.board import parse_kicad_pcb
from phosphor_eda.render.api import render_pcb_svg
from phosphor_eda.render.settings import (
    CliOverrides,
    load_render_settings_json,
    resolve_effective_settings,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from phosphor_eda.domain.pcb import Board

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDENS = Path(__file__).resolve().parent / "goldens"

KICAD_FIXTURE = FIXTURES / "swd_switch.kicad_pcb"
ALTIUM_FIXTURE = FIXTURES / "altium/pi-mx8/PCB/PiMX8MP_r0.3.PcbDoc"
ALLEGRO_FIXTURE = (
    FIXTURES
    / "orcad/opencellular-breakout/allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)

KICAD_GOLDEN = GOLDENS / "swd_switch.design.front.svg"
ALTIUM_MANIFEST = GOLDENS / "pi-mx8.design.front.manifest.json"
ALLEGRO_MANIFEST = GOLDENS / "opencellular-breakout.design.front.manifest.json"

_UPDATE = os.environ.get("PHOSPHOR_UPDATE_GOLDENS") == "1"


def _design_front_svg(
    board_path: Path,
    parse: Callable[[Path], Board],
    *,
    debug_attributes: bool = False,
) -> str:
    base = load_render_settings_json('{"extends": "phosphor:design"}')
    settings = resolve_effective_settings(
        base, CliOverrides(side="front", debug_attributes=debug_attributes or None)
    )
    return render_pcb_svg(parse(board_path), settings).svg


def _svg_manifest(svg: str) -> dict[str, object]:
    encoded = svg.encode("utf-8")
    return {
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "path_count": svg.count("<path "),
        "arc_commands": svg.count(" A "),
        "line_commands": svg.count(" L "),
    }


def _assert_text_golden(svg: str, golden: Path) -> None:
    if _UPDATE:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(svg, encoding="utf-8")
        pytest.skip(f"updated golden {golden.name}")
    assert golden.exists(), f"missing golden {golden}; run PHOSPHOR_UPDATE_GOLDENS=1 to create it"
    assert svg == golden.read_text(encoding="utf-8"), (
        f"SVG output diverged from golden {golden.name}; if intended, "
        "regenerate with PHOSPHOR_UPDATE_GOLDENS=1 and review the diff"
    )


def _assert_manifest_golden(svg: str, manifest_path: Path) -> None:
    manifest = _svg_manifest(svg)
    if _UPDATE:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        pytest.skip(f"updated manifest {manifest_path.name}")
    assert manifest_path.exists(), (
        f"missing manifest {manifest_path}; run PHOSPHOR_UPDATE_GOLDENS=1 to create it"
    )
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == expected, (
        f"SVG output diverged from manifest {manifest_path.name}; if intended, "
        "regenerate with PHOSPHOR_UPDATE_GOLDENS=1 and review the byte-size delta"
    )


@pytest.fixture(scope="module")
def kicad_svg() -> str:
    return _design_front_svg(KICAD_FIXTURE, parse_kicad_pcb)


@pytest.fixture(scope="module")
def altium_svg() -> str:
    return _design_front_svg(ALTIUM_FIXTURE, parse_altium_pcb)


@pytest.fixture(scope="module")
def allegro_svg() -> str:
    return _design_front_svg(ALLEGRO_FIXTURE, parse_allegro_pcb)


def test_kicad_design_golden(kicad_svg: str) -> None:
    _assert_text_golden(kicad_svg, KICAD_GOLDEN)


def test_altium_design_manifest(altium_svg: str) -> None:
    _assert_manifest_golden(altium_svg, ALTIUM_MANIFEST)


def test_allegro_breakout_design_manifest(allegro_svg: str) -> None:
    _assert_manifest_golden(allegro_svg, ALLEGRO_MANIFEST)


def test_kicad_render_preserves_core_data_attrs(kicad_svg: str) -> None:
    """Per-element data-* attributes are opt-in debug output; group-level
    structure attrs are always present."""
    assert "data-role=" in kicad_svg
    assert "data-source-layers=" in kicad_svg
    assert "data-kind=" not in kicad_svg

    debug_svg = _design_front_svg(KICAD_FIXTURE, parse_kicad_pcb, debug_attributes=True)
    for attr in (
        "data-kind=",
        "data-role=",
        "data-source-id=",
        "data-source-layer=",
        "data-purpose=",
    ):
        assert attr in debug_svg, f"missing {attr} in debug KiCad render"
