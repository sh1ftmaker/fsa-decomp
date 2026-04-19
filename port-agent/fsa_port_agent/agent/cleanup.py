"""Phase 3 — bottom-up m2c cleanup, driven by Claude Code via work queue.

## Two-step flow

    python -m fsa_port_agent --phase decompile --prepare --limit 20
        → writes work/cleanup/0xXXXXXXXX.prompt.md for each of the next
          20 TRIAGED (or FAILED-and-retryable) functions (bottom-up topo order),
          plus work/cleanup/batch_<iso>.manifest.json as a single fan-out spec.

    # Claude Code (this session) or Agent-tool subagents:
    #   read each .prompt.md → write sibling .response.c

    python -m fsa_port_agent --phase decompile --apply
        → lex-precheck + splice + cc-syntax gate per fn. Pass → state=CLEANED,
          archive. Fail → state=FAILED + attempts++, or PERMANENT_FAIL at cap.

No Anthropic API calls. Python is the queue manager; the subscription-backed
Claude Code session is the LLM runtime.

## Skipped states

Functions in {MATCHED_TWW, SIG_MATCHED, CLEANED, BUILDS, PERMANENT_FAIL} don't
get new prompts. TRIAGED and FAILED are both enqueued — FAILED rows advance
attempt/tier for the retry ladder.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import call_graph
from ..config import Config
from ..state_db import StateDB, FunctionRow
from ..work_queue import WorkQueue, render_prompt
from .build import _check_one, BuildError
from .context import Context, ContextBuilder


_SKIP_STATES = {"MATCHED_TWW", "SIG_MATCHED", "CLEANED", "BUILDS", "PERMANENT_FAIL"}


def _arity_of(sig: str) -> Optional[int]:
    """Count positional params in `RET fn_ADDR(a, b, c)`. Returns None on
    parse failure OR K&R-style `()` (unspecified args — compatible with any
    arity per C99); 0 only for explicit `(void)`.
    """
    lp = sig.find("(")
    rp = sig.rfind(")")
    if lp < 0 or rp <= lp:
        return None
    inner = sig[lp + 1:rp].strip()
    if not inner:
        return None  # K&R unspecified — compatible with anything, skip check
    if inner == "void":
        return 0
    depth = 0
    n = 1
    for c in inner:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            n += 1
    return n


def _arity_mismatch_reason(index, fn_addr: int) -> Optional[str]:
    """Return a reason string if the fn's body def and its own seg's first
    extern disagree on arity — an unresolvable mismatch that will thrash the
    retry ladder. None = no mismatch (or insufficient info). The gate rejects
    any body that diverges from `first_extern`; if the body can't *fit* into
    that arity, no amount of cleanup retries will converge.
    """
    loc = index.fns.get(fn_addr)
    if loc is None:
        return None
    body_arity = _arity_of(loc.signature)
    ext_sig = index.first_extern(loc.seg, fn_addr)
    if ext_sig is None or body_arity is None:
        return None
    ext_arity = _arity_of(ext_sig)
    if ext_arity is None or ext_arity == body_arity:
        return None
    return (
        f"arity mismatch: body def has {body_arity} params "
        f"({loc.signature!r}) but own seg's first extern has {ext_arity} "
        f"({ext_sig!r}). Neither can be rewritten without breaking "
        f"in-seg callsites or dropping body slots. Needs manual "
        f"inspection of the fn's true ABI."
    )


def _work_order(db: StateDB) -> list[int]:
    """Reverse-topological (leaves first), restricted to addrs in the DB."""
    known: set[int] = set(db.all_addrs())
    edges = db.load_edge_map()
    ordered = [a for a in call_graph.topo_bottom_up(edges) if a in known]
    seen = set(ordered)
    for a in known:
        if a not in seen:
            ordered.append(a)
    return ordered


# -----------------------------------------------------------------------------
# Tier selector
# -----------------------------------------------------------------------------

def _tier_for(row: FunctionRow, ctx: Context, attempt: int) -> str:
    """Haiku-first ladder.

    Empirically, Haiku handles most fns including many "complex"-looking
    ones (large / M2C_ERROR-heavy / CONSTRUCTOR-tagged). Sending those to
    Sonnet upfront burns tokens on wins Haiku would have produced for 1/5
    the cost, so attempt 1 is always cheap and escalation only kicks in
    after a real failure.

    Attempt 1: cheap (Haiku) — always.
    Attempt 2: expensive (Sonnet).
    Attempt 3+: opus (last-resort retry).
    """
    del row, ctx  # retained for signature compat / future use
    if attempt >= 3:
        return "opus"
    if attempt == 2:
        return "expensive"
    return "cheap"


def _model_for_tier(cfg: Config, tier: str) -> str:
    if tier == "opus":
        return cfg.synthesis_model
    if tier == "expensive":
        return cfg.expensive_model
    return cfg.cheap_model


# -----------------------------------------------------------------------------
# Prepare: render prompts, emit batch manifest
# -----------------------------------------------------------------------------

def _priority(topo_rank: int) -> int:
    """Leaves first = highest priority. topo_rank is 0-based from leaves."""
    return -topo_rank


def _load_prior_attempt(queue: WorkQueue, addr: int) -> tuple[str, int]:
    """Return (body, attempt_num) of the most recent archived failed attempt
    for this addr, or ("", 0) if none exists. Archives are stamped
    `<tid>.attemptN.response.c` by `_record_fail`.
    """
    done = queue.done_dir
    if not done.exists():
        return "", 0
    tid = f"0x{addr:08X}"
    best = (0, None)
    for p in done.glob(f"{tid}.attempt*.response.c"):
        # Extract N from "0xADDR.attemptN.response.c"
        try:
            n = int(p.name.split(".attempt", 1)[1].split(".", 1)[0])
        except (IndexError, ValueError):
            continue
        if n > best[0]:
            best = (n, p)
    if best[1] is None:
        return "", 0
    return best[1].read_text(errors="ignore"), best[0]


def prepare(cfg: Config, args) -> int:
    db = StateDB(cfg.state_db_path)
    try:
        builder = ContextBuilder(cfg, db)
        queue = WorkQueue(cfg.work_root, "cleanup")
        tmpl = Path(__file__).resolve().parent.parent / "prompts" / "cleanup.md"

        batch_id = "cleanup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        limit = args.limit or 0
        enqueued = 0
        skipped_no_body = 0
        skipped_done = 0
        already_queued = 0

        order = _work_order(db)
        rank_by_addr = {a: i for i, a in enumerate(order)}

        manifest_tasks: list[dict] = []
        tier_hist: dict[str, int] = {"cheap": 0, "expensive": 0, "opus": 0}

        arity_blocked = 0
        for addr in order:
            if limit and enqueued >= limit:
                break
            row = db.get_fn_by_addr(addr)
            if row is None or row.state in _SKIP_STATES:
                skipped_done += 1
                continue
            if addr not in builder.index.fns:
                skipped_no_body += 1
                continue

            # Unresolvable arity mismatches waste LLM attempts — no possible
            # body can satisfy both the first extern and the body's own
            # semantic param use. Short-circuit to PERMANENT_FAIL so the
            # operator sees exactly why.
            arity_err = _arity_mismatch_reason(builder.index, addr)
            if arity_err is not None:
                db.upsert_function(
                    addr=addr, state="PERMANENT_FAIL",
                    attempts=(row.attempts or 0) + 1, last_error=arity_err,
                )
                arity_blocked += 1
                continue

            tid = f"0x{addr:08X}"
            prompt_path = queue.dir / f"{tid}.prompt.md"
            if prompt_path.exists():
                already_queued += 1
                continue

            ctx = builder.build(addr)
            attempt = (row.attempts or 0) + 1
            tier = _tier_for(row, ctx, attempt)

            prompt_vars = ctx.as_prompt_vars()
            # On retry, surface the prior failed response + the compiler error
            # so the next attempt can diagnose and avoid the same mistake.
            if attempt > 1:
                prior_body, prior_n = _load_prior_attempt(queue, addr)
                prompt_vars["prior_response"] = prior_body.strip() or "(none archived)"
                prompt_vars["prior_error"] = (row.last_error or "").strip() or "(none)"
                prompt_vars["prior_attempt_num"] = str(prior_n or attempt - 1)
            else:
                prompt_vars["prior_response"] = ""
                prompt_vars["prior_error"] = ""
                prompt_vars["prior_attempt_num"] = ""

            prompt = render_prompt(tmpl, prompt_vars)
            seg_rel = str(builder.index.fns[addr].seg.relative_to(cfg.fsa_root))
            meta = {
                "kind": "cleanup",
                "addr": addr,
                "addr_hex": tid,
                "attempt": attempt,
                "batch_id": batch_id,
                "tier": tier,
                "model_hint": _model_for_tier(cfg, tier),
                "seg_file": seg_rel,
                "response_ext": "c",
                "context": ctx.context_stats,
            }
            queue.enqueue(tid, prompt, meta)
            enqueued += 1
            tier_hist[tier] = tier_hist.get(tier, 0) + 1

            manifest_tasks.append({
                "task_id": tid,
                "addr": addr,
                "prompt_path": str((queue.dir / f"{tid}.prompt.md").relative_to(cfg.agent_root)),
                "expected_response_path": str((queue.dir / f"{tid}.response.c").relative_to(cfg.agent_root)),
                "tier": tier,
                "model_hint": _model_for_tier(cfg, tier),
                "attempt": attempt,
                "priority": _priority(rank_by_addr.get(addr, 0)),
                "seg_file": seg_rel,
                "context": ctx.context_stats,
            })

        manifest_tasks.sort(key=lambda t: (t["priority"], t["addr"]))

        manifest = {
            "batch_id": batch_id,
            "generated_at_unix": int(time.time()),
            "limit_requested": limit or None,
            "tiers": tier_hist,
            "tasks": manifest_tasks,
        }
        manifest_path = queue.dir / f"batch_{batch_id}.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        print(f"[prepare] enqueued {enqueued} cleanup tasks → {queue.dir}")
        print(f"[prepare] tiers: cheap={tier_hist.get('cheap',0)} "
              f"expensive={tier_hist.get('expensive',0)} opus={tier_hist.get('opus',0)}")
        print(f"[prepare] skipped: {skipped_done} already-done, "
              f"{skipped_no_body} no-body, {already_queued} already-queued, "
              f"{arity_blocked} arity-mismatch→PERMANENT_FAIL")
        print(f"[prepare] manifest: {manifest_path.relative_to(cfg.agent_root)}")
        if enqueued:
            print(f"[prepare] next: have Claude Code write .response.c files in {queue.dir}")
            print(f"[prepare] then: python -m fsa_port_agent --phase decompile --apply")
        return 0
    finally:
        db.close()


# -----------------------------------------------------------------------------
# Apply: lex precheck + splice + compile gate + retry ladder
# -----------------------------------------------------------------------------

def _strip_markdown_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return text
    lines = t.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _lex_precheck(body: str, addr: int) -> Optional[str]:
    """Fast checks before touching the seg file. Returns reason string on fail."""
    if not body.strip():
        return "empty body"
    needle = f"fn_{addr:08X}".lower()
    if needle not in body.lower():
        return f"missing fn_{addr:08X} definition"
    depth = 0
    for c in body:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                return "unbalanced braces (closing before opening)"
    if depth != 0:
        return f"unbalanced braces (net {depth:+d})"
    if "```" in body:
        return "residual markdown fence"
    if "asm {" in body or "__asm" in body:
        return "raw asm block present"
    if "M2C_ERROR(" in body:
        return "residual M2C_ERROR marker"
    if "saved_reg_" in body:
        return "residual saved_reg_* local from m2c input"
    return None


def _offsets_to_lines(text: str, start: int, end: int) -> tuple[int, int]:
    """Return (1-indexed line_start, 1-indexed line_end) covering [start:end)."""
    line_start = text.count("\n", 0, start) + 1
    line_end = text.count("\n", 0, max(start, end - 1)) + 1
    return line_start, line_end


def _fn_range_errors(
    cfg: Config, seg_path: Path, fn_line_range: tuple[int, int], cc: str,
    strict: bool = False,
) -> list[BuildError]:
    """Count syntax errors inside the fn's line range. Uses unlimited errors."""
    errs = _check_one(cfg, seg_path, cc, max_errors=0, strict=strict)
    lo, hi = fn_line_range
    # Only count errors whose reported file matches this seg (cc sometimes reports
    # included headers). Match basename to tolerate absolute/relative paths.
    seg_name = seg_path.name
    return [e for e in errs if Path(e.file).name == seg_name and lo <= e.line <= hi]


