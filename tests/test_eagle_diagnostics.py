"""Diagnostics for the Eagle schematic converter.

A part that references a missing library or deviceset is dropped; that drop
must be observable via a recorded warning, not silent. Malformed vendor input
must degrade with structured diagnostics or a named, actionable error rather
than surfacing an opaque XML traceback.
"""

from pathlib import Path

import pytest

from phosphor_eda.formats.eagle.to_schematic import EagleFormatError, eagle_to_design
from phosphor_eda.query.project_loader import load_design

_KICAD_LEGACY_SCH = Path(__file__).resolve().parent / "fixtures/kicad-orangecrab/OrangeCrab.sch"

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


_SCH_WITH_MALFORMED_COORDINATE = """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.0.0">
  <drawing>
    <schematic>
      <libraries>
        <library name="test">
          <symbols>
            <symbol name="RES"><pin name="A" direction="pas"/></symbol>
          </symbols>
          <devicesets>
            <deviceset name="R" prefix="R">
              <gates><gate name="G$1" symbol="RES"/></gates>
              <devices>
                <device name="" package="R0603">
                  <connects><connect gate="G$1" pin="A" pad="1"/></connects>
                </device>
              </devices>
            </deviceset>
          </devicesets>
        </library>
      </libraries>
      <parts>
        <part name="R1" library="test" deviceset="R" device=""/>
      </parts>
      <sheets>
        <sheet>
          <instances><instance part="R1" gate="G$1" x="1" y="not-a-number"/></instances>
          <nets>
            <net name="N" class="0">
              <segment><pinref part="R1" gate="G$1" pin="A"/></segment>
            </net>
          </nets>
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


def test_eagle_malformed_coordinate_degrades_with_diagnostic(tmp_path: Path) -> None:
    sch = tmp_path / "bad-coord.sch"
    sch.write_text(_SCH_WITH_MALFORMED_COORDINATE)

    design = eagle_to_design(sch)

    # The part survives (degrade, not crash); the unparseable coordinate is
    # dropped to None and the drop is surfaced as a parse issue.
    r1 = next(c for c in design.components if c.reference == "R1")
    occurrence = r1.occurrences[0]
    assert occurrence.x == 1.0
    assert occurrence.y is None
    assert design.metadata.get("parse_issue_count") == "1"


def test_eagle_malformed_rotation_degrades_with_diagnostic(tmp_path: Path) -> None:
    sch = tmp_path / "bad-rot.sch"
    sch.write_text(
        _SCH_WITH_MALFORMED_COORDINATE.replace('x="1" y="not-a-number"', 'x="1" y="2" rot="Rnope"')
    )

    design = eagle_to_design(sch)

    # The instance survives (degrade, not crash); the unparseable rotation
    # falls back to 0 and the drop is surfaced as a parse issue.
    r1 = next(c for c in design.components if c.reference == "R1")
    occurrence = r1.occurrences[0]
    assert occurrence.rotation == 0.0
    assert design.metadata.get("parse_issue_count") == "1"


def test_eagle_malformed_xml_raises_named_error(tmp_path: Path) -> None:
    sch = tmp_path / "broken.sch"
    sch.write_text("<eagle><drawing><unclosed>")

    with pytest.raises(EagleFormatError) as exc_info:
        eagle_to_design(sch)

    assert "broken.sch" in str(exc_info.value)


def test_kicad_legacy_sch_routed_to_eagle_raises_actionable_error() -> None:
    # A KiCad v4 legacy .sch shares the extension with Eagle and would otherwise
    # surface an opaque XML ParseError. The parser must name the detected format.
    with pytest.raises(EagleFormatError) as exc_info:
        eagle_to_design(_KICAD_LEGACY_SCH)

    message = str(exc_info.value)
    assert "OrangeCrab.sch" in message
    assert "KiCad legacy" in message


def test_load_design_kicad_legacy_sch_raises_actionable_error() -> None:
    # The same actionable error must reach callers routing .sch through the
    # public loader dispatch, not just direct eagle_to_design callers.
    with pytest.raises(ValueError, match="KiCad legacy") as exc_info:
        load_design(_KICAD_LEGACY_SCH)

    assert "OrangeCrab.sch" in str(exc_info.value)
