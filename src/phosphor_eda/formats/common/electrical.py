"""Canonical pin-electrical vocabulary shared across parsers.

Each parser maps its native pin-type strings into a single canonical
vocabulary (``PinElectrical``) so the serializer, validator and SQL loader
can reason about pin electrical types without knowing the source format.

``passive`` is the default for the vast majority of pins and is omitted from
pin metadata — :func:`set_pin_electrical` encodes that skip-when-passive rule
that all parsers share.
"""

from __future__ import annotations

from enum import StrEnum

# Metadata key under which the canonical electrical value is stored.
ELECTRICAL_KEY = "electrical"


class PinElectrical(StrEnum):
    """Canonical pin electrical types.

    Values are the exact strings serialized into pin metadata — do not change
    them without updating fixtures.
    """

    INPUT = "input"
    OUTPUT = "output"
    IO = "IO"
    HI_Z = "hi-Z"
    PASSIVE = "passive"
    UNSPECIFIED = "unspecified"
    POWER = "power"
    OPEN_COLLECTOR = "open-collector"
    OPEN_EMITTER = "open-emitter"
    NO_CONNECT = "no-connect"


# KiCad pin electrical types -> canonical vocabulary.
KICAD_ELECTRICAL_MAP: dict[str, PinElectrical] = {
    "input": PinElectrical.INPUT,
    "output": PinElectrical.OUTPUT,
    "bidirectional": PinElectrical.IO,
    "tri_state": PinElectrical.HI_Z,
    "passive": PinElectrical.PASSIVE,
    "free": PinElectrical.UNSPECIFIED,
    "unspecified": PinElectrical.UNSPECIFIED,
    "power_in": PinElectrical.POWER,
    "power_out": PinElectrical.POWER,
    "open_collector": PinElectrical.OPEN_COLLECTOR,
    "open_emitter": PinElectrical.OPEN_EMITTER,
    "no_connect": PinElectrical.NO_CONNECT,
}

# Eagle pin direction attribute -> canonical vocabulary.
EAGLE_DIRECTION_MAP: dict[str, PinElectrical] = {
    "pas": PinElectrical.PASSIVE,
    "in": PinElectrical.INPUT,
    "out": PinElectrical.OUTPUT,
    "io": PinElectrical.IO,
    "sup": PinElectrical.POWER,
    "nc": PinElectrical.NO_CONNECT,
    "hiz": PinElectrical.HI_Z,
    "oc": PinElectrical.OPEN_COLLECTOR,
    "pwr": PinElectrical.POWER,
}


def set_pin_electrical(metadata: dict[str, str], value: PinElectrical | None) -> None:
    """Write the canonical electrical value into pin metadata.

    Skips ``passive`` (the default) and ``None`` so they never appear in
    serialized output — this is the rule every parser applies.
    """
    if value is not None and value is not PinElectrical.PASSIVE:
        metadata[ELECTRICAL_KEY] = value.value
