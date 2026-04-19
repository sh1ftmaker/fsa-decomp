"""Shared mwcc compile + masked-search utilities.

Both `agent/verify.py` (compiler-version probe) and `importers/tww_import.py`
(Phase 2 Gate 4) need to:

  1. Compile a TWW or FSA source with a specific mwcc version + cflags preset
  2. Extract per-function bytes and relocation masks from the resulting .o
  3. Masked-search the FSA DOL for each function

The byte-matching logic (ELF parsing, reloc masks, DOL search) already exists
in `fsa-decomp/tools/compile_search.py` and works correctly — we reuse it as
a library, not a subprocess. What we own here is *compiling TWW sources*
with TWW-rooted includes, which compile_search.py can't do (its cflags are
hardcoded to FSA's include tree).

No Anthropic API, no LLM calls — pure subprocess + byte manipulation.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config


# Candidate CW GC compiler versions to sweep. Ordered plan-first so early
# short-circuit keeps the common case cheap.
CANDIDATE_VERSIONS = [
    "GC/1.3.2",     # plan-of-record (TWW game code + JSystem)
    "GC/1.2.5n",    # Dolphin SDK
    "GC/1.3",
    "GC/1.3.2r",
    "GC/2.0",
    "GC/2.5",
    "GC/2.6",
    "GC/2.7",
]


# -----------------------------------------------------------------------------
# compile_search.py is imported as a library. Cache the module so we don't
# re-import on every call.
# -----------------------------------------------------------------------------

_cs_cache = None

def _compile_search(cfg: Config):
    global _cs_cache
    if _cs_cache is None:
        sys.path.insert(0, str(cfg.fsa_root / "tools"))
        try:
            import compile_search  # type: ignore
        finally:
            sys.path.pop(0)
        _cs_cache = compile_search
    return _cs_cache


# -----------------------------------------------------------------------------
# cflags presets, rooted at the TWW tree so TWW sources resolve their includes
# -----------------------------------------------------------------------------

def tww_cflags(cfg: Config, preset: str) -> list[str]:
    """Reconstruct FSA cflags_{preset} with TWW-rooted include paths.

    preset ∈ {"dolphin", "jsystem", "dolzel"}. Matches the definitions in
    fsa-decomp/configure.py so that TWW sources compiled here produce the
    same bytes as when they were compiled in the TWW repo originally."""
    tww = cfg.tww_root
    base = [
        "-nodefaults", "-proc gekko", "-align powerpc", "-enum int",
        "-fp hardware", "-Cpp_exceptions off", "-O4,p", "-inline auto",
        '-pragma "cats off"', '-pragma "warn_notinlined off"',
        "-maxerrors 1", "-nosyspath", "-RTTI off", "-fp_contract on",
        "-str reuse", "-multibyte",
        # Shim dir FIRST — replaces TWW's JSystem.h / dolzel.h with minimal
        # stubs so .mch / .pch expansion doesn't poison the translation unit.
        # See shim_include/JSystem/JSystem.h for rationale.
        f"-i {Path(__file__).parent / 'shim_include'}",
        f"-i {tww}/include", f"-i {tww}/src",
        f"-i {tww}/src/PowerPC_EABI_Support/MSL/MSL_C/MSL_Common/Include",
        f"-i {tww}/src/PowerPC_EABI_Support/MSL/MSL_C/MSL_Common_Embedded/Math/Include",
        f"-i {tww}/src/PowerPC_EABI_Support/MSL/MSL_C/PPC_EABI/Include",
        f"-i {tww}/src/PowerPC_EABI_Support/MSL/MSL_C++/MSL_Common/Include",
        f"-i {tww}/src/PowerPC_EABI_Support/Runtime/Inc",
        "-DVERSION=0", "-DNDEBUG=1",
    ]
    if preset == "dolphin":
        return base + ["-fp_contract off"]
    framework = base + ["-use_lmw_stmw off", "-str reuse,pool,readonly",
                        "-inline noauto", "-O3,s", "-sym on", "-fp_contract off"]
    if preset == "jsystem":
        return framework
    if preset == "dolzel":
        return framework + ["-schedule off"]
    raise ValueError(f"unknown preset: {preset!r}")


# -----------------------------------------------------------------------------
# Compile
# -----------------------------------------------------------------------------

def _wibo(cfg: Config) -> Path | None:
    p = cfg.fsa_root / "build" / "tools" / "wibo"
    return p if p.exists() else None


def _mwcc_exe(cfg: Config, version: str) -> Path | None:
    p = cfg.fsa_root / "build" / "compilers" / version / "mwcceppc.exe"
    return p if p.exists() else None


def compile_tww(cfg: Config, src: Path, version: str, preset: str,
                timeout: int = 120) -> bytes | None:
    """Compile a TWW source. Return .o bytes, or None if the compile failed.

    Errors are silent by default. Set VERIFY_DEBUG=1 to surface compiler
    stderr/stdout for troubleshooting."""
    wibo = _wibo(cfg)
    cc = _mwcc_exe(cfg, version)
    if wibo is None or cc is None:
        return None
    cflags = tww_cflags(cfg, preset)
    # shlex preserves quoted pragmas (`-pragma "cats off"` must stay as
    # two argv entries, not three); naive .split() breaks the quoting.
    split_flags: list[str] = []
    for f in cflags:
        split_flags.extend(shlex.split(f))
    with tempfile.NamedTemporaryFile(suffix=".o", delete=False) as tf:
        out = tf.name
    try:
        cmd = [str(wibo), str(cc), *split_flags, "-c", str(src), "-o", out]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=cfg.tww_root, timeout=timeout)
        if r.returncode != 0 or not Path(out).exists():
            if os.environ.get("VERIFY_DEBUG"):
                print(f"  [mwcc] rc={r.returncode}  {src.name} @ {version} ({preset})",
                      file=sys.stderr)
                if r.stdout: print(f"  [mwcc] stdout:\n{r.stdout[:1500]}", file=sys.stderr)
            return None
        return Path(out).read_bytes()
    except subprocess.TimeoutExpired:
        return None
    finally:
        try: os.unlink(out)
        except FileNotFoundError: pass


# -----------------------------------------------------------------------------
# Match
# -----------------------------------------------------------------------------

@dataclass
class MatchHit:
    name: str
    fsa_addr: int
    size: int


def extract_and_match(cfg: Config, obj: bytes) -> tuple[list[MatchHit], int, int]:
    """Extract each .text function from .o, masked-search FSA DOL.

    Returns (unambiguous_hits, miss_count, ambiguous_count). A function with
    >1 DOL matches is counted as ambiguous, not a hit — we only claim an
    address when the masked bytes pin it uniquely."""
    cs = _compile_search(cfg)
    fns = cs.extract_functions(obj)
    hits: list[MatchHit] = []
    misses = 0
    ambig = 0
    for name, fn_bytes, mask in fns:
        addrs = cs.search_dol(fn_bytes, mask)
        if len(addrs) == 1:
            hits.append(MatchHit(name=name, fsa_addr=addrs[0], size=len(fn_bytes)))
        elif len(addrs) == 0:
            misses += 1
        else:
            ambig += 1
    return hits, misses, ambig


# -----------------------------------------------------------------------------
# Sweep: try versions for a single source, return best match
# -----------------------------------------------------------------------------

@dataclass
class SweepResult:
    src: Path
    preset: str
    version: str | None       # winning version (None if nothing compiled)
    hits: list[MatchHit]
    misses: int
    ambiguous: int
    tried: list[str]          # versions actually attempted
    compile_fails: list[str]  # versions that failed to compile


PRESET_PREFERRED_VERSION = {
    "dolphin": "GC/1.2.5n",   # Dolphin SDK
    "jsystem": "GC/1.3.2",    # JSystem
    "dolzel":  "GC/1.3.2",    # TWW game code
}


def sweep_versions(cfg: Config, src: Path, preset: str,
                   versions: list[str] = None,
                   short_circuit: bool = True,
                   early_cutoff: bool = True) -> SweepResult:
    """Compile `src` under each candidate version; keep the one with most hits.

    Short-circuit strategy (why both knobs exist):
      - `short_circuit`: once we have hits AND 1.3.2 has been tried, stop.
        Good when the first try already matches — no reason to keep compiling.
      - `early_cutoff`: once we've tried 1.3.2 AND the preset's preferred
        version (e.g. 1.2.5n for dolphin) with zero hits, abandon the file.
        Files that don't match on either are vanishingly unlikely to match
        on 1.3, 1.3.2r, 2.0, 2.5 — those are minor revs / later games.
        Without this, every miss burns the full 8-version sweep.
    The first version in `versions` must be GC/1.3.2 for the cutoff to work."""
    versions = list(versions or CANDIDATE_VERSIONS)
    preferred = PRESET_PREFERRED_VERSION.get(preset)
    # Ensure preset's preferred version is tried second (right after 1.3.2).
    if preferred and preferred in versions and preferred != versions[0]:
        versions.remove(preferred)
        versions.insert(1, preferred)

    best: SweepResult | None = None
    tried: list[str] = []
    compile_fails: list[str] = []

    for v in versions:
        obj = compile_tww(cfg, src, v, preset)
        tried.append(v)
        if obj is None:
            compile_fails.append(v)
            continue
        hits, misses, ambig = extract_and_match(cfg, obj)
        if best is None or len(hits) > len(best.hits):
            best = SweepResult(src=src, preset=preset, version=v, hits=hits,
                               misses=misses, ambiguous=ambig,
                               tried=tried[:], compile_fails=compile_fails[:])
        if short_circuit and best and best.hits and "GC/1.3.2" in tried:
            break
        if (early_cutoff and (best is None or not best.hits)
                and "GC/1.3.2" in tried
                and (preferred is None or preferred in tried)):
            break

    if best is None:
        return SweepResult(src=src, preset=preset, version=None, hits=[],
                           misses=0, ambiguous=0,
                           tried=tried, compile_fails=compile_fails)
    best.tried = tried
    best.compile_fails = compile_fails
    return best
