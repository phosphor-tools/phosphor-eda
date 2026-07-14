"""Diagnostics for the Eagle schematic converter.

A part that references a missing library or deviceset is dropped; that drop
must be observable via a recorded warning, not silent.
"""

from pathlib import Path

from phosphor_eda.formats.eagle.to_schematic import eagle_to_design

_SCH_WITH_MISSING_LIBRARY = """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.0.0">
  <drawing>
    <schematic>
      <libraries>
        <library name="present">
          <devicesets>
            <deviceset name="DS"><gates/><devices/></deviceset>
          </devicesets>
        </library>
      </libraries>
      <parts>
        <part name="U1" library="ghost" deviceset="DS"/>
      </parts>
      <sheets>
        <sheet>
          <instances>
            <instance part="U1" gate="G$1" x="0" y="0"/>
          </instances>
        </sheet>
      </sheets>
    </schematic>
  </drawing>
</eagle>
"""


def test_eagle_dropped_part_records_issue(tmp_path: Path) -> None:
    sch = tmp_path / "missing.sch"
    sch.write_text(_SCH_WITH_MISSING_LIBRARY)

    design = eagle_to_design(sch)

    # The part was dropped (its library doesn't exist), and the drop is
    # surfaced as a parse issue count in the design metadata.
    assert "U1" not in {c.reference for c in design.components}
    assert design.metadata.get("parse_issue_count") == "1"


def test_eagle_dropped_part_surfaces_diagnostic_messages(tmp_path: Path) -> None:
    sch = tmp_path / "missing.sch"
    sch.write_text(_SCH_WITH_MISSING_LIBRARY)

    design = eagle_to_design(sch)

    # The drop message itself is surfaced, not just the count, so callers can
    # report what degraded rather than only how many issues occurred.
    issues = design.metadata.get("parse_issues", "")
    assert "eagle_missing_library" in issues
    assert "U1" in issues
    assert "ghost" in issues
