"""Project-level enrichment loader for native Allegro board files."""

from __future__ import annotations

from pathlib import Path

from phosphor_eda.domain.project import Project, ProjectMetadata
from phosphor_eda.formats.allegro.constraints import extract_allegro_constraints
from phosphor_eda.formats.allegro.parser import parse_allegro_records


def load_allegro_pcb_project(path: str | Path) -> Project:
    """Load board-side Allegro project enrichment without constructing a Board."""
    board_path = Path(path)
    record_set = parse_allegro_records(board_path.read_bytes(), source_name=board_path.name)
    constraints = extract_allegro_constraints(record_set)

    return Project(
        name=board_path.stem,
        metadata=ProjectMetadata(
            name=board_path.stem,
            format="allegro",
            format_version=record_set.header.version.value if record_set.header else "",
            source_paths=[str(board_path)],
        ),
        net_classes=constraints.net_classes,
        design_rules=constraints.design_rules,
        diff_pairs=constraints.diff_pairs,
    )
