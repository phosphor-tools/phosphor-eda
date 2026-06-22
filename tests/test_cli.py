import json
from importlib.metadata import version
from pathlib import Path

import pytest
from click.testing import CliRunner

import phosphor_eda.cli as cli_module
from phosphor_eda.cli import main
from phosphor_eda.domain.pcb import (
    Board,
    LayerRole,
    PadStack,
    PcbConductor,
    PcbConductorKind,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbNet,
    PcbPad,
    PcbPadType,
)
from phosphor_eda.domain.project import Project
from phosphor_eda.domain.schematic import Bus, BusKind, Component, Net, Page, Pin, Schematic
from phosphor_eda.formats.altium.pcb_project import AltiumEnrichment
from phosphor_eda.render.api import RenderResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DSN_PATH = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PCB_PATH = FIXTURES / "swd_switch.kicad_pcb"


def _write_opj(path: Path, dsn_path: Path = DSN_PATH) -> Path:
    project_path = str(dsn_path).replace("\\", "\\\\")
    path.write_text(
        f"""(ExpressProject "Pico Project"
  (ProjectVersion "19981106")
  (ProjectType "PCB")
  (Folder "Design Resources"
    (File "{project_path}"
      (Type "Schematic Design"))))
""",
        encoding="utf-8",
    )
    return path


def _write_swd_project(tmp_path: Path) -> Path:
    project = tmp_path / "swd_switch.kicad_pro"
    board = tmp_path / "swd_switch.kicad_pcb"
    board.write_bytes(PCB_PATH.read_bytes())
    project.write_text("{}", encoding="utf-8")
    return project


def _empty_board(name: str, path: Path) -> Board:
    return Board(
        name=name,
        layers=[],
        nets={},
        footprints=[],
        pads=[],
        vias=[],
        drills=[],
        conductors=[],
        artwork=[],
        pours=[],
        keepouts=[],
        source_path=str(path),
    )


def _series_passive_highlight_project(path: Path) -> Project:
    front = PcbLayer("F.Cu", roles=(LayerRole.COPPER, LayerRole.FRONT), stack_index=0)
    inner = PcbLayer("In1.Cu", roles=(LayerRole.COPPER, LayerRole.INNER), stack_index=1)
    back = PcbLayer("B.Cu", roles=(LayerRole.COPPER, LayerRole.BACK), stack_index=2)
    source_net = PcbNet(number=1, name="/CSI/I2C_MUX_SCL")
    far_net = PcbNet(number=2, name="SCL_CAM")
    footprint = PcbFootprint(
        reference="R1",
        footprint_lib="Resistor_SMD:R_0402",
        x=0.0,
        y=0.0,
        rotation=0.0,
        layer=front,
    )
    pad_stack = PadStack.simple("rect", 0.6, 0.4)
    pads = [
        PcbPad(
            id="pad:r1:1",
            number="1",
            x=0.0,
            y=0.0,
            stack=pad_stack,
            pad_type=PcbPadType.SMD,
            layers=(front,),
            net=source_net,
            footprint=footprint,
        ),
        PcbPad(
            id="pad:r1:2",
            number="2",
            x=1.0,
            y=0.0,
            stack=pad_stack,
            pad_type=PcbPadType.SMD,
            layers=(front,),
            net=far_net,
            footprint=footprint,
        ),
    ]
    board = Board(
        name="series-passive-highlight",
        layers=[front, inner, back],
        nets={source_net.number: source_net, far_net.number: far_net},
        footprints=[footprint],
        pads=pads,
        vias=[],
        drills=[],
        conductors=[
            PcbConductor(
                id="trace:scl-cam:front",
                kind=PcbConductorKind.TRACE,
                layer=front,
                data=PcbLine(1.0, 0.0, 3.0, 0.0, 0.15),
                net=far_net,
            ),
            PcbConductor(
                id="trace:scl-cam:inner",
                kind=PcbConductorKind.TRACE,
                layer=inner,
                data=PcbLine(3.0, 0.0, 3.0, 2.0, 0.15),
                net=far_net,
            ),
            PcbConductor(
                id="trace:scl-cam:back",
                kind=PcbConductorKind.TRACE,
                layer=back,
                data=PcbLine(3.0, 2.0, 1.0, 2.0, 0.15),
                net=far_net,
            ),
        ],
        artwork=[],
        pours=[],
        keepouts=[],
        source_path=str(path.with_suffix(".kicad_pcb")),
    )

    page = Page(id="page:root", name="Root")
    resistor = Component(id="component:r1", reference="R1", part="R", description="resistor")
    net_a = Net(id="net:a", name="/CSI/I2C_MUX_SCL", pages=[page])
    net_b = Net(id="net:b", name="SCL_CAM", pages=[page])
    pin_a = Pin(id="pin:r1:1", designator="1", name="1", component=resistor, net=net_a)
    pin_b = Pin(id="pin:r1:2", designator="2", name="2", component=resistor, net=net_b)
    resistor.pins = [pin_a, pin_b]
    net_a.pins = [pin_a]
    net_b.pins = [pin_b]
    page.components = [resistor]
    page.nets = [net_a, net_b]
    schematic = Schematic(
        name="series-passive-highlight",
        pages=[page],
        nets=[net_a, net_b],
        components=[resistor],
    )
    return Project(name="series-passive-highlight", schematic=schematic, boards=[board])


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert version("phosphor-eda") in result.output


