"""Phase 5 — compile-check loop (prepare/apply).

The full build loop wraps Emscripten, but we don't assume emcc is installed.
For the inner iteration we use `cc -fsyntax-only` on each nonmatch seg file,
which is nearly instant and produces structured error lines.

## Flow

    python -m fsa_port_agent --phase build --check
        → runs `cc -fsyntax-only ...` on every seg_*.c (or a targeted subset),
          collects first-error-per-file into work/fix_build/last_errors.json.

    python -m fsa_port_agent --phase build --prepare --limit N
        → renders work/fix_build/<slug>.prompt.md for each error needing a
          fix. Slug is the seg file basename + line.

    # Claude writes <slug>.response.diff (unified diff)

    python -m fsa_port_agent --phase build --apply
        → `patch -p1`-style apply of each diff, archive on success.

## Why per-error, not per-file

Some seg files have dozens of errors. Per-error prompts stay small and let
us parallelize subagents on independent failures.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import Config
from ..work_queue import WorkQueue, render_prompt
from .context import SegIndex


# cc -fsyntax-only error lines look like:
#   /path/file.c:123:45: error: use of undeclared identifier 'foo'
_ERROR_RE = re.compile(
    r'^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s+(?P<kind>error|fatal error|warning):\s+(?P<msg>.+)$'
)


@dataclass
class BuildError:
    file: str
    line: int
    col: int
    kind: str
    msg: str

    def slug(self) -> str:
        stem = Path(self.file).stem
        return f"{stem}_L{self.line}"

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "col": self.col,
                "kind": self.kind, "msg": self.msg}


def _include_dirs(cfg: Config) -> list[str]:
    """Include search paths for syntax-only checks."""
    candidates = [
        cfg.fsa_root / "include",
        cfg.fsa_root / "src",
        cfg.src_root / "nonmatch",
    ]
    return [str(p) for p in candidates if p.exists()]


# -----------------------------------------------------------------------------
# Check: syntax-check and dump errors
# -----------------------------------------------------------------------------

def _check_one(
    cfg: Config, src: Path, cc: str, max_errors: int = 1,
    strict: bool = False,
) -> list[BuildError]:
    # Pass max_errors=0 for unlimited (needed by the Phase 3 compile gate, which
    # counts errors inside a specific line range and must see all of them).
    # Default 1 bails after the first error — seg files are huge and cc will
    # otherwise cascade for minutes during --check.
    #
    # strict=True adds semantic-safety warnings promoted to errors — catches
    # categories that -fsyntax-only lets pass but that real compilation
    # (mwcc / emcc) would reject. Used for attempt ≥ 2 retries where we want
    # a tighter gate before declaring CLEANED.
    # GCC 15+ promotes a handful of pointer-type diagnostics to *default* errors
    # regardless of -Wno-everything. We downgrade them here so the gate fails
    # only on real constraint violations, not on benign pointer-type drift
    # (e.g. char * vs u8 * on args that match byte-for-byte in matched code).
    argv = [
        cc, "-fsyntax-only", "-Wno-everything",
        "-Wno-error=incompatible-pointer-types",
        "-Wno-error=int-conversion",
        f"-fmax-errors={max_errors}",
    ]
    if strict:
        # NOTE: -Werror=incompatible-pointer-types is deliberately omitted.
        # It flags benign drift like `&lbl_xxx` (char) → `char *` param even
        # on callsites that already exist byte-matched elsewhere in the tree
        # (e.g. __register_global_object in seg_80066FFC.c:12585). Raising
        # it blocks ~10% of expensive-tier attempts on warnings mwcc doesn't
        # enforce. -Werror=int-conversion still catches the genuine pointer/
        # integer confusion class.
        argv += [
            "-Werror=int-conversion",
            "-Werror=implicit-function-declaration",
            "-Werror=implicit-int",
            "-Werror=return-type",
        ]
    for inc in _include_dirs(cfg):
        argv += ["-I", inc]
    argv += ["-std=c99", str(src)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return [BuildError(file=str(src), line=1, col=1, kind="error",
                           msg="cc syntax-check timed out (>30s)")]
    errs: list[BuildError] = []
    for line in r.stderr.splitlines():
        m = _ERROR_RE.match(line)
        if m and m.group("kind").startswith(("error", "fatal")):
            errs.append(BuildError(
                file=m.group("file"), line=int(m.group("line")),
                col=int(m.group("col")), kind=m.group("kind"),
                msg=m.group("msg"),
            ))
    return errs


def check(cfg: Config, args) -> int:
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        print("[build] no C compiler found on PATH")
        return 2

    targets = sorted(cfg.nonmatch_root.glob("seg_*.c"))
    if not targets:
        print(f"[build] no seg_*.c under {cfg.nonmatch_root}")
        return 1

    if args.limit:
        targets = targets[: args.limit]

    all_errs: list[BuildError] = []
    clean = 0
    for src in targets:
        errs = _check_one(cfg, src, cc)
        if not errs:
            clean += 1
            continue
        # Take the first error per file — fixing one typically cascades.
        all_errs.append(errs[0])

    out_dir = cfg.work_root / "fix_build"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "last_errors.json").write_text(
        json.dumps([e.to_dict() for e in all_errs], indent=2)
    )

    print(f"[build] checked {len(targets)} seg files: "
          f"{clean} clean, {len(all_errs)} with first-error recorded")
    print(f"[build] errors dumped to {out_dir/'last_errors.json'}")
    return 0


# -----------------------------------------------------------------------------
# Prepare: enqueue fix prompts
# -----------------------------------------------------------------------------

_EXCERPT_LINES = 20


def _excerpt(file: Path, line: int, pad: int = _EXCERPT_LINES) -> str:
    if not file.exists():
        return ""
    lines = file.read_text(errors="ignore").splitlines()
    lo = max(0, line - 1 - pad)
    hi = min(len(lines), line + pad)
    width = len(str(hi))
    out = []
    for i in range(lo, hi):
        marker = ">>" if i + 1 == line else "  "
        out.append(f"{marker} {str(i+1).rjust(width)}  {lines[i]}")
    return "\n".join(out)


def _addr_at_line(idx: SegIndex, seg_path: Path, line: int) -> Optional[int]:
    """Reverse-lookup: which fn_addr in `idx` covers this line of `seg_path`?"""
    try:
        text = seg_path.read_text(errors="ignore")
    except OSError:
        return None
    for addr, loc in idx.fns.items():
        if loc.seg != seg_path:
            continue
        line_lo = text.count("\n", 0, loc.start) + 1
        line_hi = text.count("\n", 0, max(loc.start, loc.end - 1)) + 1
        if line_lo <= line <= line_hi:
            return addr
    return None


def _cleanup_history_for(cfg: Config, addr: int) -> tuple[str, str]:
    """Return (m2c_original, prior_cleanup) pulled from work/cleanup/done/."""
    done = cfg.work_root / "cleanup" / "done"
    tid = f"0x{addr:08X}"
    m2c_original = ""
    prior_cleanup = ""
    p = done / f"{tid}.prompt.md"
    r = done / f"{tid}.response.c"
    if p.exists():
        txt = p.read_text(errors="ignore")
        # The cleanup template wraps m2c_source in a fenced block under
        # "Raw m2c output". Extract the body between its triple-backticks.
        marker = "Raw m2c output"
        i = txt.find(marker)
        if i >= 0:
            fence = txt.find("```", i)
            if fence >= 0:
                body_start = txt.find("\n", fence) + 1
                end = txt.find("```", body_start)
                if end >= 0:
                    m2c_original = txt[body_start:end].rstrip()
    if r.exists():
        prior_cleanup = r.read_text(errors="ignore")
    return m2c_original, prior_cleanup


def prepare(cfg: Config, args) -> int:
    errs_path = cfg.work_root / "fix_build" / "last_errors.json"
    if not errs_path.exists():
        print(f"[build] no errors recorded — run --phase build --check first")
        return 1

    errors = json.loads(errs_path.read_text())
    queue = WorkQueue(cfg.work_root, "fix_build")
    tmpl = Path(__file__).resolve().parent.parent / "prompts" / "fix_build.md"

    # Synthesized-types context — paste the whole file if it exists, else empty.
    syn_path = cfg.nonmatch_root / "_synthesized_types.h"
    types_block = syn_path.read_text() if syn_path.exists() else "(none yet)"

    # SegIndex for reverse fn-lookup (needed for carrying m2c + cleanup context).
    idx = SegIndex(cfg.nonmatch_root)
    idx.build()

    limit = args.limit or 0
    enqueued = already = 0

    for e in errors:
        if limit and enqueued >= limit:
            break
        be = BuildError(**e)
        tid = be.slug()
        if (queue.dir / f"{tid}.prompt.md").exists():
            already += 1
            continue

        excerpt = _excerpt(Path(be.file), be.line)

        addr = _addr_at_line(idx, Path(be.file), be.line)
        m2c_original = prior_cleanup = ""
        if addr is not None:
            m2c_original, prior_cleanup = _cleanup_history_for(cfg, addr)

        prompt = render_prompt(tmpl, {
            "file": be.file,
            "excerpt": excerpt,
            "error": f"{be.file}:{be.line}:{be.col}: {be.kind}: {be.msg}",
            "types": types_block[:4000],
            "m2c_original": m2c_original or "(not a Phase 3 cleanup output)",
            "prior_cleanup": prior_cleanup or "(none)",
        })
        meta = {
            "kind": "fix_build",
            "file": be.file,
            "line": be.line,
            "error": be.msg,
            "addr": addr,
            "tier": "cheap",
            "model_hint": cfg.cheap_model,
            "response_ext": "diff",
        }
        queue.enqueue(tid, prompt, meta)
        enqueued += 1

    print(f"[prepare] enqueued {enqueued} fix_build tasks ({already} already queued)")
    if enqueued:
        print(f"[prepare] Claude Code writes .response.diff in {queue.dir}")
        print(f"[prepare] then: python -m fsa_port_agent --phase build --apply")
    return 0


# -----------------------------------------------------------------------------
# Apply: apply unified diffs
# -----------------------------------------------------------------------------

def _strip_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return text
    lines = t.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def apply(cfg: Config, args) -> int:
    queue = WorkQueue(cfg.work_root, "fix_build")

    patch = shutil.which("patch")
    if not patch:
        print("[apply] `patch` utility not on PATH")
        return 2

    applied = failed = 0

    for task in queue.responses():
        diff = _strip_fence(queue.response_text(task))
        if "---" not in diff or "+++" not in diff:
            print(f"[apply] {task.task_id}  FAIL (not a unified diff)")
            failed += 1
            continue

        if args.dry_run:
            print(f"[apply] {task.task_id}  DRY ({len(diff)} bytes)")
            continue

        r = subprocess.run(
            [patch, "-p1", "--forward"],
            input=diff, capture_output=True, text=True,
            cwd=str(cfg.fsa_root),
        )
        if r.returncode == 0:
            queue.mark_done(task)
            applied += 1
            print(f"[apply] {task.task_id}  OK")
        else:
            failed += 1
            tail = (r.stdout + r.stderr).splitlines()[-3:]
            print(f"[apply] {task.task_id}  FAIL ({'; '.join(tail)})")

    print(f"[apply] applied={applied} failed={failed}")
    return 0 if applied or args.dry_run else 1


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def run(cfg: Config, args) -> int:
    if getattr(args, "check", False):
        return check(cfg, args)
    if getattr(args, "prepare", False):
        return prepare(cfg, args)
    if getattr(args, "apply", False):
        return apply(cfg, args)
    queue = WorkQueue(cfg.work_root, "fix_build")
    pending = queue.pending()
    have_resp = sum(1 for _ in queue.responses())
    errs_path = cfg.work_root / "fix_build" / "last_errors.json"
    errs_count = len(json.loads(errs_path.read_text())) if errs_path.exists() else 0
    print(f"[build] last check: {errs_count} errors recorded")
    print(f"[build] queue: {len(pending)} pending, {have_resp} responses")
    print(f"[build] --check | --prepare | --apply")
    return 0
