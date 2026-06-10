"""OrCAD DSN fixture regressions for resolved net scope behavior."""

from pathlib import Path

from phosphor_eda.domain.schematic import Net
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PICO_W_DSN = FIXTURES / "dsn" / "raspberry-pi-pico-w" / "RPI-PICOW-R2.DSN"
CMIO_DSN = FIXTURES / "dsn" / "raspberry-pi-cmio" / "RPI-CMIO-V3_0-PUBLIC.DSN"


def _nets_by_name(nets: list[Net], name: str) -> list[Net]:
    return [net for net in nets if net.name == name]


def test_picow_fixture_keeps_same_named_page_nets_distinct_without_global_evidence() -> None:
    raw = parse_dsn(PICO_W_DSN)
    design = dsn_to_design(raw, name="PicoW")
    wl_gpio0_nets = _nets_by_name(design.nets, "WL_GPIO0")

    assert len(wl_gpio0_nets) == 2
    assert {page.name for net in wl_gpio0_nets for page in net.pages} == {"RP2040", "Wifi"}
    assert all(len(net.pins) >= 1 for net in wl_gpio0_nets)


def test_cmio_fixture_keeps_same_named_large_power_nets_page_local() -> None:
    raw = parse_dsn(CMIO_DSN)
    design = dsn_to_design(raw, name="CMIO")
    gnd_nets = _nets_by_name(design.nets, "GND")

    assert len(gnd_nets) == 2
    assert {
        "PAGE2 - PWR, CM, GPIO, JTAG",
        "PAGE3 - CSI, DSI, HDMI, USB",
    } == {page.name for net in gnd_nets for page in net.pages}
    assert sum(len(net.pins) for net in gnd_nets) > 30
