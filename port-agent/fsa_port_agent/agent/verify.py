"""Phase 0.5 — probe unverified assumptions before committing to Gate 4.

The FSA port plan stands on several claims that were transplanted from the
TWW decomp or hand-waved from heuristics. This module spot-checks the
load-bearing ones against the actual DOL and (optionally) a TWW clone.

Probes:

  dol_header    — DOL header vs hardcoded TEXT_OFF/ADDR/SIZE in compile_search.py
  sda_bases     — scan text for r13/r2 init prolog, decode, compare to 0x80541BC0 / 0x80542FA0
  dol_fn_count  — state DB count vs asm-dir ground truth (Gate 4 denominator)
  mftb          — count aligned 42E6 vs 42A6 to validate the "FSA uses mftb" claim
  compiler      — compile TWW game-code probe sources against every candidate mwcc version,
                  masked-search DOL, report which version wins

Running a probe never touches repo state — it's read-only inspection plus
subprocess compiles into a tempdir.
"""

from __future__ import annotations

import os
import shlex
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..config import Config
from ..state_db import StateDB


# Hardcoded in fsa-decomp/tools/compile_search.py — the claim we're validating.
CLAIMED_TEXT_OFF = 0x2600
CLAIMED_TEXT_ADDR = 0x80021840
CLAIMED_TEXT_SIZE = 0x43A4A4
CLAIMED_SDA1 = 0x80541BC0   # r13
CLAIMED_SDA2 = 0x80542FA0   # r2

# Gate 4 denominator — the number of DOL functions we're trying to match.
# dtk with fill_gaps=true emits one auto_*_text.s per DOL function, so
# counting those files is the authoritative measure. The 5,516 figure
# previously quoted in docs was dtk's "discovered symbols" count (ctors +
# exception-table entries only), which is a strict subset.
import re as _re
_ASM_FN_RE = _re.compile(r"^auto_.*_text\.s$")
_ASM_ADDR_RE = _re.compile(r"^auto_(?:\d+|fn|dtor)_([0-9A-Fa-f]{8})_text\.s$")


def _count_dol_fns(cfg: Config) -> tuple[int, int]:
    """Return (total, addressable) DOL function count from dtk's asm dir.

    total: every auto_*_text.s (includes specials like auto_main_text.s,
           auto_destroy_global_chain_text.s that have no encoded addr).
    addressable: the subset with a decodable hex address (everything we can
           correlate by key to rows in state.db).
    """
    asm_dir = cfg.asm_root
    if not asm_dir.exists():
        return (0, 0)
    total = 0
    addressable = 0
    for p in asm_dir.iterdir():
        if not _ASM_FN_RE.match(p.name):
            continue
        total += 1
        if _ASM_ADDR_RE.match(p.name):
            addressable += 1
    return (total, addressable)

# Candidate mwcc versions for the game-code compiler sweep.
# 1.3.2 is the current plan-of-record; others hedge against version drift.
CANDIDATE_VERSIONS = [
    "GC/1.2.5n",
    "GC/1.3",
    "GC/1.3.2",
    "GC/1.3.2r",
    "GC/2.0",
    "GC/2.5",
    "GC/2.6",
    "GC/2.7",
]

# TWW game-code probe sources (cflags_dolzel). JSystem is empirically validated
# for GC/1.3.2 via JKRDisposer 100% match — re-probing it wastes cycles. This
# sweep targets the genuinely untested path: whether FSA's *game-code half*
# also used GC/1.3.2, or moved to a later CW version in the ~1 year between
# TWW (2002-12) and FSA (2004-03).
#
# f_pc/f_op are Nintendo EAD's process-framework library, confirmed shared
# (decomp.me has fpcLnIt_*/fpcMtdTg_* scratches on FSA preset 228).
# d_save_init / DynamicLink are small framework entry points also likely shared.
DEFAULT_PROBE_SOURCES = [
    "src/f_pc/f_pc_pause.cpp",
    "src/f_pc/f_pc_method.cpp",
    "src/f_pc/f_pc_line_tag.cpp",
    "src/f_pc/f_pc_node.cpp",
    "src/f_op/f_op_overlap.cpp",
    "src/f_op/f_op_actor_tag.cpp",
    "src/d/d_save_init.cpp",
    "src/DynamicLink.cpp",
]


