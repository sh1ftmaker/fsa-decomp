# FSA → Browser WASM Port — Automated Pipeline Plan v2

*Supersedes BROWSER_PORT_PLAN.md (v1). Re-evaluated 2026-04-18 against the full
`decomp-research-ai` knowledge base (post-Discord mining) and the `amruth-sn/kong`
agentic reverse-engineering architecture.*

---

## 0. TL;DR — What Changed

The v1 plan was directionally right: pick the m2c + TWW-import path, skip
byte-perfect matching, build toward emscripten. Three new inputs justify a
tighter v2:

1. **Discord archive (post-mining, 92–95% coverage)** confirms decomp-permuter
   has **no public PPC support** — byte-matching game code at scale is a dead
   end regardless of budget. This kills any remaining temptation to permute.
2. **`decomp-research-ai/decomp_agent/`** is a state-machine pipeline built for
   *byte-matching* Melee. Its `REGALLOC_ONLY → PERMUTER → AI` branch is the
   most expensive path and is **entirely skippable** for our goal. We keep the
   cheap parts (m2c runner, build+diff classifier, source editor, AI fixer
   scaffolding) and discard the permuter branch.
3. **Kong (amruth-sn/kong)** solves a structurally identical problem (raw
   decompiler slop → readable code) with a five-phase agent pipeline that
   improves on `decomp_agent`'s per-function loop: **call-graph-ordered
   bottom-up processing, rich context windows (callee sigs + xrefs +
   neighboring data), pre-triage signature matching, global synthesis pass**.
   We adopt that shape.

This plan is the merger: Kong's orchestration on top of FSA's existing m2c +
TWW-import tooling, targeting an emscripten build as fast as possible.

---

## 1. Where FSA Actually Is Today

Read from the live tree, not the v1 doc:

| Asset | State |
|---|---|
| `tools/m2c_batch.py` | Ran. `src/nonmatch/` has **122 `seg_*.c` files** covering 11,556 functions. |
| `tools/compile_search.py` | Works. Used for TWW-import proof-of-concept (OS files). |
| `tools/fix_nonmatch.py` | Regex post-processor for m2c output — handles `@sym`, bitwise casts, neg offsets, vtable calls, forward decls, stub `nonmatch.h`. |
| `config/G4SE01/splits.txt` | Only 6 matched files (OS + JKRDisposer). |
| `src/dolphin/os/` + `src/JSystem/JKernel/` | Seeded. |
| Build status (Linux) | Per-file object build works; full link fails at ~48KB RSP (wibo limit). **Acceptable** — per-unit diff is what we need. |
| `configure.py` | `fill_gaps: true`, per-function auto-object layout is in place. `_nonmatch_segs` are wired as `pre-compile` steps (compiled, not linked). |
| `BROWSER_PORT_PLAN.md` (v1) | High-level, no pipeline architecture, no agent design. |

**Net:** the raw material for a non-matching build already exists. What's
missing is (a) the orchestration to get from "~6000 segfiles that each have
m2c errors" to "compiles cleanly under emcc," and (b) the HAL.

---

## 2. What to Steal From Each Source

### From `decomp-research-ai/decomp_agent/` — keep these, drop those

| Component | Verdict | Reason |
|---|---|---|
| `target_selector.py` scoring | **Keep, adapt** | Replace "near-match bonus" with "leaf-first DAG ordering" (Kong-style). |
| `build_diff.py` classifier | **Keep** | We still want pass/fail per TU, just not for permuter routing. |
| `source_editor.py` | **Keep** | Extract/insert per-function still applies to `seg_*.c` files. |
| `m2c_runner.py` | **Keep** | Already have our own — unify. |
| `ai_fixer.py` + `prompts.py` | **Rewrite** | Prompts are MWCC-regalloc-specific. Replace with *compile-fix* / *type-infer* / *struct-synthesize* prompts aimed at emscripten. |
| `permuter_runner.py` | **Drop entirely** | No public PPC permuter exists (confirmed from Discord archive). |
| State machine (`REGALLOC_ONLY → PERMUTER → AI`) | **Drop** | Wrong target metric. |

### From `amruth-sn/kong` — adopt this five-phase shape

