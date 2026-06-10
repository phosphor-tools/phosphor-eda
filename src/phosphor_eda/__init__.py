"""phosphor-eda: Query electronic schematics as structured data."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__all__ = ["PCB_EXTENSIONS", "SCHEMATIC_EXTENSIONS", "convert", "load_design", "load_project"]
__version__ = "0.1.0"

if TYPE_CHECKING:
    from phosphor_eda.query.convert import (
        PCB_EXTENSIONS,
        SCHEMATIC_EXTENSIONS,
        convert,
        load_design,
        load_project,
    )


def __getattr__(name: str) -> Any:
    if name not in __all__:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    convert_module = import_module("phosphor_eda.query.convert")
    value = getattr(convert_module, name)
    globals()[name] = value
    return value
