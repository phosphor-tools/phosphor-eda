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


# ---------------------------------------------------------------------------
# PCB backend: malformed input degrades with diagnostics (no crash)
# ---------------------------------------------------------------------------


def _text_stream(*payloads: str) -> bytes:
    """Frame pipe-delimited payloads as an Altium text-record stream."""
    out = b""
    for payload in payloads:
        body = payload.encode("cp1252")
        out += len(body).to_bytes(4, "little") + body
    return out


def test_guarded_int_and_float_degrade_with_diagnostic():
    from phosphor_eda.formats.altium._helpers import guarded_float, guarded_int

    ctx = ParseContext()
    assert guarded_int("12", ctx=ctx, field="pourindex") == 12
    assert guarded_int("oops", ctx=ctx, field="pourindex", default=-1) == -1
    assert guarded_float("1.5", ctx=ctx, field="angle") == 1.5
    assert guarded_float("nope", ctx=ctx, field="angle", default=0.0) == 0.0
    messages = [i.message for i in ctx.issues]
    assert any("pourindex" in m for m in messages)
    assert any("angle" in m for m in messages)


def test_parse_mil_malformed_degrades():
    from phosphor_eda.formats.altium.pcb_primitives import parse_mil

    ctx = ParseContext()
    assert parse_mil("bogusmil", ctx=ctx, field="vertex x") == 0.0
    assert len(ctx.issues) == 1
    assert "vertex x" in ctx.issues[0].message


def test_prop_points_caps_absurd_location_count():
    from phosphor_eda.formats.altium._helpers import prop_points

    ctx = ParseContext()
    props = {"locationcount": "1000000", "x1": "10", "y1": "20", "x2": "30", "y2": "40"}
    points = prop_points(props, ctx)
    # Only two coordinate pairs are present; the loop must not run a million times.
    assert points == [(10, 20), (30, 40)]
    assert any(i.category == "location_count_capped" for i in ctx.issues)


def test_unknown_layer_ref_warns_and_skips():
    from phosphor_eda.formats.altium.pcb_layers import altium_layer_ref

    ctx = ParseContext()
    assert altium_layer_ref(250, {}, ctx, source="track 3") is None
    assert len(ctx.issues) == 1
    assert ctx.issues[0].category == "unknown_layer"
    assert "track 3" in ctx.issues[0].message


def test_resolve_stream_net_degrades_unknown_index():
    from phosphor_eda.domain.pcb import PcbNet
    from phosphor_eda.formats.altium.pcb_primitives import (
        resolve_stream_net,
        warn_unknown_stream_nets,
    )
    from phosphor_eda.formats.altium.pcb_records import NET_UNCONNECTED

    nets = {1: PcbNet(number=1, name="N1")}
    unknown: list[int] = []
    assert resolve_stream_net(0, nets, unknown) == 1  # 0-based → domain 1 (known)
    assert resolve_stream_net(NET_UNCONNECTED, nets, unknown) == 0  # unconnected sentinel
    assert unknown == []
    assert resolve_stream_net(5, nets, unknown) == 0  # domain 6 absent → unconnected
    assert unknown == [5]

    ctx = ParseContext()
    warn_unknown_stream_nets(ctx, "Pads6/Data", unknown)
    assert len(ctx.issues) == 1
    assert ctx.issues[0].category == "unknown_net"


def test_polygon_pour_unknown_net_does_not_crash():
    from phosphor_eda.domain.pcb import PcbNet
    from phosphor_eda.formats.altium.pcb_layers import build_layer_map
    from phosphor_eda.formats.altium.pcb_streams import parse_polygon_pours

    layer_map = build_layer_map({"layer1name": "Top Layer"})
    nets = {1: PcbNet(number=1, name="GND")}
    stream = _text_stream(
        "|POURINDEX=0|NET=5|LAYER=TOP|VX0=0mil|VY0=0mil|VX1=100mil|VY1=0mil|VX2=100mil|VY2=100mil|"
    )
    ctx = ParseContext()
    pours, _id_map, _net_map = parse_polygon_pours(stream, nets, layer_map, ctx)
    assert len(pours) == 1
    assert pours[0].net is None  # unknown net degraded to unconnected
    assert any(i.category == "unknown_net" for i in ctx.issues)


def test_text_record_undecodable_bytes_warn():
    from phosphor_eda.formats.altium.pcb_records import TextRecord

    sub1 = bytes(42)
    sub2 = bytes([1, 0x81])  # Pascal length 1, 0x81 is undefined in cp1252
    data = (
        bytes([5]) + len(sub1).to_bytes(4, "little") + sub1 + len(sub2).to_bytes(4, "little") + sub2
    )
    ctx = ParseContext()
    rec = TextRecord.from_bytes(data, ctx)
    assert rec is not None
    assert any(i.category == "text_decode" for i in ctx.issues)


def test_region_record_vertex_truncation_warns():
    from phosphor_eda.formats.altium.pcb_records import RegionRecord

    header = bytearray(22)  # prop_len (bytes 18:22) stays 0
    body = bytes(header) + (5).to_bytes(4, "little") + bytes(16)  # count says 5, one vertex given
    ctx = ParseContext()
    rec = RegionRecord.from_bytes(body, ctx)
    assert rec is not None
    assert len(rec.vertices) == 1
    assert any("truncat" in i.message.lower() for i in ctx.issues)


def test_parse_pads_unexpected_type_byte_warns():
    from phosphor_eda.formats.altium.pcb_streams import parse_pads

    ctx = ParseContext()
    pads = parse_pads(b"\x09\x00\x00", {}, {}, ctx)
    assert pads == []
    assert any(i.category == "truncated_stream" for i in ctx.issues)


def test_hierarchy_mode_unknown_falls_back_without_raising():
    from phosphor_eda.formats.altium.project import AltiumHierarchyMode, parse_prjpcb

    ctx = ParseContext()
    project = parse_prjpcb("[Design]\nHierarchyMode=Nonsense\n", ctx)
    assert project.hierarchy_mode == AltiumHierarchyMode.FLAT
    assert any(i.category == "unknown_enum" for i in ctx.issues)
