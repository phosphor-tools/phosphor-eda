"""Altium net-naming policy: ladder, natural-sort autoname, Nets6 conformance.

Policy source: audit/net-naming-altium.md (4-board corpus vs Nets6 ground
truth). The autoname witnesses below are the corpus cases that distinguish
natural sort from plain ASCII sort.
"""

from pathlib import Path

import pytest

from phosphor_eda.domain.schematic import Net, NetNameKind, ScopeId, TitleBlock
from phosphor_eda.formats.altium.project import AltiumHierarchyMode, AltiumProject
from phosphor_eda.formats.altium.resolver import resolve_altium_source
from phosphor_eda.formats.altium.source import (
    AltiumHarnessMember,
    AltiumLocalNet,
    AltiumNetLabel,
    AltiumPinOccurrence,
    AltiumPort,
    AltiumPowerPort,
    AltiumSheetEntry,
    AltiumSheetSource,
    AltiumSheetSymbol,
    AltiumSourceDesign,
)
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.query.project_loader import load_project

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PIMX8_PRJPCB = FIXTURES / "altium/pi-mx8/PiMX8MP_r0.3_release.PrjPcb"


def _scope(name: str) -> ScopeId:
    return ScopeId(path=(name,))


def _label(sheet: str, name: str, index: int) -> AltiumNetLabel:
    return AltiumNetLabel(
        id=f"{sheet}:label:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=name,
        location=(index, 10),
    )


def _power(sheet: str, name: str, index: int = 1) -> AltiumPowerPort:
    return AltiumPowerPort(
        id=f"{sheet}:power:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=name,
        location=(index, 20),
        style=0,
        orientation=0,
        show_net_name=True,
    )


def _port(sheet: str, name: str, index: int = 1) -> AltiumPort:
    return AltiumPort(
        id=f"{sheet}:port:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=name,
        location=(index, 30),
        wire_coord=(index, 30),
        harness_type="",
        io_type=0,
        style=0,
    )


def _entry(sheet: str, name: str, index: int = 1) -> AltiumSheetEntry:
    return AltiumSheetEntry(
        id=f"{sheet}:entry:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        sheet_symbol_id="",
        name=name,
        coord=(index, 40),
        side=0,
        distance_from_top=0,
        harness_type="",
        io_type=0,
    )


def _symbol(sheet: str, name: str, child_source_file: str, index: int = 1) -> AltiumSheetSymbol:
    return AltiumSheetSymbol(
        id=f"{sheet}:symbol:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        name=name,
        child_source_file=child_source_file,
        location=(index, 70),
        x_size=100,
        y_size=100,
    )


def _harness_member(sheet: str, port_name: str, name: str, index: int = 1) -> AltiumHarnessMember:
    return AltiumHarnessMember(
        id=f"{sheet}:member:{index}",
        scope_id=_scope(sheet),
        source_index=index,
        connector_id=f"{sheet}:connector:1",
        port_name=port_name,
        name=name,
        coord=(index, 50),
        side=0,
        distance_from_top=0,
    )


def _pin(sheet: str, local_net_id: str, reference: str, designator: str) -> AltiumPinOccurrence:
    return AltiumPinOccurrence(
        id=f"{sheet}:pin:{reference}:{designator}",
        scope_id=_scope(sheet),
        source_index=1,
        local_net_id=local_net_id,
        component_source_id=f"{sheet}:component:{reference}",
        component_reference=reference,
        pin_designator=designator,
        pin_name="",
        location=(1, 60),
        tip=(1, 61),
    )


