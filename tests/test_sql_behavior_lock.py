"""Behavior lock for full-project output through SQL and serialization.

Loads two real projects (Altium pi-mx8 via .PrjPcb, KiCad jetson-orin via
.kicad_pro) into the DuckDB layer and pins a content hash of every table,
plus the schematic text serialization. Any change to parsed values, SQL
columns, or serialization shows up as a hash mismatch naming the table.

Regenerate after an intentional change:

    PHOSPHOR_RUN_BEHAVIOR_LOCKS=1 PHOSPHOR_UPDATE_GOLDENS=1 \
        uv run pytest cli/tests/test_sql_behavior_lock.py

The tests are slow and skipped by default. Run them explicitly when changing
parsers, SQL schema/loading, serialization, or any behavior that can alter
full-project output:

    uv run pytest cli/tests/test_sql_behavior_lock.py --run-behavior-locks
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from phosphor_eda.query.format import serialize_design
from phosphor_eda.query.project_loader import load_project
from phosphor_eda.query.sql import load_database
from phosphor_eda.query.sql.loader import TABLE_DDL

if TYPE_CHECKING:
    import duckdb

    from phosphor_eda.domain.project import Project

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "goldens" / "sql_behavior_lock.json"

_UPDATE = os.environ.get("PHOSPHOR_UPDATE_GOLDENS") == "1"
_COLUMN_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<type>[A-Z]+)\b")
_GEOMETRY_DECIMAL_PLACES = 6

PROJECTS = {
    "pi-mx8": FIXTURES / "altium/pi-mx8/PiMX8MP_r0.3_release.PrjPcb",
    "jetson-orin": FIXTURES / "kicad-jetson-orin/jetson-orin-baseboard.kicad_pro",
}

pytestmark = pytest.mark.behavior_lock


def _canon_value(table: str, column: str, value: object, *, is_geometry: bool) -> str:
    if table == "boards" and column == "source_path" and isinstance(value, str):
        value = _canon_fixture_path(value)
    if is_geometry and isinstance(value, str):
        return _canon_geojson(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, str):
        return repr(value.replace(FIXTURES.as_posix(), "<fixtures>"))
    return repr(value)


def _canon_geojson(value: str) -> str:
    return json.dumps(
        _canon_geojson_value(json.loads(value)),
        separators=(",", ":"),
        sort_keys=True,
    )


def _canon_geojson_value(value: object) -> object:
    if isinstance(value, float):
        rounded = round(value, _GEOMETRY_DECIMAL_PLACES)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, list):
        return [_canon_geojson_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canon_geojson_value(item) for key, item in value.items()}
    return value


def _canon_fixture_path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    try:
        return path.relative_to(FIXTURES.resolve()).as_posix()
    except ValueError:
        return value


def _canon_serialized(value: str) -> str:
    return value.replace(FIXTURES.as_posix(), "<fixtures>")


def _table_select_expressions(table: str) -> list[tuple[str, str, bool]]:
    if table not in TABLE_DDL:
        allowed = ", ".join(sorted(TABLE_DDL))
        raise ValueError(f"table {table!r} is not in the SQL behavior-lock allowlist: {allowed}")

    expressions: list[tuple[str, str, bool]] = []
    for line in TABLE_DDL[table].splitlines():
        if "CREATE TABLE" in line:
            continue
        match = _COLUMN_RE.match(line)
        if match is None:
            continue
        name = match["name"]
        if match["type"] == "GEOMETRY":
            # DuckDB Spatial's WKB bytes can vary across platforms for the same
            # loaded geometry. Normalized GeoJSON keeps full coordinate content
            # while avoiding platform-specific binary serialization.
            expressions.append(
                (
                    name,
                    f"CASE WHEN {name} IS NULL THEN NULL ELSE "
                    f"ST_AsGeoJSON(ST_Normalize({name}))::VARCHAR END",
                    True,
                )
            )
        else:
            expressions.append((name, name, False))
    return expressions


def _table_digest(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, object]:
    select_expressions = _table_select_expressions(table)
    expressions = ", ".join(expression for _, expression, _ in select_expressions)
    columns = [(column, is_geometry) for column, _, is_geometry in select_expressions]
    quoted_table = table.replace('"', '""')
    rows = con.execute(f'SELECT {expressions} FROM "{quoted_table}"').fetchall()
    lines = sorted(
        "|".join(
            _canon_value(table, column, value, is_geometry=is_geometry)
            for (column, is_geometry), value in zip(columns, row, strict=True)
        )
        for row in rows
    )
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {"rows": len(rows), "sha256": digest}


def _project_snapshot(project: Project) -> dict[str, object]:
    con = load_database(project)
    try:
        tables = {table: _table_digest(con, table) for table in sorted(TABLE_DDL)}
    finally:
        con.close()
    serialized = serialize_design(project.schematic) if project.schematic else ""
    return {
        "serialize_sha256": hashlib.sha256(_canon_serialized(serialized).encode()).hexdigest(),
        "tables": tables,
    }


@pytest.mark.parametrize("name", sorted(PROJECTS))
def test_project_output_locked(name: str) -> None:
    snapshot = _project_snapshot(load_project(PROJECTS[name]))

    if _UPDATE:
        existing = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        existing[name] = snapshot
        GOLDEN.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"updated golden for {name}")

    assert GOLDEN.exists(), f"missing golden {GOLDEN}; run PHOSPHOR_UPDATE_GOLDENS=1 to create it"
    expected = json.loads(GOLDEN.read_text())[name]

    expected_tables = expected["tables"]
    actual_tables = snapshot["tables"]
    assert isinstance(actual_tables, dict)
    for table in sorted(set(expected_tables) | set(actual_tables)):
        assert table in actual_tables, f"table {table} disappeared from the schema"
        assert table in expected_tables, (
            f"new table {table} not in golden; if intended, regenerate with "
            "PHOSPHOR_UPDATE_GOLDENS=1"
        )
        assert actual_tables[table] == expected_tables[table], (
            f"table {table} content diverged; if intended, regenerate with "
            "PHOSPHOR_UPDATE_GOLDENS=1"
        )
    assert snapshot["serialize_sha256"] == expected["serialize_sha256"], (
        "schematic serialization diverged; if intended, regenerate with PHOSPHOR_UPDATE_GOLDENS=1"
    )
