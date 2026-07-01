from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

import phosphor_eda.cli as cli_module
from phosphor_eda.cli import main
from phosphor_eda.domain.pcb import Board, LayerRole, PcbLayer, PcbNet
from phosphor_eda.domain.project import (
    DocumentKind,
    Project,
    ProjectDocument,
    ProjectMetadata,
    Stackup,
    StackupLayer,
)
from phosphor_eda.domain.schematic import (
    Component,
    Net,
    Page,
    PartNumber,
    Pin,
    Schematic,
    TitleBlock,
)
from phosphor_eda.domain.variants import (
    Variant,
    VariantField,
    VariantOverride,
    VariantTarget,
    VariantTargetKind,
)
from phosphor_eda.query.overview import format_project_overview

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def _overview_project() -> Project:
    root_title = TitleBlock(
        title="Motor Controller",
        revision="B",
        date="2026-06-01",
        organization="Acme Hardware",
        comments={"2": "Release notes " + "x" * 220},
    )
    top = Page(
        id="page:top",
        name="Top",
        source_file="Top.SchDoc",
        title_block=root_title,
        annotations=["Changed power sequencing " + "y" * 260, "Second note", "Third note"],
    )
    power = Page(
        id="page:power",
        name="Power",
        source_file="Power.SchDoc",
        title_block=TitleBlock(
            title="Motor Controller",
            sheet_number="2",
            sheet_total="3",
            metadata={"PageTitle": "Power Input"},
        ),
    )

    gnd = Net(id="net:gnd", name="GND", pages=[top, power], aliases={"0V"})
    signal = Net(id="net:sig", name="RESET_N", pages=[top, power])
    top.nets = [gnd, signal]
    power.nets = [gnd, signal]

    connector = Component(
        id="component:J1",
        reference="J1",
        part="Conn_02x20",
        description="Expansion\nconnector\twith notes",
        pages=[top],
    )
    connector.pins = [
        Pin(id=f"pin:J1:{idx}", designator=str(idx), name=f"P{idx}", component=connector)
        for idx in range(1, 41)
    ]
    ic = Component(
        id="component:U1",
        reference="U1",
        part="STM32H7",
        description="MCU",
        pages=[power],
        part_numbers=[PartNumber(manufacturer="ST", number="STM32H743VIT6")],
    )
    ic.pins = [
        Pin(id=f"pin:U1:{idx}", designator=str(idx), name=f"GPIO{idx}", component=ic)
        for idx in range(1, 33)
    ]
    tp = Component(
        id="component:TP1", reference="TP1", part="TestPoint", description="", pages=[top]
    )
    tp_pin = Pin(id="pin:TP1:1", designator="1", name="", component=tp, net=gnd)
    tp.pins = [tp_pin]
    two_pin_tp = Component(
        id="component:TP2", reference="TP2", part="TestPoint2", description="", pages=[top]
    )
    two_pin_tp.pins = [
        Pin(id="pin:TP2:1", designator="1", name="", component=two_pin_tp, net=gnd),
        Pin(id="pin:TP2:2", designator="2", name="", component=two_pin_tp, net=gnd),
    ]
    gnd.pins = [tp_pin, *two_pin_tp.pins]
    top.components = [connector, tp, two_pin_tp]
    power.components = [ic]

    schematic = Schematic(
        name="Motor Controller",
        pages=[top, power],
        components=[connector, ic, tp, two_pin_tp],
        nets=[gnd, signal],
    )

    board = Board(
        name="Main Board",
        layers=[
            PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT)),
            PcbLayer("B.Cu", (LayerRole.COPPER, LayerRole.BACK)),
            PcbLayer("Edge.Cuts", (LayerRole.EDGE,)),
        ],
        nets={1: PcbNet(1, "GND")},
        footprints=[],
        pads=[],
        vias=[],
        drills=[],
        conductors=[],
        artwork=[],
        pours=[],
        keepouts=[],
        source_path="/tmp/Main.PcbDoc",
    )
    board.stackup = Stackup(
        layers=[
            StackupLayer(
                name="Top Solder",
                layer_type="solder_mask",
                thickness_mm=0.01,
                material="LPI",
                side="front",
            ),
            StackupLayer(
                name="F.Cu",
                layer_type="copper",
                thickness_mm=0.035,
                copper_weight_oz=1.0,
                side="front",
                copper_orientation="normal",
            ),
            StackupLayer(
                name="Core",
                layer_type="core",
                thickness_mm=1.51,
                material="FR4",
                epsilon_r=4.2,
                loss_tangent=0.02,
            ),
            StackupLayer(
                name="B.Cu",
                layer_type="copper",
                thickness_mm=0.035,
                copper_weight_oz=1.0,
                side="back",
                copper_orientation="reversed",
            ),
            StackupLayer(
                name="Bottom Solder",
                layer_type="solder_mask",
                thickness_mm=0.01,
                material="LPI",
                side="back",
            ),
        ],
        total_thickness_mm=1.6,
        copper_finish="ENIG",
    )

    return Project(
        name="MotorControl",
        metadata=ProjectMetadata(
            name="MotorControl",
            revision="B",
            date="2026-06-01",
            organization="Acme Hardware",
            format="altium",
        ),
        documents=[
            ProjectDocument(
                path="Project.PrjPcb",
                kind=DocumentKind.OTHER,
                native_kind=".PrjPcb",
                order=1,
                exists=True,
                parsed=True,
            ),
            ProjectDocument(
                path="Broken.SchDoc",
                kind=DocumentKind.SCHEMATIC,
                native_kind="SchDoc",
                order=2,
                exists=True,
                parsed=False,
                metadata={"parse_error": "bad record"},
            ),
        ],
        schematic=schematic,
        boards=[board],
    )


