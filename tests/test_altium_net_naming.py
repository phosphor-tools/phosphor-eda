"""Altium net-naming policy: ladder, natural-sort autoname, Nets6 conformance.

Policy source: audit/net-naming-altium.md (4-board corpus vs Nets6 ground
truth). The autoname witnesses below are the corpus cases that distinguish
natural sort from plain ASCII sort.
"""

from pathlib import Path

import pytest

from phosphor_eda.domain.schematic import Net, NetNameKind, ScopeId
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
    AltiumSourceDesign,
)
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.query.convert import load_project

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
) -> AltiumSheetSource:
    return AltiumSheetSource(
        id=f"sheet:{name}",
        name=name,
        source_file=f"{name}.SchDoc",
        scope_id=_scope(name),
        local_nets=local_nets,
        sheet_symbols=[],
        sheet_entries=[],
        harness_connectors=[],
        harness_members=[],
        pin_occurrences=pins,
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
# Unverified naming options: diagnostic, no transform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "option", ["append_sheet_number_to_local_nets", "name_nets_hierarchically"]
)
def test_unverified_naming_option_warns_and_skips_transform(option: str) -> None:
    project = AltiumProject(hierarchy_mode=AltiumHierarchyMode.FLAT)
    setattr(project, option, True)
    local_net, pins = _local_net("A", "net", [("U1", "1")], labels=[_label("A", "SIG", 1)])

    ctx = ParseContext()
    net = _resolve_single_net([_sheet("A", [local_net], pins)], project, ctx)

    issues = [issue for issue in ctx.issues if issue.category == "unverified_naming_option"]
    assert len(issues) == 1
    assert "unverified" in issues[0].message
    # The name is untouched — no sheet-number suffix or hierarchical prefix.
    assert net.name == "SIG"


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
