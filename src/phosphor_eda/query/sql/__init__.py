"""DuckDB spatial query engine for EDA projects.

``load_database`` and ``schema_text`` defer importing the loader (and through
it, duckdb and shapely) until first use, so importing this package stays cheap
for callers that never run a SQL query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

    from phosphor_eda.domain.project import Project

__all__ = ["load_database", "schema_text"]


def load_database(project: Project) -> duckdb.DuckDBPyConnection:
    """Load a Project into an in-memory DuckDB database."""
    # Lazy import: duckdb is heavy and only needed when a query actually runs.
    from phosphor_eda.query.sql.loader import load_database as _load_database

    return _load_database(project)


def schema_text() -> str:
    """Return the formatted DDL for all tables, indexes, and views."""
    # Lazy import: the loader pulls in duckdb/shapely at module load.
    from phosphor_eda.query.sql.loader import schema_text as _schema_text

    return _schema_text()
