"""Format-agnostic project variant domain model."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum

from phosphor_eda.domain.schematic import FootprintModel, LibraryLink, Parameter, PartNumber


class VariantTargetKind(StrEnum):
    """The kind of design object targeted by a variant override."""

    COMPONENT = "component"
    PAGE = "page"
    PROJECT = "project"
    OTHER = "other"


class VariantField(StrEnum):
    """Public domain field affected by a variant override."""

    FITTED = "fitted"
    DNP = "dnp"
    EXCLUDE_FROM_BOM = "exclude_from_bom"
    EXCLUDE_FROM_SIMULATION = "exclude_from_simulation"
    ALTERNATE_PART = "alternate_part"
    LIB = "lib"
    FOOTPRINTS = "footprints"
    PART_NUMBERS = "part_numbers"
    DATASHEET = "datasheet"
    PARAMETER = "parameter"
    OTHER = "other"


@dataclass(frozen=True)
class VariantTarget:
    """A source-level object reference targeted by a variant override."""

    kind: VariantTargetKind
    object_id: str = ""
    reference: str = ""
    occurrence_id: str = ""
    source_id: str = ""
    scope_path: str = ""
    parameter_name: str = ""


type VariantValue = (
    None
    | bool
    | str
    | Parameter
    | LibraryLink
    | tuple[FootprintModel, ...]
    | tuple[PartNumber, ...]
)


@dataclass(frozen=True)
class VariantOverride:
    """One native variant override normalized into public domain vocabulary."""

    variant_name: str
    target: VariantTarget
    field: VariantField
    value: VariantValue
    base_value: VariantValue = None
    source_id: str = ""
    native_kind: str = ""
    applied: bool = False
    metadata: dict[str, str] = dc_field(default_factory=dict)


@dataclass
class Variant:
    """A named project variant and its native overrides."""

    name: str
    description: str = ""
    order: int = 0
    overrides: list[VariantOverride] = dc_field(default_factory=list)
    source_id: str = ""
    metadata: dict[str, str] = dc_field(default_factory=dict)
