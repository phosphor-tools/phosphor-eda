"""Intermediate primitives for Allegro board graphics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    PcbArc,
    PcbCircle,
    PcbConductorKind,
    PcbLayer,
    PcbLine,
    PcbObjectMetadata,
    PcbPolygon,
    PcbText,
)

if TYPE_CHECKING:
    from phosphor_eda.formats.allegro.records import AllegroRecordDiagnostic


class AllegroPrimitiveRole(StrEnum):
    BOARD_PROFILE = "board_profile"
    ARTWORK = "artwork"
    TEXT = "text"
    KEEPOUT = "keepout"
    DRC_MARKER = "drc_marker"


class AllegroPrimitiveKind(StrEnum):
    LINE = "line"
    ARC = "arc"
    CIRCLE = "circle"
    RECTANGLE = "rectangle"
    POLYGON = "polygon"
    TEXT = "text"


type AllegroPrimitivePayload = PcbLine | PcbArc | PcbCircle | PcbPolygon | PcbText


@dataclass(frozen=True, kw_only=True)
class AllegroGraphicPrimitive:
    id: str
    kind: AllegroPrimitiveKind
    roles: tuple[AllegroPrimitiveRole, ...]
    data: AllegroPrimitivePayload
    layer: PcbLayer | None
    source_tag: int
    source_key: int
    is_cutout: bool = False
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)

    def has_role(self, role: AllegroPrimitiveRole | str) -> bool:
        normalized = role if isinstance(role, AllegroPrimitiveRole) else AllegroPrimitiveRole(role)
        return normalized in self.roles


@dataclass(frozen=True)
class AllegroGraphics:
    board_profile: tuple[AllegroGraphicPrimitive, ...] = ()
    artwork: tuple[AllegroGraphicPrimitive, ...] = ()
    keepouts: tuple[AllegroGraphicPrimitive, ...] = ()
    diagnostics: tuple[AllegroRecordDiagnostic, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AllegroConductorPrimitive:
    id: str
    kind: PcbConductorKind
    data: PcbLine | PcbArc | PcbCircle | PcbPolygon
    layer: PcbLayer
    net_key: int | None = None
    footprint_key: int | None = None
    pour_id: str | None = None
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)


@dataclass(frozen=True, kw_only=True)
class AllegroPourPrimitive:
    id: str
    boundary: PcbPolygon
    layer: PcbLayer
    net_key: int | None = None
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)


@dataclass(frozen=True)
class AllegroCopper:
    pours: tuple[AllegroPourPrimitive, ...] = ()
    conductors: tuple[AllegroConductorPrimitive, ...] = ()
    diagnostics: tuple[AllegroRecordDiagnostic, ...] = ()
