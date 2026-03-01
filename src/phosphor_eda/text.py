"""Shared text utilities for EDA file parsing."""


def strip_overline(name: str) -> tuple[str, bool]:
    """Strip overline markup from a signal name.

    Both Altium and OrCAD Capture encode active-low overlines with
    backslash-delimited characters (e.g., ``C\\S\\`` = CS̅).

    Returns ``(clean_name, had_overline)``.
    """
    if "\\" not in name:
        return name, False
    return name.replace("\\", ""), True