@dataclass
class ProbeResult:
    name: str
    status: str           # "ok" | "warn" | "fail" | "skip"
    summary: str
    details: list[str] = field(default_factory=list)


# -----------------------------------------------------------------------------
# DOL header
# -----------------------------------------------------------------------------

def _parse_dol_header(dol: bytes) -> dict:
    """Return {text_sections: [(off, addr, size), ...], entry: int, ...}."""
    if len(dol) < 0x100:
        raise ValueError("DOL too short")
    text_offs  = struct.unpack(">7I", dol[0x00:0x1C])
    text_addrs = struct.unpack(">7I", dol[0x48:0x64])
    text_sizes = struct.unpack(">7I", dol[0x90:0xAC])
    texts = [(o, a, s) for o, a, s in zip(text_offs, text_addrs, text_sizes) if s]
    entry = struct.unpack(">I", dol[0xE0:0xE4])[0]
    return {"texts": texts, "entry": entry}


def probe_dol_header(cfg: Config) -> ProbeResult:
    if not cfg.dol_path.exists():
        return ProbeResult("dol_header", "skip",
                           f"DOL missing at {cfg.dol_path}")
    dol = cfg.dol_path.read_bytes()
    hdr = _parse_dol_header(dol)
    details = [f"entry point: 0x{hdr['entry']:08X}",
               f"text sections ({len(hdr['texts'])}):"]
    for i, (off, addr, size) in enumerate(hdr["texts"]):
        details.append(f"  [{i}] off=0x{off:X}  addr=0x{addr:08X}  size=0x{size:X}")

    # compile_search.py hardcodes the *main* text section, not [0]. Find
    # whichever section matches the claimed addr and compare sizes there.
    match_idx = None
    for i, (off, addr, size) in enumerate(hdr["texts"]):
        if off == CLAIMED_TEXT_OFF and addr == CLAIMED_TEXT_ADDR:
            match_idx = i
            break

    if match_idx is None:
        details.append("")
        details.append(f"no text section matches claimed off=0x{CLAIMED_TEXT_OFF:X} "
                       f"addr=0x{CLAIMED_TEXT_ADDR:08X}")
        return ProbeResult("dol_header", "fail",
                           "compile_search.py hardcodes bounds that don't map to any DOL text section",
                           details)

    off, addr, size = hdr["texts"][match_idx]
    details.append("")
    details.append(f"compile_search.py targets section [{match_idx}]")
    if size == CLAIMED_TEXT_SIZE:
        return ProbeResult("dol_header", "ok",
                           f"DOL header matches compile_search.py bounds (section [{match_idx}])",
                           details)
    # DOL headers pad section sizes up to 32-byte alignment. The `claimed` value
    # is dtk's trimmed size (excludes tail padding), which is the semantically
    # correct bound for byte-pattern searches — padding bytes are zeros and
    # can't match real code. If the claim is <= header size and the delta is
    # within a 32-byte alignment window, it's fine.
    delta = size - CLAIMED_TEXT_SIZE
    details.append(f"size delta: header 0x{size:X} − compile_search 0x{CLAIMED_TEXT_SIZE:X} = {delta:+d} bytes")
    if 0 < delta <= 32:
        details.append("delta is DOL tail padding — compile_search.py uses dtk's trimmed size (correct)")
        return ProbeResult("dol_header", "ok",
                           f"section [{match_idx}] matches (delta {delta:+d}B is tail padding)",
                           details)
    if delta < 0:
        return ProbeResult("dol_header", "fail",
                           f"compile_search.py claims MORE bytes than the header reports",
                           details)
    # delta > 32: unusual, worth a warning.
    return ProbeResult("dol_header", "warn",
                       f"section [{match_idx}] tail padding delta unusually large: {delta:+d}",
                       details)


