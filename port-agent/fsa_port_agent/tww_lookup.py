"""Map FSA DOL addresses to TWW C++ method bodies.

## Why

FSA shares TWW's code tree. Names decompose in the CodeWarrior mangling scheme
the same way in both games — e.g. `calcLoadTimer__17mDoAud_zelAudio_cFv` refers
to `mDoAud_zelAudio_c::calcLoadTimer()` whether it lives in FSA's DOL or TWW's.

FSA's `config/G4SE01/symbols.txt` already has the mangled name for ~22% of
DOL functions (via TWW-style naming in the split config). TWW's .cpp files
helpfully annotate each method body with its mangled name in a banner comment,
e.g.:

    /* 00000078-00000108       .text setTopPos__18daArrow_Lighteff_cFv */
    void daArrow_Lighteff_c::setTopPos() { ... }

So the lookup is an exact string match on the mangled name — no need to
demangle CodeWarrior's scheme ourselves.

## Usage

    lookup = TWWLookup(cfg)          # lazy: builds index on first call
    snippet = lookup.body_for(0x80006D84)
    if snippet:
        # inject into cleanup.md prompt as {tww_reference}

Cache lives at `port-agent/tww_lookup_index.json`. Delete it to rebuild.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .config import Config


# Matches the banner emitted by dtk in TWW's source files.
#
#   /* <start>-<end>       .text <mangled_name> */
#
# The mangled name is the trailing identifier before the closing `*/`.
_BANNER_RE = re.compile(
    r'/\*\s+[0-9A-Fa-f]+-[0-9A-Fa-f]+\s+\.text\s+([A-Za-z_][\w$<>@.:,]*)\s*\*/'
)

# Lines of the form:  name = .text:0xADDR; // type:function ...
# in symbols.txt. Keep mangled names with `__` and/or trailing `F<args>` —
# they're what show up in the TWW banner comments.
_SYMBOL_RE = re.compile(
    r'^\s*([^\s=]+)\s*=\s*\.text\s*:\s*0x([0-9A-Fa-f]+)\s*;\s*//\s*type:function'
)


def _find_body_end(text: str, brace_start: int) -> int:
    """Return exclusive offset of the matching `}` starting from `text[brace_start] == '{'`."""
    depth = 0
    i = brace_start
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


class TWWLookup:
    """Lazy index: mangled_name → (tww_file_rel, body_text).

    The index is built once on first call and cached to disk. A fresh clone
    of TWW rebuilds it in ~5s; subsequent runs load the JSON cache.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._index: Optional[dict[str, tuple[str, str]]] = None
        self._sym_to_addr: Optional[dict[str, int]] = None
        self._addr_to_sym: Optional[dict[int, str]] = None
        self.cache_path = cfg.agent_root / "tww_lookup_index.json"

    # ------------------------------------------------------------------
    # symbols.txt side
    # ------------------------------------------------------------------

    def _load_symbols(self) -> None:
        if self._addr_to_sym is not None:
            return
        addr_to_sym: dict[int, str] = {}
        sym_to_addr: dict[str, int] = {}
        if not self.cfg.symbols_path.exists():
            self._addr_to_sym = addr_to_sym
            self._sym_to_addr = sym_to_addr
            return
        for line in self.cfg.symbols_path.read_text(errors="ignore").splitlines():
            m = _SYMBOL_RE.match(line)
            if not m:
                continue
            name, addr_hex = m.group(1), m.group(2)
            addr = int(addr_hex, 16)
            # Only keep CW-mangled names (contain `__`) — auto_* placeholders
            # and bare C names won't match a TWW banner.
            if "__" not in name:
                continue
            addr_to_sym[addr] = name
            sym_to_addr[name] = addr
        self._addr_to_sym = addr_to_sym
        self._sym_to_addr = sym_to_addr

    # ------------------------------------------------------------------
    # TWW tree index
    # ------------------------------------------------------------------

    def _load_tww_index(self) -> None:
        if self._index is not None:
            return

        if self.cache_path.exists():
            try:
                raw = json.loads(self.cache_path.read_text())
                self._index = {
                    name: (entry["file"], entry["body"])
                    for name, entry in raw.items()
                }
                return
            except (json.JSONDecodeError, KeyError):
                pass  # fall through to rebuild

        self._index = self._rebuild_tww_index()
        cache = {
            name: {"file": file, "body": body}
            for name, (file, body) in self._index.items()
        }
        self.cache_path.write_text(json.dumps(cache))

    def _rebuild_tww_index(self) -> dict[str, tuple[str, str]]:
        """Walk TWW_ROOT/src/**/*.cpp (and .c), capture each method body keyed
        by its banner-comment mangled name."""
        tww_root = self.cfg.tww_root
        if not tww_root.exists():
            return {}

        out: dict[str, tuple[str, str]] = {}
        # .cpp is the vast majority of TWW; include .c for the OS/MSL files too.
        sources = list(tww_root.rglob("src/**/*.cpp")) + list(tww_root.rglob("src/**/*.c"))
        for src in sources:
            try:
                text = src.read_text(errors="ignore")
            except OSError:
                continue
            rel = str(src.relative_to(tww_root))
            # For each banner, snap forward to the next `{` and brace-match.
            for m in _BANNER_RE.finditer(text):
                mangled = m.group(1)
                if mangled in out:
                    continue  # first-wins; rare inline redeclarations
                # Find the body between this banner and the next.
                banner_end = m.end()
                next_banner = _BANNER_RE.search(text, banner_end)
                chunk_end = next_banner.start() if next_banner else len(text)
                # The body starts at the first `{` after the banner comment
                # (skipping the signature line(s)).
                brace = text.find('{', banner_end, chunk_end)
                if brace == -1:
                    continue
                body_end = _find_body_end(text, brace)
                # Keep the signature + body for readability. Back up to the
                # start of the signature line after the banner.
                sig_start = text.rfind('\n', banner_end, brace)
                sig_start = sig_start + 1 if sig_start != -1 else banner_end
                body = text[sig_start:body_end]
                out[mangled] = (rel, body)
        return out

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def body_for(self, fn_addr: int) -> Optional[str]:
        """Return the TWW method body (signature + braced body) whose mangled
        name matches FSA's `symbols.txt` entry for this DOL address."""
        self._load_symbols()
        self._load_tww_index()
        mangled = self._addr_to_sym.get(fn_addr) if self._addr_to_sym else None
        if mangled is None:
            return None
        hit = self._index.get(mangled) if self._index else None
        if hit is None:
            return None
        tww_file, body = hit
        return f"// from tww/{tww_file}\n// matching mangled name: {mangled}\n{body}"

    def name_for(self, fn_addr: int) -> Optional[str]:
        """Return the mangled CodeWarrior symbol for this DOL addr, if any.

        Useful as prompt context: tells Claude the class/method this fn is
        believed to be (per FSA's symbols.txt), even when no TWW body exists.
        """
        self._load_symbols()
        return self._addr_to_sym.get(fn_addr) if self._addr_to_sym else None

    def stats(self) -> dict:
        """Introspection: how many FSA mangled names have a matching TWW body?"""
        self._load_symbols()
        self._load_tww_index()
        fsa_mangled = set(self._addr_to_sym.values()) if self._addr_to_sym else set()
        tww_mangled = set(self._index.keys()) if self._index else set()
        return {
            "fsa_mangled_symbols": len(fsa_mangled),
            "tww_indexed_bodies": len(tww_mangled),
            "overlap": len(fsa_mangled & tww_mangled),
        }
