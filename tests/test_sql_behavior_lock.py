"""Behavior lock for full-project output through SQL and serialization.

Loads two real projects (Altium pi-mx8 via .PrjPcb, KiCad jetson-orin via
.kicad_pro) into the DuckDB layer and pins a content hash of every table,
plus the schematic text serialization. Any change to parsed values, SQL
columns, or serialization shows up as a hash mismatch naming the table.

Regenerate after an intentional change:

    PHOSPHOR_UPDATE_GOLDENS=1 uv run pytest cli/tests/test_sql_behavior_lock.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from phosphor_eda.query.convert import load_project
from phosphor_eda.query.format import serialize_design
from phosphor_eda.query.sql import load_database
from phosphor_eda.query.sql.loader import TABLE_DDL

if TYPE_CHECKING:
    import duckdb

    from phosphor_eda.domain.project import Project

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "goldens" / "sql_behavior_lock.json"

_UPDATE = os.environ.get("PHOSPHOR_UPDATE_GOLDENS") == "1"

PROJECTS = {
    "pi-mx8": FIXTURES / "altium/pi-mx8/PiMX8MP_r0.3_release.PrjPcb",
    "jetson-orin": FIXTURES / "kicad-jetson-orin/jetson-orin-baseboard.kicad_pro",
}


def _canon_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.hex()
    return repr(value)


def _table_digest(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, object]:
    rows = con.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - table names come from TABLE_DDL
    lines = sorted("|".join(_canon_value(v) for v in row) for row in rows)
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
        "serialize_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
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
