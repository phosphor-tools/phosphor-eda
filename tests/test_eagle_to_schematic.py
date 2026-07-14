"""Tests for Eagle schematic parser."""

from pathlib import Path

import pytest

from phosphor_eda.formats.eagle import eagle_to_design
from phosphor_eda.query.format import serialize_design
from phosphor_eda.query.validate import Severity, validate_design

UPSTREAM_FIXTURES = Path(__file__).resolve().parent / "upstream"
BME280_SCH = UPSTREAM_FIXTURES / "sparkfun-bme280/Hardware/SparkFun_BME280_Breakout.sch"
ADAFRUIT_SCH = UPSTREAM_FIXTURES / "adafruit-rgb-lcd-shield/adafruit_rgblcdshield.sch"


@pytest.fixture(scope="module")
def design():
    return eagle_to_design(BME280_SCH)


@pytest.fixture(scope="module")
def adafruit_design():
    return eagle_to_design(ADAFRUIT_SCH)


def _find_component(design, ref: str):
    for c in design.components:
        if c.reference == ref:
            return c
    return None


def _find_net(design, name: str):
    for n in design.nets:
        if n.name == name:
            return n
    return None


def _write_multisheet_eagle_same_name_net(tmp_path: Path) -> Path:
    schematic = tmp_path / "same-name-net.sch"
    schematic.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.6.2">
  <drawing>
    <schematic>
      <libraries>
        <library name="test">
          <symbols>
            <symbol name="RES">
              <pin name="A" direction="pas"/>
            </symbol>
          </symbols>
          <devicesets>
            <deviceset name="R" prefix="R">
              <gates>
                <gate name="G$1" symbol="RES"/>
              </gates>
              <devices>
                <device name="" package="R0603">
                  <connects>
                    <connect gate="G$1" pin="A" pad="1"/>
                  </connects>
                </device>
              </devices>
            </deviceset>
          </devicesets>
        </library>
      </libraries>
      <parts>
        <part name="R1" library="test" deviceset="R" device=""/>
        <part name="R2" library="test" deviceset="R" device=""/>
      </parts>
      <sheets>
        <sheet>
          <instances>
            <instance part="R1" gate="G$1" x="1" y="2"/>
          </instances>
          <nets>
            <net name="SHARED" class="0">
              <segment>
                <pinref part="R1" gate="G$1" pin="A"/>
              </segment>
            </net>
          </nets>
        </sheet>
        <sheet>
          <instances>
            <instance part="R2" gate="G$1" x="3" y="4"/>
          </instances>
          <nets>
            <net name="SHARED" class="0">
              <segment>
                <pinref part="R2" gate="G$1" pin="A"/>
              </segment>
            </net>
          </nets>
        </sheet>
      </sheets>
    </schematic>
  </drawing>
</eagle>
""",
        encoding="utf-8",
    )
    return schematic


def _write_eagle_annotations_fixture(tmp_path: Path) -> Path:
    schematic = tmp_path / "annotations.sch"
    schematic.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.6.2">
  <drawing>
    <schematic>
      <libraries>
        <library name="test">
          <symbols>
            <symbol name="SYM">
              <text x="0" y="0" size="1" layer="94">Symbol artwork</text>
              <pin name="A" direction="pas"/>
            </symbol>
          </symbols>
          <devicesets>
            <deviceset name="PART" prefix="U">
              <gates>
                <gate name="G$1" symbol="SYM"/>
              </gates>
              <devices>
                <device name="" package="PKG"/>
              </devices>
            </deviceset>
          </devicesets>
        </library>
      </libraries>
      <parts>
        <part name="U1" library="test" deviceset="PART" device="" value="VALUE"/>
      </parts>
      <sheets>
        <sheet>
          <plain>
            <text x="1" y="2" size="1.778" layer="97">Board note
line 2</text>
            <text x="3" y="4" size="1.778" layer="97">&gt;DRAWING_NAME</text>
          </plain>
          <instances>
            <instance part="U1" gate="G$1" x="1" y="2">
              <attribute name="NAME" value="U1" x="1" y="2"/>
            </instance>
          </instances>
          <nets>
            <net name="SDA" class="0">
              <segment>
                <label x="0" y="0" size="1.778" layer="95"/>
              </segment>
            </net>
          </nets>
        </sheet>
      </sheets>
    </schematic>
  </drawing>
</eagle>
""",
        encoding="utf-8",
    )
    return schematic


