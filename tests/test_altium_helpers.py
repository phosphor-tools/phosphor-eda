"""Tests for Altium shared parsing helpers."""

from __future__ import annotations

import struct

from phosphor_eda.formats.altium._helpers import (
    compute_pin_tip,
    distance_from_top,
    f64,
    i32,
    prop_bool,
    prop_int,
    prop_location,
    prop_points,
    prop_str,
    u16,
    u32,
)

# ---------------------------------------------------------------------------
# Property-dict helpers
# ---------------------------------------------------------------------------


class TestPropInt:
    def test_valid_int(self) -> None:
        assert prop_int({"x": "42"}, "x") == 42

    def test_missing_key(self) -> None:
        assert prop_int({}, "x") == 0

    def test_missing_key_custom_default(self) -> None:
        assert prop_int({}, "x", -1) == -1

    def test_invalid_value(self) -> None:
        assert prop_int({"x": "abc"}, "x") == 0

    def test_negative(self) -> None:
        assert prop_int({"x": "-10"}, "x") == -10


class TestPropBool:
    def test_true_uppercase(self) -> None:
        assert prop_bool({"flag": "T"}, "flag") is True

    def test_true_word(self) -> None:
        assert prop_bool({"flag": "TRUE"}, "flag") is True

    def test_true_lowercase(self) -> None:
        assert prop_bool({"flag": "true"}, "flag") is True

    def test_false_value(self) -> None:
        assert prop_bool({"flag": "F"}, "flag") is False

    def test_missing(self) -> None:
        assert prop_bool({}, "flag") is False


class TestPropStr:
    def test_basic(self) -> None:
        assert prop_str({"name": "hello"}, "name") == "hello"

    def test_missing(self) -> None:
        assert prop_str({}, "name") == ""

    def test_custom_default(self) -> None:
        assert prop_str({}, "name", default="fallback") == "fallback"

    def test_utf8_fallback(self) -> None:
        props = {"%utf8%desc": "utf8 value", "desc": "ascii value"}
        assert prop_str(props, "desc", utf8=True) == "utf8 value"

    def test_utf8_not_present(self) -> None:
        props = {"desc": "ascii value"}
        assert prop_str(props, "desc", utf8=True) == "ascii value"


class TestPropLocation:
    def test_basic(self) -> None:
        props = {"location.x": "100", "location.y": "200"}
        assert prop_location(props) == (100, 200)

    def test_missing(self) -> None:
        assert prop_location({}) == (0, 0)


class TestPropPoints:
    def test_two_points(self) -> None:
        props = {"locationcount": "2", "x1": "10", "y1": "20", "x2": "30", "y2": "40"}
        assert prop_points(props) == [(10, 20), (30, 40)]

    def test_default_count(self) -> None:
        props = {"x1": "5", "y1": "6", "x2": "7", "y2": "8"}
        assert prop_points(props) == [(5, 6), (7, 8)]

    def test_extra_locations_beyond_fifty(self) -> None:
        # Altium caps LocationCount at 50 and stores vertices 51+ as
        # ExtraLocationCount / EX{i},EY{i} (seen on 4 wires in the pi-mx8
        # fixture).
        props = {"locationcount": "50", "extralocationcount": "10"}
        for i in range(1, 51):
            props[f"x{i}"] = str(i)
            props[f"y{i}"] = str(-i)
        for i in range(51, 61):
            props[f"ex{i}"] = str(i)
            props[f"ey{i}"] = str(-i)

        points = prop_points(props)

        assert len(points) == 60
        assert points == [(i, -i) for i in range(1, 61)]


class TestDistanceFromTop:
    def test_basic(self) -> None:
        props = {"distancefromtop": "10"}
        assert distance_from_top(props) == 100  # 10 * 10

    def test_with_fraction(self) -> None:
        props = {"distancefromtop": "10", "distancefromtop_frac1": "50000"}
        # 10 * 10 + 50000 / 100000 = 100.5 → round → 100
        assert distance_from_top(props) == 100

    def test_zero(self) -> None:
        assert distance_from_top({}) == 0


class TestComputePinTip:
    def test_rightward(self) -> None:
        assert compute_pin_tip((10, 20), 30, 0) == (40, 20)

    def test_upward(self) -> None:
        assert compute_pin_tip((10, 20), 30, 1) == (10, 50)

    def test_leftward(self) -> None:
        assert compute_pin_tip((10, 20), 30, 2) == (-20, 20)

    def test_downward(self) -> None:
        assert compute_pin_tip((10, 20), 30, 3) == (10, -10)


# ---------------------------------------------------------------------------
# Binary struct readers
# ---------------------------------------------------------------------------


class TestBinaryReaders:
    def test_u16(self) -> None:
        data = b"\x00\x01\x02\x03"
        assert u16(data, 0) == 0x0100
        assert u16(data, 2) == 0x0302

    def test_i32_positive(self) -> None:
        data = b"\x01\x00\x00\x00"
        assert i32(data, 0) == 1

    def test_i32_negative(self) -> None:
        data = b"\xff\xff\xff\xff"
        assert i32(data, 0) == -1

    def test_u32(self) -> None:
        data = b"\xff\xff\xff\xff"
        assert u32(data, 0) == 0xFFFFFFFF

    def test_f64(self) -> None:
        data = struct.pack("<d", 3.14)
        result = f64(data, 0)
        assert abs(result - 3.14) < 1e-10

    def test_f64_zero(self) -> None:
        data = struct.pack("<d", 0.0)
        assert f64(data, 0) == 0.0

    def test_memoryview(self) -> None:
        """Binary readers should work with memoryview as well as bytes."""
        data = memoryview(b"\x01\x00\x00\x00\xff\xff")
        assert u16(data, 4) == 0xFFFF
        assert i32(data, 0) == 1
