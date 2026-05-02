"""DuckDB spatial query engine for EDA projects."""

from phosphor_eda.sql.loader import load_database
from phosphor_eda.sql.schema import schema_text

__all__ = ["load_database", "schema_text"]
