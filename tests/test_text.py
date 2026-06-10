"""Tests for shared text helpers."""

from phosphor_eda.formats.common.text import strip_overline


def test_strip_overline_active_low() -> None:
    clean, has_ol = strip_overline("D\\R\\D\\Y\\")
    assert clean == "DRDY"
    assert has_ol is True


def test_strip_overline_no_backslash() -> None:
    clean, has_ol = strip_overline("SCLK")
    assert clean == "SCLK"
    assert has_ol is False


def test_strip_overline_empty() -> None:
    clean, has_ol = strip_overline("")
    assert clean == ""
    assert has_ol is False


def test_strip_overline_partial() -> None:
    clean, has_ol = strip_overline("ADC_D\\R\\D\\Y\\")
    assert clean == "ADC_DRDY"
    assert has_ol is True


def test_strip_overline_underscore_in_name() -> None:
    clean, has_ol = strip_overline("S\\Y\\N\\C\\_\\I\\N\\")
    assert clean == "SYNC_IN"
    assert has_ol is True
