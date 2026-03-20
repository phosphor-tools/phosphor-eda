from pathlib import Path

import pytest
from click.testing import CliRunner

from ecad_tools.cli import main

DSN_FILE = "raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PDF_FILE = "raspberry-pi-pico/RPI-PICO-R3-PUBLIC-SCHEMATIC.pdf"

_CONVERT_SKIP = pytest.mark.skip(reason="convert command disabled")


@_CONVERT_SKIP
def test_cli_convert_dsn(tmp_path):
    runner = CliRunner()
    out = tmp_path / "test.txt"
    result = runner.invoke(main, ["convert", DSN_FILE, "-o", str(out)])
    assert result.exit_code == 0
    assert "Written to" in result.output
    assert out.exists()
    text = out.read_text()
    assert "DESIGN SUMMARY" in text


@_CONVERT_SKIP
def test_cli_convert_pdf(tmp_path):
    runner = CliRunner()
    out = tmp_path / "output.txt"
    result = runner.invoke(main, ["convert", PDF_FILE, "-o", str(out)])
    assert result.exit_code == 0
    assert "Written to" in result.output
    assert out.exists()


@_CONVERT_SKIP
def test_cli_convert_stdout():
    runner = CliRunner()
    result = runner.invoke(main, ["convert", PDF_FILE, "-o", "-"])
    assert result.exit_code == 0
    assert "PAGE 1" in result.output


@_CONVERT_SKIP
def test_cli_convert_unsupported(tmp_path):
    bad = tmp_path / "test.xyz"
    bad.write_text("hello")
    runner = CliRunner()
    result = runner.invoke(main, ["convert", str(bad)])
    assert result.exit_code != 0
    assert "Unsupported" in result.output


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


# ---- schematic list/show CLI tests ----


def test_cli_schematic_list_components():
    runner = CliRunner()
    result = runner.invoke(main, ["schematic", "list", "components", DSN_FILE])
    assert result.exit_code == 0
    assert "REF" in result.output
    assert "PART" in result.output


def test_cli_schematic_list_nets():
    runner = CliRunner()
    result = runner.invoke(main, ["schematic", "list", "nets", DSN_FILE])
    assert result.exit_code == 0
    assert "NET" in result.output


def test_cli_schematic_list_pages():
    runner = CliRunner()
    result = runner.invoke(main, ["schematic", "list", "pages", DSN_FILE])
    assert result.exit_code == 0
    assert "PAGE" in result.output


def test_cli_schematic_show_component():
    runner = CliRunner()
    result = runner.invoke(main, ["schematic", "show", "component", "U1", DSN_FILE])
    assert result.exit_code == 0
    assert "COMPONENT: U1" in result.output


def test_cli_schematic_show_component_not_found():
    runner = CliRunner()
    result = runner.invoke(main, ["schematic", "show", "component", "U999", DSN_FILE])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_schematic_show_net():
    runner = CliRunner()
    result = runner.invoke(main, ["schematic", "show", "net", "GND", DSN_FILE])
    assert result.exit_code == 0
    assert "NET: GND" in result.output


def test_cli_schematic_show_net_not_found():
    runner = CliRunner()
    result = runner.invoke(main, ["schematic", "show", "net", "NONEXISTENT_NET", DSN_FILE])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_schematic_unsupported_format(tmp_path):
    bad = tmp_path / "test.pdf"
    bad.write_text("hello")
    runner = CliRunner()
    result = runner.invoke(main, ["schematic", "list", "components", str(bad)])
    assert result.exit_code != 0
    assert "Unsupported" in result.output


# ---- filter CLI tests ----


def test_cli_list_nets_no_power():
    runner = CliRunner()
    result = runner.invoke(main, [
        "schematic", "list", "nets", "--no-power", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "NET" in result.output
    assert "GND" not in result.output


def test_cli_list_nets_power_only():
    runner = CliRunner()
    result = runner.invoke(main, [
        "schematic", "list", "nets", "--power", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "GND" in result.output


def test_cli_list_nets_by_component():
    runner = CliRunner()
    result = runner.invoke(main, [
        "schematic", "list", "nets", "-c", "U1", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "NET" in result.output
    # U1 is the RP2040 — should have GPIO nets but filtered list should be smaller
    lines = result.output.strip().splitlines()
    # At minimum: header + separator + some nets
    assert len(lines) >= 3


def test_cli_list_components_by_prefix():
    runner = CliRunner()
    result = runner.invoke(main, [
        "schematic", "list", "components", "--prefix", "U", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "U1" in result.output
    # Should not include resistors or capacitors
    for line in result.output.splitlines()[2:]:  # skip header + separator
        if line.strip():
            assert line.strip().startswith("U")


def test_cli_list_components_no_passive():
    runner = CliRunner()
    result = runner.invoke(main, [
        "schematic", "list", "components", "--no-passive", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "U1" in result.output


def test_cli_list_pages_by_component():
    runner = CliRunner()
    result = runner.invoke(main, [
        "schematic", "list", "pages", "-c", "U1", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "PAGE" in result.output


# ---- trace CLI tests ----


def test_cli_trace():
    runner = CliRunner()
    # U1 is the RP2040, U3 is the QSPI flash
    result = runner.invoke(main, [
        "schematic", "trace", "U1", "U3", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "U1" in result.output
    assert "U3" in result.output
    assert "QSPI" in result.output


def test_cli_trace_not_found():
    runner = CliRunner()
    result = runner.invoke(main, [
        "schematic", "trace", "U999", "U1", DSN_FILE,
    ])
    assert result.exit_code != 0
    assert "not found" in result.output
