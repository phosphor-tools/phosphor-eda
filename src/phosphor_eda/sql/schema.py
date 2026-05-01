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
            layer_type VARCHAR
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
            reference VARCHAR,
            part VARCHAR,
            description VARCHAR,
            page_name VARCHAR
        )
    """,
    "component_metadata": """
        CREATE TABLE component_metadata (
            reference VARCHAR,
            key VARCHAR,
            value VARCHAR
        )
    """,
    "pins": """
        CREATE TABLE pins (
            reference VARCHAR,
            designator VARCHAR,
            name VARCHAR,
            net_name VARCHAR,
            electrical VARCHAR,
            no_connect BOOLEAN
        )
    """,
    "nets": """
        CREATE TABLE nets (
            name VARCHAR,
            pin_count INTEGER,
            is_power BOOLEAN,
            net_class VARCHAR,
            diff_pair VARCHAR,
            diff_pair_polarity VARCHAR,
            aliases VARCHAR
        )
    """,
    "pages": """
        CREATE TABLE pages (
            name VARCHAR,
            component_count INTEGER,
            net_count INTEGER
        )
    """,
    "project": """
        CREATE TABLE project (
            key VARCHAR,
            value VARCHAR
        )
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
            n.name,
            n.pin_count AS sch_pin_count,
            n.is_power,
            n.net_class,
            n.diff_pair,
            COUNT(DISTINCT p.reference || '.' || p.pad_number) AS pcb_pad_count,
            COUNT(DISTINCT v.rowid) AS pcb_via_count,
            COALESCE((
                SELECT SUM(s.length_mm) FROM segments s WHERE s.net_name = n.name
            ), 0) AS trace_length_mm
        FROM nets n
        LEFT JOIN pads p ON p.net_name = n.name
        LEFT JOIN vias v ON v.net_name = n.name
        GROUP BY n.name, n.pin_count, n.is_power, n.net_class, n.diff_pair
    """,
    "width_violations": """
        CREATE VIEW width_violations AS
        SELECT s.net_name, s.layer, s.width_mm AS actual_width,
               nc.trace_width_mm AS target_width,
               s.width_mm - nc.trace_width_mm AS deviation_mm
        FROM segments s
        JOIN net_class_members ncm ON ncm.net_name = s.net_name
        JOIN net_classes nc ON nc.name = ncm.net_class
        WHERE s.width_mm != nc.trace_width_mm
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
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    for ddl in TABLE_DDL.values():
        con.execute(ddl)


def create_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create all views (must be called after tables are populated)."""
    for ddl in VIEW_DDL.values():
        con.execute(ddl)


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

    lines.append("-- Views\n")
    for name, ddl in VIEW_DDL.items():
        cleaned = "\n".join(line.strip() for line in ddl.strip().splitlines())
        lines.append(f"-- {name}")
        lines.append(cleaned)
        lines.append("")

    return "\n".join(lines)
