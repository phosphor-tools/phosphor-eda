"""DDL definitions for the DuckDB SQL schema.

Table and view definitions match the schema in docs/design/pcb-query-commands.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

TABLE_DDL: dict[str, str] = {
    "footprints": """
        CREATE TABLE footprints (
            reference VARCHAR,
            footprint_lib VARCHAR,
            x DOUBLE,
            y DOUBLE,
            rotation DOUBLE,
            side VARCHAR,
            value VARCHAR,
            geom GEOMETRY
        )
    """,
    "pads": """
        CREATE TABLE pads (
            reference VARCHAR,
            pad_number VARCHAR,
            net_name VARCHAR,
            net_number INTEGER,
            x DOUBLE,
            y DOUBLE,
            width DOUBLE,
            height DOUBLE,
            shape VARCHAR,
            drill DOUBLE,
            side VARCHAR,
            layer VARCHAR,
            pin_function VARCHAR,
            pin_type VARCHAR,
            geom GEOMETRY
        )
    """,
    "segments": """
        CREATE TABLE segments (
            net_name VARCHAR,
            net_number INTEGER,
            layer VARCHAR,
            width_mm DOUBLE,
            start_x DOUBLE,
            start_y DOUBLE,
            end_x DOUBLE,
            end_y DOUBLE,
            is_arc BOOLEAN,
            arc_center_x DOUBLE,
            arc_center_y DOUBLE,
            arc_angle DOUBLE,
            length_mm DOUBLE,
            centerline GEOMETRY,
            geom GEOMETRY
        )
    """,
    "vias": """
        CREATE TABLE vias (
            net_name VARCHAR,
            net_number INTEGER,
            x DOUBLE,
            y DOUBLE,
            diameter_mm DOUBLE,
            drill_mm DOUBLE,
            start_layer VARCHAR,
            end_layer VARCHAR,
            geom GEOMETRY,
            drill_geom GEOMETRY
        )
    """,
    "polygons": """
        CREATE TABLE polygons (
            net_name VARCHAR,
            net_number INTEGER,
            layer VARCHAR,
            geom GEOMETRY
        )
    """,
    "zones": """
        CREATE TABLE zones (
            net_name VARCHAR,
            net_number INTEGER,
            layer VARCHAR,
            priority INTEGER,
            min_thickness_mm DOUBLE,
            thermal_gap_mm DOUBLE,
            thermal_bridge_width_mm DOUBLE,
            fill_type VARCHAR,
            boundary GEOMETRY
        )
    """,
    "footprint_graphics": """
        CREATE TABLE footprint_graphics (
            reference VARCHAR,
            layer VARCHAR,
            kind VARCHAR,
            geom GEOMETRY
        )
    """,
    "graphic_texts": """
        CREATE TABLE graphic_texts (
            text VARCHAR,
            x DOUBLE,
            y DOUBLE,
            rotation DOUBLE,
            layer VARCHAR,
            font_size DOUBLE,
            justify VARCHAR
        )
    """,
    "dimensions": """
        CREATE TABLE dimensions (
            kind VARCHAR,
            value_mm DOUBLE,
            layer VARCHAR,
            start_x DOUBLE,
            start_y DOUBLE,
            end_x DOUBLE,
            end_y DOUBLE,
            text VARCHAR
        )
    """,
    "layers": """
        CREATE TABLE layers (
            position INTEGER,
            name VARCHAR,
            function VARCHAR,
            side VARCHAR,
            number INTEGER,
            thickness_mm DOUBLE,
            material VARCHAR,
            epsilon_r DOUBLE,
            loss_tangent DOUBLE,
            layer_type VARCHAR,
            copper_orientation VARCHAR
        )
    """,
    "board": """
        CREATE TABLE board (
            name VARCHAR,
            total_thickness_mm DOUBLE,
            copper_finish VARCHAR,
            pad_to_mask_clearance_mm DOUBLE,
            layer_count INTEGER,
            geom GEOMETRY
        )
    """,
    "net_classes": """
        CREATE TABLE net_classes (
            name VARCHAR,
            kind INTEGER,
            trace_width_mm DOUBLE,
            clearance_mm DOUBLE,
            via_diameter_mm DOUBLE,
            via_drill_mm DOUBLE,
            diff_pair_width_mm DOUBLE,
            diff_pair_gap_mm DOUBLE
        )
    """,
    "net_class_members": """
        CREATE TABLE net_class_members (
            net_name VARCHAR,
            net_class VARCHAR
        )
    """,
    "design_rules": """
        CREATE TABLE design_rules (
            name VARCHAR,
            kind VARCHAR,
            enabled BOOLEAN,
            priority INTEGER,
            scope1 VARCHAR,
            scope2 VARCHAR,
            layer_scope VARCHAR,
            min_value_mm DOUBLE,
            max_value_mm DOUBLE,
            target_value_mm DOUBLE
        )
    """,
    "components": """
        CREATE TABLE components (
            component_id VARCHAR PRIMARY KEY,
            reference VARCHAR NOT NULL,
            part VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            page_ids VARCHAR,
            page_names VARCHAR
        )
    """,
    "component_occurrences": """
        CREATE TABLE component_occurrences (
            occurrence_id VARCHAR PRIMARY KEY,
            component_id VARCHAR NOT NULL,
            reference VARCHAR NOT NULL,
            page_id VARCHAR NOT NULL,
            page_name VARCHAR NOT NULL,
            scope_path VARCHAR NOT NULL,
            source_id VARCHAR NOT NULL,
            part_id VARCHAR,
            x DOUBLE,
            y DOUBLE,
            rotation DOUBLE,
            mirror BOOLEAN
        )
    """,
    "component_pages": """
        CREATE TABLE component_pages (
            component_id VARCHAR NOT NULL,
            reference VARCHAR NOT NULL,
            page_id VARCHAR NOT NULL,
            page_name VARCHAR NOT NULL
        )
    """,
    "component_metadata": """
        CREATE TABLE component_metadata (
            component_id VARCHAR NOT NULL,
            reference VARCHAR NOT NULL,
            key VARCHAR NOT NULL,
            value VARCHAR NOT NULL
        )
    """,
    "component_occurrence_metadata": """
        CREATE TABLE component_occurrence_metadata (
            occurrence_id VARCHAR NOT NULL,
            component_id VARCHAR NOT NULL,
            key VARCHAR NOT NULL,
            value VARCHAR NOT NULL
        )
    """,
    "pins": """
        CREATE TABLE pins (
            pin_id VARCHAR PRIMARY KEY,
            component_id VARCHAR NOT NULL,
            reference VARCHAR NOT NULL,
            designator VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            net_id VARCHAR,
            net_name VARCHAR,
            electrical VARCHAR,
            no_connect BOOLEAN NOT NULL
        )
    """,
    "pin_occurrences": """
        CREATE TABLE pin_occurrences (
            occurrence_id VARCHAR PRIMARY KEY,
            pin_id VARCHAR NOT NULL,
            component_id VARCHAR NOT NULL,
            reference VARCHAR NOT NULL,
            designator VARCHAR NOT NULL,
            page_id VARCHAR NOT NULL,
            page_name VARCHAR NOT NULL,
            scope_path VARCHAR NOT NULL,
            source_id VARCHAR NOT NULL
        )
    """,
    "pin_occurrence_metadata": """
        CREATE TABLE pin_occurrence_metadata (
            occurrence_id VARCHAR NOT NULL,
            pin_id VARCHAR NOT NULL,
            key VARCHAR NOT NULL,
            value VARCHAR NOT NULL
        )
    """,
    "nets": """
        CREATE TABLE nets (
            net_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            pin_count INTEGER NOT NULL,
            page_ids VARCHAR,
            page_names VARCHAR,
            is_power BOOLEAN NOT NULL,
            net_class VARCHAR,
            diff_pair VARCHAR,
            diff_pair_polarity VARCHAR,
            aliases VARCHAR
        )
    """,
    "net_pages": """
        CREATE TABLE net_pages (
            net_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            page_id VARCHAR NOT NULL,
            page_name VARCHAR NOT NULL
        )
    """,
    "net_aliases": """
        CREATE TABLE net_aliases (
            net_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            alias VARCHAR NOT NULL
        )
    """,
    "net_occurrences": """
        CREATE TABLE net_occurrences (
            occurrence_id VARCHAR PRIMARY KEY,
            net_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            page_id VARCHAR NOT NULL,
            page_name VARCHAR NOT NULL,
            scope_path VARCHAR NOT NULL,
            source_local_net_id VARCHAR NOT NULL,
            source_names VARCHAR
        )
    """,
    "net_occurrence_source_names": """
        CREATE TABLE net_occurrence_source_names (
            occurrence_id VARCHAR NOT NULL,
            net_id VARCHAR NOT NULL,
            source_name VARCHAR NOT NULL
        )
    """,
    "net_metadata": """
        CREATE TABLE net_metadata (
            net_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            key VARCHAR NOT NULL,
            value VARCHAR NOT NULL
        )
    """,
    "net_occurrence_metadata": """
        CREATE TABLE net_occurrence_metadata (
            occurrence_id VARCHAR NOT NULL,
            net_id VARCHAR NOT NULL,
            key VARCHAR NOT NULL,
            value VARCHAR NOT NULL
        )
    """,
    "pages": """
        CREATE TABLE pages (
            page_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            source_file VARCHAR,
            scope_path VARCHAR NOT NULL,
            component_count INTEGER NOT NULL,
            net_count INTEGER NOT NULL
        )
    """,
    "project": """
        CREATE TABLE project (
            key VARCHAR,
            value VARCHAR
        )
    """,
}

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
}

VIEW_DDL: dict[str, str] = {
    "net_routes": """
        CREATE VIEW net_routes AS
        SELECT
            net_name,
            layer,
            SUM(length_mm) AS trace_length_mm,
            COUNT(*) AS segment_count
        FROM segments
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
            CAST(NULL AS INTEGER) AS pcb_pad_count,
            CAST(NULL AS INTEGER) AS pcb_via_count,
            CAST(NULL AS DOUBLE) AS trace_length_mm
        FROM nets n
        GROUP BY n.net_id, n.name, n.pin_count, n.is_power, n.net_class, n.diff_pair
    """,
    "width_violations": """
        CREATE VIEW width_violations AS
        SELECT s.net_name, s.layer, s.width_mm AS actual_width,
               nc.trace_width_mm AS target_width,
               s.width_mm - nc.trace_width_mm AS deviation_mm
        FROM segments s
        JOIN net_class_members ncm ON ncm.net_name = s.net_name
        JOIN net_classes nc ON nc.name = ncm.net_class
        WHERE ABS(s.width_mm - nc.trace_width_mm) > 1e-6
    """,
    "drill_histogram": """
        CREATE VIEW drill_histogram AS
        SELECT drill_mm, count, source
        FROM (
            SELECT drill_mm, COUNT(*) AS count, 'via' AS source
            FROM vias
            GROUP BY drill_mm
            UNION ALL
            SELECT drill AS drill_mm, COUNT(*) AS count, 'pad' AS source
            FROM pads
            WHERE drill > 0
            GROUP BY drill
        )
        ORDER BY drill_mm
    """,
}


def create_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Install spatial extension and create all tables."""
    _ = con.execute("INSTALL spatial")
    _ = con.execute("LOAD spatial")
    for ddl in TABLE_DDL.values():
        _ = con.execute(ddl)
    for ddl in INDEX_DDL.values():
        _ = con.execute(ddl)


def create_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create all views (must be called after tables are populated)."""
    for ddl in VIEW_DDL.values():
        _ = con.execute(ddl)


def schema_text() -> str:
    """Return formatted DDL for all tables and views (for --schema output)."""
    lines: list[str] = []
    lines.append("-- Tables\n")
    for name, ddl in TABLE_DDL.items():
        # Clean up indentation for display
        cleaned = "\n".join(line.strip() for line in ddl.strip().splitlines())
        lines.append(f"-- {name}")
        lines.append(cleaned)
        lines.append("")

    lines.append("-- Indexes\n")
    for name, ddl in INDEX_DDL.items():
        cleaned = "\n".join(line.strip() for line in ddl.strip().splitlines())
        lines.append(f"-- {name}")
        lines.append(cleaned)
        lines.append("")

    lines.append("-- Views\n")
    for name, ddl in VIEW_DDL.items():
        cleaned = "\n".join(line.strip() for line in ddl.strip().splitlines())
        lines.append(f"-- {name}")
        lines.append(cleaned)
        lines.append("")

    return "\n".join(lines)