def _write_multipart_eagle_component(tmp_path: Path) -> Path:
    schematic = tmp_path / "multipart.sch"
    schematic.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.6.2">
  <drawing>
    <schematic>
      <libraries>
        <library name="test">
          <symbols>
            <symbol name="AMP">
              <pin name="IN" direction="in"/>
              <pin name="OUT" direction="out"/>
            </symbol>
          </symbols>
          <devicesets>
            <deviceset name="DUAL">
              <gates>
                <gate name="A" symbol="AMP"/>
                <gate name="B" symbol="AMP"/>
              </gates>
              <devices>
                <device name="" package="SOIC8">
                  <connects>
                    <connect gate="A" pin="IN" pad="1"/>
                    <connect gate="A" pin="OUT" pad="2"/>
                    <connect gate="B" pin="IN" pad="3"/>
                    <connect gate="B" pin="OUT" pad="4"/>
                  </connects>
                </device>
              </devices>
            </deviceset>
          </devicesets>
        </library>
      </libraries>
      <parts>
        <part name="U1" library="test" deviceset="DUAL" device="" value="LM358"/>
      </parts>
      <sheets>
        <sheet>
          <instances>
            <instance part="U1" gate="A" x="1" y="2"/>
            <instance part="U1" gate="B" x="3" y="4" rot="MR90"/>
          </instances>
          <nets>
            <net name="AIN" class="0">
              <segment><pinref part="U1" gate="A" pin="IN"/></segment>
            </net>
            <net name="AOUT" class="0">
              <segment><pinref part="U1" gate="A" pin="OUT"/></segment>
            </net>
            <net name="BIN" class="0">
              <segment><pinref part="U1" gate="B" pin="IN"/></segment>
            </net>
            <net name="BOUT" class="0">
              <segment><pinref part="U1" gate="B" pin="OUT"/></segment>
            </net>
          </nets>
        </sheet>
      </sheets>
    </schematic>
  </drawing>
</eagle>
""",
        encoding="utf-8",
    )
    return schematic


def _write_multipart_eagle_component_across_pages(tmp_path: Path) -> Path:
    schematic = tmp_path / "multipart-across-pages.sch"
    schematic.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.6.2">
  <drawing>
    <schematic>
      <libraries>
        <library name="test">
          <symbols>
            <symbol name="AMP">
              <pin name="IN" direction="in"/>
              <pin name="OUT" direction="out"/>
            </symbol>
          </symbols>
          <devicesets>
            <deviceset name="DUAL">
              <gates>
                <gate name="A" symbol="AMP"/>
                <gate name="B" symbol="AMP"/>
              </gates>
              <devices>
                <device name="" package="SOIC8">
                  <connects>
                    <connect gate="A" pin="IN" pad="1"/>
                    <connect gate="A" pin="OUT" pad="2"/>
                    <connect gate="B" pin="IN" pad="3"/>
                    <connect gate="B" pin="OUT" pad="4"/>
                  </connects>
                </device>
              </devices>
            </deviceset>
          </devicesets>
        </library>
      </libraries>
      <parts>
        <part name="U1" library="test" deviceset="DUAL" device="" value="LM358"/>
      </parts>
      <sheets>
        <sheet>
          <instances>
            <instance part="U1" gate="A" x="1" y="2"/>
          </instances>
          <nets>
            <net name="AIN" class="0">
              <segment><pinref part="U1" gate="A" pin="IN"/></segment>
            </net>
            <net name="AOUT" class="0">
              <segment><pinref part="U1" gate="A" pin="OUT"/></segment>
            </net>
          </nets>
        </sheet>
        <sheet>
          <instances>
            <instance part="U1" gate="B" x="3" y="4" rot="MR90"/>
          </instances>
          <nets>
            <net name="BIN" class="0">
              <segment><pinref part="U1" gate="B" pin="IN"/></segment>
            </net>
            <net name="BOUT" class="0">
              <segment><pinref part="U1" gate="B" pin="OUT"/></segment>
            </net>
          </nets>
        </sheet>
      </sheets>
    </schematic>
  </drawing>
</eagle>
""",
        encoding="utf-8",
    )
    return schematic


