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
