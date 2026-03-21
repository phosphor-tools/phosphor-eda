"""phosphor-eda: Convert EE documents to LLM-friendly text."""

from phosphor_eda.convert import (
    SCHEMATIC_EXTENSIONS,
    convert,
    convert_directory,
    load_design,
)

__all__ = ["SCHEMATIC_EXTENSIONS", "convert", "convert_directory", "load_design"]
__version__ = "0.1.0"
