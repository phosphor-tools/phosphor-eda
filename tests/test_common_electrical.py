"""Tests for the shared pin-electrical vocabulary."""

from __future__ import annotations

from phosphor_eda.formats.common.electrical import (
    EAGLE_DIRECTION_MAP,
    KICAD_ELECTRICAL_MAP,
    PinElectrical,
    set_pin_electrical,
)


def test_canonical_values_match_legacy_kicad_strings() -> None:
    # Locks the canonical serialized strings against KiCad's prior _ELECTRICAL_MAP.
    expected = {
        "input": "input",
        "output": "output",
        "bidirectional": "IO",
        "tri_state": "hi-Z",
        "passive": "passive",
        "free": "unspecified",
        "unspecified": "unspecified",
        "power_in": "power",
        "power_out": "power",
        "open_collector": "open-collector",
        "open_emitter": "open-emitter",
        "no_connect": "no-connect",
    }
    assert {k: v.value for k, v in KICAD_ELECTRICAL_MAP.items()} == expected


def test_canonical_values_match_legacy_eagle_strings() -> None:
    expected = {
        "pas": "passive",
        "in": "input",
        "out": "output",
        "io": "IO",
        "sup": "power",
        "nc": "no-connect",
        "hiz": "hi-Z",
        "oc": "open-collector",
        "pwr": "power",
    }
    assert {k: v.value for k, v in EAGLE_DIRECTION_MAP.items()} == expected


def test_set_pin_electrical_skips_passive_and_none() -> None:
    meta: dict[str, str] = {}
    set_pin_electrical(meta, PinElectrical.PASSIVE)
    set_pin_electrical(meta, None)
    assert meta == {}


def test_set_pin_electrical_writes_non_passive() -> None:
    meta: dict[str, str] = {}
    set_pin_electrical(meta, PinElectrical.HI_Z)
    assert meta == {"electrical": "hi-Z"}
