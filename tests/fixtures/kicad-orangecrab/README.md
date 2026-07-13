# OrangeCrab r0.2.1 KiCad fixture

A locally converted KiCad project derived from the OrangeCrab r0.2.1 hardware
design. Only the files that diverge from upstream are kept here; the pristine
upstream tree is vendored as the `tests/upstream/orangecrab` submodule.

## Provenance

- Upstream repository: <https://github.com/gregdavill/OrangeCrab>
- Upstream path: `hardware/orangecrab_r0.2.1/`
- Upstream commit: `c511d569fe2af39467041f888bc231020f40c6ac`
- Author: Gregory Davill
- License: hardware under CERN OHL v1.2 (`LICENCE-CERN-OHL`), gateware/software
  under MIT (`LICENCE-MIT`); both copied verbatim from the upstream repository
  root.

## What changed

Upstream ships the design in KiCad 5 format (`.sch` schematics, a
`version 20171130` board, and a legacy `sym-lib-table`). This fixture is the
result of opening that project in modern KiCad, which:

- converted each `.sch` sheet to a native `.kicad_sch` (`OrangeCrab.kicad_sch`
  plus the `DECOUPLING`, `DRAM`, `FPGA`, `IO`, `POWER`, and `SDMMC` sub-sheets);
- generated `OrangeCrab-rescue.kicad_sym` for symbols rescued during the
  conversion, and added its entry to `sym-lib-table` (now `version 7`);
- created the `OrangeCrab.kicad_pro` / `OrangeCrab.kicad_prl` project files;
- re-saved `OrangeCrab.kicad_pcb` in the current board format
  (`version 20260206`).

These converted artifacts do not exist upstream and are what the KiCad
parser/enrichment/PCB tests exercise. Upstream-identical files (the legacy
`.sch` sheets, `Production/`, `plot/`, PDFs, gerbers, the `.pro`/`.lib` cache,
and footprint tables) were removed to avoid re-vendoring; retrieve them from the
`tests/upstream/orangecrab` submodule if needed.
</content>