# -----------------------------------------------------------------------------
# SDA bases — r13 and r2
# -----------------------------------------------------------------------------

def _read_text_sections(cfg: Config) -> list[tuple[bytes, int]]:
    """Return [(section_bytes, section_base_addr), ...] for all text sections.

    GC DOLs commonly have a small boot/init stub in section [0] and the main
    game code in section [1]. Scanning only one misses half the story — SDA
    init lives in the stub, but bulk code (mftb usage, etc.) lives in [1]."""
    dol = cfg.dol_path.read_bytes()
    hdr = _parse_dol_header(dol)
    return [(dol[o:o + s], a) for (o, a, s) in hdr["texts"]]


def _ha_lo_to_addr(ha: int, lo: int) -> int:
    """PPC @ha / @l pair → effective address. @ha adjusts for sign of lo."""
    addr = (ha << 16) + (lo if lo < 0x8000 else lo - 0x10000)
    return addr & 0xFFFFFFFF


def _scan_lis_addi_pair(text: bytes, reg: int) -> list[int]:
    """Find `lis rN, H` followed by `addi rN, rN, L` OR `ori rN, rN, L`.

    CW linkers emit either form. `addi` sign-extends L (so upper is @ha —
    incremented when L >= 0x8000). `ori` zero-extends L (upper is @h — raw).
    """
    hits: list[int] = []
    lis_opcode  = 0x3C000000 | (reg << 21)                # lis  rD, X     (rA=0)
    addi_match  = 0x38000000 | (reg << 21) | (reg << 16)  # addi rD, rD, X
    ori_match   = 0x60000000 | (reg << 21) | (reg << 16)  # ori  rD, rD, X  (rS=rA=reg)
    for i in range(0, len(text) - 8, 4):
        w1 = struct.unpack_from(">I", text, i)[0]
        if (w1 & 0xFFFF0000) != lis_opcode:
            continue
        w2 = struct.unpack_from(">I", text, i + 4)[0]
        hi = w1 & 0xFFFF
        lo = w2 & 0xFFFF
        if   (w2 & 0xFFFF0000) == addi_match:
            hits.append(_ha_lo_to_addr(hi, lo))        # @ha / @l (signed)
        elif (w2 & 0xFFFF0000) == ori_match:
            hits.append(((hi << 16) | lo) & 0xFFFFFFFF)  # @h / @l (unsigned)
    return hits


def _most_common(xs: list[int]) -> tuple[int, int]:
    """Return (value, count) of modal element, or (0, 0) if empty."""
    counts: dict[int, int] = {}
    for x in xs:
        counts[x] = counts.get(x, 0) + 1
    if not counts:
        return (0, 0)
    best = max(counts.items(), key=lambda kv: kv[1])
    return best


