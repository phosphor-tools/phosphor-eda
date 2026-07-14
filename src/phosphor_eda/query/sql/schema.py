"""Declarative table specs for the DuckDB SQL schema.

Each table is described once by a :class:`TableSpec`: an ordered list of
:class:`Column` definitions carrying the SQL type, optional constraint, and an
extractor that pulls the column value out of a per-row source object. Both the
``CREATE TABLE`` DDL and the named-column ``INSERT`` statements are generated
from these specs, so the on-disk schema and the loader can never drift out of
column order. Indexes, views, and ``schema_text()`` are still hand-written.

Row sources are plain domain objects for most tables; tables that need
cross-row context or precomputed geometry receive a small ``*Row`` dataclass
the loader builds and fills (see :mod:`phosphor_eda.query.sql.loader`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    import duckdb
import pandas as pd

GEOMETRY = "GEOMETRY"


@dataclass(frozen=True)
class Column[T]:
    """One column: its DDL fragment and how to extract its value from a row."""

    name: str
    sql_type: str
    extractor: Callable[[T], object]
    constraint: str = ""

    @property
    def is_geometry(self) -> bool:
        return self.sql_type == GEOMETRY

    def ddl(self) -> str:
        suffix = f" {self.constraint}" if self.constraint else ""
        return f"{self.name} {self.sql_type}{suffix}"


@dataclass(frozen=True)
class TableSpec[T]:
    """An ordered column spec generating DDL and named-column inserts."""

    name: str
    columns: tuple[Column[T], ...]

    def create_ddl(self) -> str:
        body = ",\n".join(f"            {column.ddl()}" for column in self.columns)
        return f"\n        CREATE TABLE {self.name} (\n{body}\n        )\n    "

    def values(self, source: T) -> list[object]:
        return [column.extractor(source) for column in self.columns]

    def bulk_insert(self, con: duckdb.DuckDBPyConnection, sources: Iterable[T]) -> None:
        self.bulk_insert_values(con, (self.values(source) for source in sources))

    def bulk_insert_values(
        self,
        con: duckdb.DuckDBPyConnection,
        rows: Iterable[Sequence[object]],
    ) -> None:
        data = [list(row) for row in rows]
        if not data:
            return

        stage_table = f"_{self.name}_stage"
        frame = pd.DataFrame(data=data, columns=self._stage_column_names())
        _ = con.execute(self._stage_ddl(stage_table))
        try:
            con.append(stage_table, frame, by_name=True)
            _ = con.execute(self._bulk_insert_sql(stage_table))
        finally:
            _ = con.execute(f"DROP TABLE IF EXISTS {stage_table}")

    def _stage_column_names(self) -> list[str]:
        return [
            f"{column.name}_wkb" if column.is_geometry else column.name for column in self.columns
        ]

    def _stage_ddl(self, stage_table: str) -> str:
        body = ",\n".join(
            f"            {stage_name} {self._stage_sql_type(column)}"
            for column, stage_name in zip(self.columns, self._stage_column_names(), strict=True)
        )
        return f"\n        CREATE TEMP TABLE {stage_table} (\n{body}\n        )\n    "

    def _stage_sql_type(self, column: Column[T]) -> str:
        return "BLOB" if column.is_geometry else column.sql_type

    def _bulk_insert_sql(self, stage_table: str) -> str:
        names = ", ".join(column.name for column in self.columns)
        expressions = ", ".join(
            f"ST_GeomFromWKB({stage_name})" if column.is_geometry else stage_name
            for column, stage_name in zip(self.columns, self._stage_column_names(), strict=True)
        )
        return f"INSERT INTO {self.name} ({names}) SELECT {expressions} FROM {stage_table}"


def col[T](
    name: str,
    sql_type: str,
    extractor: Callable[[T], object],
    *,
    constraint: str = "",
) -> Column[T]:
    """Concise constructor for a :class:`Column`."""
    return Column(name=name, sql_type=sql_type, extractor=extractor, constraint=constraint)


INDEX_DDL: dict[str, str] = {
    "idx_components_reference": """
        CREATE INDEX idx_components_reference ON components(reference)
    """,
    "idx_component_occurrences_component_id": """
        CREATE INDEX idx_component_occurrences_component_id ON component_occurrences(component_id)
    """,
    "idx_component_occurrences_page_id": """
        CREATE INDEX idx_component_occurrences_page_id ON component_occurrences(page_id)
    """,
    "idx_component_pages_component_id": """
        CREATE INDEX idx_component_pages_component_id ON component_pages(component_id)
    """,
    "idx_component_pages_page_id": """
        CREATE INDEX idx_component_pages_page_id ON component_pages(page_id)
    """,
    "idx_component_metadata_component_id": """
        CREATE INDEX idx_component_metadata_component_id ON component_metadata(component_id)
    """,
    "idx_component_parameters_component_id": """
        CREATE INDEX idx_component_parameters_component_id ON component_parameters(component_id)
    """,
    "idx_component_parameters_component_id_ord": """
        CREATE INDEX idx_component_parameters_component_id_ord
        ON component_parameters(component_id, ord)
    """,
    "idx_component_footprints_component_id": """
        CREATE INDEX idx_component_footprints_component_id ON component_footprints(component_id)
    """,
    "idx_component_footprints_component_id_ord": """
        CREATE INDEX idx_component_footprints_component_id_ord
        ON component_footprints(component_id, ord)
    """,
    "idx_component_part_numbers_component_id": """
        CREATE INDEX idx_component_part_numbers_component_id ON component_part_numbers(component_id)
    """,
    "idx_component_part_numbers_component_id_ord": """
        CREATE INDEX idx_component_part_numbers_component_id_ord
        ON component_part_numbers(component_id, ord)
    """,
    "idx_component_occurrence_metadata_occurrence_id": """
        CREATE INDEX idx_component_occurrence_metadata_occurrence_id
        ON component_occurrence_metadata(occurrence_id)
    """,
    "idx_component_occurrence_metadata_component_id": """
        CREATE INDEX idx_component_occurrence_metadata_component_id
        ON component_occurrence_metadata(component_id)
    """,
    "idx_pins_component_id": """
        CREATE INDEX idx_pins_component_id ON pins(component_id)
    """,
    "idx_pins_net_id": """
        CREATE INDEX idx_pins_net_id ON pins(net_id)
    """,
    "idx_pin_occurrences_pin_id": """
        CREATE INDEX idx_pin_occurrences_pin_id ON pin_occurrences(pin_id)
    """,
    "idx_pin_occurrences_page_id": """
        CREATE INDEX idx_pin_occurrences_page_id ON pin_occurrences(page_id)
    """,
    "idx_pin_occurrence_metadata_occurrence_id": """
        CREATE INDEX idx_pin_occurrence_metadata_occurrence_id
        ON pin_occurrence_metadata(occurrence_id)
    """,
    "idx_pin_occurrence_metadata_pin_id": """
        CREATE INDEX idx_pin_occurrence_metadata_pin_id ON pin_occurrence_metadata(pin_id)
    """,
    "idx_nets_name": """
        CREATE INDEX idx_nets_name ON nets(name)
    """,
    "idx_net_pages_net_id": """
        CREATE INDEX idx_net_pages_net_id ON net_pages(net_id)
    """,
    "idx_net_pages_page_id": """
        CREATE INDEX idx_net_pages_page_id ON net_pages(page_id)
    """,
    "idx_net_aliases_net_id": """
        CREATE INDEX idx_net_aliases_net_id ON net_aliases(net_id)
    """,
    "idx_net_aliases_alias": """
        CREATE INDEX idx_net_aliases_alias ON net_aliases(alias)
    """,
    "idx_net_occurrences_net_id": """
        CREATE INDEX idx_net_occurrences_net_id ON net_occurrences(net_id)
    """,
    "idx_net_occurrences_page_id": """
        CREATE INDEX idx_net_occurrences_page_id ON net_occurrences(page_id)
    """,
    "idx_net_metadata_net_id": """
        CREATE INDEX idx_net_metadata_net_id ON net_metadata(net_id)
    """,
    "idx_net_occurrence_metadata_occurrence_id": """
        CREATE INDEX idx_net_occurrence_metadata_occurrence_id
        ON net_occurrence_metadata(occurrence_id)
    """,
    "idx_net_occurrence_metadata_net_id": """
        CREATE INDEX idx_net_occurrence_metadata_net_id ON net_occurrence_metadata(net_id)
    """,
    "idx_net_occurrence_source_names_occurrence_id": """
        CREATE INDEX idx_net_occurrence_source_names_occurrence_id
        ON net_occurrence_source_names(occurrence_id)
    """,
    "idx_net_occurrence_source_names_net_id": """
        CREATE INDEX idx_net_occurrence_source_names_net_id
        ON net_occurrence_source_names(net_id)
    """,
    "idx_net_occurrence_source_names_source_name": """
        CREATE INDEX idx_net_occurrence_source_names_source_name
        ON net_occurrence_source_names(source_name)
    """,
    "idx_schematic_directives_net_id": """
        CREATE INDEX idx_schematic_directives_net_id ON schematic_directives(net_id)
    """,
    "idx_schematic_directives_occurrence_id": """
        CREATE INDEX idx_schematic_directives_occurrence_id
        ON schematic_directives(occurrence_id)
    """,
    "idx_schematic_directives_kind": """
        CREATE INDEX idx_schematic_directives_kind ON schematic_directives(kind)
    """,
    "idx_buses_name": """
        CREATE INDEX idx_buses_name ON buses(name)
    """,
    "idx_bus_members_bus_id": """
        CREATE INDEX idx_bus_members_bus_id ON bus_members(bus_id)
    """,
    "idx_bus_members_net_id": """
        CREATE INDEX idx_bus_members_net_id ON bus_members(net_id)
    """,
    "idx_page_annotations_page_id": """
        CREATE INDEX idx_page_annotations_page_id ON page_annotations(page_id)
    """,
}

VIEW_DDL: dict[str, str] = {
    "net_routes": """
        CREATE VIEW net_routes AS
        SELECT
            net_name,
            layer,
            SUM(length_mm) AS trace_length_mm,
            COUNT(*) AS segment_count
        FROM conductors
        WHERE kind IN ('trace', 'trace_arc')
        GROUP BY net_name, layer
    """,
    "net_summary": """
        CREATE VIEW net_summary AS
        SELECT
            n.net_id,
            n.name,
            n.pin_count AS sch_pin_count,
            n.is_power,
            n.net_class,
            n.diff_pair,
            -- PCB tables key nets by board-global name; scoped nets sharing a
            -- name share these board-level aggregates. Count distinct pad ids so
            -- footprint-less pads (NULL reference) and shared pad numbers both
            -- count, and the LEFT JOIN to vias cannot inflate the total.
            COUNT(DISTINCT p.id) AS pcb_pad_count,
            COUNT(DISTINCT v.id) AS pcb_via_count,
            COALESCE((
                SELECT SUM(c.length_mm)
                FROM conductors c
                WHERE c.net_name = n.name AND c.kind IN ('trace', 'trace_arc')
            ), 0) AS trace_length_mm
        FROM nets n
        LEFT JOIN pads p ON p.net_name = n.name
        LEFT JOIN vias v ON v.net_name = n.name
        GROUP BY n.net_id, n.name, n.pin_count, n.is_power, n.net_class, n.diff_pair
    """,
    "width_violations": """
        CREATE VIEW width_violations AS
        SELECT c.net_name, c.layer, c.width_mm AS actual_width,
               nc.trace_width_mm AS target_width,
               c.width_mm - nc.trace_width_mm AS deviation_mm
        FROM conductors c
        JOIN net_class_members ncm ON ncm.net_name = c.net_name
        JOIN net_classes nc ON nc.name = ncm.net_class
        WHERE c.width_mm IS NOT NULL
          AND ABS(c.width_mm - nc.trace_width_mm) > 1e-6
    """,
    "drill_histogram": """
        CREATE VIEW drill_histogram AS
        SELECT
            -- Slots carry no diameter (0.0); report the drilled width as the
            -- size and keep the slot length separate so slots are not lumped
            -- into a misleading 0.0 bucket with the diameter-less rounds.
            CASE WHEN shape = 'slot' THEN width_mm ELSE diameter_mm END AS drill_mm,
            COUNT(*) AS count,
            owner_kind AS source,
            shape,
            CASE WHEN shape = 'slot' THEN height_mm END AS slot_length_mm
        FROM drills
        GROUP BY drill_mm, owner_kind, shape, slot_length_mm
        ORDER BY drill_mm, slot_length_mm
    """,
}


def create_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create all views after tables are populated."""
    for ddl in VIEW_DDL.values():
        _ = con.execute(ddl)
