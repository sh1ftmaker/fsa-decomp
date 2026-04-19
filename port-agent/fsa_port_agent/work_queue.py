"""Filesystem work-queue for Claude-Code-driven AI work.

## Why

We don't have an Anthropic API key — only a Claude subscription. So every
AI-facing step is split in two:

    1. Python prepares:  renders prompt templates into `work/<kind>/<id>.prompt.md`
                         + a sibling `<id>.meta.json`.
    2. Claude Code runs: reads each `.prompt.md`, writes `<id>.response.<ext>`
                         (inline in the session, or via Agent-tool subagents).
    3. Python applies:   reads responses, splices / writes / DB-updates, then
                         moves the triplet into `work/<kind>/done/`.

This keeps Python out of the LLM business entirely and lets the subscription
drive all inference.

## Layout

    fsa-port-agent/work/
        cleanup/
            0x80021848.prompt.md
            0x80021848.meta.json
            0x80021848.response.c      ← written by Claude Code
            done/                      ← after apply()
        fix_build/
        type_infer/
        synthesize/

## Conventions

- `task_id` is a short slug unique within the `kind` directory. For fn-level
  work we use `0x{addr:08X}`. For file-level we use the file's basename.
- Response extension carries intent: `.c` (code), `.diff` (unified patch),
  `.md` (free-form / synthesis).
- Never hand-edit the `.prompt.md` files — regenerate via `--prepare`.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


@dataclass
class Task:
    task_id: str
    prompt_path: Path
    meta_path: Path
    meta: dict
    response_path: Optional[Path] = None   # set only when a response exists


class WorkQueue:
    def __init__(self, root: Path, kind: str):
        self.kind = kind
        self.dir = root / kind
        self.done_dir = self.dir / "done"
        self.dir.mkdir(parents=True, exist_ok=True)

    # --- producer side ------------------------------------------------------

    def enqueue(
        self,
        task_id: str,
        prompt: str,
        meta: dict,
        *,
        overwrite: bool = False,
    ) -> Task:
        prompt_path = self.dir / f"{task_id}.prompt.md"
        meta_path = self.dir / f"{task_id}.meta.json"
        if prompt_path.exists() and not overwrite:
            return Task(task_id, prompt_path, meta_path, json.loads(meta_path.read_text()))
        prompt_path.write_text(prompt)
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True))
        return Task(task_id, prompt_path, meta_path, meta)

    def pending(self) -> list[str]:
        """Task IDs with a prompt but no response yet."""
        prompts = {self._id_from_prompt(p) for p in self.dir.glob("*.prompt.md")}
        responses = {self._id_from_response(p) for p in self.dir.glob("*.response.*")}
        return sorted(prompts - responses)

    # --- consumer side ------------------------------------------------------

    def responses(self) -> Iterator[Task]:
        """Yield tasks that have both prompt + response in place."""
        for resp in sorted(self.dir.glob("*.response.*")):
            tid = self._id_from_response(resp)
            prompt_path = self.dir / f"{tid}.prompt.md"
            meta_path = self.dir / f"{tid}.meta.json"
            if not prompt_path.exists() or not meta_path.exists():
                continue
            yield Task(
                task_id=tid,
                prompt_path=prompt_path,
                meta_path=meta_path,
                meta=json.loads(meta_path.read_text()),
                response_path=resp,
            )

    def response_text(self, task: Task) -> str:
        assert task.response_path is not None
        return task.response_path.read_text(errors="ignore")

    def mark_done(self, task: Task) -> None:
        """Move prompt + meta + response into done/."""
        self.done_dir.mkdir(exist_ok=True)
        for p in (task.prompt_path, task.meta_path, task.response_path):
            if p and p.exists():
                p.rename(self.done_dir / p.name)

    def discard(self, task_id: str) -> None:
        """Drop prompt + meta (e.g., after a fatal apply error). No done archive."""
        for p in self.dir.glob(f"{task_id}.*"):
            if p.is_file():
                p.unlink()

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _id_from_prompt(p: Path) -> str:
        # "0x80021848.prompt.md" → "0x80021848"
        return p.name[: -len(".prompt.md")]

    @staticmethod
    def _id_from_response(p: Path) -> str:
        # "0x80021848.response.c" → "0x80021848"
        name = p.name
        i = name.find(".response.")
        return name[:i] if i >= 0 else p.stem

    def clear_done(self) -> int:
        """Remove everything under done/. Returns file count removed."""
        if not self.done_dir.exists():
            return 0
        n = sum(1 for _ in self.done_dir.iterdir())
        shutil.rmtree(self.done_dir)
        return n


# --- template rendering (previously in llm.py) -----------------------------

import re as _re
_VAR_RE = _re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_prompt(template_path: Path, vars: dict) -> str:
    """Interpolate `{name}` placeholders in a prompt template.

    Unknown placeholders are left intact — prompts commonly embed C bodies
    that contain literal `{` / `}`, so str.format would misfire.
    """
    tmpl = template_path.read_text()

    def sub(m):
        name = m.group(1)
        return str(vars[name]) if name in vars else m.group(0)

    return _VAR_RE.sub(sub, tmpl)
