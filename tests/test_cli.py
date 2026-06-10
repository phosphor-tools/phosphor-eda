import json
from importlib.metadata import version
from pathlib import Path

import pytest
from click.testing import CliRunner

from phosphor_eda.cli import main
from phosphor_eda.pcb_render import RenderResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DSN_FILE = str(FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN")


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert version("phosphor-eda") in result.output


# ---- schematic list/show CLI tests ----


def test_cli_schematic_list_components():
    runner = CliRunner()
    result = runner.invoke(main, ["list", "components", DSN_FILE])
    assert result.exit_code == 0
    assert "REF" in result.output
    assert "PART" in result.output


def test_cli_convert_writes_serialized_design(tmp_path):
    output = tmp_path / "pico.txt"
    runner = CliRunner()
    result = runner.invoke(main, ["convert", DSN_FILE, str(output)])
    assert result.exit_code == 0
    text = output.read_text()
    assert "NET: GND" in text


def test_cli_convert_reports_output_write_errors(tmp_path):
    missing_parent_output = tmp_path / "missing" / "pico.txt"
    runner = CliRunner()
    result = runner.invoke(main, ["convert", DSN_FILE, str(missing_parent_output)])
    assert result.exit_code != 0
    assert "Error:" in result.output
    assert str(missing_parent_output) in result.output


def test_cli_schematic_list_nets():
    runner = CliRunner()
    result = runner.invoke(main, ["list", "nets", DSN_FILE])
    assert result.exit_code == 0
    assert "NET" in result.output


def test_cli_schematic_list_pages():
    runner = CliRunner()
    result = runner.invoke(main, ["list", "pages", DSN_FILE])
    assert result.exit_code == 0
    assert "PAGE" in result.output


def test_cli_schematic_show_component():
    runner = CliRunner()
    result = runner.invoke(main, ["show", "component", "U1", DSN_FILE])
    assert result.exit_code == 0
    assert "COMPONENT: U1" in result.output


def test_cli_schematic_show_component_not_found():
    runner = CliRunner()
    result = runner.invoke(main, ["show", "component", "U999", DSN_FILE])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_schematic_show_net():
    runner = CliRunner()
    result = runner.invoke(main, ["show", "net", "GND", DSN_FILE])
    assert result.exit_code == 0
    assert "NET: GND" in result.output


def test_cli_schematic_show_net_not_found():
    runner = CliRunner()
    result = runner.invoke(main, ["show", "net", "NONEXISTENT_NET", DSN_FILE])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_schematic_unsupported_format(tmp_path):
    bad = tmp_path / "test.pdf"
    bad.write_text("hello")
    runner = CliRunner()
    result = runner.invoke(main, ["list", "components", str(bad)])
    assert result.exit_code != 0
    assert "Unsupported" in result.output


# ---- filter CLI tests ----


def test_cli_list_nets_no_power():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "nets",
            "--no-power",
            DSN_FILE,
        ],
    )
    assert result.exit_code == 0
    assert "NET" in result.output
    assert "GND" not in result.output


def test_cli_list_nets_power_only():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "nets",
            "--power",
            DSN_FILE,
        ],
    )
    assert result.exit_code == 0
    assert "GND" in result.output


def test_cli_list_nets_by_component():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "nets",
            "-c",
            "U1",
            DSN_FILE,
        ],
    )
    assert result.exit_code == 0
    assert "NET" in result.output
    # U1 is the RP2040 — should have GPIO nets but filtered list should be smaller
    lines = result.output.strip().splitlines()
    # At minimum: header + separator + some nets
    assert len(lines) >= 3


def test_cli_list_components_by_prefix():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "components",
            "--prefix",
            "U",
            DSN_FILE,
        ],
    )
    assert result.exit_code == 0
    assert "U1" in result.output
    # Should not include resistors or capacitors
    for line in result.output.splitlines()[2:]:  # skip header + separator
        if line.strip():
            assert line.strip().startswith("U")