```
Triage  →  Mass-Import  →  Decompile  →  HAL  →  Build-Integrate
(analyze) (signature)   (bottom-up)   (shim)  (emcc)
```

Kong's operational insights that transfer directly:

- **Dependency-ordered processing**: analyze leaves first, callers inherit
  already-resolved callee signatures. For FSA: walk the DOL relocation graph
  bottom-up before any LLM call.
- **Rich context windows**: never feed raw m2c output alone. Include callee
  signatures (already resolved), xrefs, nearby `.rodata` strings, and adjacent
  function bodies. The model spends fewer tokens guessing.
- **Signature matching first**: before any LLM call, try exact byte-pattern
  match against TWW libraries. Free percentage, zero cost.
- **Batch-for-easy / sequential-for-hard**: group trivial functions into a
  single prompt packed to `max_prompt_chars`; reserve 1:1 calls for the
  obfuscated tail.
- **Global synthesis pass**: one LLM call that sees many functions at once to
  unify struct layouts and naming. Critical for FSA because m2c invents
  incompatible types across files.

Kong's stack (Ghidra, z3, pygidra) is overkill for our target — we already
have better domain-specific tooling (dtk, objdiff, m2c). We just borrow the
orchestration.

### From `decomp-research-ai/COMMUNITY/` — technical constraints baked into the plan

- FSA-confirmed compiler flags are in `configure.py`. Don't re-derive.
- **TWW parity is the single biggest lever**: same compiler (GC/1.3.2), same
  SDK, same JSystem. Every matched TWW library file is a ~free import via
  `compile_search.py`.
- **`fill_gaps: true` + per-function objects** (FSA has this) mean objdiff gives
  function-level match % for every ID'd function — we can measure progress
  without any full-link.
- **Per-sub-lib cflags**: JSystem used `cflags_jsystem` (no `-schedule off`),
  Dolphin SDK uses `GC/1.2.5n`, not `1.3.2`. The existing `DolphinLib()` and
  `JSystemLib()` helpers already encode this — keep using them.

---

## 3. The v2 Pipeline (concrete)

```
┌────────────────────────────────────────────────────────────────┐
│                    orchestrator.py (kong-style)                 │
│                                                                 │
│  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌─────┐  ┌──────────┐│
│  │ Phase 1 │→ │ Phase 2  │→ │ Phase 3 │→ │ P4  │→ │ Phase 5  ││
│  │ Triage  │  │ Import   │  │ Decomp  │  │ HAL │  │ Build    ││
│  └─────────┘  └──────────┘  └─────────┘  └─────┘  └──────────┘│
│       ↓            ↓             ↓          ↓          ↓       │
│   call graph   TWW sig-hit    m2c + LLM  shims    emcc loop   │
│   state DB     free %         cleanup    (C)      fix waves   │
└────────────────────────────────────────────────────────────────┘
```

### Phase 1 — Triage (days, one-shot)

Goal: know what we're dealing with before spending any LLM tokens.

**Inputs**: `orig/sys/main.dol`, `config/G4SE01/symbols.txt` (TWW-seeded),
`build/G4SE01/asm/auto_*_text.s`.

**Outputs**:
- SQLite state DB (one row per function): address, size, caller-list,
  callee-list, string-refs, current state, confidence.
- Call-graph DAG in topological order.
- Classification tags per function: `LEAF`, `INTERNAL`, `CONSTRUCTOR`, `VTABLE_THUNK`,
  `MSL_STDLIB` (pattern-matched), `LIKELY_TWW_MATCH`, `UNKNOWN`.

**Script to build**: `tools/triage.py`

Uses `dtk dol info` (which already lists 5,516 fn addrs + sizes from the
exception table) + each `auto_*_text.s` file's `bl` relocations to build the
DAG. Pattern-match MSL/Runtime/stdlib functions against known-byte
fingerprints (from `doldecomp/dolsdk2004`, `dolsdk2001`).

### Phase 2 — Mass Import From TWW (1–2 weeks)

Goal: bulk-convert every byte-identical function for free.

**Strategy**: drive `tools/compile_search.py` over the entire TWW source tree
(cloned from `github.com/zeldaret/tww`), one file at a time, collecting hits.
Each hit becomes a `Matching` `Object()` in `configure.py` + an entry in
`splits.txt`. No LLM cost.

