"""Diagnostics for the Altium .PcbDoc parser.

Covers the dedicated parse-error type, the swallow sites that now record
warnings, and the truncated-stream detection in the record readers.
"""

import struct

from phosphor_eda.formats.altium.errors import AltiumPcbParseError
from phosphor_eda.formats.altium.pcb_layers import (
    v9_stack_layer_id_to_num,
)
from phosphor_eda.formats.altium.pcb_primitives import (
    read_binary_records,
    read_text_records,
)
from phosphor_eda.formats.altium.pcb_regions import (
    parse_region_kind,
)
from phosphor_eda.formats.common.diagnostics import ParseContext


def test_altium_pcb_parse_error_is_value_error() -> None:
    """AltiumPcbParseError stays catchable as ValueError for existing callers."""
    assert issubclass(AltiumPcbParseError, ValueError)


def test_region_kind_warns_on_non_integer() -> None:
    ctx = ParseContext()
    assert parse_region_kind({"kind": "weird"}, ctx) is None
    assert any("region kind" in issue.message for issue in ctx.issues)


def test_region_kind_no_warning_when_absent() -> None:
    ctx = ParseContext()
    assert parse_region_kind({}, ctx) is None
    assert ctx.issues == []


def test_v9_stack_layer_id_warns_on_non_integer() -> None:
    ctx = ParseContext()
    assert v9_stack_layer_id_to_num("not-an-int", ctx, key="v9_stack_layer3_layerid") is None
    assert any("stack layer id" in issue.message for issue in ctx.issues)


def test_read_binary_records_warns_on_truncation() -> None:
    """A record claiming more bytes than remain triggers a truncation warning."""
    # type byte 2, then a u32 length of 100 with no body following.
    data = bytes([2]) + struct.pack("<I", 100)
    ctx = ParseContext()
    records = read_binary_records(data, ctx, source="TestStream")
    assert records == []
    assert any(
        "TestStream" in issue.message and "trailing" in issue.message for issue in ctx.issues
    )


def test_read_text_records_warns_on_truncation() -> None:
    data = struct.pack("<I", 100) + b"abc"
    ctx = ParseContext()
    records = read_text_records(data, ctx, source="TextStream")
    assert records == []
    assert any("TextStream" in issue.message for issue in ctx.issues)