def apply(cfg: Config, args) -> int:
    db = StateDB(cfg.state_db_path)
    try:
        builder = ContextBuilder(cfg, db)
        queue = WorkQueue(cfg.work_root, "cleanup")

        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if cc is None and not args.dry_run:
            print("[apply] no C compiler on PATH — cannot run compile gate")
            return 2

        applied = 0
        failed_lex = 0
        failed_compile = 0
        permanent = 0
        readability: dict[str, int] = {
            "applied": 0,
            "var_rN_locals": 0,       # m2c register-alloc artifact
            "temp_rN_locals": 0,      # m2c scratch-register artifact
            "saved_reg_locals": 0,    # m2c callee-saved-register artifact
            "raw_offset_casts": 0,    # `*(T *)((char *)p + 0xNN)` = opaque field access
            "unk_field_refs": 0,      # unnamed struct fields (SYNTHESIZE targets)
            "goto_labels": 0,         # m2c fallthrough labels; hard to follow
            "fn_addr_calls": 0,       # unresolved fn_<ADDR> calls = call graph gaps
            "m2c_leaks": 0,           # should be 0 after the lex precheck — sanity check
        }
        readability_re = {
            "var_rN_locals": re.compile(r'\bvar_r\d+\b'),
            "temp_rN_locals": re.compile(r'\btemp_r\d+\b'),
            "saved_reg_locals": re.compile(r'\bsaved_reg_\w+\b'),
            "raw_offset_casts": re.compile(r'\*\s*\(\s*[a-zA-Z_][\w\s\*]*\s*\)\s*\(\s*\(\s*char\s*\*\s*\)'),
            "unk_field_refs": re.compile(r'\bunk_0x[0-9A-Fa-f]+\b'),
            "goto_labels": re.compile(r'\bgoto\s+\w+\s*;'),
            "fn_addr_calls": re.compile(r'\bfn_[0-9A-Fa-f]{8}\s*\('),
            "m2c_leaks": re.compile(r'\bM2C_ERROR\s*\('),
        }

        for task in queue.responses():
            addr = int(task.meta["addr"])
            attempt = int(task.meta.get("attempt", 1))
            tier = task.meta.get("tier", "cheap")
            batch_id = task.meta.get("batch_id")

            raw = queue.response_text(task)
            new_body = _strip_markdown_fence(raw).strip()

            # Elapsed = response.mtime - prompt.mtime (best-effort).
            try:
                elapsed_s = task.response_path.stat().st_mtime - task.prompt_path.stat().st_mtime
            except Exception:
                elapsed_s = None

            # --- lex precheck ---
            lex_err = _lex_precheck(new_body, addr)
            if lex_err:
                _record_fail(
                    cfg, db, queue, task,
                    addr=addr, attempt=attempt, tier=tier,
                    outcome_kind="FAILED_LEX",
                    reason=f"lex: {lex_err}",
                    elapsed_s=elapsed_s, batch_id=batch_id,
                )
                failed_lex += 1
                if attempt >= cfg.max_attempts_per_func:
                    permanent += 1
                print(f"[apply] {task.task_id}  FAIL lex ({lex_err})")
                continue

            loc = builder.index.fns.get(addr)
            if loc is None:
                _record_fail(
                    cfg, db, queue, task,
                    addr=addr, attempt=attempt, tier=tier,
                    outcome_kind="FAILED_LEX",
                    reason="fn missing in seg index",
                    elapsed_s=elapsed_s, batch_id=batch_id,
                )
                failed_lex += 1
                if attempt >= cfg.max_attempts_per_func:
                    permanent += 1
                print(f"[apply] {task.task_id}  FAIL (not in seg index)")
                continue

            if args.dry_run:
                print(f"[apply] {task.task_id}  DRY (would splice {len(new_body)} chars, attempt={attempt}, tier={tier})")
                continue

            seg_path = loc.seg
            original_body = loc.body

            # Stricter gate for expensive/opus tiers and retry attempts — these
            # are the hard fns where we want to catch semantic issues (pointer
            # mismatch, int-conversion, implicit decl) that -fsyntax-only alone
            # lets through. Cheap/first-attempt stays lenient so trivial fns
            # don't get tripped up by m2c's rough edges.
            strict = tier in ("expensive", "opus") or attempt >= 2

            # --- baseline: count errors inside fn's CURRENT line range ---
            seg_text = seg_path.read_text(errors="ignore")
            before_range = _offsets_to_lines(seg_text, loc.start, loc.end)
            before_errs = _fn_range_errors(cfg, seg_path, before_range, cc, strict=strict) if cc else []

            # --- splice ---
            if not builder.index.replace_body(addr, new_body):
                _record_fail(
                    cfg, db, queue, task,
                    addr=addr, attempt=attempt, tier=tier,
                    outcome_kind="FAILED_LEX",
                    reason="splice failed (index mismatch)",
                    elapsed_s=elapsed_s, batch_id=batch_id,
                )
                failed_lex += 1
                if attempt >= cfg.max_attempts_per_func:
                    permanent += 1
                print(f"[apply] {task.task_id}  FAIL (splice)")
                continue

            # --- compile gate on new body's line range ---
            new_loc = builder.index.fns[addr]
            seg_text2 = seg_path.read_text(errors="ignore")
            after_range = _offsets_to_lines(seg_text2, new_loc.start, new_loc.end)
            after_errs = _fn_range_errors(cfg, seg_path, after_range, cc, strict=strict) if cc else []

            # Strict gate: new fn must introduce zero in-range errors. Even if
            # the original body had errors (common — m2c emits broken C), the
            # cleanup response is meant to be compilable.
            if after_errs:
                # rollback
                builder.index.replace_body(addr, original_body)
                first = after_errs[0]
                gate_tag = "cc-strict" if strict else "cc"
                reason = f"gate[{gate_tag}] line {first.line}: {first.msg}"
                _record_fail(
                    cfg, db, queue, task,
                    addr=addr, attempt=attempt, tier=tier,
                    outcome_kind="FAILED_COMPILE",
                    reason=reason,
                    elapsed_s=elapsed_s, batch_id=batch_id,
                )
                failed_compile += 1
                if attempt >= cfg.max_attempts_per_func:
                    permanent += 1
                print(f"[apply] {task.task_id}  FAIL compile "
                      f"(before={len(before_errs)} after={len(after_errs)}) {first.msg}")
                continue

            # --- success ---
            db.upsert_function(
                addr=addr, state="CLEANED", confidence=0.6,
                attempts=attempt, last_error=None,
            )
            db.record_cleanup_attempt(
                addr=addr, attempt=attempt, tier=tier,
                outcome="CLEANED", last_error=None,
                elapsed_s=elapsed_s, batch_id=batch_id, ts=time.time(),
            )
            # Propagate the now-trusted signature to every other seg's first
            # extern for this addr, so future callers compile against the
            # correct types instead of m2c's original guess.
            try:
                prop_n = builder.index.propagate_signature(addr)
                if prop_n:
                    print(f"[apply] {task.task_id}  propagated sig → {prop_n} seg(s)")
            except Exception as exc:
                print(f"[apply] {task.task_id}  propagate_signature failed: {exc}")
            queue.mark_done(task)
            applied += 1
            readability["applied"] += 1
            for k, pat in readability_re.items():
                # `fn_addr_calls` undercounts this very fn — don't count
                # self-calls (rare; tail recursion), subtract 1 for the
                # definition-line occurrence.
                n = len(pat.findall(new_body))
                if k == "fn_addr_calls":
                    n = max(0, n - 1)
                if n:
                    readability[k] += n
            print(f"[apply] {task.task_id}  OK (attempt={attempt}, tier={tier})")

        db.conn.commit()
        print(f"[apply] applied={applied} lex_fail={failed_lex} "
              f"compile_fail={failed_compile} permanent_fail={permanent}")
        if readability["applied"]:
            n = readability["applied"]
            print("[apply] readability (per-batch, across {} CLEANED fns):".format(n))
            for key in (
                "var_rN_locals", "temp_rN_locals", "saved_reg_locals",
                "raw_offset_casts", "unk_field_refs", "goto_labels",
                "fn_addr_calls", "m2c_leaks",
            ):
                total = readability[key]
                avg = total / n
                print(f"[apply]   {key:<20s} total={total:<6d} avg/fn={avg:.2f}")
        return 0 if applied > 0 or args.dry_run else 1
    finally:
        db.close()


