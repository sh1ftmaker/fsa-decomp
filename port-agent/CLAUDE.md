# FSA Port Agent — Session Primer

You are Claude Code, working on an automated pipeline that ports
**GameCube *Four Swords Adventures* (FSA)** from a dtk decompilation
(`~/Desktop/fsa-decomp/`) to **WASM running in the browser**.

This file is the canonical hand-off. Read it once at session start, then
pick up wherever the queue + state DB say to pick up.

---

## Hard constraint: no Anthropic API key

The operator runs on a **Claude subscription**, not the API. So this repo
does not call `anthropic.Anthropic(...)` anywhere. Never add that.

Instead, every AI-facing step is split:

1. **Python prepares** a prompt file (`work/<kind>/<id>.prompt.md`) + meta.
2. **You (Claude Code) generate** the response by reading the prompt and
   writing `work/<kind>/<id>.response.<ext>` — either inline in this session
   or by spawning Agent-tool subagents in parallel.
3. **Python applies** by splicing the response back into the source tree,
   updating the state DB, and moving the triplet into `work/<kind>/done/`.

Treat the filesystem as the glue. Subagents are fine for parallel batches;
just make sure each one writes its response file before returning.

Model tier hints live in each task's `meta.json` (`"tier": "cheap"|"expensive"`
and `"model_hint": "claude-haiku-4-5" | "claude-sonnet-4-6" | "claude-opus-4-7"`).
Use those to decide whether to handle inline vs. delegate to a beefier subagent.

---

## Repo layout (three sibling dirs under `~/Desktop/`)

| Dir | Role |
| --- | --- |
| `fsa-decomp/` | The dtk-based FSA decomp. Holds `src/`, `build/G4SE01/asm/`, `config/G4SE01/splits.txt`, `tools/*.py`. **We don't modify the pipeline tools — we subprocess them.** |
| `tww/` | Cloned `zeldaret/tww` — source-of-truth donor for the TWW mass-import (Phase 2). Shares compiler/SDK/JSystem with FSA. |
| `fsa-port-agent/` | **This repo.** Orchestration layer. Python modules + prompt templates + state DB + work queue. |

`fsa-decomp/src/nonmatch/seg_*.c` already contains m2c output for
the whole DOL — our pipeline starts from there, not from scratch.

---

## Five phases

| # | Phase | Status | Driven by |
| - | ----- | ------ | --------- |
| 1 | **Triage** — populate state DB from asm, build call graph, tag LEAF/CONSTRUCTOR/VTABLE_THUNK/INTERNAL, extract data refs | scripted, done | `--phase triage` |
| 2 | **TWW mass-import** — compile TWW sources with FSA cflags, byte-match against DOL, auto-assign | scripted, done | `--phase import` (wraps `fsa-decomp/tools/compile_search.py`) |
| 3 | **Bottom-up cleanup** — m2c→compilable C for each fn, in reverse topo order | **AI loop** (prepare/apply) | `--phase decompile --prepare`, Claude writes responses, `--apply` |
| 4 | **HAL scaffold** — GX/AX/PAD/DVD/OSThread stubs for Emscripten | scripted, done | `--phase hal` |
| 5 | **Build loop** — `cc -fsyntax-only` check, FIX_BUILD prompt per error, unified-diff apply | **AI loop** (check/prepare/apply) | `--phase build --check`, `--prepare`, `--apply` |
| + | **Dashboard** — local HTTP GUI at `http://127.0.0.1:8765` with charts, DOL address strip, action buttons, live job log | scripted, done | `--phase dashboard` |

### Gate 4 — the go/no-go

After Phase 2 we need **≥20% of DOL functions (≥~1,197 / 5,981)** matched via
TWW import. Below that, the plan doesn't pencil — we'd fall back to pure
dirty-port heuristics. Run `--phase import --dry-run` to read out the number.

> **Denominator note**: 5,981 = count of `auto_*_text.s` files in
> `fsa-decomp/build/G4SE01/asm/` (dtk's `fill_gaps: true` expansion,
> one file per DOL function). The 5,516 figure in some docs is the
> narrower "discovered symbols" count from `dtk dol info` — don't use it.

---

## State DB

