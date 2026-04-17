#!/usr/bin/env python3
"""
search_dol.py — Search FSA DOL for byte patterns (with optional relocation masking).

Usage:
  python tools/search_dol.py <hex_bytes>               # literal byte search
  python tools/search_dol.py <hex_bytes> --mask <hex>  # mask before comparing

Example — find OSGetTick (mftbl r3 + blr):
  python tools/search_dol.py 7C6C42E64E800020

Example — find a function from a compiled .o with relocation masking:
  python tools/compile_search.py <src.c> <fn_name>  (use that script instead)
"""

import sys, struct
from pathlib import Path

REPO      = Path(__file__).resolve().parent.parent
DOL       = REPO / "orig/sys/main.dol"
TEXT_OFF  = 0x2600
TEXT_ADDR = 0x80021840
TEXT_SIZE = 0x43A4A4

def search(pattern: bytes, mask: bytes | None = None):
    with open(DOL, "rb") as f:
        f.seek(TEXT_OFF)
        text = f.read(TEXT_SIZE)
    if mask:
        masked_text = bytes(b & ~m for b, m in zip(text * 1, mask * (len(text)//len(mask)+1)))
        masked_pat  = bytes(b & ~m for b, m in zip(pattern, mask))
        # Rebuild with stride
        results = []
        start = 0
        while True:
            idx = masked_text.find(masked_pat, start)
            if idx == -1:
                break
            results.append(TEXT_ADDR + idx)
            start = idx + 1
        return results
    else:
        results, start = [], 0
        while True:
            idx = text.find(pattern, start)
            if idx == -1:
                break
            results.append(TEXT_ADDR + idx)
            start = idx + 1
        return results

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    pat = bytes.fromhex(args[0])
    mask = bytes.fromhex(args[args.index("--mask")+1]) if "--mask" in args else None
    hits = search(pat, mask)
    print(f"Pattern: {args[0]!r}  Matches: {len(hits)}")
    for h in hits[:50]:
        print(f"  0x{h:08X}")
    if len(hits) > 50:
        print(f"  ... ({len(hits)-50} more)")

if __name__ == "__main__":
    main()
