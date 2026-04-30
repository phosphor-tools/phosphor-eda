"""Tests for Altium parse error/warning infrastructure."""

from __future__ import annotations

from phosphor_eda.altium.enums import PinElectrical, RecordOrientation
from phosphor_eda.altium.errors import ParseContext, ParseIssue, ParseSeverity


class TestParseSeverity:
    def test_warning_less_than_error(self) -> None:
        assert ParseSeverity.WARNING < ParseSeverity.ERROR


class TestParseIssue:
    def test_defaults(self) -> None:
        issue = ParseIssue(
            severity=ParseSeverity.WARNING,
            category="test",
            message="something happened",
        )
        assert issue.record_index is None

    def test_with_record_index(self) -> None:
        issue = ParseIssue(
            severity=ParseSeverity.ERROR,
            category="parse",
            message="bad record",
            record_index=42,
        )
        assert issue.record_index == 42


class TestParseContext:
    def test_starts_empty(self) -> None:
        ctx = ParseContext()
        assert len(ctx.issues) == 0

    def test_warn_adds_warning(self) -> None:
        ctx = ParseContext()
        ctx.warn("layer", "unknown layer 99")
        assert len(ctx.issues) == 1
        assert ctx.issues[0].severity == ParseSeverity.WARNING
        assert ctx.issues[0].category == "layer"
        assert "unknown layer 99" in ctx.issues[0].message

    def test_error_adds_error(self) -> None:
        ctx = ParseContext()
        ctx.error("corrupt", "record truncated", record_index=5)
        assert len(ctx.issues) == 1
        assert ctx.issues[0].severity == ParseSeverity.ERROR
        assert ctx.issues[0].record_index == 5

    def test_accumulates_multiple_issues(self) -> None:
        ctx = ParseContext()
        ctx.warn("a", "first")
        ctx.error("b", "second")
        ctx.warn("c", "third")
        assert len(ctx.issues) == 3
        assert ctx.issues[0].severity == ParseSeverity.WARNING
        assert ctx.issues[1].severity == ParseSeverity.ERROR
        assert ctx.issues[2].severity == ParseSeverity.WARNING


class TestRequireEnum:
    def test_valid_value(self) -> None:
        ctx = ParseContext()
        result = ctx.require_enum(0, RecordOrientation, "orientation")
        assert result == RecordOrientation.RIGHTWARDS
        assert len(ctx.issues) == 0

    def test_invalid_value_returns_default(self) -> None:
        ctx = ParseContext()
        result = ctx.require_enum(99, PinElectrical, "electrical", default=PinElectrical.PASSIVE)
        assert result == PinElectrical.PASSIVE
        assert len(ctx.issues) == 1
        assert ctx.issues[0].severity == ParseSeverity.WARNING
        assert "99" in ctx.issues[0].message
        assert "PinElectrical" in ctx.issues[0].message

    def test_invalid_value_default_none(self) -> None:
        ctx = ParseContext()
        result = ctx.require_enum(55, RecordOrientation, "orientation")
        assert result is None
        assert len(ctx.issues) == 1

    def test_records_index(self) -> None:
        ctx = ParseContext()
        ctx.require_enum(99, PinElectrical, "electrical", record_index=7)
        assert ctx.issues[0].record_index == 7
