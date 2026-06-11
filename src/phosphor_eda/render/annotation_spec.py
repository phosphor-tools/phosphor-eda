"""Annotation input types parsed from JSON, plus the JSON parser.

These dataclasses are the validated form of the user-supplied annotation
block; ``parse_annotations`` turns raw JSON into an ``AnnotationSpec`` and
raises ``ValueError`` on malformed input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

# JSON data from json.loads() is inherently untyped — Any is the correct
# boundary type for validating external input before converting to dataclasses.
type JsonDict = dict[str, Any]


@dataclass
class BoxSpec:
    """Annotation box around one or more components."""

    targets: list[str]
    label: str = ""
    label_position: str = ""  # "" = auto, or "above"/"below"/"left"/"right"
    color: str = ""


@dataclass
class PointerSpec:
    """Arrow pointing at a component, pad, or net pad."""

    target: str = ""  # "U7", "U7.10", or ""
    target_net: str = ""  # net name (for net+near targeting)
    target_near: str = ""  # component ref (for net+near targeting)
    label: str = ""
    position: str = ""  # "" = auto, or position hint
    color: str = ""


@dataclass
class LabelSpec:
    """Text label attached to a component."""

    target: str = ""
    content: str = ""
    position: str = ""  # "" = auto, or hint incl. "board-*"


@dataclass
class LegendEntry:
    """Single entry in a color legend."""

    color: str
    label: str


@dataclass
class LegendSpec:
    """Color-keyed legend block."""

    title: str
    entries: list[LegendEntry]
    position: str = ""  # "" = auto, or "board-bottom" etc.


@dataclass
class AnnotationSpec:
    """Complete annotation specification parsed from JSON."""

    boxes: list[BoxSpec]
    pointers: list[PointerSpec]
    labels: list[LabelSpec]
    legend: LegendSpec | None = None


def parse_annotations(data: JsonDict) -> AnnotationSpec:
    """Validate JSON dict and return an ``AnnotationSpec``.

    Raises ``ValueError`` for missing required fields.
    """
    boxes: list[BoxSpec] = []
    for i, raw in enumerate(_as_list(data.get("boxes"))):
        d = _as_dict(raw, f"boxes[{i}]")
        raw_targets = d.get("targets")
        if not raw_targets or not isinstance(raw_targets, list):
            msg = f"boxes[{i}]: 'targets' is required and must be a non-empty list"
            raise ValueError(msg)
        target_strs: list[str] = [str(t) for t in cast("list[object]", raw_targets)]
        boxes.append(
            BoxSpec(
                targets=target_strs,
                label=str(d.get("label") or ""),
                label_position=str(d.get("label_position") or ""),
                color=str(d.get("color") or ""),
            )
        )

    pointers: list[PointerSpec] = []
    for i, raw in enumerate(_as_list(data.get("pointers"))):
        d = _as_dict(raw, f"pointers[{i}]")
        target = str(d.get("target") or "")
        target_net = str(d.get("target_net") or "")
        target_near = str(d.get("target_near") or "")
        if not target and not (target_net and target_near):
            msg = f"pointers[{i}]: 'target' or both 'target_net'+'target_near' required"
            raise ValueError(msg)
        pointers.append(
            PointerSpec(
                target=target,
                target_net=target_net,
                target_near=target_near,
                label=str(d.get("label") or ""),
                position=str(d.get("position") or ""),
                color=str(d.get("color") or ""),
            )
        )

    labels: list[LabelSpec] = []
    for i, raw in enumerate(_as_list(data.get("labels"))):
        d = _as_dict(raw, f"labels[{i}]")
        labels.append(
            LabelSpec(
                target=str(d.get("target") or ""),
                content=str(d.get("content") or ""),
                position=str(d.get("position") or ""),
            )
        )

    legend: LegendSpec | None = None
    raw_legend = data.get("legend")
    if raw_legend is not None:
        ld = _as_dict(raw_legend, "legend")
        raw_entries = ld.get("entries")
        if not raw_entries or not isinstance(raw_entries, list):
            msg = "legend: 'entries' is required and must be a non-empty list"
            raise ValueError(msg)
        entries: list[LegendEntry] = []
        for j, entry_raw in enumerate(cast("list[object]", raw_entries)):
            ed = _as_dict(entry_raw, f"legend.entries[{j}]")
            entries.append(
                LegendEntry(
                    color=str(ed.get("color") or ""),
                    label=str(ed.get("label") or ""),
                )
            )
        legend = LegendSpec(
            title=str(ld.get("title") or ""),
            entries=entries,
            position=str(ld.get("position") or ""),
        )

    return AnnotationSpec(boxes=boxes, pointers=pointers, labels=labels, legend=legend)


def _as_list(val: object) -> list[object]:
    if isinstance(val, list):
        return cast("list[object]", val)
    return []


def _as_dict(val: object, context: str) -> JsonDict:
    if isinstance(val, dict):
        return cast("JsonDict", val)
    msg = f"{context}: expected object, got {type(val).__name__}"
    raise ValueError(msg)
