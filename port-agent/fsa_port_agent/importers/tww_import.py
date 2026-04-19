"""Phase 2 — mass-import from the TWW repo.

For each TWW source file in priority order:
    1. Compile with a sweep of CW GC compiler versions (GC/1.3.2 first, then
       others), matching FSA's cflags preset for the library.
    2. Byte-pattern match each compiled function against the FSA DOL.
    3. On unambiguous hit: optionally write splits.txt + configure.py entries,
       mark state DB.

The compile + match work lives in `fsa_port_agent.mwcc` so it can be shared
with `agent/verify.py`. This module owns the *orchestration* — iteration
order, cflag-preset selection per file, aggregation, Gate 4 readout.

## Usage

    # Gate 4 dry-run: just count hits, don't modify the repo.
    python -m fsa_port_agent --phase import --dry-run

    # Full: write splits.txt + configure.py edits, populate state DB.
    python -m fsa_port_agent --phase import

    # Limit to N files (for incremental runs).
    python -m fsa_port_agent --phase import --limit 20

## Detection: which cflag preset per TWW file?

TWW repo layout hints at library:
    libs/dolphin/**, src/dolphin/**  → 'dolphin'  (Dolphin SDK, GC/1.2.5n)
    libs/JSystem/**, src/JSystem/**  → 'jsystem'  (JSystem, GC/1.3.2)
    libs/MSL_C/**, libs/Runtime/**   → 'dolphin'  (MSL/Runtime uses dolphin flags)
    src/d/**, src/m_**               → 'dolzel'   (game code, GC/1.3.2 + -schedule off)

Default if unknown: 'jsystem' (conservative — won't match but won't crash).
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..state_db import StateDB
from .. import mwcc


@dataclass
class FileResult:
    src: Path
    preset: str
    version: str | None
    hits: list[mwcc.MatchHit]
    misses: int
    ambiguous: int
    compile_failed: bool = False


def preset_for(tww_path: Path) -> str:
    parts = tww_path.parts
    for i, p in enumerate(parts):
        if p in ("libs", "src") and i + 1 < len(parts):
            key = parts[i + 1].lower()
            if "dolphin" in key:
                return "dolphin"
            if "jsystem" in key:
                return "jsystem"
            if "msl" in key or "runtime" in key or "eabi" in key:
                return "dolphin"
            if key in ("d", "m_do", "f_op", "f_pc", "jaudio"):
                return "dolzel"
    return "jsystem"


def iter_tww_sources(tww_root: Path, limit: int = 0):
    """Yield TWW C/C++ sources in import-priority order."""
    priority = [
        "libs/dolphin",
        "src/dolphin",
        "libs/MSL_C",
        "libs/Runtime",
        "libs/PowerPC_EABI_Support",
        "src/PowerPC_EABI_Support",
        "libs/JSystem",
        "src/JSystem",
        "src/f_pc",
        "src/f_op",
        "src/d",
        "src/m_Do",
    ]
    seen = set()
    yielded = 0
    for subdir in priority:
        base = tww_root / subdir
        if not base.exists():
            continue
        for src in sorted(list(base.rglob("*.c")) + list(base.rglob("*.cpp"))):
            if src in seen:
                continue
            seen.add(src)
            yield src
            yielded += 1
            if limit and yielded >= limit:
                return


# -----------------------------------------------------------------------------
# Splits.txt + configure.py writers (post-Gate-4)
# -----------------------------------------------------------------------------

def splits_entry_for(fsa_src_rel: str, hits: list[mwcc.MatchHit]) -> str:
    if not hits:
        return ""
    start = min(h.fsa_addr for h in hits)
    end   = max(h.fsa_addr + h.size for h in hits)
    return f"{fsa_src_rel}:\n\t.text       start:0x{start:08X} end:0x{end:08X}\n"


def append_splits(cfg: Config, new_entries: list[str], dry_run: bool):
    existing = cfg.splits_path.read_text()
    to_add = [e for e in new_entries if e and e.split(":")[0] + ":" not in existing]
    if not to_add:
        return
    if dry_run:
        print(f"[import] would append {len(to_add)} splits.txt entries")
        return
    cfg.splits_path.write_text(existing.rstrip() + "\n\n" + "\n".join(to_add) + "\n")
    print(f"[import] appended {len(to_add)} splits.txt entries")


def tww_to_fsa_src_path(tww_src: Path, tww_root: Path) -> str:
    try:
        rel = tww_src.relative_to(tww_root)
    except ValueError:
        return tww_src.name
    parts = rel.parts
    if parts and parts[0] in ("libs", "src"):
        parts = parts[1:]
    return "/".join(parts)


def copy_tww_source(cfg: Config, tww_src: Path, rel: str, dry_run: bool):
    dst = cfg.src_root / rel
    if dst.exists():
        return
    if dry_run:
        print(f"[import] would copy {tww_src} → {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(tww_src.read_text(errors="ignore"))


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def _sweep_one(args_tuple):
    """Worker entry point for ProcessPoolExecutor.

    Kept at module top-level so pickling works. Config is rebuilt inside the
    worker (its defaults come from env vars) — simpler than pickling a live
    dataclass that holds resolved Path fields."""
    from ..config import Config as _Config
    cfg = _Config()
    src, preset = args_tuple
    exhaustive = bool(os.environ.get("IMPORT_EXHAUSTIVE"))
    # IMPORT_VERSIONS=GC/1.3.2,GC/1.2.5n,GC/1.3 caps the sweep to a short list.
    override = os.environ.get("IMPORT_VERSIONS")
    versions = [v.strip() for v in override.split(",")] if override else None
    return src, preset, mwcc.sweep_versions(
        cfg, src, preset,
        versions=versions,
        short_circuit=not exhaustive,
        early_cutoff=not exhaustive,
    )


def _load_skip_set(log_path: Path, tww_root: Path) -> set[Path]:
    """Parse a previous import log and return the set of already-processed
    src paths so we can resume a killed run without repeating work."""
    skip: set[Path] = set()
    if not log_path.exists():
        return skip
    import re as _re
    pat = _re.compile(r'^\[import\]\s+\[\d+/\d+\]\s+(\S+)')
    for line in log_path.read_text(errors="ignore").splitlines():
        m = pat.match(line)
        if m:
            skip.add((tww_root / m.group(1)).resolve())
    return skip


def _backfill_from_db(cfg: Config, args) -> int:
    """Derive splits.txt stanzas (and note configure.py work) from state.db
    MATCHED_TWW rows.

    Used when a Gate-4 mass import was interrupted before `append_splits()`
    flushed its accumulator — state.db already has the match rows, so we can
    reconstruct the per-file address ranges without recompiling.

    Grouping key: `unit` column (set to the FSA-relative src path when the
    import inserts the row). All MATCHED_TWW rows with the same `unit` form
    one stanza; `.text start/end` spans min(addr) .. max(addr+size).
    """
    if not cfg.state_db_path.exists():
        print(f"[import] --splits-only: state.db not found at {cfg.state_db_path}")
        print(f"[import] Run a full `--phase import` first, or copy state.db in.")
        return 2

    db = StateDB(cfg.state_db_path)
    try:
        cur = db.conn.execute(
            "SELECT unit, addr, size, tww_source "
            "FROM functions "
            "WHERE state='MATCHED_TWW' AND unit IS NOT NULL "
            "ORDER BY unit, addr"
        )
        by_unit: dict[str, list[tuple[int, int, str | None]]] = {}
        for row in cur:
            by_unit.setdefault(row["unit"], []).append(
                (row["addr"], row["size"] or 0, row["tww_source"])
            )
    finally:
        db.close()

    if not by_unit:
        print("[import] --splits-only: no MATCHED_TWW rows in state.db")
        return 1

    existing_splits = cfg.splits_path.read_text()
    stanzas: list[str] = []
    already_wired: list[str] = []
    for unit, entries in sorted(by_unit.items()):
        if unit + ":" in existing_splits:
            already_wired.append(unit)
            continue
        start = min(e[0] for e in entries)
        end   = max(e[0] + e[1] for e in entries)
        stanzas.append(
            f"{unit}:\n\t.text       start:0x{start:08X} end:0x{end:08X}\n"
        )

    print(f"[import] --splits-only: {len(by_unit)} units in state.db "
          f"({len(already_wired)} already wired, {len(stanzas)} to add)")
    for u in already_wired[:5]:
        print(f"[import]   already wired: {u}")
    if len(already_wired) > 5:
        print(f"[import]   … +{len(already_wired) - 5} more already wired")

    if args.dry_run:
        for s in stanzas[:5]:
            print("[import] would add:\n" + s)
        if len(stanzas) > 5:
            print(f"[import] … +{len(stanzas) - 5} more stanzas")
        return 0

    if stanzas:
        append_splits(cfg, stanzas, dry_run=False)

    # Also emit a configure.py hint file so the operator can splice
    # Object(Matching, …) entries by hand. We group by lib helper so the
    # paste-target is obvious. (Task 3 of the post-Gate-4 backfill.)
    hints_path = _write_configure_hints(cfg, list(by_unit.keys()), already_wired)
    if stanzas:
        print(f"[import] NOTE: configure.py Object(Matching, ...) entries "
              f"still need to be wired for {len(stanzas)} units.")
        print(f"[import] Grouped suggestions written to: {hints_path}")

    return 0


def _write_configure_hints(cfg: Config, units: list[str], already_wired: list[str]) -> Path:
    """Emit configure.py Object(Matching, …) suggestions, grouped by the lib
    helper the operator will paste into (DolphinLib, JSystemLib, raw dicts).

    We don't auto-patch configure.py: the file has significant structure
    (helpers, existing lib blocks, actor RELs) that's easier to merge by
    hand than by regex. This hint file is the paste source.
    """
    already = set(already_wired)
    groups: dict[str, list[str]] = {}
    for u in units:
        if u in already:
            continue
        # Group key = (helper, lib_name) or ("raw", prefix)
        parts = u.split("/")
        if parts[0] == "dolphin" and len(parts) >= 3:
            key = f'DolphinLib("{parts[1]}", [...])'
        elif parts[0] == "JSystem" and len(parts) >= 3:
            key = f'JSystemLib("{parts[1]}", [...])'
        elif parts[0] == "PowerPC_EABI_Support":
            # MSL_C / Runtime — no helper yet; recommend one
            if "Runtime" in parts:
                key = 'PowerPC_EABI_Support / Runtime (needs new helper)'
            elif "MSL_C" in parts:
                key = 'PowerPC_EABI_Support / MSL_C (needs new helper)'
            else:
                key = 'PowerPC_EABI_Support / other (needs new helper)'
        elif parts[0] == "d" and len(parts) >= 2 and parts[1] == "actor":
            key = 'd/actor/*.cpp — each is its own ActorRel(Matching, "d_a_<name>")'
        elif parts[0] == "f_op":
            key = 'f_op (lives in main-game lib — add Object() there)'
        else:
            key = f'{parts[0]} — (manual grouping needed)'
        groups.setdefault(key, []).append(u)

    lines: list[str] = [
        "# configure.py additions — generated by `--phase import --splits-only`.",
        "# Paste each block under its named helper call in configure.py.",
        "# Anything tagged `(needs new helper)` requires you to first define the",
        "# helper (mirroring DolphinLib/JSystemLib) — see configure.py:313–354.",
        "",
    ]
    for helper, ulist in sorted(groups.items()):
        lines.append(f"## {helper}")
        for u in sorted(ulist):
            lines.append(f'    Object(Matching, "{u}"),')
        lines.append("")

    hints_path = cfg.agent_root / "configure_additions.txt"
    hints_path.write_text("\n".join(lines) + "\n")
    return hints_path


def run(cfg: Config, args) -> int:
    if getattr(args, "splits_only", False):
        return _backfill_from_db(cfg, args)

    if not cfg.tww_root.exists():
        print(f"[import] TWW_ROOT={cfg.tww_root} not found.")
        print(f"[import] Clone first: git clone https://github.com/zeldaret/tww {cfg.tww_root}")
        return 2

    db = None
    if not args.dry_run:
        db = StateDB(cfg.state_db_path)

    skip_log = os.environ.get("IMPORT_SKIP_LOG")
    skip_set: set[Path] = set()
    if skip_log:
        skip_set = _load_skip_set(Path(skip_log), cfg.tww_root)
        print(f"[import] Loaded {len(skip_set)} already-processed files from {skip_log}")

    tasks = []
    for src in iter_tww_sources(cfg.tww_root, limit=args.limit):
        if src.resolve() in skip_set:
            continue
        tasks.append((src, preset_for(src.relative_to(cfg.tww_root))))
    total_to_process = len(tasks)

    total_hits = 0
    total_misses = 0
    total_ambig = 0
    total_files = 0
    compile_fails = 0
    version_hits: dict[str, int] = {}
    per_file_stanzas: list[str] = []

    # Workers = CPU - 1, capped at 8 to leave headroom for the wibo processes
    # each worker spawns (one at a time). On an 8-core machine this typically
    # means ~7 concurrent wibo instances.
    default_workers = max(1, min(8, (os.cpu_count() or 4) - 1))
    workers = int(os.environ.get("IMPORT_WORKERS", default_workers))
    print(f"[import] Sweeping {total_to_process} files across {workers} workers")

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_sweep_one, t) for t in tasks]
        for i, fut in enumerate(as_completed(futures), 1):
            src, preset, sr = fut.result()
            total_files += 1
            rel_label = str(src.relative_to(cfg.tww_root))

            if sr.version is None:
                compile_fails += 1
                fails = ",".join(v.replace("GC/", "") for v in sr.compile_fails[:3])
                print(f"[import] [{i}/{total_to_process}] {rel_label:<48} "
                      f"preset={preset:<8} COMPILE_FAIL ({fails})")
                continue

            fn_total = len(sr.hits) + sr.misses + sr.ambiguous
            hit_ratio = f"{len(sr.hits)}/{fn_total}" if fn_total else "0/0"
            marker = "✓" if len(sr.hits) > 0 else "·"
            short_ver = sr.version.replace("GC/", "")
            print(f"[import] [{i}/{total_to_process}] {rel_label:<48} "
                  f"preset={preset:<8} ver={short_ver:<7} {hit_ratio:<8} {marker}")

            total_hits   += len(sr.hits)
            total_misses += sr.misses
            total_ambig  += sr.ambiguous
            if sr.hits:
                version_hits[sr.version] = version_hits.get(sr.version, 0) + len(sr.hits)

            if sr.hits:
                fsa_rel = tww_to_fsa_src_path(src, cfg.tww_root)
                per_file_stanzas.append(splits_entry_for(fsa_rel, sr.hits))
                if not args.dry_run:
                    copy_tww_source(cfg, src, fsa_rel, dry_run=False)
                    for h in sr.hits:
                        db.upsert_function(
                            addr=h.fsa_addr, name=h.name, size=h.size,
                            state="MATCHED_TWW", tww_source=str(src),
                            unit=fsa_rel, confidence=1.0,
                        )

    print()
    print(f"[import] Summary: {total_hits} hits, {total_misses} misses, "
          f"{total_ambig} ambiguous, {compile_fails} compile fails across {total_files} files")
    if version_hits:
        parts = sorted(version_hits.items(), key=lambda kv: -kv[1])
        print(f"[import] Hits by winning compiler: "
              + ", ".join(f"{v}={n}" for v, n in parts))

    DOL_FN_COUNT = 5981
    threshold = int(DOL_FN_COUNT * 0.20)
    pct = (total_hits / DOL_FN_COUNT * 100) if DOL_FN_COUNT else 0
    status = "PASS" if total_hits >= threshold else "FAIL"
    print(f"[import] Gate 4 ({threshold}+ hits = ≥20% of DOL): {status} "
          f"({total_hits} hits = {pct:.1f}%)")

    if per_file_stanzas and not args.dry_run:
        append_splits(cfg, per_file_stanzas, dry_run=False)
        print("[import] NOTE: configure.py Object(Matching, ...) entries still "
              "need hand-wiring. See BROWSER_PORT_PLAN_V2.md §5.")

    if db is not None:
        db.close()
    return 0 if total_hits > 0 else 1
