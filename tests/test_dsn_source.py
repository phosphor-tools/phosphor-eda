"""Tests for OrCAD DSN source connectivity extraction."""

from phosphor_eda.formats.dsn.to_schematic import dsn_to_source
from phosphor_eda.formats.common.raw_models import (
    GraphicInst,
    NetIdMapping,
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    SchematicPage,
    Wire,
    WireAlias,
)


def test_graphic_connectors_at_mapped_coordinates_become_source_objects() -> None:
    page = SchematicPage(
        name="Main",
        nets=[PageNetEntry(name="Reset_N", net_id=42)],
        globals=[GraphicInst(name="VCC_BAR", db_id=1001, loc_x=10, loc_y=20)],
        off_page_connectors=[GraphicInst(name="ToWifi", db_id=1002, loc_x=30, loc_y=40)],
        wire_net_map={(10, 20): {42}, (30, 40): {42}},
    )
    source = dsn_to_source(ParsedDesign(pages=[page]), name="Board")
    source_page = source.pages[0]

    assert source.name == "Board"
    assert [global_.name for global_ in source_page.globals] == ["VCC_BAR"]
    assert [connector.name for connector in source_page.off_page_connectors] == ["ToWifi"]
    assert source_page.globals[0].local_net_id == "page:Main:net:42"
    assert source_page.off_page_connectors[0].local_net_id == "page:Main:net:42"


def test_wire_aliases_are_preserved_as_alias_source_objects() -> None:
    page = SchematicPage(
        name="Main",
        nets=[PageNetEntry(name="SCL", net_id=7)],
        wires=[
            Wire(
                wire_id=7,
                start_x=1,
                start_y=2,
                end_x=3,
                end_y=4,
                aliases=[WireAlias(name="I2C_CLK", x=2, y=2)],
            ),
        ],
        wire_net_map={(1, 2): {7}, (3, 4): {7}},
    )
    source_page = dsn_to_source(ParsedDesign(pages=[page])).pages[0]

    assert [wire.local_net_id for wire in source_page.wires] == ["page:Main:net:7"]
    assert [alias.name for alias in source_page.wires[0].aliases] == ["I2C_CLK"]
    assert source_page.wires[0].aliases[0].name_key == "i2c_clk"


def test_page_net_ids_and_pin_net_ids_survive_as_authoritative_local_connectivity() -> None:
    page = SchematicPage(
        name="Main",
        nets=[PageNetEntry(name="MISO", net_id=99)],
        instances=[
            PlacedInstance(
                package_name="U.Normal",
                db_id=501,
                reference="U1",
                pin_connections=[
                    PinConnection(pin_number="1", pin_x=10, pin_y=20, net_id=99),
                ],
            ),
        ],
        wire_net_map={(10, 20): {123}},
    )
    raw = ParsedDesign(pages=[page], symbol_pin_names={"U": ["SO"]})
    source_page = dsn_to_source(raw).pages[0]

    assert source_page.nets[0].net_id == 99
    assert source_page.nets[0].id == "page:Main:net:99"
    assert source_page.pin_occurrences[0].local_net_id == "page:Main:net:99"
    assert source_page.pin_occurrences[0].pin_name == "SO"


def test_source_spelling_is_preserved_and_comparison_keys_are_case_folded() -> None:
    page = SchematicPage(
        name="MixedCase",
        nets=[PageNetEntry(name="Usb_Dp", net_id=5)],
        ports=[GraphicInst(name="PORT_A", db_id=10, loc_x=1, loc_y=1)],
        wire_net_map={(1, 1): {5}},
    )
    raw = ParsedDesign(
        pages=[page],
        net_id_mappings=[NetIdMapping(db_id=5, name="Usb_Dp")],
    )
    source = dsn_to_source(raw)

    assert source.pages[0].nets[0].name == "Usb_Dp"
    assert source.pages[0].nets[0].name_key == "usb_dp"
    assert source.pages[0].ports[0].name == "PORT_A"
    assert source.pages[0].ports[0].name_key == "port_a"
    assert source.hierarchy_mappings[0].name == "Usb_Dp"
    assert source.hierarchy_mappings[0].name_key == "usb_dp"