def test_cli_list_components_no_passive():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "components",
            "--no-passive",
            DSN_FILE,
        ],
    )
    assert result.exit_code == 0
    assert "U1" in result.output


def test_cli_list_pages_by_component():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "pages",
            "-c",
            "U1",
            DSN_FILE,
        ],
    )
    assert result.exit_code == 0
    assert "PAGE" in result.output


# ---- trace CLI tests ----


def test_cli_trace():
    runner = CliRunner()
    # U1 is the RP2040, U3 is the QSPI flash
    result = runner.invoke(
        main,
        [
            "trace",
            "U1",
            "U3",
            DSN_FILE,
        ],
    )
    assert result.exit_code == 0
    assert "U1" in result.output
    assert "U3" in result.output
    assert "QSPI" in result.output


def test_cli_trace_not_found():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "trace",
            "U999",
            "U1",
            DSN_FILE,
        ],
    )
    assert result.exit_code != 0
    assert "not found" in result.output


# ---- sub-sheet detection tests ----

ALTIUM_PROJECT = str(FIXTURES / "altium/qfsae-debugger/Debugger.PrjPcb")
ALTIUM_SUBSHEET = str(FIXTURES / "altium/qfsae-debugger/MCU.SchDoc")
KICAD_ROOT = str(FIXTURES / "kicad-hierarchy/root.kicad_sch")
KICAD_CHILD = str(FIXTURES / "kicad-hierarchy/child.kicad_sch")


def test_cli_rejects_altium_subsheet():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "components",
            ALTIUM_SUBSHEET,
        ],
    )
    assert result.exit_code != 0
    assert "sub-sheet" in result.output
    assert "Debugger.PrjPcb" in result.output


def test_cli_force_single_sheet_altium():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--force-single-sheet",
            "list",
            "components",
            ALTIUM_SUBSHEET,
        ],
    )
    assert result.exit_code == 0
    assert "REF" in result.output


def test_cli_rejects_kicad_child_sheet():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "components",
            KICAD_CHILD,
        ],
    )
    assert result.exit_code != 0
    assert "sub-sheet" in result.output
    assert "root.kicad_sch" in result.output


def test_cli_force_single_sheet_kicad():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--force-single-sheet",
            "list",
            "components",
            KICAD_CHILD,
        ],
    )
    assert result.exit_code == 0


def test_cli_kicad_root_not_rejected():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list",
            "pages",
            KICAD_ROOT,
        ],
    )
    assert result.exit_code == 0
    assert "PAGE" in result.output


# ---- pcb render --render-settings CLI tests ----

PCB_FILE = str(FIXTURES / "swd_switch.kicad_pcb")


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


def test_cli_render_without_file_reports_missing_file() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render"])

    assert result.exit_code != 0
    assert "missing FILE" in result.output


def test_cli_render_custom_css_file_option_is_removed() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", "--custom-css-file", "theme.css", PCB_FILE])

    assert result.exit_code != 0
    assert "No such option: --custom-css-file" in result.output


def test_cli_render_theme_option_is_removed() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", "--theme", "print", PCB_FILE])

    assert result.exit_code != 0
    assert "No such option: --theme" in result.output


def test_cli_render_supports_highlight_pad() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", "--highlight-pad", "TP3.1", PCB_FILE])

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
    parsed_board = object()
    parsed_paths: list[Path] = []

    def fake_parse_altium_pcb(path: Path) -> object:
        parsed_paths.append(path)
        return parsed_board

    def fake_render_pcb_svg(board: object, **_kwargs: object) -> RenderResult:
        assert board is parsed_board
        return RenderResult(svg="<svg></svg>")

    monkeypatch.setattr("phosphor_eda.formats.altium.pcb_parser.parse_altium_pcb", fake_parse_altium_pcb)
    monkeypatch.setattr("phosphor_eda.pcb_render.render_pcb_svg", fake_render_pcb_svg)

    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", str(prjpcb)])

    assert result.exit_code == 0, result.output
    assert parsed_paths == [pcbdoc]
    assert "<svg></svg>" in result.output