**Priority order** (largest fn count × highest hit-probability):

1. `src/dolphin/**` — OS, MTX, DVD, VI, SI, AI, EX, DSP, GX. ~80+ files.
2. `src/JSystem/**` — JKernel, JUtility, JSupport, JGadget, JFramework,
   J3DGraph*, JParticle, JAudio. ~100+ files. *Per-sub-lib cflags already
   wired.*
3. `src/MSL/**` + `src/PowerPC_EABI_Support/**` — stdlib, math, strings. ~35+
   files.
4. `src/d/**` (game code) — most TWW game code **won't match** FSA
   byte-for-byte, but some identical utility code will (d/a_cursor etc.).

**Script to build**: `tools/tww_import.py`

```
for each tww_file in priority_order:
    bytes_per_fn = compile_search(tww_file, cflags=detect(tww_file))
    for fn, addr in bytes_per_fn:
        if addr is not None:
            add_to_splits(fn, addr)
            add_to_configure(tww_file, lib=detect_lib(tww_file))
            mark_state_db(fn, "MATCHED_TWW_IMPORT")
    ninja    # verify builds
```

**Target outcome**: **30–50% of DOL** flipped from `NonMatching` stub to
`Matching` real code. Zero LLM spend. This subsumes Phase 1 of the v1 plan.

### Phase 3 — Bottom-Up Decompile + LLM Cleanup (3–6 weeks)

Goal: every function in the DOL has compilable C — not byte-perfect, just
semantically close and emscripten-buildable.

**Input state** (after Phase 2):
- `src/nonmatch/seg_*.c` — m2c output for functions TWW didn't cover.
- Each contains m2c errors: `M2C_ERROR(...)`, `MULTU_HI`, `saved_reg_*`,
  guessed `?` types (partially handled by `fix_nonmatch.py`), wrong struct
  fields.

**Algorithm** (Kong-adapted):

```
for fn in reverse_topological_order(call_graph):
    if fn.state == "MATCHED_TWW_IMPORT":
        continue
    ctx = build_context(fn):
        - callee_signatures (already resolved, from state DB)
        - caller_signatures (as hints)
        - string_refs (.rodata at referenced addresses)
        - nearby_functions (±3 in file, for style)
        - raw m2c output
        - (no raw asm unless escalating)
    if fn.is_trivial():                         # <20 insn, no M2C_ERROR
        batch_queue.add(fn, ctx)                 # packed into 8k-char batches
    else:
        result = llm.fix(CLEANUP_PROMPT, ctx)   # 1:1 call
        apply_to_seg_file(fn, result)
        update_signature_in_db(fn, result.sig)
    if not emcc_compiles(fn.segfile):
        escalate(fn)                             # retry with asm + better model
```

**Prompt templates** (replacing `decomp_agent/prompts.py`):

| Prompt | Purpose |
|---|---|
| `CLEANUP` | Turn m2c output into emcc-compilable C. Input: m2c fn + callee sigs + strings. Output: fn with real types, resolved `M2C_ERROR`, correct signature. |
| `TYPE_INFER` | Given N functions accessing the same struct, infer the struct layout. Output: `typedef struct {...}`. |
| `SYNTHESIZE` | Global pass: unify inconsistent type names across files, normalize naming. Runs once at end of phase. |
| `FIX_BUILD` | Reactive: emcc error → patch. Used in Phase 5 too. |

**Model tiering** (per Discord archive):
- Haiku 4.5 for `CLEANUP` batches (cheap, fast, 90% of volume).
- Sonnet 4.6 for `TYPE_INFER` and escalations.
- Opus 4.7 for `SYNTHESIZE` (global pass, needs context retention).

**Cost estimate**: ~11,500 fns × 90% Haiku × ~2k tokens = ~$15 Haiku + ~$10
Sonnet escalations + ~$5 Opus synthesis = **~$30 total**.

**Scripts to build** — all in a new sibling repo `../fsa-port-agent/`
(scaffolded, stubs in place). Keeps the new agent separate from both the
old `decomp-research-ai/decomp_agent/` (Melee-focused, byte-matching) and
from `fsa-decomp/tools/` (one-off converters kept as-is; the agent calls
them as subprocesses):

