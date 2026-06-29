"""Shared text utilities for EDA file parsing."""

from collections.abc import Sequence


def render_annotation_table(rows: Sequence[Sequence[str]]) -> str:
    """Render source-authored annotation table rows without inventing headers."""
    rendered_rows: list[str] = []
    for row in rows:
        cells = [_table_cell_text(cell) for cell in row]
        if any(cells):
            rendered_rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rendered_rows)


def _table_cell_text(value: str) -> str:
    text = "<br>".join(line.strip() for line in value.strip().splitlines())
    return text.replace("|", r"\|")


def strip_overline(name: str) -> tuple[str, bool]:
    """Strip overline markup from a signal name.

    Both Altium and OrCAD Capture encode active-low overlines with
    backslash-delimited characters (e.g., ``C\\S\\`` = CS̅).

    Returns ``(clean_name, had_overline)``.
    """
    if "\\" not in name:
        return name, False
    return name.replace("\\", ""), True
