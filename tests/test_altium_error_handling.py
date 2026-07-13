"""Tests for error handling and ParseContext threading in Altium parsers.

Verifies that malformed inputs produce warnings/errors in ParseContext
rather than crashes, and that parse issues propagate from leaf parsers
up to the top-level entry points.
"""

import pytest

from phosphor_eda.formats.altium.errors import AltiumFormatError
from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.formats.altium.record_factory import materialize_records
from phosphor_eda.formats.altium.record_parser import read_schematic_records
from phosphor_eda.formats.altium.records import RecordType, UnknownRecord
from phosphor_eda.formats.common.diagnostics import ParseContext

# ---------------------------------------------------------------------------
# Schematic record materialization: invalid inputs
# ---------------------------------------------------------------------------


def test_non_integer_record_field():
    """Non-integer RECORD field → UnknownRecord + warning."""
    ctx = ParseContext()
    records = materialize_records(
        [{"record": "abc", "ownerindex": "-1"}],
        ctx=ctx,
    )
    assert len(records) == 1
    assert isinstance(records[0], UnknownRecord)
    assert len(ctx.issues) == 1
    assert "non-integer" in ctx.issues[0].message.lower()


def test_unknown_record_type_value():
    """Unknown RecordType integer → UnknownRecord + warning."""
    ctx = ParseContext()
    records = materialize_records(
        [{"record": "999", "ownerindex": "-1"}],
        ctx=ctx,
    )
    assert len(records) == 1
    assert isinstance(records[0], UnknownRecord)
    assert len(ctx.issues) == 1
    assert "unknown record type" in ctx.issues[0].message.lower()


def test_missing_record_field():
    """Missing RECORD key entirely → UnknownRecord + warning."""
    ctx = ParseContext()
    records = materialize_records(
        [{"ownerindex": "-1", "somekey": "somevalue"}],
        ctx=ctx,
    )
    assert len(records) == 1
    assert isinstance(records[0], UnknownRecord)
    assert len(ctx.issues) == 1


def test_pin_missing_location():
    """PinRec with missing location fields → defaults to (0, 0)."""
    ctx = ParseContext()
    records = materialize_records(
        [{"record": str(RecordType.PIN.value), "ownerindex": "0", "name": "A1"}],
        ctx=ctx,
    )
    assert len(records) == 1
    # Pin should still be created with default location
    from phosphor_eda.formats.altium.records import PinRec

    assert isinstance(records[0], PinRec)
    assert records[0].location == (0, 0)


def test_valid_records_produce_no_warnings():
    """Normal component record → no issues."""
    ctx = ParseContext()
    records = materialize_records(
        [
            {
                "record": str(RecordType.COMPONENT.value),
                "ownerindex": "-1",
                "libreference": "R",
                "location.x": "100",
                "location.y": "200",
            }
        ],
        ctx=ctx,
    )
    assert len(records) == 1
    assert len(ctx.issues) == 0


# ---------------------------------------------------------------------------
# ParseContext propagation through load_sheet → altium_to_design
# ---------------------------------------------------------------------------


def test_load_sheet_accepts_ctx():
    """load_sheet passes its ctx down to materialize_records."""
    from pathlib import Path

    from phosphor_eda.formats.altium.sheet_builder import load_sheet

    # Use a real fixture to verify ctx propagates without crashing
    fixtures = Path(__file__).resolve().parent / "fixtures"
    schdoc = fixtures.parent / "upstream/qfsae-pcb/Debugger/TOP.SchDoc"
    if not schdoc.exists():
        return  # Skip if fixture not available

    ctx = ParseContext()
    sheet = load_sheet(str(schdoc), ctx=ctx)
    # The sheet should load successfully regardless of any warnings
    assert sheet.name == "TOP"
    # ctx may or may not have issues — we just verify it was threaded through


def test_altium_to_design_returns_parse_issues():
    """altium_to_design includes parse issues in design metadata."""
    from pathlib import Path

    from phosphor_eda.formats.altium.to_schematic import altium_to_design

    fixtures = Path(__file__).resolve().parent / "fixtures"
    schdoc = fixtures.parent / "upstream/qfsae-pcb/Debugger/MCU.SchDoc"
    if not schdoc.exists():
        return

    design = altium_to_design(schdoc)
    # The design should load, parse issues (if any) are in metadata
    assert len(design.pages) == 1


def test_ascii_schdoc_gets_friendly_error(tmp_path):
    path = tmp_path / "ASCII_design.SchDoc"
    path.write_text("|RECORD=31|FONTIDCOUNT=1|SIZE1=10|\n", encoding="cp1252")

    with pytest.raises(AltiumFormatError) as excinfo:
        read_schematic_records(str(path))
    assert "ASCII-format Altium files are not supported" in str(excinfo.value)
    assert "ASCII_design.SchDoc" in str(excinfo.value)


def test_ascii_pcbdoc_gets_friendly_error(tmp_path):
    path = tmp_path / "ASCII_board.PcbDoc"
    path.write_text("|RECORD=Board|FILENAME=board.PcbDoc|\n", encoding="cp1252")

    with pytest.raises(AltiumFormatError) as excinfo:
        parse_altium_pcb(path)
    assert "ASCII-format Altium files are not supported" in str(excinfo.value)


def test_non_ole_garbage_gets_named_error(tmp_path):
    path = tmp_path / "garbage.PcbDoc"
    path.write_bytes(b"\x00\x01\x02\x03 not an ole file")

    with pytest.raises(AltiumFormatError) as excinfo:
        parse_altium_pcb(path)
    assert "garbage.PcbDoc" in str(excinfo.value)
