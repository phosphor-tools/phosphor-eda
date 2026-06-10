"""Re-export shared models for backwards compatibility."""

from phosphor_eda.formats.common.raw_models import (
    GraphicInst,
    NetIdMapping,
    NetlistEntry,
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    SchematicPage,
    SymbolDisplayProp,
    Wire,
    WireAlias,
)

__all__ = [
    "GraphicInst",
    "NetIdMapping",
    "NetlistEntry",
    "PageNetEntry",
    "ParsedDesign",
    "PinConnection",
    "PlacedInstance",
    "SchematicPage",
    "SymbolDisplayProp",
    "Wire",
    "WireAlias",
]
