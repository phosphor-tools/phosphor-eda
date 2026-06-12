"""Tests for Altium signal-harness connectivity resolution.

The pi-mx8 fixture routes WiFi SDIO through a ``WIFI_SDIO`` harness: the CPU
sheet (06_8MPLUS_IO) and the WiFi module sheet (11_WIFI_BLE_Module) each have a
harness connector + harness port, joined on the block diagram by a signal
harness wire between the two sheet symbols' harness entries. The two sheets
declare the harness members in different orders, so resolution must match
members by name, not position.
"""

from pathlib import Path

from phosphor_eda.formats.altium.source import load_project_source_sheets
from phosphor_eda.formats.altium.to_schematic import altium_to_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PIMX8_PRJPCB = FIXTURES / "altium/pi-mx8/PiMX8MP_r0.3_release.PrjPcb"

SDIO_MEMBERS = (
    "SDIO_CLK",
    "SDIO_CMD",
    "SDIO_DATA_0",
    "SDIO_DATA_1",
    "SDIO_DATA_2",
    "SDIO_DATA_3",
)


def _sheet_by_name(sheets, name):
    return next(sheet for sheet in sheets.values() if sheet.name == name)


def test_harness_connector_matched_to_port():
    """Each harness connector resolves the harness port it feeds."""
    _project, sheets = load_project_source_sheets(PIMX8_PRJPCB)
    wifi = _sheet_by_name(sheets, "11_WIFI_BLE_Module")

    connector = next(c for c in wifi.harness_connectors if c.harness_type == "WIFI_SDIO")
    assert connector.port_name == "WIFI_SDIO"


def test_harness_members_attach_to_local_nets():
    """Harness entries land in the local net at their wire-side coordinate."""
    _project, sheets = load_project_source_sheets(PIMX8_PRJPCB)
    wifi = _sheet_by_name(sheets, "11_WIFI_BLE_Module")

    sdio_clk = next(
        local_net
        for local_net in wifi.local_nets
        if any(label.name == "SDIO_CLK" for label in local_net.net_labels)
    )
    assert [member.name for member in sdio_clk.harness_members] == ["SDIO_CLK"]
    assert all(member.coord != (0, 0) for member in sdio_clk.harness_members)
    assert all(member.port_name == "WIFI_SDIO" for member in sdio_clk.harness_members)


def test_signal_harness_wires_join_sheet_entries():
    """Harness entries joined by a signal harness wire share a local net."""
    _project, sheets = load_project_source_sheets(PIMX8_PRJPCB)
    block = _sheet_by_name(sheets, "01_Block_Diagram")

    joined = [
        local_net
        for local_net in block.local_nets
        if sum(
            1
            for entry in local_net.sheet_entries
            if entry.harness_type and entry.name == "WIFI_SDIO"
        )
        >= 2
    ]
    assert len(joined) == 1


def test_harness_nets_resolve_across_sheets():
    """Each WIFI_SDIO member resolves to one net spanning both child sheets."""
    design = altium_to_design(PIMX8_PRJPCB)

    expected_pins = {
        "SDIO_CLK": {"U2.W28", "U10.33"},
        "SDIO_CMD": {"U2.W29", "U10.28", "R107.1"},
        "SDIO_DATA_0": {"U2.Y29", "U10.30", "R111.1"},
        "SDIO_DATA_1": {"U2.Y28", "U10.29", "R110.1"},
        "SDIO_DATA_2": {"U2.V29", "U10.32", "R109.1"},
        "SDIO_DATA_3": {"U2.V28", "U10.31", "R108.1"},
    }

    for member in SDIO_MEMBERS:
        nets = [net for net in design.nets if net.name == member]
        assert len(nets) == 1, f"{member} should resolve to exactly one net, got {len(nets)}"
        net = nets[0]
        pins = {f"{pin.component.reference}.{pin.designator}" for pin in net.pins}
        assert expected_pins[member] <= pins, f"{member}: {pins}"
        pages = {page.name for page in net.pages}
        assert {"06_8MPLUS_IO", "11_WIFI_BLE_Module"} <= pages
