"""Integration tests for the typed DuckDB SQL loader."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pytest

from phosphor_eda.convert import load_project
from phosphor_eda.sql import load_database

FIXTURES = Path(__file__).parent / "fixtures"
SWD_SWITCH_PCB = FIXTURES / "swd_switch.kicad_pcb"
ORANGECRAB_PCB = FIXTURES / "orangecrab.kicad_pcb"
PI_MX8_PCB = FIXTURES / "altium/pi-mx8/PCB/PiMX8MP_r0.3.PcbDoc"

if TYPE_CHECKING:
    from collections.abc import Iterator


def _count(db: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = db.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture(scope="module")
def db() -> Iterator[duckdb.DuckDBPyConnection]:
    project = load_project(SWD_SWITCH_PCB)
    con = load_database(project)
    try:
        yield con
    finally:
        con.close()


class TestFootprints:
    def test_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM footprints") == 28

    def test_have_geometry(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM footprints WHERE geom IS NOT NULL") > 0

    def test_sides(self, db: duckdb.DuckDBPyConnection) -> None:
        rows = db.execute(
            "SELECT side, count(*) FROM footprints GROUP BY side ORDER BY side"
        ).fetchall()
        assert dict(rows) == {"back": 5, "front": 23}


class TestTypedTables:
    def test_geometry_table_is_removed(self, db: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(duckdb.CatalogException):
            db.execute("SELECT * FROM geometry").fetchall()

    def test_expected_typed_collection_counts(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads") == 120
        assert _count(db, "SELECT count(*) FROM vias") == 49
        assert _count(db, "SELECT count(*) FROM drills") == 71
        assert _count(db, "SELECT count(*) FROM conductors") == 282
        assert _count(db, "SELECT count(*) FROM artwork") == 317
        assert _count(db, "SELECT count(*) FROM board_profile") == 16

    def test_conductors_replace_segments_and_polygons(self, db: duckdb.DuckDBPyConnection) -> None:
        assert (
            _count(db, "SELECT count(*) FROM conductors WHERE kind IN ('trace', 'trace_arc')")
            == 276
        )
        assert _count(db, "SELECT count(*) FROM conductors WHERE kind = 'pour_fill'") == 6
        assert _count(db, "SELECT count(*) FROM conductors WHERE geom IS NULL") == 0

    def test_artwork_contains_text_and_graphics(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM artwork WHERE content_kind = 'text'") > 0
        assert _count(db, "SELECT count(*) FROM artwork WHERE content_kind = 'line'") > 0

    def test_board_profile_has_geometry(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM board_profile WHERE geom IS NOT NULL") == 16


class TestPadsAndVias:
    def test_unconnected_pads_use_nullable_net(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads WHERE net_number IS NULL") > 0
        assert _count(db, "SELECT count(*) FROM pads WHERE net_number = 0") == 0

    def test_pads_join_to_drills(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads p JOIN drills d ON p.drill_id = d.id") > 0

    def test_vias_join_to_drills(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM vias v JOIN drills d ON v.drill_id = d.id") == 49

    def test_pad_geometry_is_loaded(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads WHERE geom IS NULL") == 0


def test_altium_free_pad_mask_apertures_are_queryable_without_freepads_footprint() -> None:
    project = load_project(PI_MX8_PCB)
    con = load_database(project)
    try:
        rows = con.execute(
            """
            SELECT reference, mask_aperture_width, mask_aperture_height, mask_aperture_source
            FROM pads
            WHERE reference IS NULL
              AND pad_number = 'MT'
              AND mask_aperture_source IS NOT NULL
            ORDER BY x, y
            """
        ).fetchall()
    finally:
        con.close()

    assert len(rows) == 4
    for reference, width, height, source in rows:
        assert reference is None
        assert width == pytest.approx(5.8, abs=0.02)
        assert height == pytest.approx(5.85, abs=0.02)
        assert source.startswith("altium:drill-manager-template:")


class TestPoursAndKeepouts:
    def test_pours_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pours") == 2

    def test_swd_switch_has_keepouts_table(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM keepouts") == 0


class TestLayers:
    def test_layers_have_roles_but_no_primary_role(self, db: duckdb.DuckDBPyConnection) -> None:
        row = db.execute(
            """
            SELECT roles, side
            FROM layers
            WHERE name = 'F.CrtYd'
            """
        ).fetchone()

        assert row is not None
        assert set(row[0]) >= {"fabrication", "courtyard", "front"}
        assert row[1] == "front"
        with pytest.raises(duckdb.BinderException):
            db.execute("SELECT primary_role FROM layers").fetchall()

    def test_position_ordered(self, db: duckdb.DuckDBPyConnection) -> None:
        rows = db.execute(
            "SELECT position FROM layers WHERE position IS NOT NULL ORDER BY position"
        ).fetchall()
        positions = [row[0] for row in rows]
        assert positions == sorted(positions)
        assert positions


class TestBoard:
    def test_outline_exists(self, db: duckdb.DuckDBPyConnection) -> None:
        row = db.execute("SELECT geom, layer_count FROM board").fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[1] > 0


class TestViews:
    def test_net_routes_reads_conductors(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM net_routes WHERE trace_length_mm > 0") > 0

    def test_drill_histogram_reads_drills(self, db: duckdb.DuckDBPyConnection) -> None:
        rows = db.execute("SELECT * FROM drill_histogram").fetchall()
        assert rows
        sources = {row[2] for row in rows}
        assert {"via", "pad"}.issubset(sources)

    def test_spatial_distance_query(self, db: duckdb.DuckDBPyConnection) -> None:
        row = db.execute("""
            SELECT ST_Distance(a.geom, b.geom)
            FROM footprints a, footprints b
            WHERE a.reference != b.reference
            AND a.geom IS NOT NULL AND b.geom IS NOT NULL
            LIMIT 1
        """).fetchone()
        assert row is not None
        distance = float(row[0])
        assert 0 <= distance < 200


def test_kicad_keepouts_are_queryable_in_sql() -> None:
    project = load_project(ORANGECRAB_PCB)
    con = load_database(project)
    try:
        assert _count(con, "SELECT count(*) FROM keepouts") > 0
    finally:
        con.close()


def test_altium_typed_tables_are_populated() -> None:
    project = load_project(PI_MX8_PCB)
    con = load_database(project)
    try:
        assert _count(con, "SELECT count(*) FROM drills") > 0
        assert _count(con, "SELECT count(*) FROM conductors") > 0
        assert _count(con, "SELECT count(*) FROM artwork") > 0
        assert _count(con, "SELECT count(*) FROM board_profile") > 0
    finally:
        con.close()