# ---- schematic list/show CLI tests ----


def test_cli_schematic_list_components(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "components"])
    assert result.exit_code == 0
    assert "REF" in result.output
    assert "PART" in result.output


def test_cli_schematic_list_nets(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "nets"])
    assert result.exit_code == 0
    assert "NET" in result.output


def test_cli_schematic_list_pages(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "pages"])
    assert result.exit_code == 0
    assert "PAGE" in result.output


def test_cli_schematic_list_buses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    opj = _write_opj(tmp_path / "bus.opj")
    monkeypatch.setattr(
        cli_module,
        "load_project",
        lambda _path, **_kwargs: Project(name="BUS", schematic=_bus_design_for_cli()),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "buses", "--net", "DATA0"])

    assert result.exit_code == 0
    assert "BUS" in result.output
    assert "DATA[0..1]" in result.output


def test_cli_schematic_list_buses_rejects_negative_min_members(tmp_path: Path):
    opj = _write_opj(tmp_path / "bus.opj")

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "buses", "--min-members", "-1"])

    assert result.exit_code != 0
    assert "Invalid value for '--min-members'" in result.output


def test_cli_schematic_show_component(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "show", "component", "U1"])
    assert result.exit_code == 0
    assert "COMPONENT: U1" in result.output


def test_cli_schematic_show_component_not_found(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "show", "component", "U999"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_schematic_show_net(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "show", "net", "GND"])
    assert result.exit_code == 0
    assert "NET: GND" in result.output


def test_cli_schematic_show_net_not_found(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "show", "net", "NONEXISTENT_NET"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_schematic_show_bus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    opj = _write_opj(tmp_path / "bus.opj")
    monkeypatch.setattr(
        cli_module,
        "load_project",
        lambda _path, **_kwargs: Project(name="BUS", schematic=_bus_design_for_cli()),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "show", "bus", "DATA[0..1]"])

    assert result.exit_code == 0
    assert "BUS: DATA[0..1] (vector) | Members: 2" in result.output
    assert "DATA0" in result.output


def test_cli_schematic_unsupported_format(tmp_path):
    bad = tmp_path / "test.pdf"
    bad.write_text("hello")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(bad), "list", "components"])
    assert result.exit_code != 0
    assert "project file required" in result.output


def _bus_design_for_cli() -> Schematic:
    page = Page(id="page:digital", name="Digital")
    data0 = Net(id="net:data0", name="DATA0", pages=[page])
    data1 = Net(id="net:data1", name="DATA1", pages=[page])
    bus = Bus(id="bus:data", name="DATA[0..1]", kind=BusKind.VECTOR, members=[data0, data1])
    page.nets = [data0, data1]
    return Schematic(name="BUS", pages=[page], nets=[data0, data1], buses=[bus])