def _record_fail(
    cfg: Config,
    db: StateDB,
    queue: WorkQueue,
    task,
    *,
    addr: int,
    attempt: int,
    tier: str,
    outcome_kind: str,
    reason: str,
    elapsed_s: Optional[float],
    batch_id: Optional[str],
) -> None:
    """Record a failed attempt. At-cap failures become PERMANENT_FAIL; otherwise
    FAILED (retryable on next --prepare). Either way the triplet is archived so
    we don't re-apply the same bad response.
    """
    ts = time.time()
    if attempt >= cfg.max_attempts_per_func:
        db.upsert_function(
            addr=addr, state="PERMANENT_FAIL",
            attempts=attempt, last_error=reason,
        )
        db.record_cleanup_attempt(
            addr=addr, attempt=attempt, tier=tier,
            outcome="PERMANENT_FAIL", last_error=reason,
            elapsed_s=elapsed_s, batch_id=batch_id, ts=ts,
        )
    else:
        db.upsert_function(
            addr=addr, state="FAILED",
            attempts=attempt, last_error=reason,
        )
        db.record_cleanup_attempt(
            addr=addr, attempt=attempt, tier=tier,
            outcome=outcome_kind, last_error=reason,
            elapsed_s=elapsed_s, batch_id=batch_id, ts=ts,
        )
    # Archive the triplet under attempt-stamped names so retries can read
    # the prior response without the next failed attempt clobbering it.
    queue.done_dir.mkdir(exist_ok=True)
    tid = f"0x{addr:08X}"
    suffix = f".attempt{attempt}"
    for src, dst_name in (
        (task.prompt_path,   f"{tid}{suffix}.prompt.md"),
        (task.meta_path,     f"{tid}{suffix}.meta.json"),
        (task.response_path, f"{tid}{suffix}.response.c"),
    ):
        if src and src.exists():
            src.rename(queue.done_dir / dst_name)


# -----------------------------------------------------------------------------
# Entry point (dispatched from supervisor)
# -----------------------------------------------------------------------------

def run(cfg: Config, args) -> int:
    if getattr(args, "prepare", False):
        return prepare(cfg, args)
    if getattr(args, "apply", False):
        return apply(cfg, args)
    queue = WorkQueue(cfg.work_root, "cleanup")
    pending = queue.pending()
    have_resp = sum(1 for _ in queue.responses())
    print(f"[decompile] queue: {len(pending)} pending prompts, {have_resp} with responses")
    print(f"[decompile] run with --prepare to enqueue, --apply to splice back")
    return 0