`fsa-port-agent/state.db` (SQLite). One row per DOL function. Linear progression:

```
UNKNOWN → TRIAGED → { MATCHED_TWW | SIG_MATCHED | CLEANED | BUILDS | FAILED }
```

Read helpers: `get_fn_by_addr`, `get_by_state`, `get_callees`, `get_callers`,
`get_string_refs`, `load_edge_map`, `all_addrs`.

---

## Common commands

All run from `fsa-port-agent/`:

```bash
python -m fsa_port_agent --phase triage --limit 200         # populate DB
python -m fsa_port_agent --phase import --dry-run           # Gate 4 readout
python -m fsa_port_agent --phase import                     # real run, writes splits.txt

# Phase 3 — AI loop
python -m fsa_port_agent --phase decompile --prepare --limit 10
# … Claude Code processes work/cleanup/*.prompt.md → *.response.c …
python -m fsa_port_agent --phase decompile --apply

# HAL stubs
python -m fsa_port_agent --phase hal

# Status
python -m fsa_port_agent --phase decompile                  # no flag = queue status

# Phase 5 — build check + AI loop
python -m fsa_port_agent --phase build --check --limit 50   # syntax-check seg files
python -m fsa_port_agent --phase build --prepare --limit 10 # enqueue fix prompts
python -m fsa_port_agent --phase build --apply              # apply unified diffs

# Local dashboard GUI (stdlib-only HTTP server on 127.0.0.1:8765)
python -m fsa_port_agent --phase dashboard                  # Ctrl-C to stop
```

Env overrides: `FSA_ROOT`, `TWW_ROOT` (default to `~/Desktop/fsa-decomp`,
`~/Desktop/tww`).

---

## The cleanup loop in practice

When `--prepare` has filled `work/cleanup/`:

1. List `work/cleanup/*.prompt.md` (each is one function).
2. For each (or a parallel batch via Agent tool):
   - Read the prompt file.
   - The prompt contains rendered m2c output + callee/caller sigs + strings +
     nearby-matched bodies + rules. Produce a single clean C function body.
   - Write the result to `work/cleanup/<id>.response.c`. No markdown fence;
     if you include one, `--apply` strips it. The response **must** contain
     `fn_<ADDR>` in its definition or apply will reject it.
3. Run `--apply`. Python splices each response into its `seg_*.c` file and
   flips DB state to `CLEANED`.

Use cheap-tier (inline or short Haiku subagents) for `"tier": "cheap"` tasks
and save Sonnet subagents for `"tier": "expensive"` ones. Batches of ~10
parallel subagents are a reasonable unit.

---

## Swarm orchestration (Phase 3)

The `--prepare` step now emits a single **batch manifest** alongside the
per-task triplets:

    work/cleanup/batch_<iso>.manifest.json

That file is the fan-out spec. When picking up work:

1. Read the newest `work/cleanup/batch_*.manifest.json`.
2. Group `tasks` by `tier`:
   - `cheap`    → Haiku subagents (`claude-haiku-4-5`)
   - `expensive` → Sonnet subagents (`claude-sonnet-4-6`)
   - `opus`     → Opus subagents (`claude-opus-4-7`, retry-only tier)
3. Spawn ~10 parallel Agent-tool subagents per tier. Each subagent's prompt
   should be, literally:

   > Read `{prompt_path}`. Follow the CLEANUP prompt rules. Write your C
   > function body to `{expected_response_path}`. Do not write any other
   > file. Do not write any prose. Stop after one file write.

4. After all spawned agents return, run once:

       python -m fsa_port_agent --phase decompile --apply

   The Python side runs a lex precheck + `cc -fsyntax-only` compile gate
   per fn. Passing fns become CLEANED and get archived into
   `work/cleanup/done/`. Failing fns get `state=FAILED`, `attempts++`,
   and `last_error` recorded.
5. Re-enqueue FAILED rows at the escalated tier:

       python -m fsa_port_agent --phase decompile --prepare --limit 50

   `_tier_for()` bumps cheap→expensive on attempt 2, and all tiers→opus
   on attempt 3. Past `cfg.max_attempts_per_func` (default 3) the row
   becomes `PERMANENT_FAIL` and is dropped from the queue.
