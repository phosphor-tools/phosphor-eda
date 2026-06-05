"""Integration tests for the DuckDB SQL loader and CLI command."""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from phosphor_eda.convert import load_project
from phosphor_eda.sql import load_database

FIXTURES = Path(__file__).parent / "fixtures"
SWD_SWITCH_PCB = FIXTURES / "swd_switch.kicad_pcb"
JETSON_ORIN_PRO = FIXTURES / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pro"
ORANGECRAB_PCB = FIXTURES / "orangecrab.kicad_pcb"


def _count(db: duckdb.DuckDBPyConnection, sql: str) -> int:
    """Execute a COUNT query and return the scalar result."""
    row = db.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# swd_switch fixture (always available, PCB-only)
# ---------------------------------------------------------------------------


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
        side_counts = dict(rows)
        assert side_counts["front"] == 23
        assert side_counts["back"] == 5


class TestPads:
    def test_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads") == 120

    def test_net_names(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads WHERE net_name = 'VCC'") > 0

    def test_have_geometry(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads WHERE geom IS NULL") == 0


class TestSegments:
    def test_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM segments") == 276

    def test_net_resolution(self, db: duckdb.DuckDBPyConnection) -> None:
        """Segments with net_number > 0 should have net_name populated."""
        unresolved = _count(
            db, "SELECT count(*) FROM segments WHERE net_number > 0 AND net_name = ''"
        )
        assert unresolved == 0

    def test_have_geometry(self, db: duckdb.DuckDBPyConnection) -> None:
        null_geom = _count(
            db, "SELECT count(*) FROM segments WHERE geom IS NULL OR centerline IS NULL"
        )
        assert null_geom == 0


class TestVias:
    def test_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM vias") == 49

    def test_have_geometry(self, db: duckdb.DuckDBPyConnection) -> None:
        null_geom = _count(
            db,
            "SELECT count(*) FROM vias WHERE geom IS NULL OR drill_geom IS NULL",
        )
        assert null_geom == 0


class TestPolygons:
    def test_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM polygons") == 6


class TestZones:
    def test_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM zones") == 2


class TestKeepouts:
    def test_swd_switch_has_keepouts_table(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM keepouts") == 0


class TestGraphicTexts:
    def test_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM graphic_texts") == 8


class TestLayers:
    def test_has_copper(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM layers WHERE layer_type = 'copper'") > 0

    def test_position_ordered(self, db: duckdb.DuckDBPyConnection) -> None:
        """Stackup layers should have monotonically increasing positions."""
        rows = db.execute(
            "SELECT position FROM layers WHERE position IS NOT NULL ORDER BY position"
        ).fetchall()
        positions = [r[0] for r in rows]
        assert positions == sorted(positions)
        assert len(positions) > 0


class TestBoard:
    def test_outline_exists(self, db: duckdb.DuckDBPyConnection) -> None:
        row = db.execute("SELECT geom, layer_count FROM board").fetchone()
        assert row is not None
        assert row[0] is not None  # geom
        assert row[1] > 0  # layer_count


class TestViews:
    def test_net_routes(self, db: duckdb.DuckDBPyConnection) -> None:
        rows = db.execute("SELECT * FROM net_routes WHERE trace_length_mm > 0 LIMIT 5").fetchall()
        assert len(rows) > 0

    def test_drill_histogram(self, db: duckdb.DuckDBPyConnection) -> None:
        rows = db.execute("SELECT * FROM drill_histogram").fetchall()
        assert len(rows) > 0
        # Should have both via and pad sources
        sources = {r[2] for r in rows}
        assert "via" in sources
        assert "pad" in sources

    def test_spatial_distance_query(self, db: duckdb.DuckDBPyConnection) -> None:
        """ST_Distance between two footprints returns a plausible value."""
        row = db.execute("""
            SELECT ST_Distance(a.geom, b.geom)
            FROM footprints a, footprints b
            WHERE a.reference != b.reference
            AND a.geom IS NOT NULL AND b.geom IS NOT NULL
            LIMIT 1
        """).fetchone()
        assert row is not None
        distance = float(row[0])
        # Board is small, distances should be in mm range (< 200mm)
        assert 0 <= distance < 200


# ---------------------------------------------------------------------------
# OrangeCrab fixture (PCB-only, includes KiCad keepout zones)
# ---------------------------------------------------------------------------


def test_kicad_keepouts_are_queryable_in_sql() -> None:
    project = load_project(ORANGECRAB_PCB)
    con = load_database(project)
    try:
        assert _count(con, "SELECT count(*) FROM keepouts") > 0
        row = con.execute(
            """
            SELECT layers, layer, tracks, vias, copperpour, geom
            FROM keepouts
            WHERE copperpour = 'not_allowed'
            LIMIT 1
            """
        ).fetchone()
        assert row is not None
        assert "F.Cu" in row[0]
        assert row[1] in row[0]
        assert row[2] == "allowed"
        assert row[3] == "allowed"
        assert row[4] == "not_allowed"
        assert row[5] is not None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Jetson Orin fixture (skipped if unavailable)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jetson_db() -> Iterator[duckdb.DuckDBPyConnection]:
    if not JETSON_ORIN_PRO.exists():
        pytest.skip("Jetson Orin fixture not available")
    project = load_project(JETSON_ORIN_PRO)
    con = load_database(project)
    try:
        yield con
    finally:
        con.close()


class TestJetsonNetClasses:
    def test_count(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        assert _count(jetson_db, "SELECT count(*) FROM net_classes") == 8


class TestJetsonDesignRules:
    def test_count(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        assert _count(jetson_db, "SELECT count(*) FROM design_rules") == 33


class TestJetsonSchematic:
    def test_components_count(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        assert _count(jetson_db, "SELECT count(*) FROM components") == 669

    def test_nets_have_net_class(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        count = _count(jetson_db, "SELECT count(*) FROM nets WHERE net_class IS NOT NULL")
        assert count > 0

    def test_net_summary_view(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        rows = jetson_db.execute("SELECT * FROM net_summary LIMIT 5").fetchall()
        assert len(rows) > 0


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_basic_query(self) -> None:
        from click.testing import CliRunner

        from phosphor_eda.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main, ["sql", str(SWD_SWITCH_PCB), "SELECT count(*) FROM footprints"]
        )
        assert result.exit_code == 0
        assert "28" in result.output

    def test_schema_flag(self) -> None:
        from click.testing import CliRunner

        from phosphor_eda.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sql", str(SWD_SWITCH_PCB), "--schema"])
        assert result.exit_code == 0
        assert "CREATE TABLE footprints" in result.output

    def test_no_query_error(self) -> None:
        from click.testing import CliRunner

        from phosphor_eda.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sql", str(SWD_SWITCH_PCB)])
        assert result.exit_code != 0

    def test_invalid_query(self) -> None:
        from click.testing import CliRunner

        from phosphor_eda.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sql", str(SWD_SWITCH_PCB), "SELECT * FROM nonexistent"])
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    def test_spatial_query(self) -> None:
        from click.testing import CliRunner

        from phosphor_eda.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main, ["sql", str(SWD_SWITCH_PCB), "SELECT ST_Area(geom) FROM footprints LIMIT 1"]
        )
        assert result.exit_code == 0
        # Should contain a numeric value (the area)
        lines = result.output.strip().split("\n")
        # Header + separator + 1 data row + "(1 row)" footer
        assert len(lines) >= 3