def test_cli_render_prjpcb_without_existing_pcbdoc_reports_clear_error(tmp_path: Path) -> None:
    prjpcb = tmp_path / "Project.PrjPcb"
    prjpcb.write_text(
        "[Design]\nHierarchyMode=1\n\n[Document1]\nDocumentPath=boards\\Missing.PcbDoc\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", str(prjpcb)])

    assert result.exit_code != 0
    assert ".PcbDoc" in result.output


def test_cli_render_prjpcb_with_multiple_existing_pcbdocs_reports_clear_error(
    tmp_path: Path,
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

    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", str(prjpcb)])

    assert result.exit_code != 0
    assert "multiple" in result.output.lower()
    assert ".PcbDoc" in result.output


def test_cli_render_settings_inline_custom_css_is_injected(tmp_path: Path) -> None:
    settings = {
        "extends": "phosphor:review",
        "custom_css": ".board-fill { fill: rgb(1, 2, 3); }",
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["pcb", "render", PCB_FILE, "--render-settings", str(settings_file), "-o", str(out_file)],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert '<style id="custom">' in svg
    assert "rgb(1, 2, 3)" in svg


def test_cli_render_settings_from_file(tmp_path: Path) -> None:
    """--render-settings loads highlights and annotations from a JSON file."""
    settings = {
        "extends": "phosphor:review",
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
        ["pcb", "render", PCB_FILE, "--render-settings", str(settings_file), "-o", str(out_file)],
    )
    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert svg.startswith("<svg")
    assert 'class="highlight-overlay"' in svg
    assert 'data-highlight-target="net:/SWDIO_TMS"' in svg
    assert "SWD" in svg


def test_cli_render_profile_outputs_json_to_stderr(tmp_path: Path) -> None:
    settings = {"extends": "phosphor:review"}
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "pcb",
            "render",
            PCB_FILE,
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
    assert "cli.parse_board" in event_names
    assert "plan.build_inventory" in event_names
    assert "render.serialize" in event_names
    assert "svg.output" in event_names


def test_cli_render_settings_font_size_sets_annotation_size(tmp_path: Path) -> None:
    settings = {
        "extends": "phosphor:review",
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
        ["pcb", "render", PCB_FILE, "--render-settings", str(settings_file), "-o", str(out_file)],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert "font-size: 24.0px" in svg


def test_cli_render_settings_accepts_packaged_v2_settings(tmp_path: Path) -> None:
    settings = {
        "extends": "phosphor:simplified-high-contrast",
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
        ["pcb", "render", PCB_FILE, "--render-settings", str(settings_file), "-o", str(out_file)],
    )

    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert svg.startswith("<svg")
    assert "SWD" in svg


def test_cli_font_size_overrides_render_settings(tmp_path: Path) -> None:
    settings = {
        "extends": "phosphor:review",
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
            "pcb",
            "render",
            PCB_FILE,
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
    settings = {
        "extends": "phosphor:review",
        "highlights": [{"net": "/SWDIO_TMS"}],
    }
    out_file = tmp_path / "out.svg"

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["pcb", "render", PCB_FILE, "--render-settings", "-", "-o", str(out_file)],
        input=json.dumps(settings),
    )
    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert svg.startswith("<svg")
    assert 'class="highlight-overlay"' in svg
    assert 'data-highlight-target="net:/SWDIO_TMS"' in svg


def test_cli_render_settings_with_highlight_colors(tmp_path: Path) -> None:
    """Highlight colors from render settings appear in the SVG CSS."""
    settings = {
        "extends": "phosphor:review",
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
        ["pcb", "render", PCB_FILE, "--render-settings", str(settings_file), "-o", str(out_file)],
    )
    assert result.exit_code == 0, result.output
    svg = out_file.read_text()
    assert "#d4a843" in svg
    assert "#5b8abf" in svg


def test_cli_render_settings_invalid_json(tmp_path: Path) -> None:
    """Invalid JSON in render settings file produces a clear error."""
    settings_file = tmp_path / "bad.json"
    settings_file.write_text("not json")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["pcb", "render", PCB_FILE, "--render-settings", str(settings_file)],
    )
    assert result.exit_code != 0
    assert "Invalid render settings JSON" in result.output


def test_cli_render_settings_non_object(tmp_path: Path) -> None:
    """Non-object JSON (array, scalar) in render settings produces a clear error."""
    settings_file = tmp_path / "array.json"
    settings_file.write_text("[]")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["pcb", "render", PCB_FILE, "--render-settings", str(settings_file)],
    )
    assert result.exit_code != 0
    assert "must be an object" in result.output


def test_cli_render_settings_rejects_theme(tmp_path: Path) -> None:
    """Unsupported theme in render settings produces a clear error."""
    settings_file = tmp_path / "bad.json"
    settings_file.write_text(json.dumps({"theme": "neon"}))

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["pcb", "render", PCB_FILE, "--render-settings", str(settings_file)],
    )
    assert result.exit_code != 0
    assert "Render settings error" in result.output


