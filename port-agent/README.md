# fsa-port-agent

Automated pipeline for porting *The Legend of Zelda: Four Swords Adventures*
(GameCube) to a WebAssembly browser build. Operates on the `fsa-decomp/` repo
as a sibling directory; does not modify the old `decomp-research-ai/decomp_agent/`.

See `../fsa-decomp/BROWSER_PORT_PLAN_V2.md` for the strategic overview.

## Why a separate folder?

- `../decomp-research-ai/decomp_agent/` — Melee-focused, byte-perfect
  matching, uses decomp-permuter. Different goal, hardcoded paths.
- `../fsa-decomp/tools/` — one-off scripts (`m2c_batch.py`,
  `compile_search.py`, `fix_nonmatch.py`) already wired into the build.
  Kept as-is; this agent *calls* them.
- This folder — new pipeline that orchestrates the above five scripts into a
  bottom-up call-graph-ordered agent targeting emscripten output.

## Layout

```
fsa-port-agent/
├── README.md
├── pyproject.toml              # uv/pip install -e .
├── .gitignore
└── fsa_port_agent/
    ├── __init__.py
    ├── __main__.py             # python -m fsa_port_agent --phase triage
    ├── config.py               # paths, API keys, model tiers
    ├── state_db.py             # SQLite per-function state
    ├── call_graph.py           # DOL → DAG via dtk relocations
    ├── supervisor.py           # five-phase orchestrator
    ├── agent/
    │   ├── triage.py           # Phase 1
    │   ├── context.py          # build LLM context windows
    │   ├── cleanup.py          # Phase 3 m2c-cleanup loop
    │   └── synthesize.py       # Phase 3 global pass
    ├── importers/
    │   ├── tww_import.py       # Phase 2 — drives compile_search.py
    │   └── sig_match.py        # MSL/stdlib signature matching
    ├── hal/
    │   └── scaffold.py         # Phase 4 — generate stub platform files
    └── prompts/
        ├── cleanup.md          # m2c → emcc-compilable C
        ├── type_infer.md       # struct layout inference
        ├── synthesize.md       # global unification pass
        └── fix_build.md        # reactive emcc error patches
```

## CLI

```
python -m fsa_port_agent --phase triage          # build state DB + call graph
python -m fsa_port_agent --phase import          # run tww_import over TWW clone
python -m fsa_port_agent --phase decompile       # Phase 3 agent loop
python -m fsa_port_agent --phase hal             # scaffold platform/ shims
python -m fsa_port_agent --phase build           # emcc loop, fix-waves
python -m fsa_port_agent --phase all             # run end-to-end
```

Global flags: `--limit N`, `--dry-run`, `--workers N`, `--model cheap|expensive`.

## Prerequisites

- `../fsa-decomp/` builds cleanly (`python configure.py && ninja`).
- A local clone of `github.com/zeldaret/tww` for Phase 2 (path set in `config.py`).
- `ANTHROPIC_API_KEY` in env for Phases 3 and 5.
- `emscripten` in `$PATH` for Phase 5.

## Gate check (before writing Phase 3)

Phase 2 is the single go/no-go experiment. Run it against TWW's Dolphin SDK
tree (`tww/libs/dolphin/` or equivalent) and confirm **≥20% of the FSA DOL**
flips to `Matching`. If yes, continue. If no, the whole strategy is wrong and
we fall back to v1 (pure m2c everywhere with larger HAL scope).
