"""Build the DOL call graph from dtk-generated asm.

Each auto_*_text.s file contains `bl <target>` lines. Parse them into edges.
Leaves (callees with no outgoing bl) go first in topological order.
"""

import re
from pathlib import Path
from typing import Iterator


_ADDR_RE = re.compile(r'_([0-9A-Fa-f]{8})_text')

# dtk emits one instruction per line with a leading `/* addr offset bytes */` block,
# then a tab, then the mnemonic. `bl` appears mid-line, never at column 0.
# `\b` avoids matching `blr`, `ble`, `blt`, `bla`, etc.
_BL_RE = re.compile(r'\bbl\s+([A-Za-z_][\w]*)')

# Symbols that target functions use varied prefixes: fn_, dtor_, ctor_, lbl_,
# plus mangled C++ names ending in the 8-hex address. Capture the trailing
# 8-hex block as the implicit address.
_SYM_ADDR_RE = re.compile(r'_([0-9A-Fa-f]{8})$')

# dtk emits .rodata references as either `lbl_80xxxxxx@ha` or `@0x80xxxxxx`.
# Both forms appear in the same asm stream.
_DATA_REF_RE = re.compile(r'\b(?:lbl_|@0x)([0-9A-Fa-f]{8})\b')

# Heuristic: rodata lives in the 0x803x-0x805x range on FSA; code is <0x8030.
# Used only to discriminate code-labels (fn_ / lbl_ into .text) from data refs.
def _looks_like_data_addr(addr: int) -> bool:
    return 0x80300000 <= addr < 0x80600000


def iter_asm_files(asm_root: Path) -> Iterator[Path]:
    yield from sorted(asm_root.glob("auto_*_text.s"))


def file_addr(p: Path) -> int:
    m = _ADDR_RE.search(p.name)
    return int(m.group(1), 16) if m else 0


def parse_callees(asm_path: Path) -> list[str]:
    """Return target symbol names referenced by `bl` in one fn's asm."""
    text = asm_path.read_text(errors="ignore")
    return _BL_RE.findall(text)


def parse_data_refs(asm_path: Path) -> list[int]:
    """Return unique data-addr refs (probable .rodata / .data pointers).

    Deduplicated and filtered to the data-segment address range. These become
    candidates for string_refs in the state DB.
    """
    text = asm_path.read_text(errors="ignore")
    seen: set[int] = set()
    for m in _DATA_REF_RE.finditer(text):
        a = int(m.group(1), 16)
        if _looks_like_data_addr(a):
            seen.add(a)
    return sorted(seen)


def callee_addr(sym: str) -> int | None:
    m = _SYM_ADDR_RE.search(sym)
    return int(m.group(1), 16) if m else None


def topo_bottom_up(edges: dict[int, set[int]]) -> list[int]:
    """Kahn's algorithm on the reverse graph — leaves (no callees) first.

    indeg[n] = out-degree of n (how many callees remain unprocessed). Must be
    seeded over *every* node in the graph, not just those with outgoing edges,
    or pure leaves are never enqueued and nothing starts.
    """
    rev: dict[int, set[int]] = {}
    nodes: set[int] = set(edges.keys())
    for caller, callees in edges.items():
        for c in callees:
            rev.setdefault(c, set()).add(caller)
            nodes.add(c)
    indeg = {n: len(edges.get(n, ())) for n in nodes}
    queue = [n for n, d in indeg.items() if d == 0]
    order: list[int] = []
    while queue:
        n = queue.pop()
        order.append(n)
        for parent in rev.get(n, ()):
            indeg[parent] -= 1
            if indeg[parent] == 0:
                queue.append(parent)
    return order
