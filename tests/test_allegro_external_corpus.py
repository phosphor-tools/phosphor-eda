from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from test_allegro_fixture_inventory import (
    EXTERNAL_ALLEGRO_CORPUS,
    EXTERNAL_KICAD_ALLEGRO_FIXTURES,
    _corpus_board_headers,
    _read_allegro_header,
)

from phosphor_eda.formats.allegro.errors import (
    AllegroUnsupportedVersionError,
)
from phosphor_eda.formats.allegro.parser import parse_allegro_header

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.allegro_corpus
@pytest.mark.skipif(
    not EXTERNAL_ALLEGRO_CORPUS.exists(), reason="external Allegro corpus not present"
)
def test_optional_external_allegro_corpus_version_inventory_is_locked() -> None:
    boards = _corpus_board_headers(EXTERNAL_ALLEGRO_CORPUS)

    assert len(boards) == 19
    assert Counter(board.header.version_family for board in boards) == Counter(
        {"V_166": 14, "V_172": 3, "V_164": 1, "V_165": 1}
    )
    assert {board.path for board in boards if board.header.version_family == "V_172"} == {
        "beaglebone-ai/HW/BeagleBone-AI-RevA2/BeagleBone AI_RevA2_PCB_200527.brd",
        "beaglebone-ai/HW/BeagleBone-AI.brd",
        "beaglebone-black/ALLEGRO/BeagleBone Black_PCB_RevC_No Logo_210401.brd",
    }


@pytest.mark.allegro_corpus
@pytest.mark.skipif(
    not EXTERNAL_KICAD_ALLEGRO_FIXTURES.exists(),
    reason="external KiCad Allegro importer fixtures not present",
)
def test_optional_kicad_allegro_importer_fixture_inventory_is_locked() -> None:
    boards = _corpus_board_headers(EXTERNAL_KICAD_ALLEGRO_FIXTURES)

    assert len(boards) == 16
    assert Counter(board.header.version_family for board in boards) == Counter(
        {"V_166": 6, "V_172": 4, "V_174": 4, "V_175": 1, "PRE_V16": 1}
    )

    registry = EXTERNAL_KICAD_ALLEGRO_FIXTURES / "boards/board_data_registry.json"
    v18_header = EXTERNAL_KICAD_ALLEGRO_FIXTURES / "boards/CutiePi_V2_3_dbd18/header.bin"
    assert registry.exists()
    assert v18_header.exists()

    registry_text = registry.read_text(encoding="utf-8")
    assert '"CutiePi_V2_3_dbd18"' in registry_text
    assert '"formatVersion": "18.0"' in registry_text

    header_bytes = v18_header.read_bytes()
    assert _read_allegro_header(v18_header).version_family == "V_180"
    assert b"dbd414729/29" in header_bytes


def test_promoted_v18_header_layout_is_supported() -> None:
    header_file = (
        FIXTURES / "orcad/cutiepi-v18-header/kicad-allegro/boards/CutiePi_V2_3_dbd18/header.bin"
    )

    header = parse_allegro_header(header_file.read_bytes(), source_name=header_file.name)

    assert header.version.value == "V_180"
    assert header.object_count == 66541
    assert header.string_count == 1423
    assert header.unit_divisor == 1000
    assert len(header.linked_lists) == 28


def test_pre_v16_kicad_fixture_fails_with_clear_unsupported_version() -> None:
    board = EXTERNAL_KICAD_ALLEGRO_FIXTURES / "boards/v13_header/v13_header.brd"
    if not board.exists():
        pytest.skip("external KiCad Allegro importer fixtures not present")

    with pytest.raises(AllegroUnsupportedVersionError) as exc_info:
        parse_allegro_header(board.read_bytes(), source_name=board.name)

    assert exc_info.value.code == "unsupported-version"
    assert "pre-v16 Allegro files are unsupported" in str(exc_info.value)
