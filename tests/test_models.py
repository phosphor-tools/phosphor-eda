from phosphor_eda.dsn.models import ParsedDesign, PlacedInstance, SchematicPage, Wire


def test_parsed_design_defaults():
    d = ParsedDesign()
    assert d.pages == []
    assert d.string_list == []


def test_schematic_page_defaults():
    p = SchematicPage()
    assert p.name == ""
    assert p.instances == []
    assert p.nets == []
    assert p.wire_net_map == {}


def test_placed_instance_defaults():
    inst = PlacedInstance()
    assert inst.reference == ""
    assert inst.pin_connections == []


def test_wire_defaults():
    w = Wire()
    assert w.wire_id == 0
    assert w.aliases == []
    assert w.is_bus is False
