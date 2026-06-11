"""Tests for Altium ``.Annotation`` physical-designator parsing."""

from pathlib import Path

from phosphor_eda.formats.altium.annotation import (
    AnnotationDesignator,
    load_annotation_designators,
    parse_annotation_designators,
)
from phosphor_eda.formats.common.diagnostics import ParseContext

_ANNOTATION = """[DesignatorManager]
LogicalDesignator0=U1
DocumentName0=Fiber Pulse Transmitter.SchDoc
ChannelName0=FIB_PULSE1
UniqueID0=\\FEHIXTLT\\VIIQXJDH
PhysicalDesignator0=U1.1
LogicalDesignator1=U1
DocumentName1=Fiber Pulse Transmitter.SchDoc
ChannelName1=FIB_PULSE3
UniqueID1=\\TPZRYUFR\\VIIQXJDH
PhysicalDesignator1=U1.3
[SheetNumberManager]
SheetName0=Title
"""


def test_parse_maps_unique_id_path_to_physical_designator():
    designators = parse_annotation_designators(_ANNOTATION)
    assert designators == {
        "\\FEHIXTLT\\VIIQXJDH": AnnotationDesignator(
            physical_designator="U1.1",
            logical_designator="U1",
            channel_name="FIB_PULSE1",
        ),
        "\\TPZRYUFR\\VIIQXJDH": AnnotationDesignator(
            physical_designator="U1.3",
            logical_designator="U1",
            channel_name="FIB_PULSE3",
        ),
    }


def test_parse_captures_logical_designator_and_channel_name():
    designators = parse_annotation_designators(_ANNOTATION)
    entry = designators["\\FEHIXTLT\\VIIQXJDH"]
    assert entry.physical_designator == "U1.1"
    assert entry.logical_designator == "U1"
    assert entry.channel_name == "FIB_PULSE1"


def test_parse_ignores_entries_missing_a_physical_designator():
    content = (
        "[DesignatorManager]\n"
        "UniqueID0=\\AAA\\BBB\n"  # no PhysicalDesignator0
        "UniqueID1=\\CCC\\DDD\n"
        "PhysicalDesignator1=R5.2\n"
    )
    parsed = parse_annotation_designators(content)
    assert set(parsed) == {"\\CCC\\DDD"}
    assert parsed["\\CCC\\DDD"].physical_designator == "R5.2"


def test_parse_without_designator_manager_section_is_empty():
    assert parse_annotation_designators("[SheetNumberManager]\nSheetName0=Title\n") == {}


def test_parse_malformed_content_warns_and_returns_empty():
    ctx = ParseContext()
    # A stray line before any section header is not valid INI.
    result = parse_annotation_designators("not a valid ini\n[DesignatorManager]\n", ctx=ctx)
    assert result == {}
    assert len(ctx.issues) == 1
    assert ctx.issues[0].category == "annotation_parse_error"


def test_load_returns_empty_when_no_annotation_file(tmp_path: Path):
    prjpcb = tmp_path / "Board.PrjPcb"
    prjpcb.write_text("[Design]\nHierarchyMode=2\n")
    assert load_annotation_designators(prjpcb) == {}


def test_load_reads_sibling_annotation_file(tmp_path: Path):
    prjpcb = tmp_path / "Board.PrjPcb"
    prjpcb.write_text("[Design]\nHierarchyMode=2\n")
    (tmp_path / "Board.Annotation").write_text(_ANNOTATION)

    designators = load_annotation_designators(prjpcb)
    assert designators["\\FEHIXTLT\\VIIQXJDH"].physical_designator == "U1.1"
    assert designators["\\TPZRYUFR\\VIIQXJDH"].physical_designator == "U1.3"