def _selector_design_for_cli() -> Schematic:
    page = Page(id="page:main", name="Main")
    u1 = Component(id="component:u1", reference="U1", part="MCU", description="Controller")
    u2 = Component(id="component:u2", reference="U2", part="USB", description="USB PHY")
    j1 = Component(id="component:j1", reference="J1", part="CONN", description="Connector")
    usb_dp = Net(id="net:usb-dp", name="USB_DP", pages=[page], aliases={"USB_D+"})
    usb_dm = Net(id="net:usb-dm", name="USB_DM", pages=[page], aliases={"USB_D-"})
    gnd = Net(id="net:gnd", name="GND", pages=[page])
    for component, net, pin_id in (
        (u1, usb_dp, "u1-1"),
        (u2, usb_dp, "u2-1"),
        (j1, usb_dm, "j1-1"),
        (u1, gnd, "u1-2"),
    ):
        pin = Pin(
            id=pin_id,
            designator=pin_id.rsplit("-", maxsplit=1)[1],
            name="",
            component=component,
            net=net,
        )
        component.pins.append(pin)
        net.pins.append(pin)
    page.components = [u1, u2, j1]
    page.nets = [usb_dp, usb_dm, gnd]
    for component in page.components:
        component.pages.append(page)
    bus = Bus(id="bus:usb", name="USB", kind=BusKind.GROUP, members=[usb_dp, usb_dm])
    return Schematic(
        name="selectors",
        pages=[page],
        components=[u1, u2, j1],
        nets=[usb_dp, usb_dm, gnd],
        buses=[bus],
    )


# ---- filter CLI tests ----


def test_cli_list_nets_no_power(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(opj),
            "list",
            "nets",
            "--no-power",
        ],
    )
    assert result.exit_code == 0
    assert "NET" in result.output
    assert "GND" not in result.output


def test_cli_list_nets_power_only(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(opj),
            "list",
            "nets",
            "--power",
        ],
    )
    assert result.exit_code == 0
    assert "GND" in result.output


def test_cli_list_nets_by_component(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(opj),
            "list",
            "nets",
            "-c",
            "U1",
        ],
    )
    assert result.exit_code == 0
    assert "NET" in result.output
    # U1 is the RP2040 — should have GPIO nets but filtered list should be smaller
    lines = result.output.strip().splitlines()
    # At minimum: header + separator + some nets
    assert len(lines) >= 3


def test_cli_list_components_by_selector(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(opj),
            "list",
            "components",
            "--component",
            "U*",
        ],
    )
    assert result.exit_code == 0
    assert "U1" in result.output
    # Should not include resistors or capacitors
    for line in result.output.splitlines()[2:]:  # skip header + separator
        if line.strip():
            assert line.strip().startswith("U")


def test_cli_list_components_selector_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opj = _write_opj(tmp_path / "selectors.opj")
    monkeypatch.setattr(
        cli_module,
        "load_project",
        lambda _path, **_kwargs: Project(name="selectors", schematic=_selector_design_for_cli()),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["-P", str(opj), "list", "components", "--component", "U*", "--component", "!U2"],
    )

    assert result.exit_code == 0
    assert "U1" in result.output
    assert "U2" not in result.output


def test_cli_list_components_unmatched_glob_returns_empty_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opj = _write_opj(tmp_path / "selectors.opj")
    monkeypatch.setattr(
        cli_module,
        "load_project",
        lambda _path, **_kwargs: Project(name="selectors", schematic=_selector_design_for_cli()),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "components", "--component", "X*"])

    assert result.exit_code == 0
    assert result.output.strip() == "No components found."
    assert "U1" not in result.output
    assert "U2" not in result.output
    assert "J1" not in result.output