def _local_net(
    sheet: str,
    name: str,
    members: list[tuple[str, str]],
    *,
    labels: list[AltiumNetLabel] | None = None,
    powers: list[AltiumPowerPort] | None = None,
    ports: list[AltiumPort] | None = None,
    entries: list[AltiumSheetEntry] | None = None,
    harness_members: list[AltiumHarnessMember] | None = None,
) -> tuple[AltiumLocalNet, list[AltiumPinOccurrence]]:
    local_net_id = f"{sheet}:local:{name}"
    pins = [_pin(sheet, local_net_id, reference, designator) for reference, designator in members]
    return (
        AltiumLocalNet(
            id=local_net_id,
            scope_id=_scope(sheet),
            wire_points=set(),
            pin_ids=[pin.id for pin in pins],
            net_labels=labels or [],
            power_ports=powers or [],
            ports=ports or [],
            sheet_entries=entries or [],
            harness_members=harness_members or [],
            generated_name="",
        ),
        pins,
    )


def _sheet(
    name: str,
    local_nets: list[AltiumLocalNet],
    pins: list[AltiumPinOccurrence],
    *,
    sheet_number: str = "",
    sheet_symbols: list[AltiumSheetSymbol] | None = None,
) -> AltiumSheetSource:
    return AltiumSheetSource(
        id=f"sheet:{name}",
        name=name,
        source_file=f"{name}.SchDoc",
        scope_id=_scope(name),
        local_nets=local_nets,
        sheet_symbols=sheet_symbols or [],
        sheet_entries=[],
        harness_connectors=[],
        harness_members=[],
        pin_occurrences=pins,
        title_block=TitleBlock(sheet_number=sheet_number) if sheet_number else None,
    )


def _resolve_single_net(
    sheets: list[AltiumSheetSource],
    project: AltiumProject | None = None,
    ctx: ParseContext | None = None,
) -> Net:
    design = resolve_altium_source(
        AltiumSourceDesign(
            name="test",
            project=project or AltiumProject(hierarchy_mode=AltiumHierarchyMode.FLAT),
            sheets={sheet.name: sheet for sheet in sheets},
            root_sheet_name=sheets[0].name,
        ),
        ctx,
    )
    [net] = design.nets
    return net


# ---------------------------------------------------------------------------
# Autoname: Net<designator>_<pin>, natural-sort minimum member pair
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        # Designator digit runs compare numerically (corpus witnesses):
        ([("MN11", "2"), ("MN9", "1")], "NetMN9_1"),
        ([("C12", "2"), ("C9", "1")], "NetC9_1"),
        ([("R102", "2"), ("R96", "2")], "NetR96_2"),
        ([("C144", "2"), ("C48", "2")], "NetC48_2"),
        ([("D11", "1"), ("D2", "1")], "NetD2_1"),
        # Pin digit runs compare numerically too (Kasli: IC6 pins 5 and 14):
        ([("IC6", "14"), ("IC6", "5")], "NetIC6_5"),
        # Non-numeric pins work unchanged (testpoint pin id "TP"):
        ([("U5", "A1"), ("TP45", "TP")], "NetTP45_TP"),
        # BGA pins sort naturally: A3 < A10.
        ([("U5", "A10"), ("U5", "A3")], "NetU5_A3"),
    ],
)
def test_autoname_is_natural_sort_minimum_member(
    members: list[tuple[str, str]], expected: str
) -> None:
    local_net, pins = _local_net("A", "auto", members)
    net = _resolve_single_net([_sheet("A", [local_net], pins)])

    assert net.name == expected
    [name] = net.names
    assert name.kind is NetNameKind.TOOL_AUTO
    assert name.source == "altium:autoname"


# ---------------------------------------------------------------------------
# Canonical label selection: case-insensitive minimum, document-order ties
# ---------------------------------------------------------------------------


def test_canonical_label_is_case_insensitive_alphabetical_minimum() -> None:
    # Plain ASCII min would pick SD_PWRX ('P' < 'p'); Altium compares
    # case-insensitively, so SD_pwr wins.
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        labels=[_label("A", "SD_PWRX", 1), _label("A", "SD_pwr", 2)],
    )
    net = _resolve_single_net([_sheet("A", [local_net], pins)])

    assert net.name == "SD_pwr"
    assert net.aliases == {"SD_PWRX"}


