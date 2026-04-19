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
from ..tww_lookup import TWWLookup


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
    tww_reference: str | None = None
    tww_name: str | None = None
    raw_asm: str | None = None
    m2c_error_count: int = 0
    helpers: str = ""
    target_decl: str | None = None
    context_stats: dict = field(default_factory=dict)

    def as_prompt_vars(self) -> dict:
        return {
            "fn_addr": f"0x{self.fn_addr:08X}",
            "m2c_source": self.m2c_source,
            "callee_sigs": "\n".join(self.callee_sigs) or "(none)",
            "caller_sigs": "\n".join(self.caller_sigs) or "(none)",
            "strings": "\n".join(self.string_refs) or "(none)",
            "nearby": "\n\n".join(self.nearby_matched) or "(none)",
            "tww_reference": self.tww_reference or "(no matching TWW method found)",
            "tww_name": self.tww_name or "(no mangled name in symbols.txt)",
            "raw_asm": self.raw_asm or "(omitted — cleanup only)",
            "helpers": self.helpers or "(none)",
            "m2c_error_count": str(self.m2c_error_count),
            "target_decl": self.target_decl or "(no extern decl in _declarations.h — use m2c body's signature)",
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
    # seg_path → {callee_addr → "ret fn_ADDR(args)"} — first-seen extern per seg.
    # cc parses the seg file top-to-bottom and locks the first decl it sees;
    # later stanzas' extern decls are ignored (they're redundant in cc's view).
    # `_declarations.h` is NOT included by seg files, so local externs are the
    # only ground truth for callee signatures.
    seg_externs: dict[Path, dict[int, str]] = field(default_factory=dict)

    def build(self) -> None:
        self.fns.clear()
        self.seg_externs.clear()
        for seg in sorted(self.nonmatch_root.glob("seg_*.c")):
            self._ingest_seg(seg)

    def _ingest_seg(self, seg: Path) -> None:
        text = seg.read_text(errors="ignore")
        # First-seen extern per callee across the whole seg file.
        externs: dict[int, str] = {}
        for em in _EXTERN_RE.finditer(text):
            ret = em.group(1).strip()
            if ret.startswith("extern "):
                ret = ret[len("extern "):].strip()
            if not ret or ret == "extern":
                continue
            cal_hex = em.group(2)
            cal_args = em.group(3).strip()
            key = int(cal_hex, 16)
            if key in externs:
                continue
            externs[key] = f"{ret} fn_{cal_hex}({cal_args})"
        self.seg_externs[seg] = externs

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

    def first_extern(self, seg: Path, callee_addr: int) -> str | None:
        return self.seg_externs.get(seg, {}).get(callee_addr)

    def propagate_signature(self, addr: int) -> int:
        """Rewrite every seg file's first extern for `addr` to match the body def.

        Call after a fn has been CLEANED and its body is trusted. Without this,
        the next caller's seg compiles against m2c's (possibly wrong) extern
        guess and the gate rejects a correct body. Returns number of seg files
        rewritten.
        """
        loc = self.fns.get(addr)
        if loc is None:
            return 0
        target_sig = loc.signature
        addr_hex = f"{addr:08X}"
        # Match any extern for this addr, case-insensitive on hex.
        pat = re.compile(
            r'^(\s*)([A-Za-z_][\w\s\*]*?)\s+fn_' + addr_hex +
            r'\s*\(([^)]*)\)\s*;([^\n]*)$',
            re.MULTILINE | re.IGNORECASE,
        )
        rewritten = 0
        for seg, externs in list(self.seg_externs.items()):
            current = externs.get(addr)
            if not current or current == target_sig:
                continue
            # One seg may have multiple externs for this addr across stanzas;
            # cc only honors the first, but downstream tooling can get confused
            # so rewrite all occurrences consistently.
            text = seg.read_text(errors="ignore")
            def _repl(m: re.Match) -> str:
                # Preserve trailing `/* extern */` comment if present.
                tail = m.group(4)
                return f"{m.group(1)}{target_sig};{tail}"
            new_text, n = pat.subn(_repl, text)
            if n == 0:
                continue
            seg.write_text(new_text)
            self._reingest_seg(seg)
            rewritten += 1
        return rewritten

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
        self.seg_externs.pop(seg, None)
        self._ingest_seg(seg)


@dataclass
class DeclIndex:
    """Authoritative signatures from src/nonmatch/_declarations.h.

    m2c concatenates one `extern RETURN fn_ADDR(ARGS);` per callsite into this
    header. Many are contradictory, so the C front-end emits "conflicting
    types" errors for the 2nd onward — but the *first* extern per fn is what
    cc treats as canonical for downstream type-checking. Matching that form
    lets a fn's body avoid introducing a NEW error inside its own line range,
    which is what the compile gate actually tests (baseline diff).
    """
    decl_path: Path
    sigs: dict[int, str] = field(default_factory=dict)

    def build(self) -> None:
        self.sigs.clear()
        if not self.decl_path.exists():
            return
        text = self.decl_path.read_text(errors="ignore")
        # First pass: record the first-seen decl-with-args per addr. Skip bare
        # `fn_X()` (K&R unspecified-args) — a concrete arg list is always more
        # informative and avoids "conflicting types" cascades where the bare
        # form conflicts with later arg-carrying decls.
        for m in _EXTERN_RE.finditer(text):
            ret = m.group(1).strip()
            addr_hex = m.group(2)
            args = m.group(3).strip()
            if ret.startswith("extern "):
                ret = ret[len("extern "):].strip()
            if not ret or ret == "extern":
                continue
            if not args:
                continue  # skip K&R unspecified-args form
            addr = int(addr_hex, 16)
            if addr in self.sigs:
                continue  # first-seen with args wins
            self.sigs[addr] = f"{ret} fn_{addr_hex}({args})"
        # Second pass: fill in fns that only ever appeared in bare K&R form.
        for m in _EXTERN_RE.finditer(text):
            ret = m.group(1).strip()
            addr_hex = m.group(2)
            args = m.group(3).strip()
            if ret.startswith("extern "):
                ret = ret[len("extern "):].strip()
            if not ret or ret == "extern":
                continue
            addr = int(addr_hex, 16)
            if addr in self.sigs:
                continue
            self.sigs[addr] = f"{ret} fn_{addr_hex}({args})"

    def signature(self, addr: int) -> str | None:
        return self.sigs.get(addr)


class ContextBuilder:
    def __init__(self, cfg: Config, db: StateDB):
        self.cfg = cfg
        self.db = db
        self.index = SegIndex(cfg.nonmatch_root)
        self.index.build()
        self.decls = DeclIndex(cfg.nonmatch_root / "_declarations.h")
        self.decls.build()
        self.tww = TWWLookup(cfg)

    def build(self, fn_addr: int, *, n_nearby: int = 2) -> Context:
        body = self.index.body(fn_addr) or f"/* body for 0x{fn_addr:08X} not found in seg files */"

        # The seg file's FIRST-seen extern per callee is what cc uses for the
        # entire TU. Later stanzas may repeat the decl with different args, but
        # cc locks the first one. DeclIndex / SegIndex.signature are fallbacks.
        loc = self.index.fns.get(fn_addr)
        seg = loc.seg if loc else None

        # For trusted callees (CLEANED / MATCHED_TWW / BUILDS), prefer the
        # body's own signature — it reflects the cleaned-up types that have
        # been gate-verified. Other segs' externs may be stale m2c guesses
        # (propagate_signature fixes them on next cleanup, but don't rely on
        # that ordering here). For untrusted callees, the seg's first-seen
        # extern is what cc will actually bind against, so respect it.
        TRUSTED_STATES = {"CLEANED", "MATCHED_TWW", "BUILDS"}
        callee_sigs: list[str] = []
        for ca in self.db.get_callees(fn_addr):
            callee_row = self.db.get_fn_by_addr(ca)
            trusted = callee_row is not None and callee_row.state in TRUSTED_STATES
            if trusted:
                sig = (
                    self.index.signature(ca)
                    or (self.index.first_extern(seg, ca) if seg else None)
                    or self.decls.signature(ca)
                )
            else:
                sig = (
                    (self.index.first_extern(seg, ca) if seg else None)
                    or self.decls.signature(ca)
                    or self.index.signature(ca)
                )
            if sig:
                callee_sigs.append(sig + ";")

        caller_sigs: list[str] = []
        for ca in self.db.get_callers(fn_addr):
            sig = self.decls.signature(ca) or self.index.signature(ca)
            if sig:
                caller_sigs.append(sig + ";")

        # For the fn being cleaned, cc may lock an EARLIER extern in the seg
        # file before reaching the body, so the first-seen extern trumps the
        # body's signature. Fall back to the body signature, then DeclIndex.
        target_decl = (
            (self.index.first_extern(seg, fn_addr) if seg else None)
            or self.index.signature(fn_addr)
            or self.decls.signature(fn_addr)
        )

        strings = [
            f"0x{ref:08X}" + (f"  \"{prev}\"" if prev else "")
            for ref, prev in self.db.get_string_refs(fn_addr)
        ]

        nearby = self._nearby_matched(fn_addr, n_nearby)
        tww_ref = self.tww.body_for(fn_addr)
        tww_name = self.tww.name_for(fn_addr)

        m2c_errs = body.count("M2C_ERROR(")
        helpers = ""
        if m2c_errs > 0:
            ps_emu = self.cfg.nonmatch_root / "_ps_emu.h"
            if ps_emu.exists():
                helpers = ps_emu.read_text(errors="ignore")

        row = self.db.get_fn_by_addr(fn_addr)
        context_stats = {
            "has_tww_ref": bool(tww_ref),
            "callee_sigs": len(callee_sigs),
            "caller_sigs": len(caller_sigs),
            "nearby_matched": len(nearby),
            "string_refs": len(strings),
            "m2c_error_count": m2c_errs,
            "size_bytes": row.size if row else 0,
            "tag": row.tag if row else None,
        }

        return Context(
            fn_addr=fn_addr,
            m2c_source=body,
            callee_sigs=callee_sigs,
            caller_sigs=caller_sigs,
            string_refs=strings,
            nearby_matched=nearby,
            tww_reference=tww_ref,
            tww_name=tww_name,
            m2c_error_count=m2c_errs,
            helpers=helpers,
            target_decl=target_decl,
            context_stats=context_stats,
        )

    def _nearby_matched(self, fn_addr: int, n: int) -> list[str]:
        """Return up to `n` bodies of already-cleaned/matched functions that
        make good style references for the fn at `fn_addr`.

        Scoring favors:
          1. Same seg file (local naming/style is the best template).
          2. Has a TWW mangled-name hit (body already pattern-matched to TWW).
          3. Short bodies (large ones blow the prompt budget and Claude
             pattern-matches from the first ~50 lines anyway).
          4. Address proximity (tie-breaker).
        """
        if n <= 0:
            return []
        matched_states = ("CLEANED", "MATCHED_TWW", "BUILDS")
        target_loc = self.index.fns.get(fn_addr)
        target_seg = target_loc.seg if target_loc else None

        candidates: list[tuple[tuple, str]] = []
        for state in matched_states:
            for row in self.db.get_by_state(state):
                if row.addr == fn_addr:
                    continue
                loc = self.index.fns.get(row.addr)
                if loc is None:
                    continue
                body = loc.body
                if not body:
                    continue
                same_seg = int(target_seg is not None and loc.seg == target_seg)
                has_tww = int(self.tww.name_for(row.addr) is not None)
                size_penalty = min(len(body), 4000)
                addr_dist = abs(row.addr - fn_addr)
                # Lower score = better. Negative on "good" signals so they
                # sort first; positive on "bad" signals.
                score = (-same_seg, -has_tww, size_penalty, addr_dist)
                candidates.append((score, body))

        candidates.sort(key=lambda x: x[0])
        return [body for _, body in candidates[:n]]
