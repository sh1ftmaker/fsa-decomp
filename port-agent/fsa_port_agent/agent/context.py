"""Build LLM context windows, Kong-style.

For each target function, assemble:
    - raw m2c output (from src/nonmatch/seg_*.c)
    - callee signatures (resolved, from seg-file scan)
    - caller signatures (as usage hints)
    - string refs (from state DB — previews deferred to Phase 4)
    - 1-2 nearby matched functions (style reference)

Never include raw asm by default — escalation only (Phase 3 retry path).

## SegIndex

Scans every `src/nonmatch/seg_*.c` once and builds:
    - addr → (seg_path, body_lines)   for slicing a fn body
    - addr → signature                for callee/caller sig lookup

The index is built lazily on first `build()` call and cached on the instance.
Rebuild by constructing a new `ContextBuilder`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..state_db import StateDB


# Banner above each fn body in a seg file, e.g.
#   /* --- auto_03_80021840_text.s --- */
#   /* --- auto_fn_80021848_text.s --- */
_BANNER_RE = re.compile(r'/\*\s*---\s*auto_(?:\w+?_)?([0-9A-Fa-f]{8})_text\.s\s*---\s*\*/')

# Function definition opener, e.g.
#   s32 fn_80021840(void) {
#   void fn_80021848(void) {
# Restricted to definitions (trailing `{`), not `extern` declarations.
_DEF_RE = re.compile(
    r'^\s*([A-Za-z_][\w\s\*]*?)\s+fn_([0-9A-Fa-f]{8})\s*\(([^)]*)\)\s*\{',
    re.MULTILINE,
)

# Extern signature, e.g.
#   u32 fn_80028974(char *);                                 /* extern */
#   s32 fn_80021A6C(s32 arg0, s16 arg1);
_EXTERN_RE = re.compile(
    r'^\s*([A-Za-z_][\w\s\*]*?)\s+fn_([0-9A-Fa-f]{8})\s*\(([^)]*)\)\s*;',
    re.MULTILINE,
)


def _find_body_end(text: str, def_start: int) -> int:
    """Return exclusive offset of the closing `}` that terminates the fn body."""
    i = text.index('{', def_start)
    depth = 0
    while i < len(text):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


@dataclass
class Context:
    fn_addr: int
    m2c_source: str
    callee_sigs: list[str]
    caller_sigs: list[str]
    string_refs: list[str]
    nearby_matched: list[str]
    raw_asm: str | None = None

    def as_prompt_vars(self) -> dict:
        return {
            "fn_addr": f"0x{self.fn_addr:08X}",
            "m2c_source": self.m2c_source,
            "callee_sigs": "\n".join(self.callee_sigs) or "(none)",
            "caller_sigs": "\n".join(self.caller_sigs) or "(none)",
            "strings": "\n".join(self.string_refs) or "(none)",
            "nearby": "\n\n".join(self.nearby_matched) or "(none)",
            "raw_asm": self.raw_asm or "(omitted — cleanup only)",
        }


@dataclass
class _FnLoc:
    seg: Path
    body: str         # full fn body incl. opening signature line through closing brace
    signature: str    # e.g. "s32 fn_80021840(void)"
    start: int        # byte offset into seg file where `body` begins
    end: int          # byte offset where `body` ends (exclusive)


@dataclass
class SegIndex:
    """Map fn_addr → body + signature. Built once per run."""
    nonmatch_root: Path
    fns: dict[int, _FnLoc] = field(default_factory=dict)

    def build(self) -> None:
        self.fns.clear()
        for seg in sorted(self.nonmatch_root.glob("seg_*.c")):
            self._ingest_seg(seg)

    def _ingest_seg(self, seg: Path) -> None:
        text = seg.read_text(errors="ignore")
        banners = [(m.start(), int(m.group(1), 16)) for m in _BANNER_RE.finditer(text)]
        banners.append((len(text), None))
        for i in range(len(banners) - 1):
            chunk_start, _ = banners[i]
            chunk_end, _ = banners[i + 1]
            chunk = text[chunk_start:chunk_end]
            dm = _DEF_RE.search(chunk)
            if not dm:
                continue  # extern-only stanza
            sig = f"{dm.group(1).strip()} fn_{dm.group(2)}({dm.group(3).strip()})"
            body_start = chunk_start + dm.start()
            body_end = _find_body_end(text, body_start)
            body = text[body_start:body_end]
            key = int(dm.group(2), 16)
            self.fns[key] = _FnLoc(
                seg=seg, body=body, signature=sig,
                start=body_start, end=body_end,
            )

    def body(self, addr: int) -> str | None:
        f = self.fns.get(addr)
        return f.body if f else None

    def signature(self, addr: int) -> str | None:
        f = self.fns.get(addr)
        return f.signature if f else None

    def replace_body(self, addr: int, new_body: str) -> bool:
        """Splice `new_body` in place of the current body. Rewrites the seg file."""
        loc = self.fns.get(addr)
        if loc is None:
            return False
        text = loc.seg.read_text(errors="ignore")
        # Verify the cached offsets still line up before splicing.
        if text[loc.start:loc.end] != loc.body:
            # Seg file drifted under us (another writer). Rebuild this seg.
            self._reingest_seg(loc.seg)
            loc = self.fns.get(addr)
            if loc is None or text[loc.start:loc.end] != loc.body:
                return False
        new_body = new_body.rstrip() + "\n"
        patched = text[:loc.start] + new_body + text[loc.end:]
        loc.seg.write_text(patched)
        # Reindex just this seg so offsets stay correct for subsequent edits.
        self._reingest_seg(loc.seg)
        return True

    def _reingest_seg(self, seg: Path) -> None:
        for addr in [a for a, f in self.fns.items() if f.seg == seg]:
            del self.fns[addr]
        self._ingest_seg(seg)


class ContextBuilder:
    def __init__(self, cfg: Config, db: StateDB):
        self.cfg = cfg
        self.db = db
        self.index = SegIndex(cfg.nonmatch_root)
        self.index.build()

    def build(self, fn_addr: int, *, n_nearby: int = 2) -> Context:
        body = self.index.body(fn_addr) or f"/* body for 0x{fn_addr:08X} not found in seg files */"

        callee_sigs: list[str] = []
        for ca in self.db.get_callees(fn_addr):
            sig = self.index.signature(ca)
            if sig:
                callee_sigs.append(sig + ";")

        caller_sigs: list[str] = []
        for ca in self.db.get_callers(fn_addr):
            sig = self.index.signature(ca)
            if sig:
                caller_sigs.append(sig + ";")

        strings = [
            f"0x{ref:08X}" + (f"  \"{prev}\"" if prev else "")
            for ref, prev in self.db.get_string_refs(fn_addr)
        ]

        nearby = self._nearby_matched(fn_addr, n_nearby)

        return Context(
            fn_addr=fn_addr,
            m2c_source=body,
            callee_sigs=callee_sigs,
            caller_sigs=caller_sigs,
            string_refs=strings,
            nearby_matched=nearby,
        )

    def _nearby_matched(self, fn_addr: int, n: int) -> list[str]:
        """Return up to `n` bodies of already-cleaned/matched functions near this addr."""
        if n <= 0:
            return []
        matched_states = ("CLEANED", "MATCHED_TWW", "BUILDS")
        picked: list[str] = []
        for state in matched_states:
            for row in self.db.get_by_state(state):
                body = self.index.body(row.addr)
                if body:
                    picked.append(body)
                    if len(picked) >= n:
                        return picked
        return picked
