"""Low-level helpers shared across the KiCad PCB parser modules.

Coordinate/transform math, object-metadata construction, and the per-item
flag/net accessors that every section parser (footprint, zones, board)
depends on.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import sexpdata

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.domain.pcb import PcbNet, PcbObjectMetadata

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb_builder import PcbBuilder
    from phosphor_eda.formats.kicad.sexp import SExpNode


def xy(item: SExpNode) -> tuple[float, float]:
    return (sexp.num(item, 1), sexp.num(item, 2))


def at(item: SExpNode) -> tuple[float, float, float]:
    x = sexp.num(item, 1)
    y = sexp.num(item, 2)
    rotation = 0.0
    if len(item) > 3 and isinstance(item[3], (int, float)):
        rotation = float(item[3])
    return (x, y, rotation)


def transform_point(
    local_x: float,
    local_y: float,
    fp_x: float,
    fp_y: float,
    fp_rot_deg: float,
) -> tuple[float, float]:
    rad = math.radians(-fp_rot_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)
    return (
        fp_x + local_x * cos_r - local_y * sin_r,
        fp_y + local_x * sin_r + local_y * cos_r,
    )


def transform_rotation(local_rotation: float, fp_rotation: float) -> float:
    return fp_rotation + local_rotation


def maybe_transform(
    point: tuple[float, float],
    transform: tuple[float, float, float] | None,
) -> tuple[float, float]:
    if transform is None:
        return point
    return transform_point(point[0], point[1], transform[0], transform[1], transform[2])


def layer_names(item: SExpNode | None) -> list[str]:
    if item is None:
        return []
    result: list[str] = []
    for value in item[1:]:
        if isinstance(value, sexpdata.Symbol):
            result.append(value.value())
        elif isinstance(value, str):
            result.append(value)
    return result


def object_metadata(
    *,
    native_type: str,
    source_collection: str,
    native_kind: str = "",
    native_id: str = "",
    native_index: int | None = None,
    locked: bool = False,
    hidden: bool = False,
    properties: dict[str, str] | None = None,
) -> PcbObjectMetadata:
    return PcbObjectMetadata(
        source_format="kicad",
        native_type=native_type,
        native_kind=native_kind,
        native_id=native_id,
        native_index=native_index,
        source_collection=source_collection,
        locked=locked,
        hidden=hidden,
        properties=properties or {},
    )


def item_uuid(item: SExpNode) -> str:
    uuid_node = sexp.find(item, "uuid") or sexp.find(item, "tstamp")
    return sexp.val(uuid_node) if uuid_node else ""


def item_flag(item: SExpNode, flag: str) -> bool:
    """Read a KiCad boolean property in either the bare-symbol form (``locked``)
    or the list form (``(locked yes)`` / ``(locked no)``)."""
    for node in item:
        if isinstance(node, sexpdata.Symbol) and node.value() == flag:
            return True
        if isinstance(node, list) and sexp.tag(node) == flag:
            if len(node) < 2:
                return True
            return sexp_bool(node[1], default=True)
    return False


def item_locked(item: SExpNode) -> bool:
    return item_flag(item, "locked")


def item_hidden(item: SExpNode) -> bool:
    return item_flag(item, "hide")


def sexp_bool(value: object, *, default: bool) -> bool:
    raw = value.value() if isinstance(value, sexpdata.Symbol) else str(value)
    normalized = raw.lower()
    if normalized in {"yes", "true"}:
        return True
    if normalized in {"no", "false"}:
        return False
    return default


def resolve_net_node(builder: PcbBuilder, item: SExpNode, *, source: str) -> PcbNet | None:
    net_node = sexp.find(item, "net")
    if not net_node or len(net_node) < 2:
        return None
    if isinstance(net_node[1], str):
        # KiCad 10 references nets by name string instead of table number.
        return builder.resolve_net_name(net_node[1], source=source)
    number = int(sexp.num(net_node, 1))
    if number == 0:
        return None
    return builder.resolve_net_number(number, source=source)
