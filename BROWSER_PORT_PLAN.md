# FSA → Browser Multiplayer Port — Strategic Plan

## Goal

Get *Four Swords Adventures* playable in a browser with online co-op (like sm64coopdx). The
traditional decompilation approach optimizes for byte-perfect matching — the wrong metric for
this goal. A **functional non-matching port** reaches the browser much faster: register
allocation perfection is irrelevant; the permuter step (which costs most of the time/money)
is entirely skipped.

## Strategic Choice: Three Paths Evaluated

```
Path A: Dolphin → WASM emulator
  Fastest to "playable" (weeks) but ROM-dependent, no code ownership, dead end for modding

Path B: GeckoRecomp static recompilation
  N64Recomp-style approach; GeckoRecomp (the GC fork) has 27 stars, no releases,
  unproven on any GC title — not viable yet

Path C: Mass m2c + TWW library imports → WASM   ← RECOMMENDED
  Weeks to functional compilation skeleton; months to playable; you own the code
```

m2c already gets logic right for non-matching purposes — the bottleneck for this goal is
platform shims, not function translation.

---

## Phase 1: TWW Library Mass-Import (Weeks 1–3, ~40% coverage target)

TWW has these libraries fully matched and ready to pull:

| Library | Approx objects | Priority |
|---------|---------------|----------|
| Dolphin SDK (OS, MTX, DVD, VI, SI, AI, EX, DSP, GX…) | 80+ | Highest |
| JSystem (JKernel, JGadget, JSupport, J3DGraph*, JFramework) | 100+ | High |
| MSL/Runtime (stdlib, math, strings) | 35+ | High |

**Import workflow per file:**
1. Fetch TWW source from https://github.com/zeldaret/tww
2. Compile with FSA cflags to get expected binary
3. Binary-search FSA DOL for each function's bytes
4. Populate `splits.txt` + `configure.py` Object(Matching, …) entries
5. Copy source verbatim

**Validated:** objdiff's function-level comparison is relocation-aware — it correctly
handles `bl` PC-relative encoding differences between the unlinked `.o` and the linked
DOL. OSSync.c (a non-leaf function with multiple `bl` calls) matched 100% against FSA
on first compile. The mass-import approach is confirmed viable for non-leaf code.

**Script to build: `tools/tww_import.py`**

## Phase 2: m2c Batch Pipeline for Game Code (Weeks 2–5)

m2c is deterministic — zero LLM cost.

```
dtk dol disasm orig/sys/main.dol → per-function .s files
  ↓
m2c --arch ppc function.s → function.c
  ↓
fix_m2c_errors.py: replace M2C_ERROR(expr) with (expr) casts
  ↓
group_by_module.py: cluster adjacent functions into TUs
  ↓
write to src/game/{module}.c
```

All 11,556 functions processed in a few hours. Optional LLM cleanup of M2C_ERRORs at
~$5/1800 functions (~$30 total).

After this phase: project compiles (with stub HAL) as a non-matching build.

**Scripts to build: `tools/m2c_batch.py`, `tools/fix_m2c_errors.py`**

## Phase 3: Hardware Abstraction Layer (Months 1–4)

Replace GC hardware calls with portable equivalents:

| Hardware | Replacement | Notes |
|----------|-------------|-------|
| GX (GPU) | OpenGL ES 2 / WebGL | GX is a FIFO command stream; shim or re-map draw calls |
| DSP/AX (audio) | OpenAL / Web Audio API | AX mixer well-understood from Dolphin |
| PAD (input) | SDL2 → Gamepad API | Straightforward |
| DVD/filesystem | Virtual FS via Fetch API | ROM assets from blob URL |
| Memory card | localStorage | Tiny, easy |
| VI (video output) | Canvas/WebGL present | Hook GX EFB copy |

GX is the biggest challenge. Two strategies:
- **Option A**: Port Dolphin's GX emulation code as a software layer
- **Option B**: Import TWW GX source (fully matched) + thin GX→OpenGL shim

Option B is faster because the TWW GX source already explains what every call does.

New directory: `src/platform/wasm/`

## Phase 4: Emscripten Build Loop (Months 2–5, iterative)

```
emcc src/**/*.c -o fsa.wasm \
  -s USE_SDL=2 -s USE_WEBGL2=1 -s ASYNCIFY \
  -s INITIAL_MEMORY=64MB --preload-file assets/
```

Fix compilation errors in waves — they'll be systematic (missing types, wrong signatures)
and LLM-assistable at the decomp-research-ai pipeline cost.

## Phase 5: Networking (Months 4–6)

Follow the sm64coopdx architecture:
- **Transport**: PeerJS/WebRTC for browser-native P2P (no server required)
- **Sync method**: Rollback netcode (GGPO-style)
  - FSA already has local co-op — the multiplayer state is well-defined
  - Identify player state struct addresses (Action Replay codes document many)
  - Sync inputs + periodic state snapshots
- **Lua modding layer** (optional, for ecosystem)

---

## Timeline (Aggressive but Realistic)

```
Weeks 1–2:   Feasibility probe; build tww_import.py + m2c_batch.py; run on full DOL
Weeks 3–6:   Fix compilation errors until skeleton builds with stub HAL
Months 2–3:  GX → WebGL shim (largest engineering task)
Months 3–4:  Audio + input + filesystem shims; first playable frame
Months 4–5:  Emscripten WASM build; basic browser playability
Months 5–6:  PeerJS networking + rollback sync; playable with friends
```

## What NOT to Do

- **Don't byte-perfect match game code** — register allocation is irrelevant for a port
- **Don't rely on GeckoRecomp yet** — experimental, no proven GC game
- **Don't fine-tune Gemma 4** — m2c + targeted LLM passes are already the right tool

## Key Files Modified by This Plan

| File | Change |
|------|--------|
| `configure.py` | Add TWW Object(Matching, …) entries (Phase 1) |
| `config/G4SE01/splits.txt` | Add FSA address ranges for imported files (Phase 1) |
| `tools/tww_import.py` | New — automates TWW source → FSA address mapping |
| `tools/m2c_batch.py` | New — bulk m2c conversion of all unmatched functions |
| `tools/fix_m2c_errors.py` | New — systematic M2C_ERROR macro resolution |
| `src/dolphin/gx/` | New — GX HAL (Phase 3) |
| `src/platform/wasm/` | New — Emscripten platform layer |

## Verification Milestones

- After Phase 2: `python configure.py --non-matching && ninja` completes with all functions covered
- After Phase 3: `emcc` compiles without unresolved symbols
- After Phase 4: Browser tab opens, first frame renders
- After Phase 5: Two browser tabs connect via PeerJS, both players move