def _find_init_registers_block(sections: list[tuple[bytes, int]]) -> dict | None:
    """Locate the __init_registers prolog by finding the unique `lis r13` pair.

    r13 is reserved by the GC ABI for the SDA base. The compiler won't emit
    `lis r13; op r13, r13, X` anywhere except the boot prolog. Once found,
    the r2 init pair lives ±16 bytes away.

    Returns {sda1_addr, sda2_addr, section_idx, file_like_offset} or None.
    """
    lis_r13_hi = 0x3DA00000
    for sec_idx, (text, base_addr) in enumerate(sections):
        # 1. Find lis r13 + (addi|ori) r13, r13 pair.
        r13_pair_at = None
        for i in range(0, len(text) - 8, 4):
            w1 = struct.unpack_from(">I", text, i)[0]
            if (w1 & 0xFFFF0000) != lis_r13_hi:
                continue
            w2 = struct.unpack_from(">I", text, i + 4)[0]
            if   (w2 & 0xFFFF0000) == (0x38000000 | (13 << 21) | (13 << 16)):
                sda1 = _ha_lo_to_addr(w1 & 0xFFFF, w2 & 0xFFFF)
                r13_pair_at = i
                break
            elif (w2 & 0xFFFF0000) == (0x60000000 | (13 << 21) | (13 << 16)):
                sda1 = (((w1 & 0xFFFF) << 16) | (w2 & 0xFFFF)) & 0xFFFFFFFF
                r13_pair_at = i
                break

        if r13_pair_at is None:
            continue

        # 2. r2 init pair should be within ±32 bytes.
        lis_r2_hi = 0x3C400000
        for j in range(max(0, r13_pair_at - 32), min(len(text), r13_pair_at + 32), 4):
            w1 = struct.unpack_from(">I", text, j)[0]
            if (w1 & 0xFFFF0000) != lis_r2_hi:
                continue
            w2 = struct.unpack_from(">I", text, j + 4)[0]
            if   (w2 & 0xFFFF0000) == (0x38000000 | (2 << 21) | (2 << 16)):
                sda2 = _ha_lo_to_addr(w1 & 0xFFFF, w2 & 0xFFFF)
                return {"sda1": sda1, "sda2": sda2,
                        "section": sec_idx, "r13_at": r13_pair_at, "r2_at": j,
                        "base_addr": base_addr}
            if (w2 & 0xFFFF0000) == (0x60000000 | (2 << 21) | (2 << 16)):
                sda2 = (((w1 & 0xFFFF) << 16) | (w2 & 0xFFFF)) & 0xFFFFFFFF
                return {"sda1": sda1, "sda2": sda2,
                        "section": sec_idx, "r13_at": r13_pair_at, "r2_at": j,
                        "base_addr": base_addr}
        # Found r13 but not r2 — return what we have.
        return {"sda1": sda1, "sda2": None, "section": sec_idx,
                "r13_at": r13_pair_at, "r2_at": None, "base_addr": base_addr}

    return None


def probe_sda_bases(cfg: Config) -> ProbeResult:
    if not cfg.dol_path.exists():
        return ProbeResult("sda_bases", "skip", f"DOL missing at {cfg.dol_path}")
    sections = _read_text_sections(cfg)

    block = _find_init_registers_block(sections)
    if block is None:
        return ProbeResult("sda_bases", "warn",
                           "no `lis r13; op r13, r13, X` prolog found in any text section")

    r13_addr = block["base_addr"] + block["r13_at"]
    details = [
        f"__init_registers block in text section [{block['section']}]",
        f"  r13 pair at load addr 0x{r13_addr:08X}",
        f"  decoded SDA  (r13): 0x{block['sda1']:08X}  (claim 0x{CLAIMED_SDA1:08X})",
    ]
    if block["sda2"] is not None:
        r2_addr = block["base_addr"] + block["r2_at"]
        details.append(f"  r2  pair at load addr 0x{r2_addr:08X}")
        details.append(f"  decoded SDA2 (r2):  0x{block['sda2']:08X}  (claim 0x{CLAIMED_SDA2:08X})")
    else:
        details.append(f"  r2 pair: not found near r13 — SDA2 unverified")

    mismatches = []
    if block["sda1"] != CLAIMED_SDA1:
        mismatches.append(f"SDA base (r13) differs from claim")
    if block["sda2"] is not None and block["sda2"] != CLAIMED_SDA2:
        mismatches.append(f"SDA2 base (r2) differs from claim")

    if mismatches:
        return ProbeResult("sda_bases", "fail",
                           "SDA base(s) don't match documented values",
                           details + [""] + mismatches)
    if block["sda2"] is None:
        return ProbeResult("sda_bases", "warn",
                           f"SDA=0x{block['sda1']:08X} matches claim; SDA2 not decoded",
                           details)
    return ProbeResult("sda_bases", "ok",
                       f"SDA bases match claim (r13=0x{block['sda1']:08X}, r2=0x{block['sda2']:08X})",
                       details)