def _write_multipad_eagle_component(tmp_path: Path) -> Path:
    schematic = tmp_path / "multipad.sch"
    schematic.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.6.2">
  <drawing>
    <schematic>
      <libraries>
        <library name="test">
          <symbols>
            <symbol name="SYM">
              <pin name="P" direction="pas"/>
            </symbol>
          </symbols>
          <devicesets>
            <deviceset name="DS" prefix="U">
              <gates>
                <gate name="G$1" symbol="SYM"/>
              </gates>
              <devices>
                <device name="" package="PKG">
                  <connects>
                    <connect gate="G$1" pin="P" pad="3 9"/>
                  </connects>
                </device>
              </devices>
            </deviceset>
          </devicesets>
        </library>
      </libraries>
      <parts>
        <part name="U1" library="test" deviceset="DS" device="" value="X"/>
      </parts>
      <sheets>
        <sheet>
          <instances>
            <instance part="U1" gate="G$1" x="1" y="2"/>
          </instances>
          <nets>
            <net name="N1" class="0">
              <segment><pinref part="U1" gate="G$1" pin="P"/></segment>
            </net>
          </nets>
        </sheet>
      </sheets>
    </schematic>
  </drawing>
</eagle>
""",
        encoding="utf-8",
    )
    return schematic


def _write_eagle_net_without_pinrefs(tmp_path: Path) -> Path:
    schematic = tmp_path / "empty-net.sch"
    schematic.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.6.2">
  <drawing>
    <schematic>
      <libraries>
        <library name="test">
          <symbols>
            <symbol name="RES"><pin name="A" direction="pas"/></symbol>
          </symbols>
          <devicesets>
            <deviceset name="R">
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
          <instances><instance part="R1" gate="G$1" x="1" y="2"/></instances>
          <nets>
            <net name="USED" class="0">
              <segment><pinref part="R1" gate="G$1" pin="A"/></segment>
            </net>
            <net name="USED" class="0">
              <segment><wire x1="0" y1="0" x2="1" y2="0" width="0.1524" layer="91"/></segment>
            </net>
            <net name="EMPTY" class="0">
              <segment><wire x1="0" y1="1" x2="1" y2="1" width="0.1524" layer="91"/></segment>
            </net>
          </nets>
        </sheet>
      </sheets>
    </schematic>
  </drawing>
</eagle>
""",
        encoding="utf-8",
    )
    return schematic


# --- Components ---


def test_design_has_components(design):
    assert len(design.components) > 0


def test_bme280_component(design):
    u1 = _find_component(design, "U1")
    assert u1 is not None
    assert "BME280" in u1.part


def test_bme280_has_pins(design):
    u1 = _find_component(design, "U1")
    assert u1 is not None
    assert len(u1.pins) == 8


def test_power_symbols_included_with_marker(design):
    """Power symbols are included but marked with is_power_symbol metadata."""
    power_comps = [c for c in design.components if c.metadata.get("is_power_symbol") == "true"]
    assert len(power_comps) > 0, "Expected power symbols in components"
    # GND/SUPPLY symbols should be among the marked ones
    power_refs = {c.reference for c in power_comps}
    assert any(r.startswith("GND") or r.startswith("SUPPLY") for r in power_refs)