6. Loop until the manifest's `tasks` array is empty or all remaining rows
   are PERMANENT_FAIL / CLEANED.

The **falsifiable acceptance metric** is: across three consecutive batches
of 50, `CLEANED functions with a syntax error inside their fn_line_range
== 0`. Phase 5's `--phase build --check` is the check — if it emits a
within-range error for a CLEANED row, the gate has a hole.

---

## Pre-cleanup pipeline hardening (2026-04-19)

Three mechanics now guard the cleanup loop. Each one trades a tiny up-front
cost for saved LLM retries or better visibility.

**1. Arity short-circuit in `prepare()`** (`agent/cleanup.py:_arity_mismatch_reason`).
Before enqueueing a fn, compare its body def arity to the first extern in
its own seg file. If they disagree (and neither is K&R `()`), the row is
set to `PERMANENT_FAIL` with a descriptive `last_error` — no LLM attempt
is spent. Rationale: cc locks the first extern, the body def must match
it, and if the body needs more/fewer positional slots than the extern
advertises, no retry can converge. `0x803832BC` is the paradigm case
(5-param body vs 4-arg seg-local extern + in-seg 4-arg callsites —
neither side is rewriteable). As of this change, 1 fn is caught in the
first 20-fn prepare; `0x802F58A8` is another real case (3 vs 2).
K&R `()` is treated as "unknown arity — skip check" (compatible with
anything per C99).

**2. Tier-aware strict cc gate** (`agent/build.py:_check_one(strict=)`,
`agent/cleanup.py:apply`). The compile gate now runs cc with extra
`-Werror=` flags (`incompatible-pointer-types`, `int-conversion`,
`implicit-function-declaration`, `implicit-int`, `return-type`) when
`tier in (expensive, opus)` OR `attempt >= 2`. This is the advisor-scoped
substitute for an mwcc gate: catches ~70% of what mwcc would catch (the
semantic-safety class) with zero new parser code. On gcc most of these
are default-errors already; the flags are a safety net for clang users
where `-Wno-everything` would otherwise silence them. Failed rows are
tagged `gate[cc-strict]` vs `gate[cc]` in `cleanup_attempts.last_error`
so triage can tell which gate rejected them.

**3. Synthesize scan pass** (`agent/synthesize.py:scan`, `--phase synthesize --scan`).
Read-only: walks every CLEANED fn body, regex-scans for `p->unk_0xNN`
AND m2c's dominant raw-offset cast form `*((char *)p + 0xNN)`, buckets
by declared arg type (from the body signature). Output:
`work/synthesize/scan_<iso>.json` with per-bucket `total_refs`,
`distinct_offsets`, `fn_count`, `example_addrs`, plus named/raw form
breakdown. Zero source mutations. Feeds the later full synthesis pass
(struct typedef emission). First run showed `arg0:void *` with 136
distinct offsets across 50 fns — the "void \* sea" waiting for named
struct layouts.

### The per-fn `attempts` / `last_error` columns

`state_db.FunctionRow.attempts` and `.last_error` are surfaced to Python
via `_row_to_fn`. `prepare()` reads `row.attempts` to decide tier; retry
ladder: cheap→expensive on attempt 2, all→opus on attempt 3, past
`cfg.max_attempts_per_func` (default 4) → `PERMANENT_FAIL`.

### signature propagation

After a fn cleans successfully, `SegIndex.propagate_signature(addr)`
rewrites every OTHER seg's first-seen extern for that addr to match the
body's now-trusted signature. Without this, downstream callers compile
against m2c's original callsite guess and the gate rejects an otherwise-
correct body with "conflicting types". Runs automatically in `apply()`
after CLEANED is committed, logs `propagated sig → N seg(s)`.

---

## Phase 3 progress (2026-04-19)

Decompile state (of 5,977 DOL functions):

| State | Count | Source |
| --- | --- | --- |
| `MATCHED_TWW` | 340 | Phase 2 byte-import (7.2% — capped) |
| `CLEANED` | 406 | Phase 3 AI cleanup — 314 at attempt 1 (Haiku), 65 at 2 (Sonnet), 23 at 3 (Opus), 4 at 4 |
| `FAILED` | 67 | retryable, waiting for next escalation round |
| `PERMANENT_FAIL` | 33 | exhausted attempts; not re-queueable |
| `TRIAGED` | 5,471 | remaining Phase 3 queue |

