"""CLI entry point: python -m fsa_port_agent --phase <name>."""

import argparse
import sys

from .config import Config
from .supervisor import run_phase

PHASES = ["triage", "import", "decompile", "synthesize", "hal", "build", "dashboard", "verify", "all"]


def main() -> int:
    parser = argparse.ArgumentParser(prog="fsa-port-agent")
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 = unlimited")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", choices=["cheap", "expensive"], default="cheap")
    parser.add_argument("--prepare", action="store_true",
                        help="Phase 3/5: enqueue prompt files into work/<kind>/")
    parser.add_argument("--apply", action="store_true",
                        help="Phase 3/5: splice back responses written by Claude Code")
    parser.add_argument("--check", action="store_true",
                        help="Phase 5: run syntax check and dump errors")
    parser.add_argument("--scan", action="store_true",
                        help="synthesize: read-only scan of CLEANED fns for "
                             "unk_0xNN field refs bucketed by struct-ptr arg type")
    parser.add_argument("--splits-only", action="store_true",
                        help="Phase 2: skip compile/match, only backfill "
                             "splits.txt + configure.py from state.db "
                             "(MATCHED_TWW rows).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="dashboard bind host (default 127.0.0.1 — keep local)")
    parser.add_argument("--port", type=int, default=8765,
                        help="dashboard bind port (default 8765)")
    parser.add_argument("--probe", default=None,
                        help="verify: run a single probe (dol_header|sda_bases|dol_fn_count|mftb|compiler)")
    parser.add_argument("--probe-src", default=None,
                        help="verify: override compiler-probe source (single .c/.cpp file path)")
    args = parser.parse_args()

    cfg = Config()
    return run_phase(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