def test_component_value_metadata(design):
    r1 = _find_component(design, "R1")
    assert r1 is not None
    assert "Value" in r1.metadata
    assert r1.metadata["Value"] == "4.7K"


def test_component_description(design):
    """Descriptions come from deviceset description."""
    jp1 = _find_component(design, "JP1")
    assert jp1 is not None
    assert jp1.description  # should have a non-empty description


# --- Nets ---


def test_design_has_nets(design):
    assert len(design.nets) > 0


def test_gnd_net(design):
    gnd = _find_net(design, "GND")
    assert gnd is not None
    assert len(gnd.pins) > 3


def test_power_net(design):
    net_3v3 = _find_net(design, "3.3V")
    assert net_3v3 is not None
    assert len(net_3v3.pins) > 3


def test_signal_net(design):
    sda = _find_net(design, "SDI/SDA")
    assert sda is not None
    # U1, R1, JP1, JP2 should be connected
    refs = {p.component.reference for p in sda.pins}
    assert "U1" in refs
    assert "R1" in refs


def test_net_connects_correct_pins(design):
    """Verify specific pin connections for the !CS net."""
    cs = _find_net(design, "!CS")
    assert cs is not None
    pin_ids = {(p.component.reference, p.designator) for p in cs.pins}
    # U1 CS pin, R3 pin 1, JP2 pin 1
    assert ("R3", "1") in pin_ids
    assert ("JP2", "1") in pin_ids


# --- Pin metadata ---


def test_pin_electrical_metadata(adafruit_design):
    """Pins with non-passive direction should have electrical metadata."""
    # MCP23017 (IC1) in the Adafruit design has in/out/pwr pin directions
    ic1 = _find_component(adafruit_design, "IC1")
    assert ic1 is not None
    pins_with_electrical = [p for p in ic1.pins if "electrical" in p.metadata]
    assert len(pins_with_electrical) > 0


# --- Page ---


def test_single_page(design):
    assert len(design.pages) == 1


def test_public_model_links_are_bidirectional(design):
    for page in design.pages:
        assert page.id
        for component in page.components:
            assert component.id
            assert page in component.pages
        for net in page.nets:
            assert net.id
            assert page in net.pages

    for component in design.components:
        assert component.pages
        assert component.occurrences
        for pin in component.pins:
            assert pin.id
            assert pin.component is component
            if pin.net is not None:
                assert pin in pin.net.pins

    for net in design.nets:
        assert net.pages
        assert net.occurrences
        for pin in net.pins:
            assert pin.net is net


def test_eagle_same_named_nets_are_global_across_sheets(tmp_path):
    """Eagle connects net segments with the same net name across sheets."""
    design = eagle_to_design(_write_multisheet_eagle_same_name_net(tmp_path))

    shared = _find_net(design, "SHARED")
    assert shared is not None
    assert len(design.pages) == 2
    assert len([net for net in design.nets if net.name == "SHARED"]) == 1
    assert {pin.component.reference for pin in shared.pins} == {"R1", "R2"}
    assert {page.name for page in shared.pages} == {"Sheet 1", "Sheet 2"}


def test_multipart_eagle_component_collects_gate_occurrences(tmp_path):
    design = eagle_to_design(_write_multipart_eagle_component(tmp_path))

    component = _find_component(design, "U1")
    assert component is not None
    assert len([candidate for candidate in design.components if candidate.reference == "U1"]) == 1
    assert [pin.designator for pin in component.pins] == ["1", "2", "3", "4"]
    assert len(component.occurrences) == 2
    assert {occurrence.metadata["eagle_gate"] for occurrence in component.occurrences} == {"A", "B"}
    assert {pin.net.name for pin in component.pins if pin.net is not None} == {
        "AIN",
        "AOUT",
        "BIN",
        "BOUT",
    }


