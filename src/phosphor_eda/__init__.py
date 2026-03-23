"""phosphor-eda: Query electronic schematics as structured data."""

from phosphor_eda.convert import (
    SCHEMATIC_EXTENSIONS,
    convert,
    load_design,
)

__all__ = ["SCHEMATIC_EXTENSIONS", "convert", "load_design"]
__version__ = "0.1.0"
