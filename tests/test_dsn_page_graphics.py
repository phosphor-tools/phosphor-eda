"""Wave 5 locks: page-tail graphics, notes, net properties, DsnStream, T0x34.

Covers findings F1-F5 and B3: the page continues past bus entries into a
GraphicInst section (CommentText notes + Line/Box/Ellipse shapes + Bitmap/OLE
image envelopes), wires carry net-property evidence, the DsnStream holds
design GUID/version, T0x34 records per-net display state, and every top-level
OLE stream is inventoried.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import olefile
import pytest

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.parser import parse_dsn, parse_symbol_types
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design
from phosphor_eda.query.project_loader import load_project
from phosphor_eda.query.sql import load_database

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PICO_DSN = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PICOW_DSN = FIXTURES / "dsn/raspberry-pi-pico-w/RPI-PICOW-R2.DSN"
CMIO_DSN = FIXTURES / "dsn/raspberry-pi-cmio/RPI-CMIO-V3_0-PUBLIC.DSN"
LAUNCHXL_DSN = (
    FIXTURES
    / "orcad/cp-smartgarden-launchxl-cc1310/Document/Hardware/mcu/swrc319/Cadence"
    / "LAUNCHXL-CC1310.DSN"
)
BREAKOUT_DSN = (
    FIXTURES
    / "orcad/opencellular-breakout/orcad/OpenCellular/electronics/breakout/schematic/dsn"
    / "OC_CONNECT_1_BRKOUT_BRD.DSN"
)
POWER_UNIT_DSN = (
    FIXTURES
    / "orcad/opencellular-power-unit/orcad/OpenCellular/electronics/power-unit/Rev-A/schematics"
    / "POWER_SOURCE_BOARD_20180717.DSN"
)
SYNC_DSN = (
    FIXTURES
    / "orcad/opencellular-sync/orcad/OpenCellular/electronics/sync/schematics/dsn"
    / "FB_CONNECT1_SYNC_LIFE-3_V1P1.DSN"
)
STEPPER_DSN = FIXTURES / "orcad/rohm-stepper-driver-ctrl/Design Files for Rev 1.0/STEPPER.DSN"
BREAKOUT_OPJ = (
    FIXTURES
    / "orcad/opencellular-breakout/orcad/OpenCellular/electronics/breakout/schematic/dsn"
    / "OC_CONNECT_1_BRKOUT_BRD.opj"
)

# The eight committed OrCAD/DSN fixtures the G1 census was taken over.
COMMITTED_DSN_FILES = (
    CMIO_DSN,
    PICO_DSN,
    PICOW_DSN,
    LAUNCHXL_DSN,
    BREAKOUT_DSN,
    POWER_UNIT_DSN,
    SYNC_DSN,
    STEPPER_DSN,
)


# --- F1/F2: graphic census + residue-0 lock -------------------------------


def test_graphic_census_matches_findings_g1() -> None:
    """Corpus-wide census over the eight committed fixtures (finding G1)."""
    notes = 0
    shapes: Counter[str] = Counter()
    images: Counter[str] = Counter()
    for dsn in COMMITTED_DSN_FILES:
        design = parse_dsn(dsn, ParseContext())
        for page in design.pages:
            notes += len(page.comment_texts)
            for shape in page.page_graphics:
                shapes[shape.kind] += 1
            for image in page.page_images:
                images[image.kind] += 1

    assert notes == 765
    assert shapes == Counter({"line": 546, "box": 154, "ellipse": 82})
    assert images == Counter({"bitmap": 3, "ole_embed": 1})


def test_page_tail_parses_to_residue_zero_without_diagnostics() -> None:
    """Every committed page parses to the exact stream end with no page-tail warnings."""
    page_tail_categories = {
        "dsn_page_tail",
        "dsn_bus_entry",
        "dsn_graphic_section",
        "dsn_graphic_inst",
        "dsn_unknown_structure",
        "dsn_t0x34",
        "dsn_t0x35",
        "dsn_page_tail_structures",
    }
    for dsn in COMMITTED_DSN_FILES:
        ctx = ParseContext()
        _ = parse_dsn(dsn, ctx)
        offenders = [issue for issue in ctx.issues if issue.category in page_tail_categories]
        assert offenders == [], f"{dsn.name}: {[i.message for i in offenders]}"


def test_comment_text_notes_are_decoded_verbatim() -> None:
    """CommentText decodes coordinates, font, and text byte-for-byte."""
    pico = parse_dsn(PICO_DSN, ParseContext())
    disable_flash = [
        note
        for page in pico.pages
        for note in page.comment_texts
        if "Disable Flash boot" in note.text
    ]
    assert len(disable_flash) == 1
    note = disable_flash[0]
    assert note.text == "Disable Flash boot\r\n(forces USB boot)"
    assert note.loc_x == 190
    assert note.loc_y == 550
    assert note.font_idx == 12

    stepper = parse_dsn(STEPPER_DSN, ParseContext())
    dnp = [
        note.text
        for page in stepper.pages
        for note in page.comment_texts
        if "Populate R5" in note.text
    ]
    # Verbatim from the bytes: bare CR line breaks, not CRLF.
    assert dnp == ["Do Not \rPopulate R5 \r- Leave as \rJumper"]


def test_notes_surface_as_public_page_annotations() -> None:
    """CommentText notes reach the public model as page annotations (SQL channel)."""
    design = dsn_to_design(parse_dsn(PICO_DSN), name="Pico")
    annotations = [text for page in design.pages for text in page.annotations]
    assert "Disable Flash boot\r\n(forces USB boot)" in annotations
    # Every non-empty CommentText becomes one annotation.
    raw_notes = sum(
        1 for page in parse_dsn(PICO_DSN).pages for note in page.comment_texts if note.text
    )
    assert len(annotations) == raw_notes


def test_shape_records_carry_coordinates_and_style() -> None:
    """Line/Box/Ellipse shapes retain typed coordinates for rendering fidelity."""
    design = parse_dsn(POWER_UNIT_DSN, ParseContext())
    shapes = [shape for page in design.pages for shape in page.page_graphics]
    assert shapes, "power-unit has page-tail shapes"
    assert {shape.kind for shape in shapes} <= {"line", "box", "ellipse"}
    # A shape's corner coordinates are populated (not all-zero envelopes).
    assert any((shape.x1, shape.y1, shape.x2, shape.y2) != (0, 0, 0, 0) for shape in shapes)


def test_ole_embed_envelope_size_is_kept_without_payload() -> None:
    """The 8 MB OLE block-diagram embed is kept as size + kind, not decoded bytes."""
    design = parse_dsn(POWER_UNIT_DSN, ParseContext())
    ole_images = [
        image for page in design.pages for image in page.page_images if image.kind == "ole_embed"
    ]
    assert len(ole_images) == 1
    assert ole_images[0].payload_size > 1_000_000


# --- F3: wire net properties ----------------------------------------------


def test_wire_net_property_census_matches_findings_g2() -> None:
    """CDS_PHYS_NET_NAME / DIFFERENTIAL_PAIR / VOLTAGE counts across the corpus."""
    counts: Counter[str] = Counter()
    for dsn in COMMITTED_DSN_FILES:
        design = parse_dsn(dsn, ParseContext())
        for page in design.pages:
            for wire in page.wires:
                for name, _value in wire.net_properties:
                    counts[name] += 1
    assert counts == Counter({"CDS_PHYS_NET_NAME": 33, "DIFFERENTIAL_PAIR": 4, "VOLTAGE": 11})


def test_net_properties_surface_as_net_metadata_with_provenance() -> None:
    """Net properties reach public net metadata as JSON evidence with provenance.

    Evidence attaches to nets that survive resolution; the raw wire layer keeps
    the full corpus census (locked separately).
    """
    design = dsn_to_design(parse_dsn(POWER_UNIT_DSN), name="power-unit")
    records: list[dict[str, object]] = []
    for net in design.nets:
        raw = net.metadata.get("dsn_net_properties")
        if raw:
            records.extend(json.loads(raw))
        count = net.metadata.get("dsn_net_property_count")
        if count is not None:
            assert int(count) == len(json.loads(net.metadata["dsn_net_properties"]))
    names = Counter(record["name"] for record in records)
    # All three raw property kinds surface publicly on power-unit nets.
    assert set(names) == {"CDS_PHYS_NET_NAME", "DIFFERENTIAL_PAIR", "VOLTAGE"}
    diff = [record for record in records if record["name"] == "DIFFERENTIAL_PAIR"]
    assert len(diff) == 4
    assert all(record["value"] == "DP1" for record in diff)
    # Provenance fields are present on every record.
    assert all({"page", "wire_db_id", "net_id"} <= record.keys() for record in records)


def test_cds_phys_net_name_value_pairs_are_exact() -> None:
    """Exact CDS_PHYS_NET_NAME evidence from the breakout USB3 differential nets."""
    design = parse_dsn(BREAKOUT_DSN, ParseContext())
    phys = {
        value
        for page in design.pages
        for wire in page.wires
        for name, value in wire.net_properties
        if name == "CDS_PHYS_NET_NAME"
    }
    assert {"USB3_RX0_P", "USB3_RX0_N"} <= phys


# --- F4: DsnStream ---------------------------------------------------------


def test_dsn_stream_carries_guid_and_time_format() -> None:
    design = parse_dsn(LAUNCHXL_DSN, ParseContext())
    stream = design.dsn_stream
    assert stream is not None
    assert stream.library_guid == "{F688673A-FCF2-4316-A40D-26970EB30AF0}"
    assert stream.time_format_index == "0"
    assert stream.version_info == {}


def test_dsn_stream_decodes_17_4_era_version_json() -> None:
    design = parse_dsn(PICOW_DSN, ParseContext())
    stream = design.dsn_stream
    assert stream is not None
    assert stream.version_info["InstalledVersionBase"] == "17.4-2019"
    assert stream.version_info["License"] == "orcad_ee_expert_suite"


def test_design_stream_metadata_is_public() -> None:
    design = dsn_to_design(parse_dsn(PICOW_DSN), name="PicoW")
    assert design.metadata["dsn_design_guid"] == "{C34EDB0C-9873-4828-B59F-76B86B3136ED}"
    assert design.metadata["dsn_installed_version_base"] == "17.4-2019"
    assert design.metadata["dsn_license"] == "orcad_ee_expert_suite"


# --- F5: T0x34 + Symbols/$Types$ ------------------------------------------


def test_t0x34_ids_live_in_the_wire_runtime_net_space() -> None:
    """Every T0x34 record id is a runtime page-net id (finding G4)."""
    design = parse_dsn(STEPPER_DSN, ParseContext())
    page = next(page for page in design.pages if page.name == "Top")
    t34_ids = {record.net_id for record in page.net_display_props}
    wire_net_ids = {wire.wire_id for wire in page.wires}
    assert t34_ids
    assert t34_ids <= wire_net_ids


def test_t0x34_total_census_matches_findings() -> None:
    total = 0
    for dsn in COMMITTED_DSN_FILES:
        design = parse_dsn(dsn, ParseContext())
        total += sum(len(page.net_display_props) for page in design.pages)
    assert total == 1925


def test_symbol_types_decode_name_to_structure_type() -> None:
    stepper = parse_dsn(STEPPER_DSN, ParseContext())
    types = {entry.name: entry.type_id for entry in stepper.symbol_types}
    assert types == {"ERC": 0x4B, "ERC_PHYSICAL": 0x4B}

    pico = parse_dsn(PICO_DSN, ParseContext())
    # Pico's Symbols/$Types$ stream is empty.
    assert pico.symbol_types == []


def test_symbol_types_parser_handles_empty_stream() -> None:
    assert parse_symbol_types(b"", ParseContext()) == []


# --- B3: unknown-stream inventory -----------------------------------------


def test_committed_fixtures_have_no_unknown_streams() -> None:
    """After F4/F5, the committed fixtures inventory no unknown top-level streams."""
    for dsn in COMMITTED_DSN_FILES:
        ctx = ParseContext()
        design = parse_dsn(dsn, ctx)
        assert design.stream_inventory.unknown_streams == [], dsn.name
        assert not any(issue.category == "dsn_unknown_stream" for issue in ctx.issues)


def test_known_unparsed_inventory_classifies_skipped_streams() -> None:
    """Intentionally-skipped catalogs land in known_unparsed, not unknown."""
    design = parse_dsn(STEPPER_DSN, ParseContext())
    known = {ref.path for ref in design.stream_inventory.known_unparsed_streams}
    assert {"AdminData", "HSObjects", "Graphics/$Types$"} <= known
    assert any(path.endswith(" Directory") for path in known)
    # DsnStream and Symbols/$Types$ are now parsed, not inventoried.
    assert "DsnStream" not in known
    assert "Symbols/$Types$" not in known


def test_parse_symbol_types_matches_raw_stream_bytes() -> None:
    """Direct decode of the STEPPER Symbols/$Types$ stream bytes."""
    with olefile.OleFileIO(str(STEPPER_DSN)) as ole:
        data = ole.openstream("Symbols/$Types$").read()
    types = parse_symbol_types(data, ParseContext())
    assert [(entry.name, entry.type_id) for entry in types] == [
        ("ERC", 0x4B),
        ("ERC_PHYSICAL", 0x4B),
    ]


# --- End-to-end: DSN notes + net properties reach SQL ---------------------


def test_dsn_notes_and_net_properties_reach_sql() -> None:
    """The full project->SQL path surfaces notes and net-property evidence."""
    project = load_project(BREAKOUT_OPJ)
    db = load_database(project)
    try:
        annotation_rows = db.execute("SELECT text FROM page_annotations").fetchall()
        assert len(annotation_rows) == 31
        assert ("BOARD TO BOARD CONNECTOR",) in annotation_rows

        net_property_rows = db.execute(
            "SELECT value FROM net_metadata WHERE key = 'dsn_net_properties'"
        ).fetchall()
        assert len(net_property_rows) == 6
        payloads = [record for (raw,) in net_property_rows for record in json.loads(raw)]
        assert {record["name"] for record in payloads} == {"CDS_PHYS_NET_NAME"}
        assert "USB3_RX0_N" in {record["value"] for record in payloads}
    finally:
        db.close()


@pytest.mark.parametrize("dsn", COMMITTED_DSN_FILES, ids=lambda p: p.name)
def test_all_committed_pages_parse_cleanly(dsn: Path) -> None:
    """No parse errors surface on any committed fixture (regression guard)."""
    ctx = ParseContext()
    _ = parse_dsn(dsn, ctx)
    fatal = [issue for issue in ctx.issues if issue.category.startswith("dsn_graphic")]
    assert fatal == []