def test_case_insensitively_equal_labels_tie_break_by_document_order() -> None:
    # Corpus witness BB_CON_PCIe_CLK_REQ vs BB_CON_PCIE_CLK_REQ: the labels
    # are case-insensitively equal; the earlier document's spelling won.
    # Single witness — treated as an approximation. Both spellings survive
    # in names/aliases.
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        labels=[_label("A", "BB_CON_PCIe_CLK_REQ", 1), _label("A", "BB_CON_PCIE_CLK_REQ", 2)],
    )
    net = _resolve_single_net([_sheet("A", [local_net], pins)])

    assert net.name == "BB_CON_PCIe_CLK_REQ"
    assert net.aliases == {"BB_CON_PCIE_CLK_REQ"}
    assert {name.name for name in net.names} == {
        "BB_CON_PCIe_CLK_REQ",
        "BB_CON_PCIE_CLK_REQ",
    }


def test_same_text_labels_preserve_each_observation() -> None:
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        labels=[_label("A", "SDA", 1), _label("A", "SDA", 2)],
    )
    net = _resolve_single_net([_sheet("A", [local_net], pins)])

    assert net.name == "SDA"
    assert [(name.name, name.source) for name in net.names] == [
        ("SDA", "A:label:1"),
        ("SDA", "A:label:2"),
    ]


# ---------------------------------------------------------------------------
# Priority ladder
# ---------------------------------------------------------------------------


def test_ladder_sheet_entries_beat_ports() -> None:
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        ports=[_port("A", "PORT_NAME")],
        entries=[_entry("A", "ENTRY_NAME")],
    )
    net = _resolve_single_net(
        [_sheet("A", [local_net], pins)],
        AltiumProject(
            hierarchy_mode=AltiumHierarchyMode.FLAT,
            allow_port_net_names=True,
            allow_sheet_entry_net_names=True,
        ),
    )

    assert net.name == "ENTRY_NAME"
    assert "PORT_NAME" in net.aliases


def test_ladder_gated_tiers_fall_through_to_autoname() -> None:
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        ports=[_port("A", "PORT_NAME")],
        entries=[_entry("A", "ENTRY_NAME")],
    )
    net = _resolve_single_net(
        [_sheet("A", [local_net], pins)],
        AltiumProject(
            hierarchy_mode=AltiumHierarchyMode.FLAT,
            allow_port_net_names=False,
            allow_sheet_entry_net_names=False,
        ),
    )

    # Gated evidence cannot name the net but stays as aliases.
    assert net.name == "NetU1_1"
    assert {"PORT_NAME", "ENTRY_NAME"} <= net.aliases


def test_harness_fallback_uses_dot_separated_bundle_signal() -> None:
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        harness_members=[_harness_member("A", "SFP_3", "TX_FAULT")],
    )
    net = _resolve_single_net([_sheet("A", [local_net], pins)])

    assert net.name == "SFP_3.TX_FAULT"
    [name] = net.names
    assert name.kind is NetNameKind.TOOL_AUTO


def test_label_beats_harness_fallback() -> None:
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        labels=[_label("A", "SFP3_RD_N", 1)],
        harness_members=[_harness_member("A", "SFP_RX_TX_3", "RD_N")],
    )
    net = _resolve_single_net([_sheet("A", [local_net], pins)])

    assert net.name == "SFP3_RD_N"
    assert net.aliases == {"SFP_RX_TX_3.RD_N"}


def test_non_winning_evidence_lands_in_names_with_kinds() -> None:
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        labels=[_label("A", "SIG_LABEL", 1)],
        powers=[_power("A", "VCC")],
        entries=[_entry("A", "SIG_ENTRY")],
    )
    net = _resolve_single_net([_sheet("A", [local_net], pins)])

    assert net.name == "SIG_LABEL"
    assert net.aliases == {"VCC", "SIG_ENTRY"}
    by_name = {name.name: name for name in net.names}
    assert set(by_name) == {"SIG_LABEL", "VCC", "SIG_ENTRY"}
    assert all(name.kind is NetNameKind.LABEL for name in net.names)
    assert by_name["SIG_LABEL"].source == "A:label:1"
    assert by_name["SIG_LABEL"].scope == _scope("A")


