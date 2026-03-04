from pathlib import Path

from click.testing import CliRunner

from ecad_tools.cli import main

DSN_FILE = "raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PDF_FILE = "raspberry-pi-pico/RPI-PICO-R3-PUBLIC-SCHEMATIC.pdf"


def test_cli_convert_dsn(tmp_path):
    runner = CliRunner()
    out = tmp_path / "test.txt"
    result = runner.invoke(main, ["convert", DSN_FILE, "-o", str(out)])
    assert result.exit_code == 0
    assert "Written to" in result.output
    assert out.exists()
    text = out.read_text()
    assert "DESIGN SUMMARY" in text


def test_cli_convert_pdf(tmp_path):
    runner = CliRunner()
    out = tmp_path / "output.txt"
    result = runner.invoke(main, ["convert", PDF_FILE, "-o", str(out)])
    assert result.exit_code == 0
    assert "Written to" in result.output
    assert out.exists()


def test_cli_convert_stdout():
    runner = CliRunner()
    result = runner.invoke(main, ["convert", PDF_FILE, "-o", "-"])
    assert result.exit_code == 0
    assert "PAGE 1" in result.output


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