# ---- error boundary + render warnings (plan 03) ----


def test_cli_render_unknown_net_warns_on_stderr_and_exits_zero() -> None:
    """An unresolved highlight target prints a warning to stderr but still renders."""
    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", "-n", "DOES_NOT_EXIST", PCB_FILE])

    assert result.exit_code == 0, result.stderr
    assert "Highlight target not found" in result.stderr
    assert "DOES_NOT_EXIST" in result.stderr
    # The SVG still goes to stdout.
    assert "<svg" in result.stdout


def test_cli_list_components_unknown_net_errors(tmp_path: Path) -> None:
    """list components -n on an unknown net: one-line error, exit 1."""
    runner = CliRunner()
    result = runner.invoke(main, ["list", "components", "-n", "NOPE_NET", DSN_FILE])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "NOPE_NET" in result.output
    assert "not found" in result.output


def test_cli_list_nets_unknown_component_errors() -> None:
    """list nets -c on an unknown component: symmetrical error, exit 1.

    Previously this silently printed an empty table.
    """
    runner = CliRunner()
    result = runner.invoke(main, ["list", "nets", "-c", "NOPE_COMP", DSN_FILE])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "NOPE_COMP" in result.output
    assert "not found" in result.output


def test_cli_unknown_net_and_component_errors_are_symmetrical() -> None:
    """Unknown net and unknown component both produce a one-line error + exit 1."""
    runner = CliRunner()
    net_result = runner.invoke(main, ["list", "components", "-n", "ZZZ", DSN_FILE])
    comp_result = runner.invoke(main, ["list", "nets", "-c", "ZZZ", DSN_FILE])

    assert net_result.exit_code == comp_result.exit_code == 1
    assert "not found" in net_result.output
    assert "not found" in comp_result.output


def test_cli_corrupt_schematic_reports_one_line_error(tmp_path: Path) -> None:
    """A corrupt schematic file produces a one-line parse error and exit 1, not a traceback."""
    bad = tmp_path / "broken.kicad_sch"
    bad.write_text("(this is not a valid kicad schematic")

    runner = CliRunner()
    result = runner.invoke(main, ["list", "components", str(bad)])

    assert result.exit_code == 1
    assert "failed to parse" in result.output
    assert "Traceback" not in result.output


def test_cli_corrupt_pcb_reports_one_line_error(tmp_path: Path) -> None:
    """A corrupt PCB file produces a one-line parse error and exit 1."""
    bad = tmp_path / "broken.kicad_pcb"
    bad.write_text("(this is not a valid kicad pcb")

    runner = CliRunner()
    result = runner.invoke(main, ["pcb", "render", str(bad)])

    assert result.exit_code == 1
    assert "failed to parse" in result.output
    assert "Traceback" not in result.output