# ---------------------------------------------------------------------------
# Project naming options
# ---------------------------------------------------------------------------


def test_append_sheet_number_to_local_nets_uses_title_block_sheet_number() -> None:
    project = AltiumProject(
        hierarchy_mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        append_sheet_number_to_local_nets=True,
    )
    local_net, pins = _local_net("A", "net", [("U1", "1")], labels=[_label("A", "SIG", 1)])

    ctx = ParseContext()
    net = _resolve_single_net([_sheet("A", [local_net], pins, sheet_number="5")], project, ctx)

    assert net.name == "SIG_5"
    assert "SIG" in net.aliases
    assert not [issue for issue in ctx.issues if issue.category == "unverified_naming_option"]


def test_append_sheet_number_to_local_nets_preserves_polarity_suffix() -> None:
    project = AltiumProject(
        hierarchy_mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        append_sheet_number_to_local_nets=True,
    )
    local_net, pins = _local_net("A", "net", [("U1", "1")], labels=[_label("A", "USB_P", 1)])

    net = _resolve_single_net([_sheet("A", [local_net], pins, sheet_number="5")], project)

    assert net.name == "USB_5_P"
    assert "USB_P" in net.aliases


def test_append_sheet_number_to_local_nets_suffixes_sheet_one_polarity_names() -> None:
    project = AltiumProject(
        hierarchy_mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        append_sheet_number_to_local_nets=True,
    )
    local_net, pins = _local_net("A", "net", [("U1", "1")], labels=[_label("A", "USB_P", 1)])

    net = _resolve_single_net([_sheet("A", [local_net], pins, sheet_number="1")], project)

    assert net.name == "USB_1_P"
    assert "USB_P" in net.aliases


def test_append_sheet_number_to_local_nets_suffixes_label_names_on_sheet_entry_nets() -> None:
    project = AltiumProject(
        hierarchy_mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        append_sheet_number_to_local_nets=True,
        allow_port_net_names=False,
    )
    entry_net, entry_pins = _local_net(
        "A",
        "entry",
        [("U0", "1")],
        labels=[_label("A", "ENTRY_SIG", 0)],
        entries=[_entry("A", "ENTRY_SIG")],
    )
    port_net, port_pins = _local_net(
        "A",
        "port",
        [("U1", "1")],
        labels=[_label("A", "PORT_SIG", 1)],
        ports=[_port("A", "PORT_SIG")],
    )
    harness_net, harness_pins = _local_net(
        "A",
        "harness",
        [("U2", "1")],
        labels=[_label("A", "HARNESS_SIG", 2)],
        harness_members=[_harness_member("A", "BUNDLE", "HARNESS_SIG")],
    )

    design = resolve_altium_source(
        AltiumSourceDesign(
            name="test",
            project=project,
            sheets={
                "A": _sheet(
                    "A",
                    [entry_net, port_net, harness_net],
                    [*entry_pins, *port_pins, *harness_pins],
                    sheet_number="5",
                )
            },
            root_sheet_name="A",
        )
    )

    assert {net.name for net in design.nets} == {"ENTRY_SIG_5", "PORT_SIG", "HARNESS_SIG"}


def test_append_sheet_number_to_local_nets_is_idempotent() -> None:
    project = AltiumProject(
        hierarchy_mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        append_sheet_number_to_local_nets=True,
    )
    local_net, pins = _local_net(
        "A",
        "net",
        [("U1", "1")],
        labels=[_label("A", "FPGA_CONF_CS_1", 1)],
    )

    net = _resolve_single_net([_sheet("A", [local_net], pins, sheet_number="1")], project)

    assert net.name == "FPGA_CONF_CS_1"