**Escalation ladder proven (2026-04-19 session).** Dispatched a haiku-first
sweep (~360 attempt-1 Haiku calls via `claude -p` CLI fan-out) then ran the
ladder through Sonnet attempt-2 and Opus attempt-3. Observed rescue rates:

- Sonnet attempt-2 rescues ~60% of Haiku-1 failures
- Opus attempt-3 rescues ~5/6 of Sonnet-2 failures (small N)

First-pass Haiku success is ~76%; after the full ladder ~86% of attempted
functions end up CLEANED. Residual failures cluster in int↔ptr conversion
(~27%), unusual operators (ptr-minus-float, unary-deref-u32), and
implicit-declaration leaks of PPC intrinsics (`__frsqrte`, etc.).

**Prompt patch (2026-04-19).** `prompts/cleanup.md` now carries a loud
anti-m2c-arity block **above** the `{m2c_source}` field rather than in a
trailing checklist: m2c drops callee args it cannot prove, so the
"Already-resolved callee signatures" block is explicitly authoritative
over m2c's callsite shape. This was the specific fix for the ~34% arity
bucket that dominated pre-patch failures. After the patch, Sonnet
attempt-2 failures shifted to a semantic mix — arity dropped to ~17%
within the pilot's own FAILs.

### Subagent dispatch note

Agent-tool subagents (`Agent(subagent_type=...)`) **cannot** recursively
spawn more Agent calls — the tool is not in their toolset. They can,
however, shell out to the `claude` CLI directly:

```
~/.local/bin/claude -p \
  --model claude-haiku-4-5 \
  --no-session-persistence \
  --allowedTools "Read,Write" \
  -- "Read {prompt_path}. Follow the CLEANUP prompt rules. Write your single cleaned C function body to {response_path}. Do not write any other file. Do not emit any prose. Do not wrap the response in markdown fences. Stop after one file write."
```

This is how 2026-04-19's shard agents fanned out 55 TIDs each in
parallel. OAuth credentials from `~/.claude/.credentials.json` are
picked up automatically — no API key. Dispatch throttle at 15
concurrent; collect logs at `/tmp/<tag>_<TID>.log` for postmortem.

### state.db is tracked

`port-agent/state.db` is **committed to git** (as of 2026-04-19) so
Phase-3 progress persists across sessions and contributors. Rebuilding
from scratch would re-queue already-CLEANED rows and waste LLM calls.
Only `state.db-journal/-wal/-shm` (ephemeral SQLite bookkeeping) are
gitignored.

---

## Prompt templates

In `fsa_port_agent/prompts/`:

- `cleanup.md` — per-function m2c → compilable C
- `type_infer.md` — struct typedef synthesis from `p->unk_0xNN` usage
- `synthesize.md` — global-pass rename + type unification (run at end of Phase 3)
- `fix_build.md` — Phase 5 reactive patch from a compile error

Rendering uses `{name}` placeholders, left intact on miss (C bodies contain
literal braces — don't use `str.format`).

---

## Pointers

- **Strategic plan:** `~/Desktop/fsa-decomp/BROWSER_PORT_PLAN_V2.md` (five-phase design,
  Kong-adapted architecture, budget numbers)
- **README:** `fsa-port-agent/README.md` (rationale for the separate folder)
- **Gate 4 tool:** `fsa-decomp/tools/compile_search.py` (masked byte matcher)
- **m2c output:** `fsa-decomp/src/nonmatch/seg_*.c` — each seg file groups
  functions by a `/* --- auto_XX_ADDR_text.s --- */` banner

---

## Do NOT

- Add `anthropic` as a dependency or import it.
- Call the Anthropic API directly from Python anywhere in this repo.
- Hand-edit `work/cleanup/*.prompt.md` — regenerate via `--prepare`.
- Modify `fsa-decomp/tools/` unless explicitly asked; we wrap, not fork.
- Rewrite m2c output byte-for-byte matching. Browser port is semantic, not
  byte-matching — there is no PPC permuter branch in this pipeline.
