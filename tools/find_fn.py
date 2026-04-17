#!/usr/bin/env python3
"""
find_fn.py — Find function addresses/sizes in FSA DOL via dtk.

Usage:
  python tools/find_fn.py <name>       # substring match
  python tools/find_fn.py OS           # show all OS* functions
  python tools/find_fn.py --addr 0x8007E6F4  # look up by address
"""

import subprocess, sys, re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOL  = REPO / "orig/sys/main.dol"
DTK  = REPO / "build/tools/dtk"

def get_info():
    r = subprocess.run([str(DTK), "dol", "info", str(DOL)],
                       capture_output=True, text=True, cwd=REPO)
    return r.stdout

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    info = get_info()
    if "--addr" in args:
        target = int(args[args.index("--addr")+1], 16)
        for line in info.splitlines():
            m = re.search(r'0x([0-9A-Fa-f]{8})\s*\|\s*(0x[0-9A-Fa-f]+|\?)\s*\|', line)
            if m and int(m.group(1), 16) == target:
                print(line.strip())
    else:
        query = args[0].lower()
        for line in info.splitlines():
            if query in line.lower():
                print(line.strip())

if __name__ == "__main__":
    main()