- `fsa_port_agent/supervisor.py` — five-phase orchestrator.
- `fsa_port_agent/agent/triage.py` — Phase 1.
- `fsa_port_agent/agent/context.py` — Kong-style context windows.
- `fsa_port_agent/agent/cleanup.py` — Phase 3 bottom-up loop.
- `fsa_port_agent/agent/synthesize.py` — global pass.
- `fsa_port_agent/importers/tww_import.py` — Phase 2, wraps `compile_search.py`.
- `fsa_port_agent/hal/scaffold.py` — Phase 4 stub generator.
- `fsa_port_agent/state_db.py` — SQLite schema (flat, no permuter branch).
- `fsa_port_agent/call_graph.py` — DOL asm → DAG.
- `fsa_port_agent/prompts/*.md` — CLEANUP / TYPE_INFER / SYNTHESIZE / FIX_BUILD.

### Phase 4 — Hardware Abstraction Layer (4–8 weeks, overlaps Phase 3)

Goal: replace every GC-hardware call with an emscripten-friendly stub.

**The HAL is pre-enumerated** because FSA ≈ TWW. Every call we need is
already documented. Scope is finite:

| GC System | Replacement | Approach | Effort |
|---|---|---|---|
| **GX** (graphics) | WebGL2 shim | Option A: port Dolphin's software GX (drop-in). Option B: hand-map draw calls to GL. **Go A** — 10× faster to "first frame." | Weeks |
| **AX/DSP/JAudio** | Web Audio via miniaudio | JAudio (not MusyX — FSA uses JAudio from TWW). Miniaudio → emscripten = supported. | Weeks |
| **PAD** (input) | Gamepad API + keyboard | SDL2 under emscripten handles both. | Days |
| **DVD** (filesystem) | Emscripten FS + fetch | Virtual FS preloaded with game assets. Async reads = Asyncify. | Days |
| **VI** | Canvas present | GX EFB copy → WebGL framebuffer → `<canvas>` | Days |
| **OSThread** | pthreads + SharedArrayBuffer | Emscripten pthread support. Cross-origin isolation required. | Days |
| **OSTime** | `performance.now()` | Scale ticks. Preserve RNG timing. | Hours |
| **Memory card** | `localStorage` | Trivial. | Hours |
| **Endian** | Load-time swap | Big→little conversion for binary assets. Macro in include/. | Days |

**Directory layout**:
```
src/platform/
├── gx/          # GX → WebGL (or Dolphin port)
├── audio/       # JAudio → miniaudio
├── input/       # PAD → SDL gamepad
├── fs/          # DVD → emscripten FS + fetch
├── thread/      # OSThread → pthreads
└── wasm_main.c  # emscripten entry, loop pump
```

**Crucial detail from the porting strategies doc**: don't `#ifdef PLATFORM_PC`
throughout the game code. Put the ifdefs in `include/dolphin/*.h`. Game code
stays untouched; headers route to stubs.

### Phase 5 — Emscripten Build Loop (iterative, ongoing)

```
emcc src/**/*.c -o build/wasm/fsa.js \
  -s USE_SDL=2 -s USE_WEBGL2=1 -s ASYNCIFY \
  -s ALLOW_MEMORY_GROWTH=1 -s INITIAL_MEMORY=128MB \
  -s USE_PTHREADS=1 -s PTHREAD_POOL_SIZE=4 \
  -O2 -g -sASSERTIONS \
  --preload-file orig/files@/assets
```

Errors come in waves:
- **Wave 1 (hundreds)**: missing types, duplicate forward decls, unresolved
  `M2C_ERROR`. Already partially fixed by `fix_nonmatch.py`; residuals go
  through `FIX_BUILD` prompts in batches.
- **Wave 2 (dozens)**: signature mismatches between seg files. Fixed by
  the `SYNTHESIZE` global pass re-running with build errors as input.
- **Wave 3 (one-off)**: actual semantic bugs. Human-in-the-loop.

