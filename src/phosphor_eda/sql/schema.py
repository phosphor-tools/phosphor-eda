"""DDL definitions for the DuckDB SQL schema."""

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
            id VARCHAR,
            reference VARCHAR,
            pad_number VARCHAR,
            net_name VARCHAR,
            net_number INTEGER,
            x DOUBLE,
            y DOUBLE,
            width DOUBLE,
            height DOUBLE,
            shape VARCHAR,
            pad_type VARCHAR,
            drill_id VARCHAR,
            drill DOUBLE,
            side VARCHAR,
            primary_layer VARCHAR,
            layers VARCHAR[],
            pin_function VARCHAR,
            pin_type VARCHAR,
            mask_aperture_width DOUBLE,
            mask_aperture_height DOUBLE,
            mask_aperture_source VARCHAR,
            geom GEOMETRY
        )
    """,
    "vias": """
        CREATE TABLE vias (
            id VARCHAR,
            net_name VARCHAR,
            net_number INTEGER,
            x DOUBLE,
            y DOUBLE,
            diameter_mm DOUBLE,
            drill_id VARCHAR,
            via_type VARCHAR,
            start_layer VARCHAR,
            end_layer VARCHAR,
            layers VARCHAR[],
            geom GEOMETRY
        )
    """,
    "drills": """
        CREATE TABLE drills (
            id VARCHAR,
            owner_kind VARCHAR,
            owner_id VARCHAR,
            plating VARCHAR,
            shape VARCHAR,
            x DOUBLE,
            y DOUBLE,
            diameter_mm DOUBLE,
            width_mm DOUBLE,
            height_mm DOUBLE,
            rotation DOUBLE,
            layers VARCHAR[],
            geom GEOMETRY
        )
    """,
    "conductors": """
        CREATE TABLE conductors (
            id VARCHAR,
            kind VARCHAR,
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
            footprint_ref VARCHAR,
            pour_id VARCHAR,
            centerline GEOMETRY,
            geom GEOMETRY
        )
    """,
    "artwork": """
        CREATE TABLE artwork (
            id VARCHAR,
            purpose VARCHAR,
            content_kind VARCHAR,
            footprint_ref VARCHAR,
            layer VARCHAR,
            text VARCHAR,
            x DOUBLE,
            y DOUBLE,
            rotation DOUBLE,
            font_size DOUBLE,
            geom GEOMETRY
        )
    """,
    "board_profile": """
        CREATE TABLE board_profile (
            id VARCHAR,
            kind VARCHAR,
            layer VARCHAR,
            is_cutout BOOLEAN,
            geom GEOMETRY
        )
    """,
    "pours": """
        CREATE TABLE pours (
            id VARCHAR,
            name VARCHAR,
            net_name VARCHAR,
            net_number INTEGER,
            primary_layer VARCHAR,
            layers VARCHAR[],
            priority INTEGER,
            fill_mode VARCHAR,
            hatch_style VARCHAR,
            grid_mm DOUBLE,
            track_width_mm DOUBLE,
            min_thickness_mm DOUBLE,
            thermal_gap_mm DOUBLE,
            thermal_bridge_width_mm DOUBLE,
            connect_pads_clearance_mm DOUBLE,
            fill_conductor_ids VARCHAR[],
            footprint_ref VARCHAR,
            source_format VARCHAR,
            native_type VARCHAR,
            native_kind VARCHAR,
            native_id VARCHAR,
            native_index INTEGER,
            metadata JSON,
            boundary GEOMETRY
        )
    """,
    "keepouts": """
        CREATE TABLE keepouts (
            id VARCHAR,
            name VARCHAR,
            footprint_ref VARCHAR,
            primary_layer VARCHAR,
            layers VARCHAR[],
            tracks VARCHAR,
            vias VARCHAR,
            pads VARCHAR,
            copper_pours VARCHAR,
            footprints VARCHAR,
            source_format VARCHAR,
            native_type VARCHAR,
            native_kind VARCHAR,
            native_id VARCHAR,
            native_index INTEGER,
            metadata JSON,
            boundary GEOMETRY
        )
    """,
    "layers": """
        CREATE TABLE layers (
            position INTEGER,
            name VARCHAR,
            roles VARCHAR[],
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
        FROM conductors
        WHERE kind IN ('trace', 'trace_arc')
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
            COUNT(DISTINCT v.id) AS pcb_via_count,
            COALESCE((
                SELECT SUM(c.length_mm)
                FROM conductors c
                WHERE c.net_name = n.name AND c.kind IN ('trace', 'trace_arc')
            ), 0) AS trace_length_mm
        FROM nets n
        LEFT JOIN pads p ON p.net_name = n.name
        LEFT JOIN vias v ON v.net_name = n.name
        GROUP BY n.name, n.pin_count, n.is_power, n.net_class, n.diff_pair
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
        SELECT diameter_mm AS drill_mm, COUNT(*) AS count, owner_kind AS source
        FROM drills
        GROUP BY diameter_mm, owner_kind
        ORDER BY diameter_mm
    """,
}


def create_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Install spatial extension and create all tables."""
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    for ddl in TABLE_DDL.values():
        con.execute(ddl)


def create_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create all views after tables are populated."""
    for ddl in VIEW_DDL.values():
        con.execute(ddl)


def schema_text() -> str:
    """Return formatted DDL for all tables and views."""
    lines: list[str] = ["-- Tables\n"]
    for name, ddl in TABLE_DDL.items():
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