# -----------------------------------------------------------------------------
# DOL function count
# -----------------------------------------------------------------------------

def probe_dol_fn_count(cfg: Config) -> ProbeResult:
    asm_total, asm_addressable = _count_dol_fns(cfg)
    db_count = None
    db_phantom_zero = False
    if cfg.state_db_path.exists():
        db = StateDB(cfg.state_db_path)
        try:
            addrs = db.all_addrs()
            db_count = len(addrs)
            db_phantom_zero = 0 in set(addrs)
        finally:
            db.close()

    details = [
        f"dtk asm dir total:         {asm_total or '(asm dir missing — run fsa-decomp configure.py && ninja)'}",
        f"  addressable (auto_{{NN|fn|dtor}}_ADDR_text.s): {asm_addressable}",
        f"  specials (auto_main_text.s, auto_destroy_global_chain_text.s, ...): {asm_total - asm_addressable}",
        f"state.db addrs:            {db_count if db_count is not None else '(no DB)'}",
    ]
    if db_phantom_zero:
        details.append(f"  ⚠ includes phantom addr 0x00000000 (triage bug)")

    if asm_total == 0:
        return ProbeResult("dol_fn_count", "skip",
                           f"no asm dir at {cfg.asm_root}", details)
    if db_count is None:
        return ProbeResult("dol_fn_count", "warn",
                           f"authoritative DOL fn count: {asm_total} (no state.db to cross-check)",
                           details)

    db_real = db_count - (1 if db_phantom_zero else 0)
    # Expect every addressable asm to map to a DB row.
    missing = asm_addressable - db_real
    extra   = db_real - asm_addressable
    if missing == 0 and extra == 0:
        return ProbeResult("dol_fn_count", "ok",
                           f"DOL fn count = {asm_total} (DB matches addressable subset)",
                           details)
    details.append("")
    if missing > 0:
        details.append(f"  DB is missing {missing} addressable function(s) — re-run --phase triage")
    if extra > 0:
        details.append(f"  DB has {extra} extra addr(s) beyond asm dir — inspect")
    details.append("Gate 4 denominator should use asm_total = "
                   f"{asm_total}, NOT the legacy 5,516 figure.")
    return ProbeResult("dol_fn_count", "warn",
                       f"authoritative = {asm_total}; DB real = {db_real} (delta {missing - extra:+d})",
                       details)


# -----------------------------------------------------------------------------
# mftb vs mfspr
# -----------------------------------------------------------------------------

def probe_mftb(cfg: Config) -> ProbeResult:
    """mftb extended opcode = 371 (bytes 42 E6); mfspr = 339 (bytes 42 A6).

    Both sit in bits 21-30 of the instruction word, so the low 2 bytes are
    `X6` with X = 0x42 or 0x42 depending on XO's low bits. At aligned word
    offset +2 both patterns land at the same place. Count each."""
    if not cfg.dol_path.exists():
        return ProbeResult("mftb", "skip", f"DOL missing at {cfg.dol_path}")
    sections = _read_text_sections(cfg)

    mftb_count = 0
    mfspr_count = 0
    for (text, _addr) in sections:
        for i in range(2, len(text) - 2, 4):
            lo16 = (text[i] << 8) | text[i + 1]
            if   lo16 == 0x42E6: mftb_count  += 1
            elif lo16 == 0x42A6: mfspr_count += 1

    details = [
        f"mftb  (XO=371, bytes 42 E6): {mftb_count}",
        f"mfspr (XO=339, bytes 42 A6): {mfspr_count}",
        "",
        "note: the docs' claim is about timebase reads specifically — FSA uses",
        "`mftb` (XO=371) rather than the `mfspr tbr=268/269` alternative.",
        "mfspr outnumbering mftb is normal (LR/CTR/MSR/HID* reads use mfspr).",
    ]
    # Existence check: the claim is that mftb is used at all for timebase.
    # The heavy diff that would matter is mftb_count == 0, which would mean
    # a different codegen path and break our m2c banner decoding.
    if mftb_count == 0 and mfspr_count == 0:
        return ProbeResult("mftb", "warn",
                           "neither mftb nor mfspr found — unexpected", details)
    if mftb_count == 0:
        return ProbeResult("mftb", "fail",
                           "no mftb found — timebase reads may use `mfspr tbr=` form instead",
                           details)
    return ProbeResult("mftb", "ok",
                       f"mftb present ({mftb_count} occurrences) — claim holds", details)


