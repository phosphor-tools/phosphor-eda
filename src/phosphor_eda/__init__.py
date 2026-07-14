"""phosphor-eda: Query electronic schematics as structured data."""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import version as distribution_version
from typing import TYPE_CHECKING, Any

# Public entry points, resolved lazily from their owning modules so importing
# the package stays cheap (rendering pulls in heavy geometry dependencies).
_LAZY_EXPORTS = {
    "PCB_EXTENSIONS": "phosphor_eda.query.project_loader",
    "PROJECT_EXTENSIONS": "phosphor_eda.query.project_loader",
    "SCHEMATIC_EXTENSIONS": "phosphor_eda.query.project_loader",
    "load_design": "phosphor_eda.query.project_loader",
    "load_pcb": "phosphor_eda.query.project_loader",
    "load_project": "phosphor_eda.query.project_loader",
    "RenderSettings": "phosphor_eda.render.api",
    "render_pcb_svg": "phosphor_eda.render.api",
}

__all__ = [
    "PCB_EXTENSIONS",
    "PROJECT_EXTENSIONS",
    "SCHEMATIC_EXTENSIONS",
    "RenderSettings",
    "load_design",
    "load_pcb",
    "load_project",
    "render_pcb_svg",
]
__version__ = distribution_version("phosphor-eda")

if TYPE_CHECKING:
    from phosphor_eda.query.project_loader import (
        PCB_EXTENSIONS,
        PROJECT_EXTENSIONS,
        SCHEMATIC_EXTENSIONS,
        load_design,
        load_pcb,
        load_project,
    )
    from phosphor_eda.render.api import RenderSettings, render_pcb_svg


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
