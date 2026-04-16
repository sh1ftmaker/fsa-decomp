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

| File | Functions | Status |
|------|-----------|--------|
| `src/dolphin/os/OS.c` | OSGetArenaHi, OSGetArenaLo, OSSetArenaHi, OSSetArenaLo | **100% ✅** |
| `src/dolphin/os/OSCache.c` | DCEnable, DCInvalidateRange, DCFlushRange, DCStoreRange, DCFlushRangeNoSync, DCStoreRangeNoSync, DCZeroRange, ICInvalidateRange, ICFlashInvalidate, ICEnable, __LCEnable, LCEnable, LCDisable, LCStoreBlocks, LCStoreData, LCQueueWait, L2Disable, L2GlobalInvalidate, DMAErrorHandler, L2Init, L2Enable, __OSCacheInit | **pending verify** |
| `src/dolphin/os/OSTime.c` | OSGetTime, OSGetTick, __OSGetSystemTime, GetDates, OSTicksToCalendarTime | **pending verify** |
| `src/main/main.cpp` | everything else | NonMatching stub |

Overall: ~0.03% matched. All infrastructure is in place to scale this up.

> **Note**: OSCache.c and OSTime.c are committed but not yet ninja-verified on Linux.
> Run `python configure.py && ninja` to confirm they match. If there are mismatches,
> check the compiler error/diff output and compare against the DOL using objdiff.

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
| `include/dolphin/os.h` | OS function/variable declarations |

## Next Steps

### Immediate (verify OSCache.c + OSTime.c)

1. **Run `python configure.py && ninja`** — OSCache.c and OSTime.c are committed but not
   yet build-verified. Check objdiff output and fix any mismatches.
   - OSCache.c: `.text 0x80040F20–0x80041598`
   - OSTime.c: `.text 0x800463E0–0x80046804`, `.data 0x80495FE8–0x80496048`
   - If mismatch in OSCache.c gap functions (DCStoreRange, DCStoreRangeNoSync, DCZeroRange,
     __LCEnable, LCStoreBlocks, L2Disable, L2Init) — those were in gap regions (not in the
     dtk exception table), so they may need adjustment
2. **Add `stddef.h` stub** — `include/dolphin/types.h` includes `stddef.h` which isn't found
   by mwcceppc without MSL. Either add a minimal stub at `include/stddef.h` (`NULL`, `size_t`,
   `ptrdiff_t`) or remove the include and define only what's needed.

### Next Dolphin OS files (in order)

3. **OSInterrupt.c** — OSDisableInterrupts, OSEnableInterrupts, OSRestoreInterrupts
   - Verified at FSA: `0x80042638`, size `0x14` (OSDisableInterrupts confirmed by disasm)
   - TWW source: `src/dolphin/os/OSInterrupt.c` (fetched to `/tmp/OSInterrupt.c` previously)
   - Note: TWW's OSInterrupt.c has many more functions; only these 3 are confirmed in FSA
     at that address. Check dtk exception table for full range.
4. **OSSync.c** — `__OSInitSystemCall` at FSA `0x80045044`
5. **OSContext.c** — `OSContext` save/restore functions
6. **OSThread.c** — threading primitives

### Broader roadmap

7. **Import MTX**: matrix math — simple, very likely identical
8. **Import MSL**: standard library functions
9. **Import JKernel**: memory management
10. **Clean up m2c scratches**: the 6 fpc/dr_matrix_set scratches have matching assembly but
    need M2C_ERROR macros replaced with proper C before they can be committed
11. **Update symbols.txt**: as functions are confirmed at FSA addresses, update from TWW placeholders

### Useful reference

- `dtk dol info orig/sys/main.dol` — lists all 5,516 functions with FSA addresses + sizes
  (discovered via exception table). Use this instead of binary pattern search where possible.
- mftb vs mfspr: FSA uses `mftb` (XO=371, bytes `42E6`) not `mfspr` (XO=339, bytes `42A6`)
- SDA bases: r13 = `0x80541BC0`, r2 = `0x80542FA0`