def test_name_nets_hierarchically_warns_and_skips_transform() -> None:
    project = AltiumProject(
        hierarchy_mode=AltiumHierarchyMode.FLAT,
        name_nets_hierarchically=True,
    )
    local_net, pins = _local_net("A", "net", [("U1", "1")], labels=[_label("A", "SIG", 1)])

    ctx = ParseContext()
    net = _resolve_single_net([_sheet("A", [local_net], pins)], project, ctx)

    issues = [issue for issue in ctx.issues if issue.category == "unverified_naming_option"]
    assert len(issues) == 1
    assert "unverified" in issues[0].message
    # The name is untouched — no hierarchical prefix.
    assert net.name == "SIG"


def test_hierarchical_bus_sheet_entries_connect_child_labels_by_member_name() -> None:
    project = AltiumProject(
        hierarchy_mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL,
        allow_sheet_entry_net_names=True,
    )
    left_symbol = _symbol("Top", "Left", "Left.SchDoc", 1)
    right_symbol = _symbol("Top", "Right", "Right.SchDoc", 2)
    left_entry = _entry("Top", "GPIO[0..1]", 1)
    left_entry.sheet_symbol_id = left_symbol.id
    right_entry = _entry("Top", "GPIO[0..1]", 2)
    right_entry.sheet_symbol_id = right_symbol.id
    left_parent_net, left_parent_pins = _local_net(
        "Top",
        "left_bus",
        [],
        entries=[left_entry],
    )
    right_parent_net, right_parent_pins = _local_net(
        "Top",
        "right_bus",
        [],
        entries=[right_entry],
    )
    left_child_net, left_child_pins = _local_net(
        "Left",
        "gpio0",
        [("U1", "1")],
        labels=[_label("Left", "GPIO0", 1)],
    )
    right_child_net, right_child_pins = _local_net(
        "Right",
        "gpio0",
        [("J1", "1")],
        labels=[_label("Right", "GPIO0", 1)],
    )

    design = resolve_altium_source(
        AltiumSourceDesign(
            name="test",
            project=project,
            sheets={
                "Top": _sheet(
                    "Top",
                    [left_parent_net, right_parent_net],
                    [*left_parent_pins, *right_parent_pins],
                    sheet_symbols=[left_symbol, right_symbol],
                ),
                "Left": _sheet("Left", [left_child_net], left_child_pins),
                "Right": _sheet("Right", [right_child_net], right_child_pins),
            },
            root_sheet_name="Top",
        )
    )

    [net] = [net for net in design.nets if net.name == "GPIO0"]
    assert {(pin.component.reference, pin.designator) for pin in net.pins} == {
        ("U1", "1"),
        ("J1", "1"),
    }


def test_harness_members_connect_to_peer_sheet_labels_by_member_name() -> None:
    project = AltiumProject(hierarchy_mode=AltiumHierarchyMode.HIERARCHICAL_POWER_GLOBAL)
    left_symbol = _symbol("Top", "Left", "Left.SchDoc", 1)
    right_symbol = _symbol("Top", "Right", "Right.SchDoc", 2)
    left_entry = _entry("Top", "BUS", 1)
    left_entry.sheet_symbol_id = left_symbol.id
    left_entry.harness_type = "BUS"
    right_entry = _entry("Top", "BUS", 2)
    right_entry.sheet_symbol_id = right_symbol.id
    right_entry.harness_type = "BUS"
    parent_net, parent_pins = _local_net(
        "Top",
        "harness_conduit",
        [],
        entries=[left_entry, right_entry],
    )
    left_port_net, left_port_pins = _local_net(
        "Left",
        "bus_port",
        [],
        ports=[_port("Left", "BUS")],
    )
    left_port_net.ports[0].harness_type = "BUS"
    left_member_net, left_member_pins = _local_net(
        "Left",
        "sig",
        [("U1", "1")],
        labels=[_label("Left", "SIG", 1)],
    )
    right_port_net, right_port_pins = _local_net(
        "Right",
        "bus_port",
        [],
        ports=[_port("Right", "BUS")],
    )
    right_port_net.ports[0].harness_type = "BUS"
    right_member_net, right_member_pins = _local_net(
        "Right",
        "sig",
        [("J1", "1")],
        labels=[_label("Right", "SIG", 1)],
        harness_members=[_harness_member("Right", "BUS", "SIG")],
    )

    design = resolve_altium_source(
        AltiumSourceDesign(
            name="test",
            project=project,
            sheets={
                "Top": _sheet(
                    "Top",
                    [parent_net],
                    parent_pins,
                    sheet_symbols=[left_symbol, right_symbol],
                ),
                "Left": _sheet(
                    "Left",
                    [left_port_net, left_member_net],
                    [*left_port_pins, *left_member_pins],
                ),
                "Right": _sheet(
                    "Right",
                    [right_port_net, right_member_net],
                    [*right_port_pins, *right_member_pins],
                ),
            },
            root_sheet_name="Top",
        )
    )

    [net] = [net for net in design.nets if net.name == "SIG"]
    assert {(pin.component.reference, pin.designator) for pin in net.pins} == {
        ("U1", "1"),
        ("J1", "1"),
    }