# -----------------------------------------------------------------------------
# Compiler sweep
# -----------------------------------------------------------------------------

def _available_compilers(cfg: Config) -> dict[str, Path]:
    """Return {version: mwcceppc.exe path} for each present candidate."""
    compilers_root = cfg.fsa_root / "build" / "compilers"
    out: dict[str, Path] = {}
    for v in CANDIDATE_VERSIONS:
        p = compilers_root / v / "mwcceppc.exe"
        if p.exists():
            out[v] = p
    return out


def _wibo(cfg: Config) -> Path | None:
    """Path to wibo wrapper (required to run mwcc on Linux). None if missing."""
    p = cfg.fsa_root / "build" / "tools" / "wibo"
    return p if p.exists() else None


def _tww_cflags_dolzel(cfg: Config) -> list[str]:
    """FSA's cflags_dolzel reconstructed with include paths rooted at TWW_ROOT.

    compile_search.py's CFLAGS_* presets hardcode the FSA repo layout, so
    they can't compile TWW sources directly. We use the same *flag set* but
    retarget the -i dirs at TWW_ROOT so the TWW headers resolve.
    """
    tww = cfg.tww_root
    base = [
        "-nodefaults", "-proc gekko", "-align powerpc", "-enum int",
        "-fp hardware", "-Cpp_exceptions off", "-O4,p", "-inline auto",
        '-pragma "cats off"', '-pragma "warn_notinlined off"',
        "-maxerrors 1", "-nosyspath", "-RTTI off", "-fp_contract on",
        "-str reuse", "-multibyte",
        f"-i {tww}/include", f"-i {tww}/src",
        f"-i {tww}/src/PowerPC_EABI_Support/MSL/MSL_C/MSL_Common/Include",
        f"-i {tww}/src/PowerPC_EABI_Support/MSL/MSL_C/MSL_Common_Embedded/Math/Include",
        f"-i {tww}/src/PowerPC_EABI_Support/MSL/MSL_C/PPC_EABI/Include",
        f"-i {tww}/src/PowerPC_EABI_Support/MSL/MSL_C++/MSL_Common/Include",
        f"-i {tww}/src/PowerPC_EABI_Support/Runtime/Inc",
        "-DVERSION=0", "-DNDEBUG=1",
    ]
    jsystem = base + ["-use_lmw_stmw off", "-str reuse,pool,readonly",
                      "-inline noauto", "-O3,s", "-sym on", "-fp_contract off"]
    return jsystem + ["-schedule off"]  # = dolzel