def test_cli_list_nets_by_net_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opj = _write_opj(tmp_path / "selectors.opj")
    monkeypatch.setattr(
        cli_module,
        "load_project",
        lambda _path, **_kwargs: Project(name="selectors", schematic=_selector_design_for_cli()),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "nets", "--net", "USB*"])

    assert result.exit_code == 0
    assert "USB_DP" in result.output
    assert "USB_DM" in result.output
    assert "GND" not in result.output


def test_cli_show_net_selector_outputs_multiple_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opj = _write_opj(tmp_path / "selectors.opj")
    monkeypatch.setattr(
        cli_module,
        "load_project",
        lambda _path, **_kwargs: Project(name="selectors", schematic=_selector_design_for_cli()),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "show", "net", "USB*"])

    assert result.exit_code == 0
    assert "NET: USB_DP" in result.output
    assert "\n\nNET: USB_DM" in result.output


def test_cli_show_net_unmatched_glob_reports_no_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opj = _write_opj(tmp_path / "selectors.opj")
    monkeypatch.setattr(
        cli_module,
        "load_project",
        lambda _path, **_kwargs: Project(name="selectors", schematic=_selector_design_for_cli()),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "show", "net", "NO_MATCH*"])

    assert result.exit_code == 0
    assert result.output.strip() == "No nets found."


def test_cli_list_components_no_passive(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(opj),
            "list",
            "components",
            "--no-passive",
        ],
    )
    assert result.exit_code == 0
    assert "U1" in result.output


def test_cli_list_pages_by_component(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(opj),
            "list",
            "pages",
            "-c",
            "U1",
        ],
    )
    assert result.exit_code == 0
    assert "PAGE" in result.output


# ---- trace CLI tests ----


def test_cli_trace(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    # U1 is the RP2040, U3 is the QSPI flash
    result = runner.invoke(
        main,
        [
            "-P",
            str(opj),
            "trace",
            "U1",
            "U3",
        ],
    )
    assert result.exit_code == 0
    assert "U1" in result.output
    assert "U3" in result.output
    assert "QSPI" in result.output


def test_cli_trace_not_found(tmp_path: Path):
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(opj),
            "trace",
            "U999",
            "U1",
        ],
    )
    assert result.exit_code != 0
    assert "not found" in result.output


# ---- pcb render --render-settings CLI tests ----


def test_cli_render_settings_schema_outputs_json_without_file() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", "--render-settings-schema"])

    assert result.exit_code == 0, result.output
    schema = json.loads(result.output)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "theme" not in schema["properties"]
    assert "font_size" not in schema["properties"]
    assert "font_size_px" not in schema["properties"]
    assert "include" not in schema["properties"]
    assert "highlight_behavior" not in schema["properties"]
    assert "style_rules" not in schema["properties"]
    assert "fontSizePx" in schema["properties"]
    assert "source" in schema["properties"]
    assert "tokens" in schema["properties"]
    assert "pad" in json.dumps(schema["properties"]["highlights"])
    assert schema["examples"]


def test_cli_render_settings_schema_exits_before_other_option_validation() -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["pcb", "render", "--render-settings-schema", "--side", "invalid"],
    )

    assert result.exit_code == 0, result.output
    schema = json.loads(result.output)
    assert schema["type"] == "object"


def test_cli_render_without_project_reports_missing_project() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render"])

    assert result.exit_code != 0
    assert "-P/--project" in result.output


def test_cli_render_accepts_direct_pcb_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcb = tmp_path / "Board.kicad_pcb"
    pcb.write_text("", encoding="utf-8")
    parsed_board = _empty_board("Board", pcb)
    parsed_paths: list[Path] = []

    def fake_load_pcb(path: Path) -> Board:
        parsed_paths.append(path)
        return parsed_board

    def fake_render_pcb_svg(board: Board, _settings: object, **_kwargs: object) -> RenderResult:
        assert board is parsed_board
        return RenderResult(svg="<svg></svg>")

    monkeypatch.setattr("phosphor_eda.cli.load_pcb", fake_load_pcb)
    monkeypatch.setattr("phosphor_eda.render.api.render_pcb_svg", fake_render_pcb_svg)

    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", str(pcb)])

    assert result.exit_code == 0, result.output
    assert parsed_paths == [pcb]
    assert "<svg></svg>" in result.output


