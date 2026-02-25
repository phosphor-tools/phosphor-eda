from pathlib import Path

from click.testing import CliRunner

from ecad_tools.cli import main

DSN_FILE = "raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PDF_FILE = "raspberry-pi-pico/RPI-PICO-R3-PUBLIC-SCHEMATIC.pdf"


def test_cli_parse_dsn(tmp_path):
    runner = CliRunner()
    out = tmp_path / "test.netlist.txt"
    result = runner.invoke(main, ["parse-dsn", DSN_FILE, "-o", str(out)])
    assert result.exit_code == 0
    assert "Netlist written" in result.output
    assert out.exists()


def test_cli_extract_pdf(tmp_path):
    runner = CliRunner()
    out = tmp_path / "output.txt"
    result = runner.invoke(main, ["extract-pdf", PDF_FILE, "-o", str(out)])
    assert result.exit_code == 0
    assert "Extracted" in result.output
    assert out.exists()


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
