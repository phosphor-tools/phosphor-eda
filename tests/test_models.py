from ecad_tools.dsn.models import ParsedDesign, PlacedInstance, Wire


def test_parsed_design_defaults():
    d = ParsedDesign()
    assert d.instances == []
    assert d.wires == []
    assert d.page_name == ""


def test_placed_instance_defaults():
    inst = PlacedInstance()
    assert inst.reference == ""
    assert inst.pin_connections == []


def test_wire_defaults():
    w = Wire()
    assert w.wire_id == 0
    assert w.aliases == []
    assert w.is_bus is False