def test_cli_render_custom_css_file_option_is_removed(tmp_path: Path) -> None:
    project = _write_swd_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["-P", str(project), "pcb", "render", "--custom-css-file", "theme.css"],
    )

    assert result.exit_code != 0
    assert "No such option: --custom-css-file" in result.output


def test_cli_render_theme_option_is_removed(tmp_path: Path) -> None:
    project = _write_swd_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(project), "pcb", "render", "--theme", "print"])

    assert result.exit_code != 0
    assert "No such option: --theme" in result.output


def test_cli_render_supports_highlight_pad(tmp_path: Path) -> None:
    project = _write_swd_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["-P", str(project), "pcb", "render", "--highlight-pad", "TP3.1"],
    )

    assert result.exit_code == 0, result.output
    assert 'class="highlight-overlay"' in result.output
    assert 'data-highlight-target="pad:TP3.1"' in result.output
    assert 'data-source-id="pad:' in result.output
    assert ":TP3:1:copper:" in result.output
    assert 'data-source-ids="' not in result.output


def test_cli_render_prjpcb_resolves_single_existing_pcbdoc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board_dir = tmp_path / "boards"
    board_dir.mkdir()
    pcbdoc = board_dir / "Board.PcbDoc"
    pcbdoc.write_text("")
    prjpcb = tmp_path / "Project.PrjPcb"
    prjpcb.write_text(
        "[Design]\nHierarchyMode=1\n\n[Document1]\nDocumentPath=boards\\Board.PcbDoc\n"
    )
    parsed_board = _empty_board("Board", pcbdoc)
    parsed_paths: list[Path] = []

    def fake_parse_altium_pcb(path: Path, _ctx: object = None) -> Board:
        parsed_paths.append(path)
        return parsed_board

    def fake_load_altium_enrichment(_path: Path, _ctx: object) -> AltiumEnrichment:
        return AltiumEnrichment(design_rules=[], net_classes=[], diff_pairs=[])

    def fake_render_pcb_svg(board: Board, _settings: object, **_kwargs: object) -> RenderResult:
        assert board is parsed_board
        return RenderResult(svg="<svg></svg>")

    monkeypatch.setattr(
        "phosphor_eda.formats.altium.project_loader.parse_altium_pcb",
        fake_parse_altium_pcb,
    )
    monkeypatch.setattr(
        "phosphor_eda.formats.altium.project_loader.load_altium_enrichment",
        fake_load_altium_enrichment,
    )
    monkeypatch.setattr("phosphor_eda.render.api.render_pcb_svg", fake_render_pcb_svg)

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(prjpcb), "pcb", "render"])

    assert result.exit_code == 0, result.output
    assert parsed_paths == [pcbdoc]
    assert "<svg></svg>" in result.output


def test_cli_render_prjpcb_without_existing_pcbdoc_reports_clear_error(tmp_path: Path) -> None:
    prjpcb = tmp_path / "Project.PrjPcb"
    prjpcb.write_text(
        "[Design]\nHierarchyMode=1\n\n[Document1]\nDocumentPath=boards\\Missing.PcbDoc\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(prjpcb), "pcb", "render"])

    assert result.exit_code != 0
    assert "no renderable PCB board" in result.output


def test_cli_render_prjpcb_with_multiple_existing_pcbdocs_reports_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "First.PcbDoc"
    second = tmp_path / "Second.PcbDoc"
    first.write_text("")
    second.write_text("")
    prjpcb = tmp_path / "Project.PrjPcb"
    prjpcb.write_text(
        "[Design]\n"
        "HierarchyMode=1\n\n"
        "[Document1]\n"
        "DocumentPath=First.PcbDoc\n\n"
        "[Document2]\n"
        "DocumentPath=Second.PcbDoc\n"
    )

    def fake_parse_altium_pcb(path: Path, _ctx: object = None) -> Board:
        return _empty_board(path.stem, path)

    def fake_load_altium_enrichment(_path: Path, _ctx: object) -> AltiumEnrichment:
        return AltiumEnrichment(design_rules=[], net_classes=[], diff_pairs=[])

    monkeypatch.setattr(
        "phosphor_eda.formats.altium.project_loader.parse_altium_pcb",
        fake_parse_altium_pcb,
    )
    monkeypatch.setattr(
        "phosphor_eda.formats.altium.project_loader.load_altium_enrichment",
        fake_load_altium_enrichment,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(prjpcb), "pcb", "render"])

    assert result.exit_code != 0
    assert "multiple" in result.output.lower()
    assert "First.PcbDoc" in result.output


