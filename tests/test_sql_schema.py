"""Schema migration guard for the declarative SQL table specs.

The DDL strings and INSERT column lists used to be maintained by hand in two
places (``schema.py`` and ``loader.py``); they are now generated from a single
``TableSpec`` per table. This test pins the generated DDL against a committed
snapshot so a spec edit that would silently change the on-disk schema fails
loudly. The snapshot is the schema as it shipped before the spec refactor,
modulo two intentional changes documented inline.
"""

from __future__ import annotations

import json
from pathlib import Path

from phosphor_eda.query.sql.loader import TABLE_DDL
from phosphor_eda.query.sql.schema import INDEX_DDL, VIEW_DDL

SNAPSHOT = Path(__file__).parent / "fixtures" / "sql_schema_snapshot.json"


def _load_snapshot() -> dict[str, dict[str, str]]:
    return json.loads(SNAPSHOT.read_text())


def test_table_ddl_matches_snapshot() -> None:
    expected = _load_snapshot()["tables"]
    assert expected == TABLE_DDL


def test_index_ddl_matches_snapshot() -> None:
    expected = _load_snapshot()["indexes"]
    assert expected == INDEX_DDL


def test_view_ddl_matches_snapshot() -> None:
    expected = _load_snapshot()["views"]
    assert expected == VIEW_DDL


def test_design_rules_uses_domain_column_name() -> None:
    # Reconciled to the domain field name; the old DDL-only name is gone.
    assert "preferred_value_mm DOUBLE" in TABLE_DDL["design_rules"]
    assert "target_value_mm" not in TABLE_DDL["design_rules"]


def test_board_drops_always_null_mask_clearance_column() -> None:
    # No domain source ever populated it, so the column was dropped.
    assert "pad_to_mask_clearance_mm" not in TABLE_DDL["board"]
