from __future__ import annotations

from allegro_oracle_helpers import (
    CP_SMARTGARDEN_PLACEMENT_LOG,
    ROHM_DRILL,
    ROHM_REPORT,
    ROHM_VIEW_ENV,
)

from phosphor_eda.formats.allegro.oracle import (
    ALLEGRO_ORACLE_COVERAGE,
    OracleEvidenceKind,
    parse_excellon_drill,
    parse_gerber_report,
    parse_placement_log,
    parse_view_env_manifest,
)


def test_excellon_drill_oracle_matches_reported_tool_counts() -> None:
    """Proves NC drill tool diameters and coordinates agree with Allegro's report.

    Cannot prove copper connectivity, padstack intent, layer artwork, or constraints.
    """
    drill = parse_excellon_drill(ROHM_DRILL)
    report = parse_gerber_report(ROHM_REPORT)

    assert drill.units == "inch"
    assert drill.zero_suppression == "trailing"
    assert [tool.code for tool in drill.tools] == [f"{index:02d}" for index in range(1, 12)]
    assert sum(drill.hit_counts_by_tool.values()) == 2168
    assert drill.hit_counts_by_tool["01"] == 912
    assert drill.hit_counts_by_tool["11"] == 4
    assert report.drill_aperture_counts_by_tool == drill.hit_counts_by_tool
    assert report.drill_aperture_pad_counts_by_tool["10"] == 0


def test_gerber_manifest_oracle_locks_layer_files_and_report_counts() -> None:
    """Proves exported Gerber/drill layer manifest and artwork count report.

    Cannot prove native source object ownership, dynamic pours, or editable constraints.
    """
    manifest = parse_view_env_manifest(ROHM_VIEW_ENV)
    report = parse_gerber_report(ROHM_REPORT)

    assert tuple(layer.filename for layer in manifest.layers) == (
        "L1.GBR",
        "L2.GBR",
        "L3.GBR",
        "L4.GBR",
        "L31.GBR",
        "L32.GBR",
        "L41.GBR",
        "L60.GBR",
        "DRILL.DRL",
    )
    assert report.layers_by_filename["L1.GBR"].trace_count == 189147
    assert report.layers_by_filename["L1.GBR"].pad_count == 3673
    assert report.layers_by_filename["DRILL.DRL"].pad_count == 2152


def test_placement_log_oracle_locks_committed_component_count_evidence() -> None:
    """Proves Allegro reported the number of placed components exported to text.

    Cannot prove component coordinates or rotations because no stable placement
    coordinate file is committed with this fixture.
    """
    placement = parse_placement_log(CP_SMARTGARDEN_PLACEMENT_LOG)

    assert ALLEGRO_ORACLE_COVERAGE[OracleEvidenceKind.PLACEMENT].cannot_prove.issuperset(
        {"placement", "geometry"}
    )
    assert placement.board_file == "LAUNCHXL-CC1310.brd"
    assert placement.output_file == "place_txt.txt"
    assert placement.placed_component_count == 143