def _compile_tww_source(cfg: Config, src: Path, version: str, wibo: Path) -> bytes | None:
    """Compile a TWW source with the given mwcc version + FSA's cflags_dolzel.

    Runs from cfg.tww_root so any remaining relative paths in the source
    (e.g., `#include "global.h"`) resolve correctly."""
    cc = cfg.fsa_root / "build" / "compilers" / version / "mwcceppc.exe"
    if not cc.exists():
        return None
    cflags = _tww_cflags_dolzel(cfg)
    # shlex.split preserves quoted pragmas (`-pragma "cats off"` must stay as
    # two argv entries, not three). Naive f.split() breaks the quoting.
    split_flags: list[str] = []
    for f in cflags:
        split_flags.extend(shlex.split(f))
    with tempfile.NamedTemporaryFile(suffix=".o", delete=False) as tf:
        out = tf.name
    try:
        cmd = [str(wibo), str(cc), *split_flags, "-c", str(src), "-o", out]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=cfg.tww_root, timeout=120)
        if r.returncode != 0 or not Path(out).exists():
            if os.environ.get("VERIFY_DEBUG"):
                print(f"  [debug] rc={r.returncode}  {src.name} @ {version}", file=sys.stderr)
                if r.stdout: print(f"  [debug] stdout:\n{r.stdout[:1500]}", file=sys.stderr)
                if r.stderr: print(f"  [debug] stderr:\n{r.stderr[:500]}", file=sys.stderr)
            return None
        return Path(out).read_bytes()
    except subprocess.TimeoutExpired:
        return None
    finally:
        try: os.unlink(out)
        except FileNotFoundError: pass


def _probe_one_source(cfg: Config, src: Path, versions: dict[str, Path],
                      wibo: Path) -> dict[str, int]:
    """Compile src with each version, masked-search DOL. Return {version: match_count}."""
    sys.path.insert(0, str(cfg.fsa_root / "tools"))
    try:
        import compile_search  # type: ignore
    finally:
        sys.path.pop(0)

    scores: dict[str, int] = {}
    for version in versions:
        try:
            obj = _compile_tww_source(cfg, src, version, wibo)
        except Exception:
            scores[version] = -1
            continue
        if not obj:
            scores[version] = -1
            continue
        try:
            fns = compile_search.extract_functions(obj)
        except Exception:
            scores[version] = -1
            continue
        matches = 0
        for _name, fn_bytes, mask in fns:
            hits = compile_search.search_dol(fn_bytes, mask)
            if len(hits) >= 1:
                matches += 1
        scores[version] = matches
    return scores


