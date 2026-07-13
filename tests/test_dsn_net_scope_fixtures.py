"""OrCAD DSN load regressions for legacy standalone fixtures.

These fixtures are bare DSN exports without their original OPJ, packaged
netlist, or PCB sidecars. They are useful parser smoke tests, but they are not
net-scope oracles; complete OrCAD fixture trees live under fixtures/orcad.
"""

from fixture_paths import FIXTURES

from phosphor_eda.domain.schematic import Net
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design

PICO_W_DSN = FIXTURES / "dsn" / "raspberry-pi-pico-w" / "RPI-PICOW-R2.DSN"
CMIO_DSN = FIXTURES / "dsn" / "raspberry-pi-cmio" / "RPI-CMIO-V3_0-PUBLIC.DSN"


def _nets_by_name(nets: list[Net], name: str) -> list[Net]:
    return [net for net in nets if net.name == name]


def test_picow_standalone_fixture_loads_named_wifi_gpio_net() -> None:
    raw = parse_dsn(PICO_W_DSN)
    design = dsn_to_design(raw, name="PicoW")
    wl_gpio0_nets = _nets_by_name(design.nets, "WL_GPIO0")

    assert wl_gpio0_nets
    assert {page.name for net in wl_gpio0_nets for page in net.pages} == {"RP2040", "Wifi"}
    assert all(len(net.pins) >= 1 for net in wl_gpio0_nets)


def test_cmio_standalone_fixture_loads_large_named_ground_net() -> None:
    raw = parse_dsn(CMIO_DSN)
    design = dsn_to_design(raw, name="CMIO")
    gnd_nets = _nets_by_name(design.nets, "GND")

    assert gnd_nets
    assert {
        "PAGE2 - PWR, CM, GPIO, JTAG",
        "PAGE3 - CSI, DSI, HDMI, USB",
    } == {page.name for net in gnd_nets for page in net.pages}
    assert sum(len(net.pins) for net in gnd_nets) > 30