def test_multipart_eagle_component_across_pages_has_pin_occurrences(tmp_path):
    design = eagle_to_design(_write_multipart_eagle_component_across_pages(tmp_path))

    component = _find_component(design, "U1")
    assert component is not None
    assert len([candidate for candidate in design.components if candidate.reference == "U1"]) == 1
    assert {occurrence.metadata["eagle_gate"] for occurrence in component.occurrences} == {"A", "B"}
    assert {occurrence.page.name for occurrence in component.occurrences} == {
        "Sheet 1",
        "Sheet 2",
    }

    pins_by_designator = {pin.designator: pin for pin in component.pins}
    assert set(pins_by_designator) == {"1", "2", "3", "4"}
    assert all(len(pin.occurrences) == 1 for pin in pins_by_designator.values())
    assert pins_by_designator["1"].occurrences[0].page.name == "Sheet 1"
    assert pins_by_designator["1"].occurrences[0].source_id.endswith(":instance:U1:A:pin:IN")
    assert pins_by_designator["1"].occurrences[0].metadata == {
        "eagle_gate": "A",
        "eagle_pin": "IN",
        "eagle_pad": "1",
    }
    assert pins_by_designator["3"].occurrences[0].page.name == "Sheet 2"
    assert pins_by_designator["3"].occurrences[0].source_id.endswith(":instance:U1:B:pin:IN")


def test_multipad_connect_yields_one_pin_per_pad(tmp_path):
    """A space-separated connect pad maps one logical pin to several pads."""
    design = eagle_to_design(_write_multipad_eagle_component(tmp_path))

    component = _find_component(design, "U1")
    assert component is not None
    pins_by_designator = {pin.designator: pin for pin in component.pins}
    assert set(pins_by_designator) == {"3", "9"}
    assert {pin.name for pin in component.pins} == {"P"}

    net = _find_net(design, "N1")
    assert net is not None
    assert {(pin.component.reference, pin.designator) for pin in net.pins} == {
        ("U1", "3"),
        ("U1", "9"),
    }

    assert pins_by_designator["3"].occurrences[0].metadata == {
        "eagle_gate": "G$1",
        "eagle_pin": "P",
        "eagle_pad": "3",
    }
    assert pins_by_designator["9"].occurrences[0].metadata == {
        "eagle_gate": "G$1",
        "eagle_pin": "P",
        "eagle_pad": "9",
    }


def test_eagle_named_net_without_pinrefs_does_not_create_empty_occurrence(tmp_path):
    design = eagle_to_design(_write_eagle_net_without_pinrefs(tmp_path))

    assert _find_net(design, "EMPTY") is None
    used = _find_net(design, "USED")
    assert used is not None
    assert [net.name for net in design.nets] == ["USED"]
    assert [net.name for net in design.pages[0].nets] == ["USED"]
    assert len(used.occurrences) == 1


def test_eagle_sheet_plain_text_becomes_page_annotations(tmp_path):
    design = eagle_to_design(_write_eagle_annotations_fixture(tmp_path))

    assert design.pages[0].annotations == ["Board note\nline 2"]


def test_eagle_fixture_includes_sheet_notes(design):
    annotations = design.pages[0].annotations

    assert any("SJ3 controls the lowest bit" in annotation for annotation in annotations)
    assert any(annotation.startswith("MODES:") for annotation in annotations)


# --- Validation ---


def test_validation_no_errors(design):
    findings = validate_design(design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert errors == [], [f.message for f in errors]


# --- Adafruit board ---


def test_adafruit_components(adafruit_design):
    assert len(adafruit_design.components) > 10


def test_adafruit_nets(adafruit_design):
    assert len(adafruit_design.nets) > 10


def test_adafruit_no_errors(adafruit_design):
    findings = validate_design(adafruit_design)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert errors == [], [f.message for f in errors]


# --- Convert API ---


def test_convert_api():
    text = serialize_design(eagle_to_design(BME280_SCH))
    assert "DESIGN SUMMARY" in text
    assert "COMPONENTS" in text
    assert "NETS" in text
