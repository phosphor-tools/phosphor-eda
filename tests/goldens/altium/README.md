# Altium goldens

## `pi-mx8-pcb-netlist.json`

Derived data used as an external-tool oracle by
`tests/test_altium_net_naming_conformance.py`. It is not a hand-authored golden:
it is the net list extracted from the OV-Tech Pi.MX8 PCB and is compared against
the net names our loader derives from the matching schematic project.

### Provenance

- Source repository: <https://github.com/OV-Tech-GmbH/Pi.MX8>
  (vendored as the `tests/upstream/pi-mx8` submodule).
- Source commit: `25bef74335cf939554c54e5a5aa8ff00191458be`.
- Extracted from:
  `01_Electronics/PiMX8MP_r0.3_release/PCB/PiMX8MP_r0.3.PcbDoc`.
- Upstream license: electronics are CERN-OHL-S-2.0
  (see `tests/upstream/pi-mx8/LICENSE`). This file is a derived work of that
  design used only as a test oracle.

### Format

JSON with a `net_count`, a `nets` array (each entry a `name` plus the `members`
list of `[component_reference, pad_designator]` pairs on that net), and a
`source` string recording how it was produced.

### Regenerating

The oracle is produced by reading the Altium PCB document's `Nets6` OLE stream
and pad net assignments from `PiMX8MP_r0.3.PcbDoc` in the pinned `pi-mx8`
submodule, then emitting each net name with the set of `(reference, pad)`
members it touches. Regenerate it whenever the submodule is bumped to a revision
that changes PCB connectivity, and re-run the conformance test to confirm the
`477` matched nets still hold.