**Milestone progression** (verifiable):
- M1: `tools/tww_import.py` completes. Splits file has 30%+ matching entries.
- M2: Every `seg_*.c` compiles under gcc with `-D_NO_MWCC_`. (Emscripten gate.)
- M3: `emcc` links successfully with HAL stubs returning fake data.
- M4: First frame renders (anything — even a cleared buffer).
- M5: Title screen runs.
- M6: In-game movement, co-op over PeerJS.

---

## 4. Networking (Phase 6, not blocking port)

Defer. FSA has **local co-op built in** — the game-state-per-player struct is
already defined by the engine. Once the single-player port is running:

- Transport: **PeerJS** (WebRTC mesh, no server).
- Sync: **rollback** on input (GGPO-style). FSA is deterministic at 60 fps
  given same inputs + same RNG seed.
- Alternative: **snapshot sync** if determinism breaks under emscripten float
  quirks. Sync the player-state struct + RNG state each frame.

This phase is *after* M6. Do not let networking concerns shape Phases 1–5.

---

## 5. Concrete First-Two-Weeks Work List

Work in `../fsa-port-agent/` (scaffolded; see that repo's README). Do not
modify `../decomp-research-ai/decomp_agent/` or `fsa-decomp/tools/`.

1. Flesh out `fsa_port_agent/state_db.py` — schema is in place; add read
   helpers used by the other modules.
2. Flesh out `fsa_port_agent/agent/triage.py` — file exists and parses asm;
   extend to emit topological order + refine `classify()`.
3. Flesh out `fsa_port_agent/importers/tww_import.py` — parse
   `compile_search.py` stdout, write `splits.txt` + `configure.py` diffs,
   mark state DB.
4. Clone `zeldaret/tww` to `~/Desktop/tww/` and run
   `python -m fsa_port_agent --phase import --limit 80`. **This is Gate 4.**
   If the Dolphin-SDK subset alone doesn't flip ≥20% of the DOL, fall back
   to v1.
5. Only if Gate 4 passes: flesh out `agent/cleanup.py` + `agent/context.py`
   + the prompt templates. Start with `--limit 20` dry runs.

Gate 4 is the critical experiment. Prior evidence (OS.c 100%, JKRDisposer.cpp
100%, per the CLAUDE.md) says this works but only the N=2 sample. Scale it to
N=80 Dolphin files before writing any LLM code.

---

## 6. What This Plan Explicitly Skips

- **Byte-perfect matching of game code.** Register allocation is irrelevant
  for WASM; the last 1% is months of effort with no port value.
- **decomp-permuter.** No public PPC support. Closed door.
- **GeckoRecomp.** 27 stars, no releases, no proven GC title. Wait 12 months
  and re-evaluate.
- **Dolphin-in-WASM.** Fastest to playable but zero code ownership. Not the
  goal.
- **Ghidra.** Already have m2c + dtk which are domain-specific and faster.
  Revisit only for struct discovery if `TYPE_INFER` prompts fail at scale.
- **Full-link Linux build.** wibo RSP limit makes this flaky. Per-unit
  compile+diff is sufficient; emscripten replaces it entirely in Phase 5.

---

## 7. Timeline Snapshot

| Window | Milestone |
|---|---|
| Weeks 1–2 | Triage + TWW-import go/no-go (M1) |
| Weeks 3–6 | Phase 3 agent running, seg files compilable under gcc (M2) |
| Months 2–3 | HAL phase 4; `emcc` links (M3); first frame (M4) |
| Months 3–4 | Title screen, in-game (M5) |
| Months 4–5 | Co-op via PeerJS (M6) |

Aggressive. Achievable if Gate 4 passes. If it fails, fall back to v1's pure
m2c-everywhere approach and accept a longer HAL phase.

---

## 8. Relationship to v1 (`BROWSER_PORT_PLAN.md`)

v1 is correct at the strategy level (Path C, m2c + TWW + emscripten). v2
supersedes it at the pipeline level: adds Kong's call-graph-ordered agent,
specifies the five-phase orchestrator to build, drops the permuter branch
cleanly, and identifies Gate 4 as the single experiment that determines
feasibility.

Keep v1 for the three-paths comparison (A/B/C) — no need to re-litigate.
Treat v2 as the operating document.
