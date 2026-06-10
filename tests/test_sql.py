"""Integration tests for the typed DuckDB SQL loader and CLI command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pytest
from click.testing import CliRunner

from phosphor_eda.cli import main
from phosphor_eda.convert import load_project
from phosphor_eda.domain.pcb import (
    LayerRole,
    Pcb,
    PcbConductor,
    PcbConductorKind,
    PcbDrillPlating,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbNet,
    PcbPadType,
    PcbVia,
    PcbViaType,
)
from phosphor_eda.domain.pcb_builder import PcbBuilder
from phosphor_eda.domain.project import Project
from phosphor_eda.domain.schematic import (
    Component,
    ComponentOccurrence,
    Net,
    NetOccurrence,
    Page,
    Pin,
    PinOccurrence,
    Schematic,
    ScopeId,
)
from phosphor_eda.sql import load_database

FIXTURES = Path(__file__).parent / "fixtures"
SWD_SWITCH_PCB = FIXTURES / "swd_switch.kicad_pcb"
ORANGECRAB_PCB = FIXTURES / "orangecrab.kicad_pcb"
PI_MX8_PCB = FIXTURES / "altium/pi-mx8/PCB/PiMX8MP_r0.3.PcbDoc"
JETSON_ORIN_PRO = FIXTURES / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pro"

if TYPE_CHECKING:
    from collections.abc import Iterator


def _count(db: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = db.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


def _constructed_schematic() -> Schematic:
    power_page = Page(
        id="page:power",
        name="Power",
        source_file="power.kicad_sch",
        scope_id=ScopeId(path=("root", "power")),
    )
    control_page = Page(
        id="page:control",
        name="Control",
        source_file="control.kicad_sch",
        scope_id=ScopeId(path=("root", "control")),
    )

    controller = Component(
        id="component:u1",
        reference="U1",
        part="STM32H7",
        description="Microcontroller",
        pages=[power_page, control_page, power_page],
        metadata={"manufacturer": "ST"},
    )
    duplicate_a = Component(
        id="component:power:u7",
        reference="U7",
        part="SN74LVC1T45",
        description="Level translator",
        pages=[power_page],
    )
    duplicate_b = Component(
        id="component:control:u7",
        reference="U7",
        part="SN74LVC1T45",
        description="Level translator",
        pages=[control_page],
    )

    sync = Net(
        id="net:sync",
        name="SYNC",
        pages=[power_page, control_page, power_page],
        aliases={"GLOBAL_SYNC", "SYNC,ALT", "SYNC_IN"},
        metadata={
            "selected_name_source": "global_label",
            "selected_name_source_id": "net-occurrence:sync:power",
            "source_format": "constructed",
            "source_local_net_ids": "control/local-8,power/local-12",
            "source_scope_ids": "/root/control,/root/power",
        },
    )
    reset_power = Net(
        id="net:reset:power",
        name="RESET",
        pages=[power_page],
        metadata={
            "selected_name_source": "local_label",
            "selected_name_source_id": "net-occurrence:reset:power",
            "source_format": "constructed",
            "source_local_net_ids": "power/local-2",
            "source_scope_ids": "/root/power",
        },
    )
    reset_control = Net(
        id="net:reset:control",
        name="RESET",
        pages=[control_page],
        metadata={
            "selected_name_source": "local_label",
            "selected_name_source_id": "net-occurrence:reset:control",
            "source_format": "constructed",
            "source_local_net_ids": "control/local-2",
            "source_scope_ids": "/root/control",
        },
    )

    controller_sync = Pin(
        id="pin:u1:10",
        component=controller,
        designator="10",
        name="SYNC",
        net=sync,
        metadata={"electrical": "input"},
    )
    controller_reset = Pin(
        id="pin:u1:11",
        component=controller,
        designator="11",
        name="RESET_N",
        net=reset_power,
        metadata={"electrical": "input"},
    )
    duplicate_a_pin = Pin(
        id="pin:power:u7:1",
        component=duplicate_a,
        designator="1",
        name="A",
        net=reset_power,
        metadata={"electrical": "passive"},
    )
    duplicate_b_pin = Pin(
        id="pin:control:u7:1",
        component=duplicate_b,
        designator="1",
        name="A",
        net=reset_control,
        metadata={"electrical": "passive"},
    )
    unconnected = Pin(
        id="pin:control:u7:2",
        component=duplicate_b,
        designator="2",
        name="B",
        no_connect=True,
    )
    controller_sync.occurrences = [
        PinOccurrence(
            id="pin-occurrence:u1:sync:power",
            pin=controller_sync,
            page=power_page,
            scope_id=power_page.scope_id,
            source_id="power/U1A/pin10",
            metadata={
                "source_component": "power/U1A",
                "source_local_net_id": "power/local-12",
            },
        ),
        PinOccurrence(
            id="pin-occurrence:u1:sync:control",
            pin=controller_sync,
            page=control_page,
            scope_id=control_page.scope_id,
            source_id="control/U1B/pin10",
            metadata={
                "source_component": "control/U1B",
                "source_local_net_id": "control/local-8",
            },
        ),
    ]
    duplicate_a_pin.occurrences = [
        PinOccurrence(
            id="pin-occurrence:power:u7:1",
            pin=duplicate_a_pin,
            page=power_page,
            scope_id=power_page.scope_id,
            source_id="power/U7/pin1",
        )
    ]

    controller.pins = [controller_sync, controller_reset]
    duplicate_a.pins = [duplicate_a_pin]
    duplicate_b.pins = [duplicate_b_pin, unconnected]
    sync.pins = [controller_sync]
    reset_power.pins = [controller_reset, duplicate_a_pin]
    reset_control.pins = [duplicate_b_pin]

    controller.occurrences = [
        ComponentOccurrence(
            id="occurrence:u1:power",
            component=controller,
            page=power_page,
            scope_id=power_page.scope_id,
            source_id="power/U1A",
            part_id="A",
            x=10.0,
            y=20.0,
            rotation=90.0,
            mirror=False,
            metadata={"source_block": "U1A", "sheet_symbol": "power-mcu"},
        ),
        ComponentOccurrence(
            id="occurrence:u1:control",
            component=controller,
            page=control_page,
            scope_id=control_page.scope_id,
            source_id="control/U1B",
            part_id="B",
            x=30.0,
            y=40.0,
            rotation=0.0,
            mirror=True,
            metadata={"source_block": "U1B", "sheet_symbol": "control-mcu"},
        ),
    ]
    duplicate_a.occurrences = [
        ComponentOccurrence(
            id="occurrence:power:u7",
            component=duplicate_a,
            page=power_page,
            scope_id=power_page.scope_id,
            source_id="power/U7",
        )
    ]
    duplicate_b.occurrences = [
        ComponentOccurrence(
            id="occurrence:control:u7",
            component=duplicate_b,
            page=control_page,
            scope_id=control_page.scope_id,
            source_id="control/U7",
        )
    ]

    sync.occurrences = [
        NetOccurrence(
            id="net-occurrence:sync:power",
            net=sync,
            page=power_page,
            scope_id=power_page.scope_id,
            source_local_net_id="power/local-12",
            source_names={"SYNC", "GLOBAL,SYNC", "GLOBAL_SYNC"},
            metadata={"source_label_kind": "global", "source_sheet": "Power"},
        ),
        NetOccurrence(
            id="net-occurrence:sync:control",
            net=sync,
            page=control_page,
            scope_id=control_page.scope_id,
            source_local_net_id="control/local-8",
            source_names={"SYNC_IN"},
            metadata={"source_label_kind": "port", "source_sheet": "Control"},
        ),
    ]
    reset_power.occurrences = [
        NetOccurrence(
            id="net-occurrence:reset:power",
            net=reset_power,
            page=power_page,
            scope_id=power_page.scope_id,
            source_local_net_id="power/local-2",
            source_names={"RESET"},
        )
    ]
    reset_control.occurrences = [
        NetOccurrence(
            id="net-occurrence:reset:control",
            net=reset_control,
            page=control_page,
            scope_id=control_page.scope_id,
            source_local_net_id="control/local-2",
            source_names={"RESET"},
        )
    ]

    power_page.components = [controller, duplicate_a]
    control_page.components = [controller, duplicate_b]
    power_page.nets = [sync, reset_power]
    control_page.nets = [sync, reset_control]

    return Schematic(
        name="Constructed SQL",
        pages=[power_page, control_page],
        components=[controller, duplicate_a, duplicate_b],
        nets=[sync, reset_power, reset_control],
    )


def _constructed_pcb() -> Pcb:
    """One footprint J1 with one pad on net RESET, one trace, and one via."""
    builder = PcbBuilder("Constructed PCB")
    front = builder.add_layer(PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT)))
    back = builder.add_layer(PcbLayer("B.Cu", (LayerRole.COPPER, LayerRole.BACK)))
    reset = builder.add_net(PcbNet(number=1, name="RESET"))
    connector = builder.add_footprint(
        PcbFootprint(
            reference="J1",
            footprint_lib="Connector_Test",
            x=1.0,
            y=1.0,
            rotation=0.0,
            layer=front,
        )
    )
    builder.add_pad(
        id="pad:J1:1",
        number="1",
        x=1.0,
        y=1.0,
        width=1.0,
        height=1.0,
        shape="rect",
        pad_type=PcbPadType.SMD,
        layers=(front,),
        net=reset,
        footprint=connector,
    )
    builder.add_conductor_object(
        PcbConductor(
            id="trace:1",
            kind=PcbConductorKind.TRACE,
            layer=front,
            data=PcbLine(0.0, 0.0, 10.0, 0.0, 0.2),
            net=reset,
        )
    )
    via_drill = builder.add_drill(
        id="drill:via:1",
        x=5.0,
        y=0.0,
        diameter=0.4,
        plating=PcbDrillPlating.PLATED,
        layers=(front, back),
    )
    builder.add_via_object(
        PcbVia(
            id="via:1",
            x=5.0,
            y=0.0,
            diameter=0.8,
            layers=(front, back),
            drill=via_drill,
            net=reset,
            via_type=PcbViaType.THROUGH,
        )
    )
    return builder.build()


@pytest.fixture
def constructed_db() -> Iterator[duckdb.DuckDBPyConnection]:
    project = Project(
        name="Constructed SQL",
        schematic=_constructed_schematic(),
        pcb=_constructed_pcb(),
    )
    con = load_database(project)
    try:
        yield con
    finally:
        con.close()


class TestConstructedSchematicSql:
    def test_logical_components_and_occurrences(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        component_rows = constructed_db.execute(
            """
            SELECT component_id, reference, page_ids, page_names
            FROM components
            ORDER BY component_id
            """
        ).fetchall()
        assert component_rows == [
            ("component:control:u7", "U7", "page:control", "Control"),
            ("component:power:u7", "U7", "page:power", "Power"),
            ("component:u1", "U1", "page:control,page:power", "Control,Power"),
        ]
        assert _count(constructed_db, "SELECT count(*) FROM components WHERE reference = 'U1'") == 1

        occurrence_rows = constructed_db.execute(
            """
            SELECT component_id, reference, page_id, page_name, scope_path, source_id, part_id
            FROM component_occurrences
            WHERE component_id = 'component:u1'
            ORDER BY occurrence_id
            """
        ).fetchall()
        assert occurrence_rows == [
            (
                "component:u1",
                "U1",
                "page:control",
                "Control",
                "/root/control",
                "control/U1B",
                "B",
            ),
            (
                "component:u1",
                "U1",
                "page:power",
                "Power",
                "/root/power",
                "power/U1A",
                "A",
            ),
        ]
        assert _count(constructed_db, "SELECT count(*) FROM component_occurrences") == 4

        occurrence_metadata_rows = constructed_db.execute(
            """
            SELECT occurrence_id, component_id, key, value
            FROM component_occurrence_metadata
            ORDER BY occurrence_id, key
            """
        ).fetchall()
        assert occurrence_metadata_rows == [
            ("occurrence:u1:control", "component:u1", "sheet_symbol", "control-mcu"),
            ("occurrence:u1:control", "component:u1", "source_block", "U1B"),
            ("occurrence:u1:power", "component:u1", "sheet_symbol", "power-mcu"),
            ("occurrence:u1:power", "component:u1", "source_block", "U1A"),
        ]

    def test_duplicate_references_have_distinct_component_ids(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        rows = constructed_db.execute(
            """
            SELECT component_id, reference
            FROM components
            WHERE reference = 'U7'
            ORDER BY component_id
            """
        ).fetchall()
        assert rows == [
            ("component:control:u7", "U7"),
            ("component:power:u7", "U7"),
        ]

    def test_pins_link_by_ids_and_keep_friendly_columns(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        rows = constructed_db.execute(
            """
            SELECT
                pin_id, component_id, reference, designator, name,
                net_id, net_name, electrical, no_connect
            FROM pins
            ORDER BY pin_id
            """
        ).fetchall()
        assert rows == [
            (
                "pin:control:u7:1",
                "component:control:u7",
                "U7",
                "1",
                "A",
                "net:reset:control",
                "RESET",
                "passive",
                False,
            ),
            (
                "pin:control:u7:2",
                "component:control:u7",
                "U7",
                "2",
                "B",
                None,
                None,
                None,
                True,
            ),
            (
                "pin:power:u7:1",
                "component:power:u7",
                "U7",
                "1",
                "A",
                "net:reset:power",
                "RESET",
                "passive",
                False,
            ),
            (
                "pin:u1:10",
                "component:u1",
                "U1",
                "10",
                "SYNC",
                "net:sync",
                "SYNC",
                "input",
                False,
            ),
            (
                "pin:u1:11",
                "component:u1",
                "U1",
                "11",
                "RESET_N",
                "net:reset:power",
                "RESET",
                "input",
                False,
            ),
        ]

    def test_pin_occurrences_preserve_source_provenance(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        rows = constructed_db.execute(
            """
            SELECT
                occurrence_id, pin_id, component_id, reference, designator,
                page_id, page_name, scope_path, source_id
            FROM pin_occurrences
            ORDER BY occurrence_id
            """
        ).fetchall()
        assert rows == [
            (
                "pin-occurrence:power:u7:1",
                "pin:power:u7:1",
                "component:power:u7",
                "U7",
                "1",
                "page:power",
                "Power",
                "/root/power",
                "power/U7/pin1",
            ),
            (
                "pin-occurrence:u1:sync:control",
                "pin:u1:10",
                "component:u1",
                "U1",
                "10",
                "page:control",
                "Control",
                "/root/control",
                "control/U1B/pin10",
            ),
            (
                "pin-occurrence:u1:sync:power",
                "pin:u1:10",
                "component:u1",
                "U1",
                "10",
                "page:power",
                "Power",
                "/root/power",
                "power/U1A/pin10",
            ),
        ]

        metadata_rows = constructed_db.execute(
            """
            SELECT occurrence_id, key, value
            FROM pin_occurrence_metadata
            ORDER BY occurrence_id, key
            """
        ).fetchall()
        assert metadata_rows == [
            (
                "pin-occurrence:u1:sync:control",
                "source_component",
                "control/U1B",
            ),
            (
                "pin-occurrence:u1:sync:control",
                "source_local_net_id",
                "control/local-8",
            ),
            ("pin-occurrence:u1:sync:power", "source_component", "power/U1A"),
            ("pin-occurrence:u1:sync:power", "source_local_net_id", "power/local-12"),
        ]

    def test_nets_occurrences_and_metadata_preserve_identity_and_provenance(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        duplicate_name_rows = constructed_db.execute(
            """
            SELECT net_id, name, page_ids, page_names
            FROM nets
            WHERE name = 'RESET'
            ORDER BY net_id
            """
        ).fetchall()
        assert duplicate_name_rows == [
            ("net:reset:control", "RESET", "page:control", "Control"),
            ("net:reset:power", "RESET", "page:power", "Power"),
        ]

        occurrence_rows = constructed_db.execute(
            """
            SELECT
                occurrence_id, net_id, name, page_id, page_name,
                scope_path, source_local_net_id, source_names
            FROM net_occurrences
            ORDER BY occurrence_id
            """
        ).fetchall()
        assert occurrence_rows == [
            (
                "net-occurrence:reset:control",
                "net:reset:control",
                "RESET",
                "page:control",
                "Control",
                "/root/control",
                "control/local-2",
                "RESET",
            ),
            (
                "net-occurrence:reset:power",
                "net:reset:power",
                "RESET",
                "page:power",
                "Power",
                "/root/power",
                "power/local-2",
                "RESET",
            ),
            (
                "net-occurrence:sync:control",
                "net:sync",
                "SYNC",
                "page:control",
                "Control",
                "/root/control",
                "control/local-8",
                "SYNC_IN",
            ),
            (
                "net-occurrence:sync:power",
                "net:sync",
                "SYNC",
                "page:power",
                "Power",
                "/root/power",
                "power/local-12",
                "GLOBAL,SYNC,GLOBAL_SYNC,SYNC",
            ),
        ]

        occurrence_metadata_rows = constructed_db.execute(
            """
            SELECT occurrence_id, net_id, key, value
            FROM net_occurrence_metadata
            ORDER BY occurrence_id, key
            """
        ).fetchall()
        assert occurrence_metadata_rows == [
            ("net-occurrence:sync:control", "net:sync", "source_label_kind", "port"),
            ("net-occurrence:sync:control", "net:sync", "source_sheet", "Control"),
            ("net-occurrence:sync:power", "net:sync", "source_label_kind", "global"),
            ("net-occurrence:sync:power", "net:sync", "source_sheet", "Power"),
        ]

        alias_rows = constructed_db.execute(
            "SELECT net_id, aliases FROM nets WHERE aliases IS NOT NULL ORDER BY net_id"
        ).fetchall()
        assert alias_rows == [
            ("net:sync", "GLOBAL_SYNC,SYNC,ALT,SYNC_IN"),
        ]

    def test_normalized_component_and_net_page_tables(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        component_rows = constructed_db.execute(
            """
            SELECT component_id, reference, page_id, page_name
            FROM component_pages
            ORDER BY component_id, page_id
            """
        ).fetchall()
        assert component_rows == [
            ("component:control:u7", "U7", "page:control", "Control"),
            ("component:power:u7", "U7", "page:power", "Power"),
            ("component:u1", "U1", "page:control", "Control"),
            ("component:u1", "U1", "page:power", "Power"),
        ]

        net_rows = constructed_db.execute(
            """
            SELECT net_id, name, page_id, page_name
            FROM net_pages
            ORDER BY net_id, page_id
            """
        ).fetchall()
        assert net_rows == [
            ("net:reset:control", "RESET", "page:control", "Control"),
            ("net:reset:power", "RESET", "page:power", "Power"),
            ("net:sync", "SYNC", "page:control", "Control"),
            ("net:sync", "SYNC", "page:power", "Power"),
        ]

    def test_normalized_net_aliases_and_source_names_are_exact_rows(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        alias_rows = constructed_db.execute(
            """
            SELECT alias
            FROM net_aliases
            WHERE net_id = 'net:sync'
            ORDER BY alias
            """
        ).fetchall()
        assert alias_rows == [("GLOBAL_SYNC",), ("SYNC,ALT",), ("SYNC_IN",)]

        source_name_rows = constructed_db.execute(
            """
            SELECT occurrence_id, source_name
            FROM net_occurrence_source_names
            WHERE net_id = 'net:sync'
            ORDER BY occurrence_id, source_name
            """
        ).fetchall()
        assert source_name_rows == [
            ("net-occurrence:sync:control", "SYNC_IN"),
            ("net-occurrence:sync:power", "GLOBAL,SYNC"),
            ("net-occurrence:sync:power", "GLOBAL_SYNC"),
            ("net-occurrence:sync:power", "SYNC"),
        ]

        metadata_rows = constructed_db.execute(
            """
            SELECT component_id, reference, key, value
            FROM component_metadata
            ORDER BY component_id, key
            """
        ).fetchall()
        assert metadata_rows == [
            ("component:u1", "U1", "manufacturer", "ST"),
        ]

        metadata_rows = constructed_db.execute(
            "SELECT net_id, name, key, value FROM net_metadata ORDER BY net_id, key"
        ).fetchall()
        assert metadata_rows == [
            ("net:reset:control", "RESET", "selected_name_source", "local_label"),
            (
                "net:reset:control",
                "RESET",
                "selected_name_source_id",
                "net-occurrence:reset:control",
            ),
            ("net:reset:control", "RESET", "source_format", "constructed"),
            ("net:reset:control", "RESET", "source_local_net_ids", "control/local-2"),
            ("net:reset:control", "RESET", "source_scope_ids", "/root/control"),
            ("net:reset:power", "RESET", "selected_name_source", "local_label"),
            (
                "net:reset:power",
                "RESET",
                "selected_name_source_id",
                "net-occurrence:reset:power",
            ),
            ("net:reset:power", "RESET", "source_format", "constructed"),
            ("net:reset:power", "RESET", "source_local_net_ids", "power/local-2"),
            ("net:reset:power", "RESET", "source_scope_ids", "/root/power"),
            ("net:sync", "SYNC", "selected_name_source", "global_label"),
            ("net:sync", "SYNC", "selected_name_source_id", "net-occurrence:sync:power"),
            ("net:sync", "SYNC", "source_format", "constructed"),
            (
                "net:sync",
                "SYNC",
                "source_local_net_ids",
                "control/local-8,power/local-12",
            ),
            ("net:sync", "SYNC", "source_scope_ids", "/root/control,/root/power"),
        ]

    def test_net_summary_groups_schematic_by_net_id_with_name_joined_pcb_counts(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        # PCB tables key nets by board-global name, so both scoped RESET nets
        # share the board-level pad/via/trace aggregates.
        rows = constructed_db.execute(
            """
            SELECT net_id, name, sch_pin_count, pcb_pad_count, pcb_via_count, trace_length_mm
            FROM net_summary
            WHERE name = 'RESET'
            ORDER BY net_id
            """
        ).fetchall()
        assert rows == [
            ("net:reset:control", "RESET", 1, 1, 1, 10.0),
            ("net:reset:power", "RESET", 2, 1, 1, 10.0),
        ]

    def test_loader_referential_integrity(self, constructed_db: duckdb.DuckDBPyConnection) -> None:
        page_ids = {
            page_id for (page_id,) in constructed_db.execute("SELECT page_id FROM pages").fetchall()
        }
        page_names = {
            name for (name,) in constructed_db.execute("SELECT name FROM pages").fetchall()
        }
        component_page_names = constructed_db.execute(
            """
            SELECT component_id, page_ids, page_names
            FROM components
            WHERE page_names IS NOT NULL
            """
        ).fetchall()
        for component_id, page_ids_csv, page_names_csv in component_page_names:
            for page_name in str(page_names_csv).split(","):
                assert page_name in page_names, component_id
            for page_id in str(page_ids_csv).split(","):
                assert page_id in page_ids, component_id

        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM component_pages cp
                LEFT JOIN components c ON c.component_id = cp.component_id
                WHERE c.component_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM component_pages cp
                LEFT JOIN pages p ON p.page_id = cp.page_id
                WHERE p.page_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM component_occurrences co
                LEFT JOIN components c ON c.component_id = co.component_id
                WHERE c.component_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM component_occurrence_metadata com
                LEFT JOIN component_occurrences co ON co.occurrence_id = com.occurrence_id
                WHERE co.occurrence_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM component_occurrences co
                LEFT JOIN pages p ON p.page_id = co.page_id
                WHERE p.page_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM net_occurrence_metadata nom
                LEFT JOIN net_occurrences no ON no.occurrence_id = nom.occurrence_id
                WHERE no.occurrence_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM pins pin
                LEFT JOIN components c ON c.component_id = pin.component_id
                WHERE c.component_id IS NULL
                """,
            )
            == 0
        )

    def test_schematic_metadata_indexes_exist(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        rows = constructed_db.execute(
            """
            SELECT index_name
            FROM duckdb_indexes()
            WHERE table_name IN (
                'component_metadata',
                'component_occurrence_metadata',
                'net_metadata',
                'net_occurrence_metadata',
                'pin_occurrence_metadata'
            )
            ORDER BY index_name
            """
        ).fetchall()
        assert rows == [
            ("idx_component_metadata_component_id",),
            ("idx_component_occurrence_metadata_component_id",),
            ("idx_component_occurrence_metadata_occurrence_id",),
            ("idx_net_metadata_net_id",),
            ("idx_net_occurrence_metadata_net_id",),
            ("idx_net_occurrence_metadata_occurrence_id",),
            ("idx_pin_occurrence_metadata_occurrence_id",),
            ("idx_pin_occurrence_metadata_pin_id",),
        ]
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM pins pin
                LEFT JOIN nets n ON n.net_id = pin.net_id
                WHERE pin.net_id IS NOT NULL AND n.net_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM component_metadata cm
                LEFT JOIN components c ON c.component_id = cm.component_id
                WHERE c.component_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM net_occurrences no
                LEFT JOIN nets n ON n.net_id = no.net_id
                WHERE n.net_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM net_occurrences no
                LEFT JOIN pages p ON p.page_id = no.page_id
                WHERE p.page_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM net_pages np
                LEFT JOIN nets n ON n.net_id = np.net_id
                WHERE n.net_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM net_pages np
                LEFT JOIN pages p ON p.page_id = np.page_id
                WHERE p.page_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM net_aliases na
                LEFT JOIN nets n ON n.net_id = na.net_id
                WHERE n.net_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM net_occurrence_source_names nosn
                LEFT JOIN net_occurrences no ON no.occurrence_id = nosn.occurrence_id
                WHERE no.occurrence_id IS NULL
                """,
            )
            == 0
        )
        assert (
            _count(
                constructed_db,
                """
                SELECT count(*)
                FROM net_metadata nm
                LEFT JOIN nets n ON n.net_id = nm.net_id
                WHERE n.net_id IS NULL
                """,
            )
            == 0
        )


# ---------------------------------------------------------------------------
# swd_switch fixture (always available, PCB-only)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db() -> Iterator[duckdb.DuckDBPyConnection]:
    project = load_project(SWD_SWITCH_PCB)
    con = load_database(project)
    try:
        yield con
    finally:
        con.close()


class TestFootprints:
    def test_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM footprints") == 28

    def test_have_geometry(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM footprints WHERE geom IS NOT NULL") > 0

    def test_sides(self, db: duckdb.DuckDBPyConnection) -> None:
        rows = db.execute(
            "SELECT side, count(*) FROM footprints GROUP BY side ORDER BY side"
        ).fetchall()
        assert dict(rows) == {"back": 5, "front": 23}


class TestTypedTables:
    def test_geometry_table_is_removed(self, db: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(duckdb.CatalogException):
            db.execute("SELECT * FROM geometry").fetchall()

    def test_expected_typed_collection_counts(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads") == 120
        assert _count(db, "SELECT count(*) FROM vias") == 49
        assert _count(db, "SELECT count(*) FROM drills") == 71
        assert _count(db, "SELECT count(*) FROM conductors") == 282
        assert _count(db, "SELECT count(*) FROM artwork") == 317
        assert _count(db, "SELECT count(*) FROM board_profile") == 16

    def test_conductors_replace_segments_and_polygons(self, db: duckdb.DuckDBPyConnection) -> None:
        assert (
            _count(db, "SELECT count(*) FROM conductors WHERE kind IN ('trace', 'trace_arc')")
            == 276
        )
        assert _count(db, "SELECT count(*) FROM conductors WHERE kind = 'pour_fill'") == 6
        assert _count(db, "SELECT count(*) FROM conductors WHERE geom IS NULL") == 0

    def test_artwork_contains_text_and_graphics(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM artwork WHERE content_kind = 'text'") > 0
        assert _count(db, "SELECT count(*) FROM artwork WHERE content_kind = 'line'") > 0

    def test_board_profile_has_geometry(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM board_profile WHERE geom IS NOT NULL") == 16


class TestPadsAndVias:
    def test_unconnected_pads_use_nullable_net(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads WHERE net_number IS NULL") > 0
        assert _count(db, "SELECT count(*) FROM pads WHERE net_number = 0") == 0

    def test_pads_join_to_drills(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads p JOIN drills d ON p.drill_id = d.id") > 0

    def test_vias_join_to_drills(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM vias v JOIN drills d ON v.drill_id = d.id") == 49

    def test_pad_geometry_is_loaded(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads WHERE geom IS NULL") == 0


def test_altium_free_pad_mask_apertures_are_queryable_without_freepads_footprint() -> None:
    project = load_project(PI_MX8_PCB)
    con = load_database(project)
    try:
        rows = con.execute(
            """
            SELECT reference, mask_aperture_width, mask_aperture_height, mask_aperture_source
            FROM pads
            WHERE reference IS NULL
              AND pad_number = 'MT'
              AND mask_aperture_source IS NOT NULL
            ORDER BY x, y
            """
        ).fetchall()
    finally:
        con.close()

    assert len(rows) == 4
    for reference, width, height, source in rows:
        assert reference is None
        assert width == pytest.approx(5.8, abs=0.02)
        assert height == pytest.approx(5.85, abs=0.02)
        assert source.startswith("altium:drill-manager-template:")


class TestPoursAndKeepouts:
    def test_pours_count(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pours") == 2

    def test_swd_switch_has_keepouts_table(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM keepouts") == 0


class TestLayers:
    def test_layers_have_roles_but_no_primary_role(self, db: duckdb.DuckDBPyConnection) -> None:
        row = db.execute(
            """
            SELECT roles, side
            FROM layers
            WHERE name = 'F.CrtYd'
            """
        ).fetchone()

        assert row is not None
        assert set(row[0]) >= {"fabrication", "courtyard", "front"}
        assert row[1] == "front"
        with pytest.raises(duckdb.BinderException):
            db.execute("SELECT primary_role FROM layers").fetchall()

    def test_position_ordered(self, db: duckdb.DuckDBPyConnection) -> None:
        rows = db.execute(
            "SELECT position FROM layers WHERE position IS NOT NULL ORDER BY position"
        ).fetchall()
        positions = [row[0] for row in rows]
        assert positions == sorted(positions)
        assert positions


class TestBoard:
    def test_outline_exists(self, db: duckdb.DuckDBPyConnection) -> None:
        row = db.execute("SELECT geom, layer_count FROM board").fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[1] > 0


class TestViews:
    def test_net_routes_reads_conductors(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM net_routes WHERE trace_length_mm > 0") > 0

    def test_drill_histogram_reads_drills(self, db: duckdb.DuckDBPyConnection) -> None:
        rows = db.execute("SELECT * FROM drill_histogram").fetchall()
        assert rows
        sources = {row[2] for row in rows}
        assert {"via", "pad"}.issubset(sources)

    def test_spatial_distance_query(self, db: duckdb.DuckDBPyConnection) -> None:
        row = db.execute("""
            SELECT ST_Distance(a.geom, b.geom)
            FROM footprints a, footprints b
            WHERE a.reference != b.reference
            AND a.geom IS NOT NULL AND b.geom IS NOT NULL
            LIMIT 1
        """).fetchone()
        assert row is not None
        distance = float(row[0])
        assert 0 <= distance < 200


def test_kicad_keepouts_are_queryable_in_sql() -> None:
    project = load_project(ORANGECRAB_PCB)
    con = load_database(project)
    try:
        assert _count(con, "SELECT count(*) FROM keepouts") > 0
    finally:
        con.close()


def test_altium_typed_tables_are_populated() -> None:
    project = load_project(PI_MX8_PCB)
    con = load_database(project)
    try:
        assert _count(con, "SELECT count(*) FROM drills") > 0
        assert _count(con, "SELECT count(*) FROM conductors") > 0
        assert _count(con, "SELECT count(*) FROM artwork") > 0
        assert _count(con, "SELECT count(*) FROM board_profile") > 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Jetson Orin fixture (skipped if unavailable)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jetson_db() -> Iterator[duckdb.DuckDBPyConnection]:
    if not JETSON_ORIN_PRO.exists():
        pytest.skip("Jetson Orin fixture not available")
    project = load_project(JETSON_ORIN_PRO)
    con = load_database(project)
    try:
        yield con
    finally:
        con.close()


class TestJetsonNetClasses:
    def test_count(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        assert _count(jetson_db, "SELECT count(*) FROM net_classes") == 8


class TestJetsonDesignRules:
    def test_count(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        assert _count(jetson_db, "SELECT count(*) FROM design_rules") == 33


class TestJetsonSchematic:
    def test_components_count(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        assert _count(jetson_db, "SELECT count(*) FROM components") == 666

    def test_nets_have_net_class(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        count = _count(jetson_db, "SELECT count(*) FROM nets WHERE net_class IS NOT NULL")
        assert count > 0

    def test_net_summary_view(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        rows = jetson_db.execute("SELECT * FROM net_summary LIMIT 5").fetchall()
        assert len(rows) > 0


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_basic_query(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["sql", str(SWD_SWITCH_PCB), "SELECT count(*) FROM footprints"]
        )
        assert result.exit_code == 0
        assert "28" in result.output

    def test_schema_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["sql", str(SWD_SWITCH_PCB), "--schema"])
        assert result.exit_code == 0
        assert "CREATE TABLE footprints" in result.output

    def test_no_query_error(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["sql", str(SWD_SWITCH_PCB)])
        assert result.exit_code != 0

    def test_invalid_query(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["sql", str(SWD_SWITCH_PCB), "SELECT * FROM nonexistent"])
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    def test_spatial_query(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["sql", str(SWD_SWITCH_PCB), "SELECT ST_Area(geom) FROM footprints LIMIT 1"]
        )
        assert result.exit_code == 0
        # Should contain a numeric value (the area)
        lines = result.output.strip().split("\n")
        # Header + separator + 1 data row + "(1 row)" footer
        assert len(lines) >= 3
