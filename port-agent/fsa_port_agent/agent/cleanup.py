"""Phase 3 — bottom-up m2c cleanup, driven by Claude Code via work queue.

## Two-step flow

    python -m fsa_port_agent --phase decompile --prepare --limit 20
        → writes work/cleanup/0xXXXXXXXX.prompt.md for each of the next
          20 TRIAGED functions (bottom-up topo order).

    # Claude Code (this session) or Agent-tool subagents:
    #   read each .prompt.md → write sibling .response.c

    python -m fsa_port_agent --phase decompile --apply
        → splices each .response.c back into its seg_*.c, marks state=CLEANED
          in the DB, archives the triplet under work/cleanup/done/.

No Anthropic API calls. Period. Python is the queue manager; the
subscription-backed Claude Code session is the LLM runtime.

## Skipped states

Functions already in {MATCHED_TWW, SIG_MATCHED, CLEANED, BUILDS} don't need
cleanup. Only TRIAGED + FAILED (for retries) are enqueued.
"""

from __future__ import annotations

from pathlib import Path

from .. import call_graph
from ..config import Config
from ..state_db import StateDB
from ..work_queue import WorkQueue, render_prompt
from .context import ContextBuilder


_SKIP_STATES = {"MATCHED_TWW", "SIG_MATCHED", "CLEANED", "BUILDS"}


def _work_order(db: StateDB) -> list[int]:
    """Reverse-topological (leaves first), restricted to addrs in the DB.

    The edge map contains callee addrs that aren't in our file set (imports,
    external syscalls). We keep them in the topo sort for ordering purposes
    but strip them from the work list — we can't clean what we don't have.
    """
    known: set[int] = set(db.all_addrs())
    edges = db.load_edge_map()
    ordered = [a for a in call_graph.topo_bottom_up(edges) if a in known]
    seen = set(ordered)
    for a in known:
        if a not in seen:
            ordered.append(a)
    return ordered


def _needs_cleanup(db: StateDB, addr: int) -> bool:
    row = db.get_fn_by_addr(addr)
    if row is None:
        return False
    return row.state not in _SKIP_STATES


# -----------------------------------------------------------------------------
# Prepare: render prompts into work/cleanup/
# -----------------------------------------------------------------------------

def _tier_hint(fn_size: int) -> str:
    """Heuristic: small fns go cheap tier; large/complex ones expensive."""
    if fn_size > 2048:
        return "expensive"
    return "cheap"


def prepare(cfg: Config, args) -> int:
    db = StateDB(cfg.state_db_path)
    try:
        builder = ContextBuilder(cfg, db)
        queue = WorkQueue(cfg.work_root, "cleanup")
        tmpl = Path(__file__).resolve().parent.parent / "prompts" / "cleanup.md"

        limit = args.limit or 0
        enqueued = 0
        skipped_no_body = 0
        skipped_done = 0
        already_queued = 0

        for addr in _work_order(db):
            if limit and enqueued >= limit:
                break
            row = db.get_fn_by_addr(addr)
            if row is None or row.state in _SKIP_STATES:
                skipped_done += 1
                continue
            if addr not in builder.index.fns:
                skipped_no_body += 1
                continue

            tid = f"0x{addr:08X}"
            prompt_path = queue.dir / f"{tid}.prompt.md"
            if prompt_path.exists():
                already_queued += 1
                if limit and already_queued + enqueued >= limit:
                    break
                continue

            ctx = builder.build(addr)
            row = db.get_fn_by_addr(addr)
            tier = _tier_hint(row.size if row else 0)

            prompt = render_prompt(tmpl, ctx.as_prompt_vars())
            meta = {
                "kind": "cleanup",
                "addr": addr,
                "addr_hex": tid,
                "tier": tier,
                "model_hint": cfg.expensive_model if tier == "expensive" else cfg.cheap_model,
                "seg_file": str(builder.index.fns[addr].seg.relative_to(cfg.fsa_root)),
                "response_ext": "c",
            }
            queue.enqueue(tid, prompt, meta)
            enqueued += 1

        print(f"[prepare] enqueued {enqueued} cleanup tasks → {queue.dir}")
        print(f"[prepare] skipped: {skipped_done} already-done, "
              f"{skipped_no_body} no-body, {already_queued} already-queued")
        if enqueued:
            print(f"[prepare] next: have Claude Code write .response.c files in {queue.dir}")
            print(f"[prepare] then: python -m fsa_port_agent --phase decompile --apply")
        return 0
    finally:
        db.close()


# -----------------------------------------------------------------------------
# Apply: splice responses back, update DB, archive
# -----------------------------------------------------------------------------

def _strip_markdown_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return text
    lines = t.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def apply(cfg: Config, args) -> int:
    db = StateDB(cfg.state_db_path)
    try:
        builder = ContextBuilder(cfg, db)
        queue = WorkQueue(cfg.work_root, "cleanup")

        applied = 0
        failed = 0

        for task in queue.responses():
            addr = int(task.meta["addr"])
            raw = queue.response_text(task)
            new_body = _strip_markdown_fence(raw).strip()

            if not new_body or f"fn_{addr:08X}".lower() not in new_body.lower():
                db.upsert_function(
                    addr=addr, state="FAILED",
                    last_error="response missing expected fn_<addr> definition",
                )
                failed += 1
                print(f"[apply] {task.task_id}  FAIL (malformed response)")
                continue

            if args.dry_run:
                print(f"[apply] {task.task_id}  DRY (would splice {len(new_body)} chars)")
                continue

            if builder.index.replace_body(addr, new_body):
                db.upsert_function(addr=addr, state="CLEANED", confidence=0.6)
                queue.mark_done(task)
                applied += 1
                print(f"[apply] {task.task_id}  OK")
            else:
                db.upsert_function(
                    addr=addr, state="FAILED",
                    last_error="splice failed (fn not in seg index)",
                )
                failed += 1
                print(f"[apply] {task.task_id}  FAIL (splice)")

        print(f"[apply] applied={applied} failed={failed}")
        return 0 if applied > 0 or args.dry_run else 1
    finally:
        db.close()


# -----------------------------------------------------------------------------
# Entry point (dispatched from supervisor)
# -----------------------------------------------------------------------------

def run(cfg: Config, args) -> int:
    if getattr(args, "prepare", False):
        return prepare(cfg, args)
    if getattr(args, "apply", False):
        return apply(cfg, args)
    # Default: print queue status so the operator knows what to do next.
    queue = WorkQueue(cfg.work_root, "cleanup")
    pending = queue.pending()
    have_resp = sum(1 for _ in queue.responses())
    print(f"[decompile] queue: {len(pending)} pending prompts, {have_resp} with responses")
    print(f"[decompile] run with --prepare to enqueue, --apply to splice back")
    return 0
