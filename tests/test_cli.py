from pathlib import Path

import pytest
from click.testing import CliRunner

from phosphor_eda.cli import main

DSN_FILE = "raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


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
    result = runner.invoke(main, [
        "list", "nets", "--no-power", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "NET" in result.output
    assert "GND" not in result.output


def test_cli_list_nets_power_only():
    runner = CliRunner()
    result = runner.invoke(main, [
        "list", "nets", "--power", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "GND" in result.output


def test_cli_list_nets_by_component():
    runner = CliRunner()
    result = runner.invoke(main, [
        "list", "nets", "-c", "U1", DSN_FILE,
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
        "list", "components", "--prefix", "U", DSN_FILE,
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
        "list", "components", "--no-passive", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "U1" in result.output


def test_cli_list_pages_by_component():
    runner = CliRunner()
    result = runner.invoke(main, [
        "list", "pages", "-c", "U1", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "PAGE" in result.output


# ---- trace CLI tests ----


def test_cli_trace():
    runner = CliRunner()
    # U1 is the RP2040, U3 is the QSPI flash
    result = runner.invoke(main, [
        "trace", "U1", "U3", DSN_FILE,
    ])
    assert result.exit_code == 0
    assert "U1" in result.output
    assert "U3" in result.output
    assert "QSPI" in result.output


def test_cli_trace_not_found():
    runner = CliRunner()
    result = runner.invoke(main, [
        "trace", "U999", "U1", DSN_FILE,
    ])
    assert result.exit_code != 0
    assert "not found" in result.output


# ---- sub-sheet detection tests ----

ALTIUM_PROJECT = "altium-test/TestBoard_X9/TestBoard_X9.PrjPcb"
ALTIUM_SUBSHEET = "altium-test/TestBoard_X9/ADC.SchDoc"
KICAD_ROOT = "tests/fixtures/kicad-hierarchy/root.kicad_sch"
KICAD_CHILD = "tests/fixtures/kicad-hierarchy/child.kicad_sch"


@pytest.mark.skipif(
    not Path(ALTIUM_SUBSHEET).exists(), reason="Altium test data not available",
)
def test_cli_rejects_altium_subsheet():
    runner = CliRunner()
    result = runner.invoke(main, [
        "list", "components", ALTIUM_SUBSHEET,
    ])
    assert result.exit_code != 0
    assert "sub-sheet" in result.output
    assert "TestBoard_X9.PrjPcb" in result.output


@pytest.mark.skipif(
    not Path(ALTIUM_SUBSHEET).exists(), reason="Altium test data not available",
)
def test_cli_force_single_sheet_altium():
    runner = CliRunner()
    result = runner.invoke(main, [
        "--force-single-sheet", "list", "components", ALTIUM_SUBSHEET,
    ])
    assert result.exit_code == 0
    assert "REF" in result.output


def test_cli_rejects_kicad_child_sheet():
    runner = CliRunner()
    result = runner.invoke(main, [
        "list", "components", KICAD_CHILD,
    ])
    assert result.exit_code != 0
    assert "sub-sheet" in result.output
    assert "root.kicad_sch" in result.output


def test_cli_force_single_sheet_kicad():
    runner = CliRunner()
    result = runner.invoke(main, [
        "--force-single-sheet", "list", "components", KICAD_CHILD,
    ])
    assert result.exit_code == 0


def test_cli_kicad_root_not_rejected():
    runner = CliRunner()
    result = runner.invoke(main, [
        "list", "pages", KICAD_ROOT,
    ])
    assert result.exit_code == 0
    assert "PAGE" in result.output
