"""Tests for multi-sheet Altium parsing."""

from pathlib import Path

from phosphor_eda.formats.altium.to_schematic import altium_to_design
from phosphor_eda.query.format import serialize_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
QFSAE_PRJPCB = UPSTREAM_FIXTURES / "qfsae-pcb/Debugger/Debugger.PrjPcb"
MCU_SCHDOC = UPSTREAM_FIXTURES / "qfsae-pcb/Debugger/MCU.SchDoc"
# pi-mx8 keeps its schematics in a SCH/ subdirectory while sheet-symbol
# FileName records reference children by bare filename, so hierarchical net
# resolution must reconcile the two path spellings.
PIMX8_PRJPCB = (
    UPSTREAM_FIXTURES / "pi-mx8/01_Electronics/PiMX8MP_r0.3_release/PiMX8MP_r0.3_release.PrjPcb"
)


def _net_by_name(design, name):
    for net in design.nets:
        if net.name == name or name in net.aliases:
            return net
    return None


def test_parse_single_schdoc():
    design = altium_to_design(MCU_SCHDOC)
    assert len(design.pages) == 1
    assert design.pages[0].name == "MCU"


def test_parse_prjpcb_all_sheets():
    design = altium_to_design(QFSAE_PRJPCB)
    assert len(design.pages) == 4
    names = {p.name for p in design.pages}
    assert "TOP" in names
    assert "MCU" in names
    assert "Power" in names
    assert "Connectors" in names


def test_parse_prjpcb_total_components():
    design = altium_to_design(QFSAE_PRJPCB)
    total = sum(len(p.components) for p in design.pages)
    assert total == 39  # 0 (TOP) + 23 (MCU, U1 parts grouped) + 9 (Power) + 7 (Connectors)


def test_parse_prjpcb_cross_sheet_nets():
    design = altium_to_design(QFSAE_PRJPCB)
    gnd = next((n for n in design.nets if n.name == "GND"), None)
    assert gnd is not None
    gnd_refs = {p.component.reference for p in gnd.pins}
    assert len(gnd_refs) > 20


def test_child_listed_first_still_detects_hierarchy(tmp_path: Path):
    """SMART hierarchy detection keys on the structural root, not project order.

    The qfsae Debugger project is hierarchical (TOP owns MCU/Power/Connectors
    through sheet symbols). Listing a child document before TOP must not flip
    SMART detection to FLAT/GLOBAL.
    """
    src_dir = QFSAE_PRJPCB.parent
    for name in ("TOP.SchDoc", "MCU.SchDoc", "Power.SchDoc", "Connectors.SchDoc"):
        (tmp_path / name).write_bytes((src_dir / name).read_bytes())
    prjpcb = tmp_path / "Debugger.PrjPcb"
    prjpcb.write_text(
        "[Design]\nHierarchyMode=0\n\n"
        "[Document1]\nDocumentPath=MCU.SchDoc\n\n"
        "[Document2]\nDocumentPath=TOP.SchDoc\n\n"
        "[Document3]\nDocumentPath=Power.SchDoc\n\n"
        "[Document4]\nDocumentPath=Connectors.SchDoc\n"
    )

    design = altium_to_design(prjpcb)
    assert design.metadata["altium_effective_hierarchy_mode"] == "HIERARCHICAL_POWER_GLOBAL"

    reference = altium_to_design(QFSAE_PRJPCB)
    assert (
        design.metadata["altium_effective_hierarchy_mode"]
        == (reference.metadata["altium_effective_hierarchy_mode"])
    )
    assert len(design.nets) == len(reference.nets)


def test_pimx8_cross_subdir_hierarchy_connects():
    """A hierarchical net must connect across the SCH/ subdirectory boundary.

    SD_PWR_ON is sourced on the board-to-board connector sheet (J3.75) and
    routed through the block diagram down to the PMIC sheet (U1.23). Sheet
    symbols reference these children by bare filename while the project lists
    them under SCH/, so resolving this net proves the path spellings are
    reconciled.
    """
    design = altium_to_design(PIMX8_PRJPCB)

    net = _net_by_name(design, "SD_PWR_ON")
    assert net is not None, "SD_PWR_ON net should resolve"

    pins = {f"{p.component.reference}.{p.designator}" for p in net.pins}
    assert {"U1.23", "J3.75"} <= pins

    pages = {page.name for page in net.pages}
    assert {"02_8MPLUS_PMIC", "14_BTB_Connector_P1-P100"} <= pages


def test_pimx8_hierarchy_resolves_many_cross_sheet_nets():
    """Cross-subdir hierarchy should connect the design broadly, not just once.

    Dozens of nets bridge child sheets through the block-diagram sheet
    symbols; global power rails alone would connect only a handful.
    """
    design = altium_to_design(PIMX8_PRJPCB)

    spanning = 0
    for net in design.nets:
        child_pages = {page.name for page in net.pages if page.name != "01_Block_Diagram"}
        if len(child_pages) >= 2:
            spanning += 1

    assert spanning >= 20


def test_multipart_component_is_single_block_without_designator_suffix():
    """Case A: a multi-gate package stays one component with no per-piece suffix.

    The qfsae U1 is a multi-part symbol (gates grouped into one package). It must
    serialize as a single logical component, and per-instance designator logic
    must not add ``U1 [..]`` noise to it.
    """
    design = altium_to_design(QFSAE_PRJPCB)
    u1_components = [c for c in design.components if c.reference == "U1"]
    assert len(u1_components) == 1
    for occurrence in u1_components[0].occurrences:
        assert occurrence.physical_designator == ""

    out = serialize_design(design)
    assert "U1 [" not in out


def test_parse_prjpcb_write_design(tmp_path):
    from phosphor_eda.query.format import write_design

    design = altium_to_design(QFSAE_PRJPCB, name="QFSAE")
    out = tmp_path / "qfsae-netlist.txt"
    write_design(design, out)
    content = out.read_text()
    assert "COMPONENTS" in content
    assert "NETS" in content
    assert "GND" in content
    assert "VCC3V3" in content
