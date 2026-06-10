"""DuckDB spatial query engine for EDA projects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.sql.schema import schema_text

if TYPE_CHECKING:
    import duckdb

    from phosphor_eda.domain.project import Project

__all__ = ["load_database", "schema_text"]


def load_database(project: Project) -> duckdb.DuckDBPyConnection:
    """Load a PCB into an in-memory DuckDB database."""
    from phosphor_eda.sql.loader import load_database as _load_database

    return _load_database(project)
