# Raspberry Pi OrCAD `.DSN` fixtures

These are OrCAD Capture schematic design files (`.DSN`, binary OLE compound
documents) published by Raspberry Pi Ltd as part of their official hardware
design-files downloads. Raspberry Pi distributes them as ZIP archives from the
documentation site (<https://datasheets.raspberrypi.com>, the "Design files"
download on each product's documentation page); the `.DSN` committed here is the
schematic extracted from that archive, kept under its original distributed
filename.

| Directory              | Original filename           | Design                            |
| ---------------------- | --------------------------- | --------------------------------- |
| `raspberry-pi-cmio`    | `RPI-CMIO-V3_0-PUBLIC.DSN`  | Compute Module IO Board           |
| `raspberry-pi-pico`    | `RPI-PICO-R3-PUBLIC.DSN`    | Raspberry Pi Pico                 |
| `raspberry-pi-pico-w`  | `RPI-PICOW-R2.DSN`          | Raspberry Pi Pico W               |

No upstream Git repository exists for these designs (Raspberry Pi ships them as
standalone ZIP downloads rather than a versioned repo), so they are vendored
in-tree rather than added as submodules.

Redistribution terms are unverified pending review: Raspberry Pi publishes these
design files for public download but the fixtures here have not been checked
against an explicit redistribution license. Confirm the licensing before
shipping them in any distributed artifact.
