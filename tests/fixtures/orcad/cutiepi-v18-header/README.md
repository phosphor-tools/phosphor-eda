# CutiePi V18 Allegro Header Fixture

This fixture preserves KiCad's Allegro importer header-only evidence for the
CutiePi 2.3 board after DB Doctor 18 rewrote the header.

- Source fixture path:
  `qa/data/pcbnew/plugins/allegro/boards/CutiePi_V2_3_dbd18/header.bin`
- Upstream project URL recorded by KiCad:
  `https://github.com/cutiepi-io/cutiepi-board`
- License recorded by KiCad: `BSD-3-Clause`
- KiCad registry board name: `CutiePi_V2_3_dbd18`
- KiCad registry format version: `18.0`

Only the header bytes are committed. This fixture proves the V18 header layout
and linked-list word order; it does not prove full V18 board record parsing.
