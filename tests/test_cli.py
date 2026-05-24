import json
from importlib.metadata import version
from pathlib import Path

import pytest
from click.testing import CliRunner

from phosphor_eda.cli import main

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
    assert "font_size_px" in schema["properties"]
    assert "include" in schema["properties"]
    assert "style_rules" in schema["properties"]
    assert "pad" in json.dumps(schema["properties"]["highlights"])
    assert schema["examples"]


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
    assert 'data-component="TP3"' in result.output
    assert 'data-pad="1"' in result.output


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

    def fake_render_pcb_svg(board: object, **_kwargs: object) -> str:
        assert board is parsed_board
        return "<svg></svg>"

    monkeypatch.setattr("phosphor_eda.altium.pcb_parser.parse_altium_pcb", fake_parse_altium_pcb)
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
    settings = {"custom_css": ".board-fill { fill: rgb(1, 2, 3); }"}
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
    assert 'style id="highlight"' in svg
    assert "SWD" in svg


def test_cli_render_settings_font_size_sets_annotation_size(tmp_path: Path) -> None:
    settings = {
        "font_size_px": 24,
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
        "extends": "phosphor:print-callout",
        "font_size_px": 64,
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
        "font_size_px": 12,
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
    assert 'style id="highlight"' in svg


def test_cli_render_settings_with_highlight_colors(tmp_path: Path) -> None:
    """Highlight colors from render settings appear in the SVG CSS."""
    settings = {
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
