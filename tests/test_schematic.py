"""Tests for schematic dataclass defaults and basic relationships."""

from phosphor_eda.domain.schematic import Component, Net, Page, Pin, Schematic, ScopeId


def test_pin_defaults() -> None:
    comp = Component(id="component-u1", reference="U1", part="MCU", description="")
    pin = Pin(id="component-u1-1", designator="1", name="VCC", component=comp)

    assert pin.id == "component-u1-1"
    assert pin.designator == "1"
    assert pin.name == "VCC"
    assert pin.component is comp
    assert pin.net is None
    assert pin.no_connect is False


def test_component_with_pins() -> None:
    comp = Component(id="component-u7", reference="U7", part="AD7768-1", description="ADC")
    pin = Pin(id="component-u7-10", designator="10", name="SCLK", component=comp)

    comp.pins.append(pin)

    assert comp.pins == [pin]
    assert pin.component is comp


def test_net_connects_pins() -> None:
    comp_a = Component(id="component-u1", reference="U1", part="MCU", description="")
    comp_b = Component(id="component-u7", reference="U7", part="ADC", description="")
    net = Net(id="net-adc-sclk", name="ADC_SCLK")
    pin_a = Pin(
        id="component-u1-l3",
        designator="L3",
        name="LPSPI1_SCK",
        component=comp_a,
        net=net,
    )
    pin_b = Pin(
        id="component-u7-10",
        designator="10",
        name="SCLK",
        component=comp_b,
        net=net,
    )

    net.pins.extend([pin_a, pin_b])

    assert net.pins == [pin_a, pin_b]
    assert net.pins[0].component.reference == "U1"
    assert net.pins[1].component.reference == "U7"


def test_page_defaults() -> None:
    page = Page(id="page-adc", name="ADC")

    assert page.id == "page-adc"
    assert page.name == "ADC"
    assert page.source_file == ""
    assert page.scope_id == ScopeId(path=())
    assert page.components == []
    assert page.nets == []


def test_design_holds_pages() -> None:
    page = Page(id="page-adc", name="ADC")
    design = Schematic(name="TEST", pages=[page])

    assert design.name == "TEST"
    assert design.pages == [page]


def test_pin_no_connect() -> None:
    comp = Component(id="component-u7", reference="U7", part="ADC", description="")
    pin = Pin(
        id="component-u7-26",
        designator="26",
        name="AIN-",
        component=comp,
        no_connect=True,
    )

    assert pin.no_connect is True
    assert pin.net is None


def test_net_bus_property() -> None:
    net = Net(id="net-data0", name="DATA0", bus="DATA[0..7]")

    assert net.bus == "DATA[0..7]"


def test_scope_id_string_is_path_like() -> None:
    assert str(ScopeId(path=())) == "/"
    assert str(ScopeId(path=("root", "sheet-a"))) == "/root/sheet-a"