def test_cli_render_board_selector_reports_ambiguous_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "Board.PcbDoc"
    second = second_dir / "Board.PcbDoc"
    first.write_text("")
    second.write_text("")
    prjpcb = tmp_path / "Project.PrjPcb"
    prjpcb.write_text(
        "[Design]\n"
        "HierarchyMode=1\n\n"
        "[Document1]\n"
        "DocumentPath=first\\Board.PcbDoc\n\n"
        "[Document2]\n"
        "DocumentPath=second\\Board.PcbDoc\n"
    )

    def fake_parse_altium_pcb(path: Path, _ctx: object = None) -> Board:
        return _empty_board(path.stem, path)

    def fake_load_altium_enrichment(_path: Path, _ctx: object) -> AltiumEnrichment:
        return AltiumEnrichment(design_rules=[], net_classes=[], diff_pairs=[])

    monkeypatch.setattr(
        "phosphor_eda.formats.altium.project_loader.parse_altium_pcb",
        fake_parse_altium_pcb,
    )
    monkeypatch.setattr(
        "phosphor_eda.formats.altium.project_loader.load_altium_enrichment",
        fake_load_altium_enrichment,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(prjpcb), "pcb", "render", "--board", "Board"])

    assert result.exit_code != 0
    assert "ambiguous" in result.output
    assert "Board (Board.PcbDoc)" in result.output


def test_cli_render_settings_inline_custom_css_is_injected(tmp_path: Path) -> None:
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:realistic",
        "custom_css": ".board-fill { fill: rgb(1, 2, 3); }",
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "-o",
            str(out_file),
        ],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert '<style id="custom">' in svg
    assert "rgb(1, 2, 3)" in svg


def test_cli_render_explicit_empty_custom_css_clears_settings_css(tmp_path: Path) -> None:
    """--custom-css '' clears custom CSS coming from the settings file."""
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:realistic",
        "custom_css": ".board-fill { fill: rgb(1, 2, 3); }",
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "--custom-css",
            "",
            "-o",
            str(out_file),
        ],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert '<style id="custom">' not in svg


def test_cli_render_custom_css_is_emitted_after_annotation_styles(tmp_path: Path) -> None:
    """User CSS must come after generated annotation CSS so it can override it."""
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:realistic",
        "custom_css": ".annotation-label { fill: rgb(1, 2, 3); }",
        "annotations": {
            "pointers": [{"target": "TP3", "label": "SWD"}],
        },
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "-o",
            str(out_file),
        ],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert svg.index('<style id="annotations">') < svg.index('<style id="custom">')


def test_cli_render_settings_from_file(tmp_path: Path) -> None:
    """--render-settings loads highlights and annotations from a JSON file."""
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:realistic",
        "highlights": [{"net": "/SWDIO_TMS"}],
        "annotations": {
            "pointers": [{"target": "TP3", "label": "SWD"}],
        },
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "-o",
            str(out_file),
        ],
    )
    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert svg.startswith("<svg")
    assert 'class="highlight-overlay"' in svg
    assert 'data-highlight-target="net:/SWDIO_TMS"' in svg
    assert "SWD" in svg