def test_format_project_overview_contains_project_inventory() -> None:
    output = format_project_overview(_overview_project())

    assert "Project\n" in output
    assert "  Name: MotorControl" in output
    assert "  Format: altium" in output
    assert "  Title: Motor Controller" in output
    assert "Documents\n" in output
    assert "exists, parsed" in output
    assert "Error: bad record" in output
    assert "Schematic\n" in output
    assert "  Multi-page signal nets: 1" in output
    assert "Schematic Pages\n" in output
    assert "Power Input" in output
    assert "Boards\n" in output
    assert "STACKUP" in output
    assert "2 copper, 2 solder mask, 1.600 mm" in output
    assert "Stackup\n" in output
    assert "Main Board: 2 copper layers, 5 physical layers, 1.600 mm total, finish ENIG" in output
    assert any(
        "Core" in line
        and "core" in line
        and "1.510 mm" in line
        and "FR4" in line
        and "4.2" in line
        and "0.02" in line
        for line in output.splitlines()
    )
    assert any(
        "F.Cu" in line and "copper" in line and "0.035 mm" in line and "1.0" in line
        for line in output.splitlines()
    )


def test_format_project_overview_lists_variants_when_present() -> None:
    project = _overview_project()
    project.variants = [
        Variant(
            name="Base manufacturing",
            overrides=[
                VariantOverride(
                    variant_name="Base manufacturing",
                    target=VariantTarget(kind=VariantTargetKind.COMPONENT, reference="R1"),
                    field=VariantField.FITTED,
                    value=False,
                )
            ],
        ),
        Variant(name="TPU"),
    ]
    project.selected_variant_name = "TPU"

    output = format_project_overview(project)

    assert "Variants\n" in output
    assert "  Active: TPU" in output
    assert "Base manufacturing" in output
    assert output.index("Variants\n") < output.index("Documents\n")


def test_format_project_overview_important_components_and_notes() -> None:
    output = format_project_overview(_overview_project())

    assert "Component Prefixes" not in output
    assert "Constraints" not in output
    assert "Suggested" not in output
    assert "Multi-page Signal Nets" not in output
    assert "Components with >= 32 pins" in output
    assert "Connectors (J*, P*, CN*, X*)" in output
    assert "Test points (TP*)" in output
    assert "J1  pins=40  page=Top  symbol=Conn_02x20  desc=Expansion connector with notes" in output
    assert "U1  pins=32  page=Power  mpn=ST STM32H743VIT6  symbol=STM32H7  desc=MCU" in output
    assert output.index("mpn=ST STM32H743VIT6") < output.index("symbol=STM32H7")
    assert "TP1  pins=1  page=Top  net=GND  symbol=TestPoint" in output
    assert "TP2  pins=2  page=Top  net=GND  symbol=TestPoint2" in output
    assert "..." in output
    assert "... 1 more annotation omitted" in output


def test_overview_cli_uses_project_option(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    project_path = tmp_path / "Project.PrjPcb"
    project_path.write_text("", encoding="utf-8")

    def fake_load_project(_path: Path, **_kwargs: object) -> Project:
        return _overview_project()

    monkeypatch.setattr(cli_module, "load_project", fake_load_project)

    result = CliRunner().invoke(main, ["-P", str(project_path), "overview"])

    assert result.exit_code == 0, result.output
    assert "Project\n" in result.output
    assert "Omitted\n" in result.output
