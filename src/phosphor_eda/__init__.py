"""phosphor-eda: Query electronic schematics as structured data."""

from phosphor_eda.convert import (
    PCB_EXTENSIONS,
    SCHEMATIC_EXTENSIONS,
    convert,
    load_design,
    load_project,
)

__all__ = ["PCB_EXTENSIONS", "SCHEMATIC_EXTENSIONS", "convert", "load_design", "load_project"]
__version__ = "0.1.0"