def test_cli_render_profile_outputs_json_to_stderr(tmp_path: Path) -> None:
    project = _write_swd_project(tmp_path)
    settings = {"extends": "phosphor:realistic"}
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "--profile-render",
            "-o",
            str(out_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_file.read_text().startswith("<svg")
    profile = json.loads(result.stderr.split("Wrote", maxsplit=1)[1].split("\n", maxsplit=1)[1])
    event_names = [event["name"] for event in profile["events"]]
    assert "cli.load_project" in event_names
    assert "plan.build_inventory" in event_names
    assert "render.serialize" in event_names
    assert "svg.output" in event_names


def test_cli_render_settings_font_size_sets_annotation_size(tmp_path: Path) -> None:
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:realistic",
        "fontSizePx": 24,
        "annotations": {
            "pointers": [{"target": "TP3", "label": "SWD"}],
        },
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "-o",
            str(out_file),
        ],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert "font-size: 24.0px" in svg


def test_cli_render_settings_accepts_packaged_v2_settings(tmp_path: Path) -> None:
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:documentation",
        "fontSizePx": 64,
        "annotations": {
            "pointers": [{"target": "TP3.1", "label": "SWD"}],
        },
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "-o",
            str(out_file),
        ],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert svg.startswith("<svg")
    assert "SWD" in svg


def test_cli_font_size_overrides_render_settings(tmp_path: Path) -> None:
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:realistic",
        "fontSizePx": 12,
        "annotations": {
            "pointers": [{"target": "TP3", "label": "SWD"}],
        },
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "--font-size",
            "24",
            "-o",
            str(out_file),
        ],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert "font-size: 24.0px" in svg
    assert "font-size: 12.0px" not in svg


def test_cli_render_settings_from_stdin(tmp_path: Path) -> None:
    """--render-settings - reads JSON from stdin."""
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:realistic",
        "highlights": [{"net": "/SWDIO_TMS"}],
    }
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            "-",
            "-o",
            str(out_file),
        ],
        input=json.dumps(settings),
    )
    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert svg.startswith("<svg")
    assert 'class="highlight-overlay"' in svg
    assert 'data-highlight-target="net:/SWDIO_TMS"' in svg


def test_cli_render_settings_with_highlight_colors(tmp_path: Path) -> None:
    """Highlight colors from render settings appear in the SVG CSS."""
    project = _write_swd_project(tmp_path)
    settings = {
        "extends": "phosphor:realistic",
        "highlights": [
            {"net": "/SWDIO_TMS", "color": "#d4a843"},
            {"net": "/SWDCLK_TCK", "color": "#5b8abf"},
        ],
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project),
            "pcb",
            "render",
            "--render-settings",
            str(settings_file),
            "-o",
            str(out_file),
        ],
    )
    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert "#d4a843" in svg
    assert "#5b8abf" in svg


def test_cli_render_settings_invalid_json(tmp_path: Path) -> None:
    """Invalid JSON in render settings file produces a clear error."""
    project = _write_swd_project(tmp_path)
    settings_file = tmp_path / "bad.json"
    settings_file.write_text("not json")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["-P", str(project), "pcb", "render", "--render-settings", str(settings_file)],
    )
    assert result.exit_code != 0
    assert "Invalid render settings JSON" in result.output


def test_cli_render_settings_non_object(tmp_path: Path) -> None:
    """Non-object JSON (array, scalar) in render settings produces a clear error."""
    project = _write_swd_project(tmp_path)
    settings_file = tmp_path / "array.json"
    settings_file.write_text("[]")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["-P", str(project), "pcb", "render", "--render-settings", str(settings_file)],
    )
    assert result.exit_code != 0
    assert "must be an object" in result.output


def test_cli_render_settings_rejects_theme(tmp_path: Path) -> None:
    """Unsupported theme in render settings produces a clear error."""
    project = _write_swd_project(tmp_path)
    settings_file = tmp_path / "bad.json"
    settings_file.write_text(json.dumps({"theme": "neon"}))

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["-P", str(project), "pcb", "render", "--render-settings", str(settings_file)],
    )
    assert result.exit_code != 0
    assert "Render settings error" in result.output


# ---- error boundary + render warnings (plan 03) ----