def probe_compiler(cfg: Config, args) -> ProbeResult:
    details: list[str] = []

    # 0. Preconditions
    if not cfg.dol_path.exists():
        return ProbeResult("compiler", "skip", f"DOL missing at {cfg.dol_path}")

    wibo = _wibo(cfg)
    if wibo is None:
        return ProbeResult("compiler", "skip",
                           f"wibo not at {cfg.fsa_root / 'build/tools/wibo'} — "
                           "run fsa-decomp's setup_tools.sh first")

    versions = _available_compilers(cfg)
    if not versions:
        return ProbeResult("compiler", "skip",
                           f"no candidate compilers under {cfg.fsa_root / 'build/compilers'}")
    details.append(f"candidate compilers available: {', '.join(versions)}")

    # 1. Source list
    sources: list[Path] = []
    override = getattr(args, "probe_src", None)
    if override:
        p = Path(override).expanduser().resolve()
        if not p.exists():
            return ProbeResult("compiler", "fail",
                               f"--probe-src {p} does not exist")
        sources = [p]
    else:
        if not cfg.tww_root.exists():
            return ProbeResult("compiler", "skip",
                               f"TWW clone not at {cfg.tww_root} — either\n"
                               f"  git clone https://github.com/zeldaret/tww {cfg.tww_root}\n"
                               f"or pass --probe-src PATH to a single .c/.cpp file")
        for rel in DEFAULT_PROBE_SOURCES:
            p = cfg.tww_root / rel
            if p.exists():
                sources.append(p)
        if not sources:
            return ProbeResult("compiler", "skip",
                               f"none of the default probe sources exist under {cfg.tww_root}")
    details.append(f"probe sources: {len(sources)}")
    for s in sources:
        details.append(f"  {s.relative_to(s.parents[3]) if len(s.parents) > 3 else s}")

    # 2. Sweep
    totals: dict[str, int] = {v: 0 for v in versions}
    per_source: list[tuple[Path, dict[str, int]]] = []
    for src in sources:
        scores = _probe_one_source(cfg, src, versions, wibo)
        per_source.append((src, scores))
        for v, n in scores.items():
            if n > 0:
                totals[v] += n

    details.append("")
    details.append("per-source match counts (cflags_dolzel):")
    header = "  " + "source".ljust(48) + "  " + "  ".join(v.rjust(10) for v in versions)
    details.append(header)
    for src, scores in per_source:
        name = src.name[:48]
        row = "  " + name.ljust(48) + "  " + "  ".join(
            (str(scores.get(v, 0)) if scores.get(v, -1) >= 0 else "FAIL").rjust(10)
            for v in versions
        )
        details.append(row)

    details.append("")
    details.append("totals:")
    for v in sorted(totals, key=lambda v: -totals[v]):
        details.append(f"  {v:12s} {totals[v]}")

    # 3. Verdict
    if sum(totals.values()) == 0:
        return ProbeResult("compiler", "warn",
                           "zero matches across all versions — probe sources may be too divergent "
                           "or TWW has diverged from FSA in these files",
                           details)

    top_score = max(totals.values())
    top_versions = [v for v, n in totals.items() if n == top_score]
    plan_score  = totals.get("GC/1.3.2", 0)

    if plan_score == top_score and plan_score > 0:
        if len(top_versions) == 1:
            summary = f"GC/1.3.2 wins cleanly ({plan_score} matches)"
        else:
            others = [v for v in top_versions if v != "GC/1.3.2"]
            summary = (f"GC/1.3.2 tied at top ({plan_score} matches) with "
                       f"{', '.join(others)} — byte-identical codegen on these sources")
            details.append("")
            details.append("interpretation: GC/1.3.2 is not contradicted, but these probe")
            details.append("sources don't discriminate between CW versions. Pick files that")
            details.append("exercise version-specific codegen (float ops, templates, vtables)")
            details.append("or add known-FSA-address sources as ground truth.")
        return ProbeResult("compiler", "ok", summary, details)

    # Plan version strictly behind winner.
    gap = top_score - plan_score
    pct_gap = (gap / top_score * 100) if top_score else 0
    summary = (f"{top_versions[0]} leads with {top_score} matches; GC/1.3.2 has "
               f"{plan_score} ({pct_gap:.0f}% behind)")
    # 20%+ gap is meaningful evidence against the assumed compiler.
    status = "fail" if pct_gap >= 20 else "warn"
    return ProbeResult("compiler", status, summary, details)


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------

PROBES = {
    "dol_header":   probe_dol_header,
    "sda_bases":    probe_sda_bases,
    "dol_fn_count": probe_dol_fn_count,
    "mftb":         probe_mftb,
    "compiler":     probe_compiler,
}


def _print_result(r: ProbeResult) -> None:
    badge = {"ok": "  OK  ", "warn": " WARN ", "fail": " FAIL ", "skip": " SKIP "}[r.status]
    print(f"[{badge}] {r.name:14s} {r.summary}")
    for line in r.details:
        print(f"          {line}")


def run(cfg: Config, args) -> int:
    selected = getattr(args, "probe", None)
    if selected and selected not in PROBES:
        print(f"unknown probe '{selected}'. available: {', '.join(PROBES)}")
        return 2

    probe_names = [selected] if selected else list(PROBES)
    print(f"[verify] running {len(probe_names)} probe(s): {', '.join(probe_names)}")
    print()

    results: list[ProbeResult] = []
    for name in probe_names:
        fn = PROBES[name]
        try:
            r = fn(cfg, args) if name == "compiler" else fn(cfg)
        except Exception as e:
            r = ProbeResult(name, "fail", f"probe raised: {type(e).__name__}: {e}")
        results.append(r)
        _print_result(r)
        print()

    # Exit non-zero if any FAIL; WARN/SKIP are informational.
    fails = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    skips = [r for r in results if r.status == "skip"]
    oks   = [r for r in results if r.status == "ok"]
    print(f"[verify] summary: {len(oks)} ok, {len(warns)} warn, {len(fails)} fail, {len(skips)} skip")
    return 1 if fails else 0