# ---------------------------------------------------------------------------
# Nets6 conformance: pi-mx8 fixture, case-insensitive join
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pimx8_net_names() -> tuple[set[str], set[str]]:
    project = load_project(PIMX8_PRJPCB)
    assert project.schematic is not None
    assert project.board is not None
    schematic_names = {net.name for net in project.schematic.nets}
    pcb_names = {net.name for net in project.board.nets.values() if net.name}
    return schematic_names, pcb_names


def test_pimx8_nets6_names_match_schematic_by_pin_membership() -> None:
    project = load_project(PIMX8_PRJPCB)
    assert project.schematic is not None
    assert project.board is not None

    schematic_by_members = {
        frozenset((pin.component.reference, pin.designator) for pin in net.pins): net
        for net in project.schematic.nets
        if net.pins
    }

    mismatches: list[tuple[str, str]] = []
    unmatched: list[str] = []
    for board_net in project.board.nets.values():
        if not board_net.name:
            continue
        members = frozenset(
            (pad.footprint.reference, pad.number)
            for pad in project.board.pads
            if pad.net is board_net and pad.footprint is not None
        )
        schematic_net = schematic_by_members.get(members)
        if schematic_net is None:
            unmatched.append(board_net.name)
            continue
        if schematic_net.name != board_net.name:
            mismatches.append((board_net.name, schematic_net.name))

    assert unmatched == []
    assert mismatches == []


def test_pimx8_schematic_names_cover_nets6_case_insensitively(
    pimx8_net_names: tuple[set[str], set[str]],
) -> None:
    """Every pi-mx8 Nets6 net name resolves from the schematic.

    The audit measured 97.8% case-insensitive prediction accuracy corpus-wide
    (98.1% exact on pi-mx8 by membership vote); the name-set join on this
    fixture measures 476/476. Case-insensitive is the robust join key — PCB
    casing legitimately lags schematic label edits (Altium ECO compares
    net names case-insensitively).
    """
    schematic_names, pcb_names = pimx8_net_names
    assert len(pcb_names) == 476

    schematic_casefold = {name.casefold() for name in schematic_names}
    ci_matched = {name for name in pcb_names if name.casefold() in schematic_casefold}
    assert len(ci_matched) == 476

    exact_matched = pcb_names & schematic_names
    assert len(exact_matched) == 476


def test_pimx8_autonames_match_nets6(pimx8_net_names: tuple[set[str], set[str]]) -> None:
    """All 29 tool-generated Net<ref>_<pin> names in Nets6 are reproduced."""
    schematic_names, pcb_names = pimx8_net_names
    pcb_autonames = {name for name in pcb_names if name.startswith("Net")}
    assert len(pcb_autonames) == 29
    assert pcb_autonames <= schematic_names
