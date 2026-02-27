"""Parser for Altium .PrjPcb project files (INI format)."""

import configparser
from dataclasses import dataclass, field


@dataclass
class AltiumProject:
    """Parsed Altium project file."""

    hierarchy_mode: int = 1
    schematic_paths: list[str] = field(default_factory=list)


def parse_prjpcb(content: str) -> AltiumProject:
    """Parse a .PrjPcb file's text content into an AltiumProject."""
    parser = configparser.ConfigParser(strict=False)
    content = content.lstrip("\ufeff")
    parser.read_string(content)

    project = AltiumProject()

    if parser.has_section("Design"):
        project.hierarchy_mode = parser.getint("Design", "HierarchyMode", fallback=1)

    for section in parser.sections():
        if section.startswith("Document"):
            doc_path = parser.get(section, "DocumentPath", fallback="")
            if doc_path.lower().endswith(".schdoc"):
                project.schematic_paths.append(doc_path)

    return project


def parse_prjpcb_file(path: str) -> AltiumProject:
    """Read and parse a .PrjPcb file from disk."""
    with open(path, encoding="utf-8-sig") as f:
        return parse_prjpcb(f.read())
