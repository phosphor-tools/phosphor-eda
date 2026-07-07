from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

OPENCELLULAR_BREAKOUT_ROOT = FIXTURES / "orcad/opencellular-breakout"
OPENCELLULAR_BREAKOUT_IPC356 = (
    OPENCELLULAR_BREAKOUT_ROOT
    / "allegro/OpenCellular/electronics/breakout/gerbers/OC_CONNECT-1_BREAKOUT_LIFE-3.ipc"
)
OPENCELLULAR_BREAKOUT_NETLIST = (
    OPENCELLULAR_BREAKOUT_ROOT / "orcad/OpenCellular/electronics/breakout/schematic/Netlist"
)

OPENCELLULAR_SYNC_ROOT = FIXTURES / "orcad/opencellular-sync"
OPENCELLULAR_SYNC_IPC356 = (
    OPENCELLULAR_SYNC_ROOT
    / "allegro/OpenCellular/electronics/sync/gerbers/Fb_Connect1_SYNC_Life-3.ipc"
)
OPENCELLULAR_SYNC_NETLIST = (
    OPENCELLULAR_SYNC_ROOT / "orcad/OpenCellular/electronics/sync/schematics/Netlist"
)

CP_SMARTGARDEN_ALLEGRO = (
    FIXTURES / "orcad/cp-smartgarden-launchxl-cc1310/Document/Hardware/mcu/swrc319/Cadence/Allegro"
)
CP_SMARTGARDEN_NETLIST = CP_SMARTGARDEN_ALLEGRO
CP_SMARTGARDEN_PLACEMENT_LOG = CP_SMARTGARDEN_ALLEGRO / "log/plctxt.log"

ROHM_GERBER_ROOT = (
    FIXTURES / "orcad/rohm-stepper-driver-ctrl/Design Files for Rev 1.0/Gerbers & Panel CAD"
)
ROHM_DRILL = ROHM_GERBER_ROOT / "DRILL.DRL"
ROHM_VIEW_ENV = ROHM_GERBER_ROOT / "VIEW.ENV"
ROHM_REPORT = ROHM_GERBER_ROOT / "REPORT.DOC"
