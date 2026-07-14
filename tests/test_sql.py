"""Integration tests for the typed DuckDB SQL loader and CLI command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pytest
from click.testing import CliRunner

from phosphor_eda.cli import main
from phosphor_eda.domain.pcb import (
    Board,
    LayerRole,
    PadStack,
    PcbConductor,
    PcbConductorKind,
    PcbDrill,
    PcbDrillPlating,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbNet,
    PcbPad,
    PcbPadType,
    PcbVia,
    PcbViaType,
)
from phosphor_eda.domain.pcb_builder import PcbBuilder
from phosphor_eda.domain.project import DesignRule, DiffPair, NetClass, Project
from phosphor_eda.domain.schematic import (
    Bus,
    BusKind,
    Component,
    ComponentOccurrence,
    DnpSource,
    FootprintModel,
    LibraryLink,
    Net,
    NetOccurrence,
    Page,
    Parameter,
    PartNumber,
    Pin,
    PinOccurrence,
    Schematic,
    SchematicDirective,
    SchematicDirectiveKind,
    ScopeId,
    TitleBlock,
)
from phosphor_eda.domain.variant_materializer import materialize_project_variant
from phosphor_eda.domain.variants import (
    Variant,
    VariantField,
    VariantOverride,
    VariantTarget,
    VariantTargetKind,
)
from phosphor_eda.formats.allegro import load_allegro_pcb_project
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.raw_models import (
    DsnPackage,
    DsnPackageDevice,
    DsnPackageDevicePin,
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    SchematicPage,
)
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design
from phosphor_eda.formats.kicad.board import parse_kicad_pcb
from phosphor_eda.query.project_loader import load_project
from phosphor_eda.query.sql import load_database

FIXTURES = Path(__file__).parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
RFSOC_DSN = UPSTREAM_FIXTURES / "rfsoc-frontend/RFMC_Frontend/RFMC_FRONTEND_V1_00.DSN"
SWD_SWITCH_PCB = UPSTREAM_FIXTURES / "debugotron/hw/swd_switch/swd_switch.kicad_pcb"
ORANGECRAB_PRO = FIXTURES / "kicad-orangecrab/OrangeCrab.kicad_pro"
PI_MX8_PRJPCB = (
    UPSTREAM_FIXTURES / "pi-mx8/01_Electronics/PiMX8MP_r0.3_release/PiMX8MP_r0.3_release.PrjPcb"
)
JETSON_ORIN_PRO = UPSTREAM_FIXTURES / "jetson-orin" / "jetson-orin-baseboard.kicad_pro"
ALLEGRO_BREAKOUT_BRD = (
    UPSTREAM_FIXTURES
    / "opencellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def _count(db: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = db.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


def test_orcad_package_pin_metadata_is_queryable_in_sql() -> None:
    raw = ParsedDesign(
        pages=[
            SchematicPage(
                name="Main",
                nets=[PageNetEntry(name="SIG", net_id=1)],
                instances=[
                    PlacedInstance(
                        package_name="SYNTH.Normal",
                        source_package="SYNTH",
                        db_id=100,
                        reference="U1",
                        pin_connections=[PinConnection(pin_number="1", net_id=1)],
                    )
                ],
            )
        ],
        packages={
            "Packages/SYNTH": DsnPackage(
                name="SYNTH",
                source_library="synthetic.olb",
                devices=[
                    DsnPackageDevice(
                        refdes_suffix="SYNTH",
                        pins=[DsnPackageDevicePin(order=0, package_pin="A1")],
                    )
                ],
            )
        },
        symbol_pin_names={"SYNTH": ["IN"]},
    )
    con = load_database(Project(name="orcad", schematic=dsn_to_design(raw)))
    try:
        pin_rows = con.execute(
            """
            SELECT p.reference, p.designator, pom.key, pom.value
            FROM pins p
            JOIN pin_occurrence_metadata pom USING (pin_id)
            WHERE pom.key IN ('dsn_package_device', 'dsn_package_pin')
            ORDER BY pom.key
            """
        ).fetchall()
        component_rows = con.execute(
            """
            SELECT reference, key, value
            FROM component_metadata
            WHERE key IN ('dsn_package_name', 'dsn_source_library')
            ORDER BY key
            """
        ).fetchall()
    finally:
        con.close()

    assert pin_rows == [
        ("U1", "1", "dsn_package_device", "SYNTH"),
        ("U1", "1", "dsn_package_pin", "A1"),
    ]
    assert component_rows == [
        ("U1", "dsn_package_name", "SYNTH"),
        ("U1", "dsn_source_library", "synthetic.olb"),
    ]


def test_orcad_repeated_sheet_scope_path_is_queryable_in_sql() -> None:
    """E5: occurrence-scoped repeated sheets expose their hierarchical
    ``scope_path`` in SQL, distinguishing each channel instantiation."""
    con = load_database(Project(name="rfsoc", schematic=dsn_to_design(parse_dsn(RFSOC_DSN))))
    try:
        rows = con.execute(
            """
            SELECT reference, scope_path
            FROM component_occurrences
            WHERE reference IN ('R23', 'R107')
              AND scope_path LIKE '/DAC_ADC_TOP/%/DAC_ADC'
            ORDER BY reference
            """
        ).fetchall()
        distinct_channel_scopes = con.execute(
            """
            SELECT COUNT(DISTINCT scope_path)
            FROM component_occurrences
            WHERE scope_path LIKE '/DAC_ADC_TOP/CH%/DAC_ADC'
            """
        ).fetchone()
    finally:
        con.close()

    assert ("R107", "/DAC_ADC_TOP/CH7/DAC_ADC") in rows
    assert ("R23", "/DAC_ADC_TOP/CH0/DAC_ADC") in rows
    # Eight DAC channels, each a distinct scope path.
    assert distinct_channel_scopes is not None
    assert distinct_channel_scopes[0] == 8


def _write_swd_project(tmp_path: Path) -> Path:
    project_file = tmp_path / "swd_switch.kicad_pro"
    board_file = tmp_path / "swd_switch.kicad_pcb"
    board_file.write_bytes(SWD_SWITCH_PCB.read_bytes())
    project_file.write_text("{}", encoding="utf-8")
    return project_file


def _constructed_schematic() -> Schematic:
    power_page = Page(
        id="page:power",
        name="Power",
        source_file="power.kicad_sch",
        scope_id=ScopeId(path=("root", "power")),
        annotations=["First note", "Second note"],
        title_block=TitleBlock(
            title="Power Sheet",
            revision="A",
            date="2026-01-02",
            organization="Phosphor",
            sheet_number="1",
            sheet_total="2",
            comments={"1": "constructed"},
        ),
    )
    control_page = Page(
        id="page:control",
        name="Control",
        source_file="control.kicad_sch",
        scope_id=ScopeId(path=("root", "control")),
        annotations=["Control note"],
    )

    controller = Component(
        id="component:u1",
        reference="U1",
        part="STM32H7",
        description="Microcontroller",
        pages=[power_page, control_page, power_page],
        parameters=[
            Parameter(name="MPN", value="STM32H743VI", visible=True, source="constructed"),
            Parameter(name="Voltage", value="3.3V", source="constructed"),
        ],
        lib=LibraryLink(symbol="MCU_ST:STM32H743VI", library="MCU_ST", source="project"),
        footprints=[
            FootprintModel(
                name="Package_QFP:LQFP-100_14x14mm_P0.5mm",
                library="Package_QFP",
                is_current=True,
                description="LQFP-100",
            )
        ],
        part_numbers=[PartNumber(manufacturer="ST", number="STM32H743VIT6")],
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
        dnp=True,
        dnp_source=DnpSource.EXPLICIT,
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
    sync_directive = SchematicDirective(
        kind=SchematicDirectiveKind.NET_CLASS,
        value="TIMING",
        source="constructed",
        source_id="directive:sync:power",
        native_name="Netclass",
        x=10.0,
        y=20.0,
        metadata={"raw": "Timing"},
    )
    sync.directives = [sync_directive]

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
            physical_designator="U1.1",
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
            directives=[sync_directive],
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
    control_bus = Bus(
        id="bus:control",
        name="CTRL{SYNC RESET}",
        kind=BusKind.GROUP,
        members=[sync, reset_control],
        metadata={"source_format": "constructed", "source_id": "control/bus-1"},
    )

    return Schematic(
        name="Constructed SQL",
        pages=[power_page, control_page],
        components=[controller, duplicate_a, duplicate_b],
        nets=[sync, reset_power, reset_control],
        buses=[control_bus],
    )


def _constructed_pcb() -> Board:
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
    builder.add_pad_object(
        PcbPad(
            id="pad:J1:1",
            number="1",
            x=1.0,
            y=1.0,
            stack=PadStack.simple("rect", 1.0, 1.0),
            pad_type=PcbPadType.SMD,
            layers=(front,),
            net=reset,
            footprint=connector,
        )
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
    via_drill = builder.add_drill_object(
        PcbDrill(
            id="drill:via:1",
            x=5.0,
            y=0.0,
            diameter=0.4,
            plating=PcbDrillPlating.PLATED,
            layers=(front, back),
        )
    )
    builder.add_via_object(
        PcbVia(
            id="via:1",
            x=5.0,
            y=0.0,
            stack=PadStack.simple("circle", 0.8, 0.8),
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
        boards=[_constructed_pcb()],
        net_classes=[
            NetClass(
                name="TIMING",
                clearance_mm=0.15,
                trace_width_mm=0.2,
                via_diameter_mm=0.5,
                via_drill_mm=0.25,
                members=["SYNC"],
                properties={"source_format": "constructed"},
            )
        ],
        design_rules=[
            DesignRule(
                name="Timing clearance",
                kind="clearance",
                priority=1,
                scope1="InNetClass('TIMING')",
                min_value_mm=0.15,
                properties={"source_format": "constructed"},
            )
        ],
        diff_pairs=[
            DiffPair(
                name="USB",
                positive_net="USB_P",
                negative_net="USB_N",
                properties={"source_format": "constructed"},
            )
        ],
    )
    con = load_database(project)
    try:
        yield con
    finally:
        con.close()


def test_sql_exports_variants_and_effective_component_state() -> None:
    component = Component(id="component:r1", reference="R1", part="10k", description="")
    variant = Variant(
        name="no-r1",
        order=1,
        overrides=[
            VariantOverride(
                variant_name="no-r1",
                target=VariantTarget(kind=VariantTargetKind.COMPONENT, object_id="component:r1"),
                field=VariantField.FITTED,
                value=False,
                native_kind="altium_not_fitted",
            ),
            VariantOverride(
                variant_name="no-r1",
                target=VariantTarget(kind=VariantTargetKind.COMPONENT, object_id="component:r1"),
                field=VariantField.EXCLUDE_FROM_SIMULATION,
                value=True,
                native_kind="kicad_exclude_from_sim",
            ),
        ],
    )
    project = Project(
        name="Variant SQL",
        schematic=Schematic(name="Variant SQL", components=[component]),
        variants=[variant],
    )
    materialize_project_variant(project, variant_name="no-r1")

    con = load_database(project)
    try:
        component_row = con.execute(
            """
            SELECT dnp, dnp_source, exclude_from_simulation
            FROM components
            WHERE component_id = 'component:r1'
            """
        ).fetchone()
        assert component_row == (True, "active_variant", True)
        assert con.execute(
            "SELECT value FROM project WHERE key = 'selected_variant'"
        ).fetchone() == ("no-r1",)
        variant_row = con.execute(
            """
            SELECT variant_name, active, override_count, not_fitted_count
            FROM project_variants
            """
        ).fetchone()
        assert variant_row == ("no-r1", True, 2, 1)
        override_rows = con.execute(
            """
            SELECT field, applied, value, base_value
            FROM variant_overrides
            ORDER BY ord
            """
        ).fetchall()
        assert override_rows == [
            ("fitted", True, "false", "true"),
            ("exclude_from_simulation", True, "true", "false"),
        ]
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
            SELECT component_id, reference, page_id, page_name, scope_path, source_id,
                   part_id, physical_designator
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
                None,
            ),
            (
                "component:u1",
                "U1",
                "page:power",
                "Power",
                "/root/power",
                "power/U1A",
                "A",
                "U1.1",
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

    def test_component_enrichment_tables(self, constructed_db: duckdb.DuckDBPyConnection) -> None:
        component_row = constructed_db.execute(
            """
            SELECT lib_symbol, lib_library, dnp, dnp_source
            FROM components
            WHERE component_id = 'component:u1'
            """
        ).fetchone()
        dnp_row = constructed_db.execute(
            """
            SELECT dnp, dnp_source
            FROM components
            WHERE component_id = 'component:control:u7'
            """
        ).fetchone()
        parameter_rows = constructed_db.execute(
            """
            SELECT name, value, visible
            FROM component_parameters
            WHERE component_id = 'component:u1'
            ORDER BY ord
            """
        ).fetchall()
        footprint_rows = constructed_db.execute(
            """
            SELECT name, library, is_current, description
            FROM component_footprints
            WHERE component_id = 'component:u1'
            ORDER BY ord
            """
        ).fetchall()
        part_rows = constructed_db.execute(
            """
            SELECT manufacturer, number
            FROM component_part_numbers
            WHERE component_id = 'component:u1'
            ORDER BY ord
            """
        ).fetchall()

        assert component_row == ("MCU_ST:STM32H743VI", "MCU_ST", False, None)
        assert dnp_row == (True, "explicit")
        assert parameter_rows == [
            ("MPN", "STM32H743VI", True),
            ("Voltage", "3.3V", False),
        ]
        assert footprint_rows == [
            ("Package_QFP:LQFP-100_14x14mm_P0.5mm", "Package_QFP", True, "LQFP-100"),
        ]
        assert part_rows == [("ST", "STM32H743VIT6")]

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

        directive_rows = constructed_db.execute(
            """
            SELECT
                directive_id, net_id, net_name, occurrence_id, page_id, scope_path,
                kind, value, source, source_id, native_name, x, y, metadata
            FROM schematic_directives
            ORDER BY directive_id
            """
        ).fetchall()
        assert directive_rows == [
            (
                "net-occurrence:sync:power:directive:0001",
                "net:sync",
                "SYNC",
                "net-occurrence:sync:power",
                "page:power",
                "/root/power",
                "net_class",
                "TIMING",
                "constructed",
                "directive:sync:power",
                "Netclass",
                10.0,
                20.0,
                '{"raw":"Timing"}',
            ),
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

    def test_buses_and_members_are_normalized(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        bus_rows = constructed_db.execute(
            """
            SELECT bus_id, name, kind, member_count, members
            FROM buses
            ORDER BY bus_id
            """
        ).fetchall()
        assert bus_rows == [
            ("bus:control", "CTRL{SYNC RESET}", "group", 2, "SYNC,RESET"),
        ]

        member_rows = constructed_db.execute(
            """
            SELECT bus_id, name, kind, net_id, net_name, ord
            FROM bus_members
            ORDER BY bus_id, ord
            """
        ).fetchall()
        assert member_rows == [
            ("bus:control", "CTRL{SYNC RESET}", "group", "net:sync", "SYNC", 1),
            ("bus:control", "CTRL{SYNC RESET}", "group", "net:reset:control", "RESET", 2),
        ]

    def test_project_rules_and_net_classes_are_loaded(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        net_class_rows = constructed_db.execute(
            """
            SELECT name, clearance_mm, trace_width_mm, via_diameter_mm, via_drill_mm, properties
            FROM net_classes
            ORDER BY name
            """
        ).fetchall()
        member_rows = constructed_db.execute(
            "SELECT net_name, net_class FROM net_class_members ORDER BY net_name"
        ).fetchall()
        design_rule_rows = constructed_db.execute(
            """
            SELECT name, kind, priority, scope1, min_value_mm, properties
            FROM design_rules
            ORDER BY name
            """
        ).fetchall()
        diff_pair_rows = constructed_db.execute(
            """
            SELECT name, positive_net, negative_net, properties
            FROM diff_pairs
            ORDER BY name
            """
        ).fetchall()
        net_row = constructed_db.execute(
            "SELECT name, net_class FROM nets WHERE net_id = 'net:sync'"
        ).fetchone()

        assert net_class_rows == [
            ("TIMING", 0.15, 0.2, 0.5, 0.25, '{"source_format":"constructed"}')
        ]
        assert member_rows == [("SYNC", "TIMING")]
        assert design_rule_rows == [
            (
                "Timing clearance",
                "clearance",
                1,
                "InNetClass('TIMING')",
                0.15,
                '{"source_format":"constructed"}',
            )
        ]
        assert diff_pair_rows == [("USB", "USB_P", "USB_N", '{"source_format":"constructed"}')]
        assert net_row == ("SYNC", "TIMING")

    def test_page_annotations_are_loaded(self, constructed_db: duckdb.DuckDBPyConnection) -> None:
        rows = constructed_db.execute(
            """
            SELECT page_id, page_name, ord, text
            FROM page_annotations
            ORDER BY page_id, ord
            """
        ).fetchall()

        assert rows == [
            ("page:control", "Control", 1, "Control note"),
            ("page:power", "Power", 1, "First note"),
            ("page:power", "Power", 2, "Second note"),
        ]

    def test_title_blocks_are_loaded(self, constructed_db: duckdb.DuckDBPyConnection) -> None:
        rows = constructed_db.execute(
            """
            SELECT title, revision, date, organization, sheet_number, sheet_total, comments
            FROM title_blocks
            ORDER BY page_id
            """
        ).fetchall()

        assert rows == [
            ("Power Sheet", "A", "2026-01-02", "Phosphor", "1", "2", '{"1":"constructed"}'),
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
                'pin_occurrence_metadata',
                'schematic_directives'
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
            ("idx_schematic_directives_kind",),
            ("idx_schematic_directives_net_id",),
            ("idx_schematic_directives_occurrence_id",),
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
                FROM schematic_directives sd
                LEFT JOIN net_occurrences no ON no.occurrence_id = sd.occurrence_id
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
                FROM schematic_directives sd
                LEFT JOIN nets n ON n.net_id = sd.net_id
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
    project = Project(name=SWD_SWITCH_PCB.stem, boards=[parse_kicad_pcb(SWD_SWITCH_PCB)])
    con = load_database(project)
    try:
        yield con
    finally:
        con.close()


@pytest.fixture(scope="module")
def altium_db() -> Iterator[duckdb.DuckDBPyConnection]:
    project = load_project(PI_MX8_PRJPCB)
    con = load_database(project)
    try:
        yield con
    finally:
        con.close()


@pytest.fixture(scope="module")
def allegro_db() -> Iterator[duckdb.DuckDBPyConnection]:
    project = load_allegro_pcb_project(ALLEGRO_BREAKOUT_BRD)
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

    def test_artwork_geometry_is_loaded(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM artwork WHERE geom IS NOT NULL") > 0

    def test_board_profile_has_geometry(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM board_profile WHERE geom IS NOT NULL") == 16


class TestPadsAndVias:
    def test_constructed_pad_and_via_stack_columns(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        pad_row = constructed_db.execute(
            "SELECT stack_mode, copper_layers FROM pads WHERE id = 'pad:J1:1'"
        ).fetchone()
        via_row = constructed_db.execute(
            "SELECT stack_mode, copper_layers FROM vias WHERE id = 'via:1'"
        ).fetchone()

        assert pad_row == ("simple", ["F.Cu"])
        assert via_row == ("simple", ["F.Cu", "B.Cu"])

    def test_unconnected_pads_use_nullable_net(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads WHERE net_number IS NULL") > 0
        assert _count(db, "SELECT count(*) FROM pads WHERE net_number = 0") == 0

    def test_pads_join_to_drills(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads p JOIN drills d ON p.drill_id = d.id") > 0

    def test_vias_join_to_drills(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM vias v JOIN drills d ON v.drill_id = d.id") == 49

    def test_pad_geometry_is_loaded(self, db: duckdb.DuckDBPyConnection) -> None:
        assert _count(db, "SELECT count(*) FROM pads WHERE geom IS NULL") == 0


def test_altium_free_pad_mask_apertures_are_queryable_without_freepads_footprint(
    altium_db: duckdb.DuckDBPyConnection,
) -> None:
    rows = altium_db.execute(
        """
        SELECT reference, mask_aperture_width, mask_aperture_height, mask_aperture_source
        FROM pads
        WHERE reference IS NULL
          AND pad_number = 'MT'
          AND mask_aperture_source IS NOT NULL
        ORDER BY x, y
        """
    ).fetchall()

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

    def test_layer_count_without_stackup_uses_pcb_layers(
        self, constructed_db: duckdb.DuckDBPyConnection
    ) -> None:
        # The constructed project has no stackup metadata; layer_count must
        # still reflect the two copper layers on the Board itself.
        row = constructed_db.execute("SELECT layer_count FROM board").fetchone()
        assert row is not None
        assert row[0] == 2


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
    project = load_project(ORANGECRAB_PRO)
    con = load_database(project)
    try:
        assert _count(con, "SELECT count(*) FROM keepouts") > 0
    finally:
        con.close()


def test_altium_typed_tables_are_populated(altium_db: duckdb.DuckDBPyConnection) -> None:
    assert _count(altium_db, "SELECT count(*) FROM drills") > 0
    assert _count(altium_db, "SELECT count(*) FROM conductors") > 0
    assert _count(altium_db, "SELECT count(*) FROM artwork") > 0
    assert _count(altium_db, "SELECT count(*) FROM board_profile") > 0
    assert _count(altium_db, "SELECT count(*) FROM net_classes") > 0
    assert _count(altium_db, "SELECT count(*) FROM design_rules") > 0
    assert _count(altium_db, "SELECT count(*) FROM nets WHERE net_class IS NOT NULL") > 0


def test_allegro_breakout_typed_tables_are_populated(
    allegro_db: duckdb.DuckDBPyConnection,
) -> None:
    assert _count(allegro_db, "SELECT count(*) FROM boards") == 1
    assert _count(allegro_db, "SELECT count(*) FROM layers") == 186
    assert _count(allegro_db, "SELECT count(*) FROM footprints") == 68
    assert _count(allegro_db, "SELECT count(*) FROM pads") == 364
    assert _count(allegro_db, "SELECT count(*) FROM vias") == 178
    assert _count(allegro_db, "SELECT count(*) FROM drills") == 288
    assert _count(allegro_db, "SELECT count(*) FROM conductors") == 1619
    assert _count(allegro_db, "SELECT count(*) FROM artwork") == 19652
    assert _count(allegro_db, "SELECT count(*) FROM board_profile") == 4
    assert _count(allegro_db, "SELECT count(*) FROM keepouts") == 0


def test_allegro_breakout_sql_views_read_typed_board_collections(
    allegro_db: duckdb.DuckDBPyConnection,
) -> None:
    route_count = _count(
        allegro_db,
        "SELECT count(*) FROM net_routes WHERE trace_length_mm > 0",
    )
    width_violation_count = _count(allegro_db, "SELECT count(*) FROM width_violations")
    drill_histogram_rows = allegro_db.execute(
        """
        SELECT round(drill_mm, 4), source, count
        FROM drill_histogram
        ORDER BY drill_mm, source
        """
    ).fetchall()

    assert route_count == 65
    assert width_violation_count == 33
    assert drill_histogram_rows == [
        (0.3048, "via", 178),
        (0.7, "pad", 5),
        (0.889, "pad", 16),
        (0.9, "pad", 12),
        (0.92, "pad", 4),
        (1.0, "pad", 4),
        (1.016, "pad", 48),
        (1.25, "pad", 3),
        (1.3, "pad", 4),
        (1.4, "pad", 4),
        (1.75, "pad", 1),
        (2.286, "pad", 3),
        (2.3, "pad", 2),
        (4.5, "pad", 4),
    ]


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


@pytest.mark.behavior_lock
class TestJetsonNetClasses:
    def test_count(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        assert _count(jetson_db, "SELECT count(*) FROM net_classes") == 8


@pytest.mark.behavior_lock
class TestJetsonDesignRules:
    def test_count(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        assert _count(jetson_db, "SELECT count(*) FROM design_rules") == 33


@pytest.mark.behavior_lock
class TestJetsonComponentEnrichment:
    def test_component_parameters_loaded(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        components = _count(jetson_db, "SELECT count(*) FROM components")
        count = _count(jetson_db, "SELECT count(*) FROM component_parameters")
        # Every KiCad symbol carries at least the four mandatory properties.
        assert count >= components * 4

    def test_parameter_join_returns_mpn(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        row = jetson_db.execute(
            """
            SELECT p.value FROM component_parameters p
            JOIN components c USING (component_id)
            WHERE c.reference = 'C52' AND p.name = 'MPN'
            """
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert str(row[0]).strip() != ""

    def test_component_footprints_loaded(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        count = _count(jetson_db, "SELECT count(*) FROM component_footprints WHERE is_current")
        assert count > 0

    def test_part_numbers_loaded(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        count = _count(jetson_db, "SELECT count(*) FROM component_part_numbers")
        assert count > 0

    def test_dnp_components_flagged(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        count = _count(
            jetson_db,
            "SELECT count(*) FROM components WHERE dnp AND dnp_source = 'explicit'",
        )
        assert count > 0

    def test_lib_columns_populated(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        count = _count(jetson_db, "SELECT count(*) FROM components WHERE lib_symbol IS NOT NULL")
        assert count > 0

    def test_title_blocks_loaded(self, jetson_db: duckdb.DuckDBPyConnection) -> None:
        column_rows = jetson_db.execute("PRAGMA table_info('title_blocks')").fetchall()
        columns = {str(row[1]) for row in column_rows}
        assert {
            "title",
            "revision",
            "date",
            "organization",
            "org_address",
            "document_number",
            "sheet_number",
            "sheet_total",
            "author",
            "drawn_by",
            "checked_by",
            "approved_by",
            "created_date",
            "modified_date",
            "cage_code",
            "comments",
            "metadata",
        }.issubset(columns)

        row = jetson_db.execute(
            "SELECT title, organization, sheet_number, sheet_total "
            "FROM title_blocks JOIN pages USING (page_id) LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert str(row[0]).strip() != ""
        assert len(row) == 4


@pytest.mark.behavior_lock
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
    def test_basic_query(self, tmp_path: Path) -> None:
        project_file = _write_swd_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["-P", str(project_file), "sql", "SELECT count(*) FROM footprints"]
        )
        assert result.exit_code == 0
        assert "(1 row)" in result.output

    def test_schema_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["sql", "--schema"])
        assert result.exit_code == 0
        assert "CREATE TABLE footprints" in result.output

    def test_no_query_error(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["sql"])
        assert result.exit_code != 0

    def test_invalid_query(self, tmp_path: Path) -> None:
        project_file = _write_swd_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["-P", str(project_file), "sql", "SELECT * FROM nonexistent"],
        )
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    def test_spatial_query(self, tmp_path: Path) -> None:
        project_file = _write_swd_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["-P", str(project_file), "sql", "SELECT ST_Area(geom) FROM footprints LIMIT 1"],
        )
        assert result.exit_code == 0
        # Should contain a numeric value (the area)
        lines = result.output.strip().split("\n")
        # Header + separator + 1 data row + "(1 row)" footer
        assert len(lines) >= 3

    def test_statement_without_result_set_does_not_crash(self, tmp_path: Path) -> None:
        # A comment-only statement executes but yields no result set (DuckDB
        # returns no cursor); the command must report that cleanly, not crash on
        # a missing description.
        project_file = _write_swd_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["-P", str(project_file), "sql", "--", "-- just a comment"])
        assert result.exit_code == 0, result.output
        assert result.exception is None
        assert "no result set" in result.output.lower()


def test_spatial_engine_error_mentions_network_requirement() -> None:
    from phosphor_eda.cli import _spatial_engine_error

    exc = duckdb.IOException("Failed to download extension 'spatial'")
    translated = _spatial_engine_error(exc)
    message = translated.format_message().lower()
    assert "network" in message
    assert "spatial" in message
