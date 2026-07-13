"""Tests for Altium .PrjPcb project parser."""

import textwrap
from pathlib import Path

import pytest

from phosphor_eda.formats.altium.project import AltiumHierarchyMode, parse_prjpcb, parse_prjpcb_file

FIXTURES = Path(__file__).resolve().parent / "fixtures"
QFSAE_PRJPCB = FIXTURES / "altium/qfsae-debugger/Debugger.PrjPcb"


def test_parse_prjpcb_from_string():
    content = textwrap.dedent("""\
        [Design]
        HierarchyMode=3
        ChannelRoomNamingStyle=0

        [Document1]
        DocumentPath=Main.SchDoc

        [Document2]
        DocumentPath=Power.SchDoc

        [Document3]
        DocumentPath=Board.PcbDoc
    """)
    project = parse_prjpcb(content)
    assert project.hierarchy_mode is AltiumHierarchyMode.GLOBAL
    assert project.schematic_paths == ["Main.SchDoc", "Power.SchDoc"]


def test_parse_prjpcb_filters_non_schematic():
    content = textwrap.dedent("""\
        [Design]
        HierarchyMode=1

        [Document1]
        DocumentPath=Sheet1.SchDoc

        [Document2]
        DocumentPath=Board.PcbDoc

        [Document3]
        DocumentPath=Sheet2.SchDoc
    """)
    project = parse_prjpcb(content)
    assert project.schematic_paths == ["Sheet1.SchDoc", "Sheet2.SchDoc"]
    assert project.hierarchy_mode is AltiumHierarchyMode.FLAT


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, AltiumHierarchyMode.SMART),
        (1, AltiumHierarchyMode.FLAT),
        (2, AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL),
        (3, AltiumHierarchyMode.GLOBAL),
        (4, AltiumHierarchyMode.HIERARCHICAL_POWER_LOCAL),
    ],
)
def test_parse_prjpcb_maps_hierarchy_mode_to_enum(value: int, expected: AltiumHierarchyMode):
    content = textwrap.dedent(f"""\
        [Design]
        HierarchyMode={value}
    """)

    project = parse_prjpcb(content)

    assert project.hierarchy_mode is expected


@pytest.mark.parametrize("value", ["99", "unknown"])
def test_parse_prjpcb_rejects_unknown_hierarchy_mode(value: str):
    content = textwrap.dedent(f"""\
        [Design]
        HierarchyMode={value}
    """)

    with pytest.raises(ValueError, match="HierarchyMode"):
        parse_prjpcb(content)


def test_parse_prjpcb_parses_connectivity_booleans():
    content = textwrap.dedent("""\
        [Design]
        AllowPortNetNames=0
        AllowSheetEntryNetNames=1
        AppendSheetNumberToLocalNets=true
        NameNetsHierarchically=false
        NetlistSinglePinNets=1
        PowerPortNamesTakePriority=1
    """)

    project = parse_prjpcb(content)

    assert project.allow_port_net_names is False
    assert project.allow_sheet_entry_net_names is True
    assert project.append_sheet_number_to_local_nets is True
    assert project.name_nets_hierarchically is False
    assert project.netlist_single_pin_nets is True
    assert project.power_port_names_take_priority is True


def test_parse_real_prjpcb():
    project = parse_prjpcb_file(str(QFSAE_PRJPCB))
    assert project.hierarchy_mode is AltiumHierarchyMode.SMART
    assert project.allow_port_net_names is False
    assert project.allow_sheet_entry_net_names is True
    assert project.append_sheet_number_to_local_nets is False
    assert project.name_nets_hierarchically is False
    assert project.netlist_single_pin_nets is False
    assert project.power_port_names_take_priority is False
    assert len(project.schematic_paths) == 4
    assert "TOP.SchDoc" in project.schematic_paths
    assert "MCU.SchDoc" in project.schematic_paths
