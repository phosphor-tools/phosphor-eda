"""Parser for Altium ``.Annotation`` files (per-instance physical designators).

Multi-channel and repeated hierarchical sheets give each logical component a
distinct physical reference designator per instance. Altium records these in a
sibling ``<project>.Annotation`` file under the ``[DesignatorManager]`` section,
keyed by the component's hierarchical unique-id path
(e.g. ``\\FEHIXTLT\\VIIQXJDH`` -> ``U1.3``).
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.formats.common.diagnostics import ParseContext

_UNIQUE_ID_KEY = re.compile(r"uniqueid(\d+)")


@dataclass(frozen=True, slots=True)
class AnnotationDesignator:
    """A per-instance designator entry from an Altium ``.Annotation`` file.

    ``physical_designator`` is the assigned reference for this instance (e.g.
    ``U1.3``). ``logical_designator`` (e.g. ``U1``) lets callers sanity-check
    the mapping; ``channel_name`` is useful provenance for repeated sheets.
    """

    physical_designator: str
    logical_designator: str = ""
    channel_name: str = ""


def parse_annotation_designators(
    content: str,
    ctx: ParseContext | None = None,
) -> dict[str, AnnotationDesignator]:
    """Map hierarchical unique-id paths to per-instance designator entries.

    Each ``[DesignatorManager]`` entry ``N`` pairs a ``UniqueID<N>`` path with
    the ``PhysicalDesignator<N>`` assigned to that instance, alongside the
    ``LogicalDesignator<N>`` and ``ChannelName<N>``. Returns a mapping for every
    entry that has both a unique-id path and a physical designator.

    Malformed content is reported on *ctx* (when provided) and yields an empty
    mapping rather than raising.
    """
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    content = content.lstrip("﻿")
    try:
        parser.read_string(content)
    except configparser.Error as exc:
        if ctx is not None:
            ctx.warn("annotation_parse_error", f"Could not parse .Annotation file: {exc}")
        return {}

    if not parser.has_section("DesignatorManager"):
        return {}

    section = parser["DesignatorManager"]
    result: dict[str, AnnotationDesignator] = {}
    # configparser lowercases keys; match UniqueID<N> and pair with the
    # PhysicalDesignator of the same index.
    for key, value in section.items():
        match = _UNIQUE_ID_KEY.fullmatch(key)
        if match is None:
            continue
        index = match.group(1)
        uid_path = value.strip()
        physical = section.get(f"physicaldesignator{index}", "").strip()
        if not uid_path or not physical:
            continue
        result[uid_path] = AnnotationDesignator(
            physical_designator=physical,
            logical_designator=section.get(f"logicaldesignator{index}", "").strip(),
            channel_name=section.get(f"channelname{index}", "").strip(),
        )
    return result


def load_annotation_designators(
    prjpcb_path: Path,
    ctx: ParseContext | None = None,
) -> dict[str, AnnotationDesignator]:
    """Load per-instance designators from a project's sibling ``.Annotation`` file.

    Returns an empty mapping when no ``.Annotation`` file accompanies the
    project (single sheets and un-annotated projects have none). Unreadable or
    malformed files are reported on *ctx* and yield an empty mapping.
    """
    annotation_path = prjpcb_path.with_suffix(".Annotation")
    if not annotation_path.is_file():
        return {}
    try:
        content = annotation_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        content = annotation_path.read_text(encoding="latin-1")
    except OSError as exc:
        if ctx is not None:
            ctx.warn("annotation_parse_error", f"Could not read .Annotation file: {exc}")
        return {}
    return parse_annotation_designators(content, ctx=ctx)
