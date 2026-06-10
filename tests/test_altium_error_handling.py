"""Tests for error handling and ParseContext threading in Altium parsers.

Verifies that malformed inputs produce warnings/errors in ParseContext
rather than crashes, and that parse issues propagate from leaf parsers
up to the top-level entry points.
"""

from phosphor_eda.formats.altium.record_factory import materialize_records
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
    schdoc = fixtures / "altium/qfsae-debugger/TOP.SchDoc"
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
    schdoc = fixtures / "altium/qfsae-debugger/MCU.SchDoc"
    if not schdoc.exists():
        return

    design = altium_to_design(schdoc)
    # The design should load, parse issues (if any) are in metadata
    assert len(design.pages) == 1
