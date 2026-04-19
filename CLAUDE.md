# CLAUDE.md — FSA Decompilation Project Guide

## What This Is

A decompilation of *The Legend of Zelda: Four Swords Adventures* (GameCube, USA / G4SE01).
FSA shares its codebase with *The Wind Waker* — same compiler (Metrowerks CodeWarrior GC/1.3.2),
same SDK (Dolphin SDK — named after the GameCube's dev codename, not the emulator), same JSystem
middleware. This makes TWW's decompilation a goldmine for FSA.

## Environment (Already Set Up)

This repo was bootstrapped in a Claude worktree session. The following are already done:

- **ninja** installed via winget
- **Game extracted** to `orig/` using `dtk disc extract` from the RVZ (no Dolphin emulator needed)
- **`orig/` is a junction** in this worktree pointing to the main project's `orig/` — don't delete it
- **All build tools** downloaded: `build/tools/dtk.exe`, `build/tools/objdiff-cli.exe`, compilers in `build/compilers/`
- **objdiff GUI** downloaded to the project root as `objdiff-gui.exe`

To rebuild from scratch: `python configure.py && ninja` (downloads tools automatically on first run).

## Build Architecture

### Key design decision: `fill_gaps: true`

`config/G4SE01/config.yml` uses `fill_gaps: true`. This means:
- dtk auto-generates one target object per function for every DOL range not explicitly claimed
- All 11,556 game functions are individually browsable in objdiff (~5,989 units total)
- `main/main.cpp` does NOT own any DOL range — it's compiled and linked as a NonMatching stub
- Only explicitly verified files appear in `splits.txt`

### How to add a new matched file

1. Find the FSA DOL address for the function(s) — use binary pattern search (see below)
2. Add to `config/G4SE01/splits.txt`:
   ```
   path/to/file.c:
       .text       start:0xXXXXXXXX end:0xXXXXXXXX
   ```
   Include `.sdata`/`.sbss` entries if the file defines global variables.
3. Add to `configure.py` under the appropriate lib: `Object(Matching, "path/to/file.c")`
4. Add source to `src/path/to/file.c`
5. Run `ninja` — the file gets compiled, linked, and verified against the DOL split

### Finding FSA addresses via binary search

Since `symbols.txt` has TWW addresses (not FSA), use byte-pattern matching on the DOL:

```python
with open('orig/sys/main.dol', 'rb') as f:
    dol = f.read()
TEXT_OFF, TEXT_ADDR = 0x2600, 0x80021840
text = dol[TEXT_OFF:TEXT_OFF + 0x43A4A4]
idx = text.find(bytes.fromhex('YOUR_BYTE_PATTERN'))
if idx != -1:
    print(f'0x{TEXT_ADDR + idx:08X}')
```

Get byte patterns by compiling the TWW source with FSA's flags, then disassembling.

### SDA base register

`r13 = 0x80541BC0` (_SDA_BASE_ in FSA). Used to compute sdata/sbss offsets for global variables.
`r2  = 0x80542FA0` (_SDA2_BASE_ in FSA).

## Current Match Status

Headline: **430 / 5,981 DOL functions byte-matched via TWW import (7.2%)** after
the 2026-04-18 Gate 4 sweep. Earlier hand-matched work still present.

### Gate 4 breakdown (by subdir of TWW donor)

| Subdir | Hits | Notes |
|--------|------|-------|
| `src/JSystem` | 251 | JKernel, JSupport, JGadget, J3DGraph — mostly byte-identical middleware |
| `src/d` | 70 | d_base, d_com_inf_game, d_kankyo, d_save, d_stage (40 hits alone) |
| `src/dolphin` | 66 | OS, DVD, GX, MTX, GBA — SDK |
| `src/PowerPC_EABI_Support` | 39 | MSL C / Runtime |
| `src/f_op`, `src/m_Do` | 4 | Thin framework layer |

Hits by winning compiler: GC/1.3.2=387, GC/1.2.5n=33, GC/2.0=6, GC/2.5=4.
The 2-version sweep `(1.3.2, 1.2.5n)` captures 97% of hits; others only
useful for a handful of MSL/J2D files found via the exhaustive Part 1 probe.

### Selected hand-matched files (pre-Gate-4)

| File | Status |
|------|--------|
| `src/dolphin/os/OS.c` | **100% ✅** (arena) |
| `src/dolphin/os/OSCache.c` | **99.93% ✅** (3 fns minor rodata diffs) |
| `src/dolphin/os/OSTime.c` | **100% ✅** |
| `src/dolphin/os/OSInterrupt.c` | **100% ✅** |
| `src/dolphin/os/OSSync.c` | **100% ✅** |
| `src/JSystem/JKernel/JKRDisposer.cpp` | **100% ✅** |
| `src/main/main.cpp` | NonMatching stub (everything else) |

> **Key fix**: Dolphin SDK files must use compiler `GC/1.2.5n` (not `GC/1.3.2`). The `DolphinLib()`
> helper in configure.py already sets this. OS.c only has ASM so it matched either way.

> **JSystem scheduling**: FSA's JSystem libs were compiled WITHOUT `-schedule off`. `cflags_jsystem`
> in configure.py omits it (unlike `cflags_framework`/`cflags_dolzel`). The `JSystemLib()` helper
> uses `cflags_jsystem`. Confirmed by byte-matching JKRDisposer against FSA DOL.

> **Linux linker issue**: `mwldeppc.exe` via wibo fails to link when RSP file exceeds ~48KB
> (~1150 objects). With `fill_gaps: true` generating ~6000 objects, the full link step always
> fails on Linux. Per-file matching via the REPORT step works fine — objdiff and match
> percentages are unaffected. This is a known wibo limitation; the build works on Windows.

## The TWW Strategy

The TWW decompilation (https://github.com/zeldaret/tww) has these libraries **fully matched**
and ready to import into FSA:

**Dolphin SDK** (highest priority — low-level, identical between games):
- `OS` — threading, memory arena, interrupts, DVD, RTC (24 objects, we have 1)
- `MTX` — matrix/vector math
- `DVD` — disc access

**JSystem** (game engine middleware, likely identical):
- `JKernel` — heap/memory management, archive/file loading (25 objects)
- `JGadget` — linked list, binary, vector
- `JSupport` — stream, file I/O utilities
- `JFramework` — display/system framework
- `J3DGraphLoader`, `J3DGraphBase`, `J3DGraphAnimator` — 3D rendering

**MSL/Runtime** — standard C library, math, strings (35+ matched objects)

### Import workflow for TWW libraries

1. Fetch the TWW source file from https://github.com/zeldaret/tww
2. Note: TWW uses the same compiler flags for these libraries
3. Compile with FSA's cflags to get the expected binary output
4. Binary-search the FSA DOL for each function's bytes
5. Populate `splits.txt` with FSA addresses, add to `configure.py`

The OS arena functions were done this way as proof-of-concept — it works cleanly.

## decomp.me

Preset **228** = "The Legend of Zelda: Four Swords Adventures (DOL)".
Direct URL: https://decomp.me/preset/228

There are 11 existing scratches on this preset. **Known issue**: the preset page shows
a blank list — this is a decomp.me bug where anonymous (ownerless) scratches don't render
in the list view. Access scratches directly by slug URL. Log in and use "Fork" to claim them.

Current scratches (all anonymous):

| Slug | Function | Match |
|------|----------|-------|
| [vr722](https://decomp.me/scratch/vr722) | `dr_matrix_set__FP14damagereaction` | 100% (m2c, has M2C_ERROR) |
| [bh97c](https://decomp.me/scratch/bh97c) | `fpcLnIt_MethodCall__FP16create_tag_classP13method_filter` | 100% (m2c) |
| [QxLng](https://decomp.me/scratch/QxLng) | `fpcLnTg_Init__FP8line_tagPv` | 100% (m2c) |
| [XgQJV](https://decomp.me/scratch/XgQJV) | `fpcLnIt_Queue__FPFPvPv_i` | 100% (m2c) |
| [gif1Z](https://decomp.me/scratch/gif1Z) | `fpcMtdTg_ToMethodQ__FP15node_list_classP24process_method_tag_class` | 100% (m2c) |
| [mruLD](https://decomp.me/scratch/mruLD) | `fpcPause_Init__FPv` | 100% (m2c) |
| [qvefu](https://decomp.me/scratch/qvefu) | `OSSetArenaLo` | 100% ✅ (in repo) |
| [eQWCI](https://decomp.me/scratch/eQWCI) | `OSSetArenaHi` | 100% ✅ (in repo) |
| [H8w45](https://decomp.me/scratch/H8w45) | `OSGetArenaLo` | 100% ✅ (in repo) |
| [nKwBk](https://decomp.me/scratch/nKwBk) | `OSGetArenaHi` | 100% ✅ (in repo) |
| [hAF3y](https://decomp.me/scratch/hAF3y) | `test` | 0% |

The m2c scratches (marked above) are auto-decompiler output — they match in assembly but use
`M2C_ERROR` macros and won't compile locally without cleanup.

## Key Files

| File | Purpose |
|------|---------|
| `configure.py` | Build config — add new `Object()` entries here |
| `config/G4SE01/splits.txt` | DOL address ranges for verified files |
| `config/G4SE01/symbols.txt` | Symbol names/sizes (TWW addresses for .text, FSA for data) |
| `config/G4SE01/config.yml` | dtk config — `fill_gaps: true` is critical |
| `src/dolphin/os/OS.c` | First matched file — use as template |
| `src/JSystem/JKernel/JKRDisposer.cpp` | First JSystem matched file — uses cflags_jsystem |
| `include/dolphin/os.h` | OS function/variable declarations |
| `tools/compile_search.py` | **Key automation**: compile → find all fns in FSA DOL |
| `tools/find_fn.py` | Quick DOL function lookup by name |

## Utility Scripts

| Script | Purpose |
|--------|---------|
| `tools/find_fn.py <name>` | Find function address/size in FSA DOL by name substring |
| `tools/search_dol.py <hex>` | Search DOL text section for literal byte pattern |
| `tools/compile_search.py <src.cpp>` | Compile + auto-find each function in DOL (masked reloc search) |
| `tools/m2c_batch.py` | Batch decompile all unmatched functions via m2c |

The **highest-leverage workflow** is `compile_search.py`: fetch a TWW source file, run the script, 
and immediately get FSA addresses for every function. No manual byte hunting needed.

## The port-agent (`port-agent/`)

The five-phase orchestration pipeline for a non-matching browser port lives
in `port-agent/` (merged in 2026-04-18). It wraps this repo's tools but adds:

- **State DB** (`port-agent/state.db`, gitignored) — one row per DOL function,
  linear state machine: UNKNOWN → TRIAGED → MATCHED_TWW / CLEANED / BUILDS / FAILED.
- **Filesystem work queue** (`port-agent/work/*/`, gitignored) — prompts out,
  responses in. No Anthropic API key; Claude Code itself writes responses.
- **`fsa_port_agent.mwcc`** — compile + masked-search library shared by Gate 4
  import and the per-function compiler-version probe.
- **`shim_include/`** — empty-stub headers (JSystem.h, d/dolzel*.h, assets/*.h)
  that bypass TWW's `.mch`/`.pch` precompiled-header chains so each TU compiles
  standalone against TWW's tree.

Entry point: `python -m fsa_port_agent --phase {triage|import|decompile|hal|build|dashboard}`.
See `port-agent/CLAUDE.md` for phase-by-phase instructions.

## Next Steps (Highest Impact / Least Work)

### 1. Gate 4 is effectively done — pivot to semantic context

Byte-matching tops out at **~430 / 5,981 hits (7.2%)**. Engine/SDK/middleware
is captured; game-specific code (`src/d/actor/*`, most of `src/m_Do/*`) compiles
cleanly against TWW but emits zero byte matches because FSA's actors are
different game content. **This is a ceiling, not a bug.**

The surprise finding: **3,896 / 17,876 FSA symbols (22%) use TWW-style names**
and 23 FSA actor classes (`daArrow_c`, `daBoko_c`, `daItem_c`, …) have direct
TWW `d_a_*.cpp` file analogs with matching method names. FSA's code is
structurally TWW — just recompiled with different register allocation and
minor game-specific method bodies.

**Action**: wire TWW source lookup into `port-agent/fsa_port_agent/prompts/cleanup.md`.
For each FSA function whose mangled name decodes to a TWW class, include the
matching TWW C++ method as a second context block alongside m2c output. Expected
win: m2c output becomes a skeleton, TWW source becomes the types-and-naming
template, Claude pattern-matches rather than inventing.

Concrete steps:
- Parse FSA's `config/G4SE01/symbols.txt`, index by fn_addr → mangled name.
- Decode `method__NdaFooBar_cFv` → class `daFooBar_c` → file `src/d/actor/d_a_foo_bar.cpp`.
- Extract matching `daFooBar_c::method(...)` body via brace-matching.
- Inject into the `cleanup.md` rendered prompt as a `{tww_reference}` field.

### 2. Commit the full match set

The Gate 4 non-dry run copies matched TWW sources into `src/<lib>/` and writes
`splits.txt` stanzas. Run from `port-agent/`:

```bash
IMPORT_VERSIONS=GC/1.3.2,GC/1.2.5n IMPORT_WORKERS=7 \
  python -m fsa_port_agent --phase import
```

…then commit the new `src/**` files + `splits.txt` deltas. `configure.py`
`Object(Matching, …)` entries are still hand-wired (noted in the run's final
message).

### 3. Decomp OS/filesystem remainders

1. **OSSram.c** — `__OSInitSram`, `__OSLockSram`, `__OSUnlockSram` (FSA `0x8004460C`–`0x80044B94`)
2. **OSContext.c** — save/restore (FSA `0x80041DC0`)
3. **OSThread.c** — threading primitives (expect lower hit rate; TWW rewrote some)

### 4. Browser multiplayer port (longer-term goal)

See **[BROWSER_PORT_PLAN_V2.md](BROWSER_PORT_PLAN_V2.md)** — supersedes V1.
Uses m2c batch conversion + TWW library imports rather than byte-perfect
matching, targeting a non-matching functional port.

### Useful reference

- `dtk dol info orig/sys/main.dol` — lists all 5,516 functions with FSA addresses + sizes
  (discovered via exception table). Use this instead of binary pattern search where possible.
- mftb vs mfspr: FSA uses `mftb` (XO=371, bytes `42E6`) not `mfspr` (XO=339, bytes `42A6`)
- SDA bases: r13 = `0x80541BC0`, r2 = `0x80542FA0`