def test_cli_render_unknown_net_warns_on_stderr_and_exits_zero(tmp_path: Path) -> None:
    """An unresolved highlight target prints a warning to stderr but still renders."""
    project = _write_swd_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(project), "pcb", "render", "-n", "DOES_NOT_EXIST"])

    assert result.exit_code == 0, result.stderr
    assert "Highlight target not found" in result.stderr
    assert "DOES_NOT_EXIST" in result.stderr
    # The SVG still goes to stdout.
    assert "<svg" in result.stdout


def test_cli_render_net_highlight_without_schematic_warns_and_matches_exact(
    tmp_path: Path,
) -> None:
    """A net highlight on a board with no discoverable schematic falls back
    to exact matching with a warning."""
    project = _write_swd_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["-P", str(project), "pcb", "render", "-n", "GND"],
    )

    assert result.exit_code == 0, result.stderr
    assert "project contains no loadable schematic" in result.stderr
    assert "<svg" in result.stdout


def test_cli_render_net_highlight_traverses_series_passives_via_schematic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Highlighting one side of a series resistor lights the far-side net's
    copper on every layer."""
    project_file = tmp_path / "series-passive-highlight.kicad_pro"
    project_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli_module,
        "load_project",
        lambda path, **_kwargs: _series_passive_highlight_project(path),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-P",
            str(project_file),
            "pcb",
            "render",
            "-n",
            "/CSI/I2C_MUX_SCL",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert "project contains no loadable schematic" not in result.stderr
    assert 'data-net-name="SCL_CAM"' in result.stdout
    highlight_block = result.stdout.split('data-highlight-target="net:/CSI/I2C_MUX_SCL"')[1]
    overlay = highlight_block.split("</svg>")[0]
    assert 'data-role="highlight.copper.front"' in overlay
    assert 'data-role="highlight.copper.back"' in overlay
    assert 'data-role="highlight.copper.inner' in overlay


def test_cli_list_components_unknown_net_errors(tmp_path: Path) -> None:
    """list components -n on an unknown net: one-line error, exit 1."""
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "components", "-n", "NOPE_NET"])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "NOPE_NET" in result.output
    assert "not found" in result.output


def test_cli_list_nets_unknown_component_errors(tmp_path: Path) -> None:
    """list nets -c on an unknown component: symmetrical error, exit 1.

    Previously this silently printed an empty table.
    """
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(opj), "list", "nets", "-c", "NOPE_COMP"])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "NOPE_COMP" in result.output
    assert "not found" in result.output


def test_cli_unknown_net_and_component_errors_are_symmetrical(tmp_path: Path) -> None:
    """Unknown net and unknown component both produce a one-line error + exit 1."""
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()
    net_result = runner.invoke(main, ["-P", str(opj), "list", "components", "-n", "ZZZ"])
    comp_result = runner.invoke(main, ["-P", str(opj), "list", "nets", "-c", "ZZZ"])

    assert net_result.exit_code == comp_result.exit_code == 1
    assert "not found" in net_result.output
    assert "not found" in comp_result.output


def test_cli_corrupt_schematic_reports_one_line_error(tmp_path: Path) -> None:
    """A corrupt project file produces a one-line parse error and exit 1, not a traceback."""
    bad = tmp_path / "broken.kicad_pro"
    bad.write_text("(this is not a valid kicad schematic")

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(bad), "list", "components"])

    assert result.exit_code == 1
    assert "failed to parse" in result.output
    assert "Traceback" not in result.output


def test_cli_corrupt_pcb_reports_one_line_error(tmp_path: Path) -> None:
    """A corrupt PCB file produces a one-line parse error and exit 1."""
    project = tmp_path / "broken.kicad_pro"
    bad = tmp_path / "broken.kicad_pcb"
    project.write_text("{}", encoding="utf-8")
    bad.write_text("(this is not a valid kicad pcb")

    runner = CliRunner()
    result = runner.invoke(main, ["-P", str(project), "pcb", "render"])

    assert result.exit_code == 1
    assert "failed to parse" in result.output
    assert "Traceback" not in result.output
