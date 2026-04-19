"""Local dashboard — stdlib-only HTTP server.

## Why stdlib

This tool must run without network access. No Flask, no Django, no fetches
to CDNs. Everything ships in the repo: charts are hand-rolled SVG, requests
are `http.server`.

## Security posture

Binds to 127.0.0.1 only. Subprocess actuation is restricted to a fixed
allow-list of `--phase` invocations — no shell strings pass through from
the UI. Still, treat this as a single-operator tool; it has no auth.

## Endpoints

    GET  /                    index.html
    GET  /static/<name>       css/js
    GET  /api/state           counts + Gate 4 readout
    GET  /api/functions       ?state=&tag=&limit=&offset=
    GET  /api/address_strip   compact [addr,state] list for DOL visualization
    GET  /api/queue           work queue status
    GET  /api/jobs            list of recent/active jobs (id, cmd, status)
    POST /api/run             {"action": "triage"|...}  → starts a job
    GET  /api/jobs/<id>       job status + tailed stdout
"""

from __future__ import annotations

import http.server
import json
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import Config
from ..state_db import StateDB
from ..work_queue import WorkQueue


STATIC_DIR = Path(__file__).resolve().parent / "static"
DOL_FN_COUNT = 5981  # auto_*_text.s count in build/G4SE01/asm/ (dtk fill_gaps)


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    cmd: list[str]
    status: str = "pending"            # pending | running | done | failed
    returncode: Optional[int] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    log: deque = field(default_factory=lambda: deque(maxlen=5000))

    def to_dict(self, include_log: bool = False) -> dict:
        d = {
            "id": self.id,
            "cmd": self.cmd,
            "status": self.status,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log_lines": len(self.log),
        }
        if include_log:
            d["log"] = list(self.log)
        return d


class JobRunner:
    """Runs subprocess jobs serially in a background thread."""

    # Allow-list: UI action → argv tail for `python -m fsa_port_agent`.
    ACTIONS: dict[str, list[str]] = {
        "triage":               ["--phase", "triage"],
        "triage_limit_200":     ["--phase", "triage", "--limit", "200"],
        "import_dry":           ["--phase", "import", "--dry-run"],
        "import_real":          ["--phase", "import"],
        "import_limit_20":      ["--phase", "import", "--limit", "20", "--dry-run"],
        "cleanup_prepare_10":   ["--phase", "decompile", "--prepare", "--limit", "10"],
        "cleanup_prepare_50":   ["--phase", "decompile", "--prepare", "--limit", "50"],
        "cleanup_apply":        ["--phase", "decompile", "--apply"],
        "cleanup_status":       ["--phase", "decompile"],
        "hal":                  ["--phase", "hal"],
        "build_check":          ["--phase", "build", "--check"],
        "build_check_limit_50": ["--phase", "build", "--check", "--limit", "50"],
        "build_prepare_10":     ["--phase", "build", "--prepare", "--limit", "10"],
        "build_apply":          ["--phase", "build", "--apply"],
        "build_status":         ["--phase", "build"],
    }

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active: Optional[Job] = None

    def start(self, action: str) -> Job:
        if action not in self.ACTIONS:
            raise ValueError(f"unknown action: {action}")
        argv = [sys.executable, "-m", "fsa_port_agent", *self.ACTIONS[action]]
        job = Job(id=uuid.uuid4().hex[:8], cmd=argv)
        with self._lock:
            self.jobs[job.id] = job
        t = threading.Thread(target=self._run, args=(job,), daemon=True)
        t.start()
        return job

    def _run(self, job: Job) -> None:
        with self._lock:
            self._active = job
        job.status = "running"
        job.log.append(f"$ {' '.join(job.cmd)}")
        try:
            proc = subprocess.Popen(
                job.cmd, cwd=str(self.cfg.agent_root),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                job.log.append(line.rstrip("\n"))
            proc.wait()
            job.returncode = proc.returncode
            job.status = "done" if proc.returncode == 0 else "failed"
        except Exception as e:
            job.log.append(f"[runner] exception: {e}")
            job.status = "failed"
        finally:
            job.finished_at = time.time()
            with self._lock:
                if self._active is job:
                    self._active = None

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def recent(self, n: int = 20) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.started_at, reverse=True)[:n]


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def read_state_snapshot(cfg: Config) -> dict:
    """One-shot dump used by /api/state."""
    if not cfg.state_db_path.exists():
        return {
            "db_exists": False,
            "state_counts": {},
            "tag_counts": {},
            "total_functions": 0,
            "gate4": {"threshold": int(DOL_FN_COUNT * 0.20), "dol_total": DOL_FN_COUNT,
                      "matched_tww": 0, "pct": 0.0, "passing": False},
        }
    db = StateDB(cfg.state_db_path)
    try:
        state_counts = db.stats()
        tag_counts = {
            t: n for t, n in db.conn.execute(
                "SELECT tag, COUNT(*) FROM functions WHERE tag IS NOT NULL GROUP BY tag"
            )
        }
        total = sum(state_counts.values())
        matched_tww = state_counts.get("MATCHED_TWW", 0)
        threshold = int(DOL_FN_COUNT * 0.20)
        pct = (matched_tww / DOL_FN_COUNT * 100) if DOL_FN_COUNT else 0.0
        return {
            "db_exists": True,
            "state_counts": state_counts,
            "tag_counts": tag_counts,
            "total_functions": total,
            "gate4": {
                "threshold": threshold, "dol_total": DOL_FN_COUNT,
                "matched_tww": matched_tww, "pct": pct,
                "passing": matched_tww >= threshold,
            },
        }
    finally:
        db.close()


def read_functions(cfg: Config, state: Optional[str], tag: Optional[str],
                   limit: int, offset: int) -> list[dict]:
    if not cfg.state_db_path.exists():
        return []
    db = StateDB(cfg.state_db_path)
    try:
        where = []
        params: list = []
        if state:
            where.append("state=?"); params.append(state)
        if tag:
            where.append("tag=?"); params.append(tag)
        sql = "SELECT addr, name, size, tag, state, confidence, unit FROM functions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY addr LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = db.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def read_address_strip(cfg: Config) -> dict:
    """Compact state-per-address, binned, for the DOL strip visualization.

    We bin into ~1024 buckets so the front-end can paint a 1024-wide strip
    without shipping every row.
    """
    if not cfg.state_db_path.exists():
        return {"bins": [], "min_addr": 0, "max_addr": 0}
    db = StateDB(cfg.state_db_path)
    try:
        rows = db.conn.execute(
            "SELECT addr, state FROM functions ORDER BY addr"
        ).fetchall()
        if not rows:
            return {"bins": [], "min_addr": 0, "max_addr": 0}
        addrs = [r["addr"] for r in rows]
        lo, hi = addrs[0], addrs[-1]
        bins = 1024
        span = max(1, hi - lo)
        bucket: list[dict] = [{"state": None, "count": 0} for _ in range(bins)]
        # State priority for bin coloring — "done-er" states win over TRIAGED.
        priority = {
            "BUILDS": 6, "CLEANED": 5, "MATCHED_TWW": 4,
            "SIG_MATCHED": 3, "TRIAGED": 2, "FAILED": 1, "UNKNOWN": 0,
        }
        for r in rows:
            idx = min(bins - 1, int((r["addr"] - lo) * bins / span))
            b = bucket[idx]
            b["count"] += 1
            if b["state"] is None or priority.get(r["state"], 0) > priority.get(b["state"], 0):
                b["state"] = r["state"]
        return {"bins": bucket, "min_addr": lo, "max_addr": hi}
    finally:
        db.close()


def read_queue_status(cfg: Config) -> dict:
    kinds = ["cleanup", "fix_build", "type_infer", "synthesize"]
    out = {}
    for k in kinds:
        q = WorkQueue(cfg.work_root, k)
        pending = q.pending()
        responses = sum(1 for _ in q.responses())
        done_count = len(list(q.done_dir.iterdir())) if q.done_dir.exists() else 0
        out[k] = {
            "pending": len(pending),
            "pending_ids": pending[:20],
            "with_responses": responses,
            "done_files": done_count,
            "dir": str(q.dir),
        }
    return out


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    cfg: Config           # set by make_server
    runner: JobRunner     # set by make_server

    # Silence default access-log; the terminal is for subprocess output.
    def log_message(self, *a, **kw): pass

    # --- routing --------------------------------------------------------

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        qs = urllib.parse.parse_qs(url.query)

        if path == "/":
            return self._send_static("index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            return self._send_static(name)

        if path == "/api/state":
            return self._send_json(read_state_snapshot(self.cfg))
        if path == "/api/address_strip":
            return self._send_json(read_address_strip(self.cfg))
        if path == "/api/queue":
            return self._send_json(read_queue_status(self.cfg))
        if path == "/api/functions":
            state = qs.get("state", [None])[0]
            tag = qs.get("tag", [None])[0]
            limit = int(qs.get("limit", ["50"])[0])
            offset = int(qs.get("offset", ["0"])[0])
            return self._send_json(read_functions(self.cfg, state, tag, limit, offset))
        if path == "/api/jobs":
            return self._send_json({"jobs": [j.to_dict() for j in self.runner.recent()]})
        if path.startswith("/api/jobs/"):
            jid = path[len("/api/jobs/"):]
            job = self.runner.get(jid)
            if not job:
                return self._send_json({"error": "not found"}, status=404)
            return self._send_json(job.to_dict(include_log=True))
        if path == "/api/actions":
            return self._send_json({"actions": sorted(self.runner.ACTIONS.keys())})

        return self._send_404()

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path != "/api/run":
            return self._send_404()
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body)
            action = payload["action"]
            job = self.runner.start(action)
            return self._send_json({"job_id": job.id, "cmd": job.cmd})
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send_json({"error": str(e)}, status=400)

    # --- helpers --------------------------------------------------------

    def _send_static(self, name: str, ctype: str | None = None):
        p = STATIC_DIR / name
        if not p.exists() or not p.is_file() or STATIC_DIR not in p.resolve().parents \
                and p.resolve() != (STATIC_DIR / name).resolve():
            return self._send_404()
        ctype = ctype or _guess_ctype(name)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, status: int = 200):
        data = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_404(self):
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"not found")


def _guess_ctype(name: str) -> str:
    if name.endswith(".html"): return "text/html; charset=utf-8"
    if name.endswith(".css"):  return "text/css; charset=utf-8"
    if name.endswith(".js"):   return "application/javascript; charset=utf-8"
    if name.endswith(".svg"):  return "image/svg+xml"
    if name.endswith(".json"): return "application/json; charset=utf-8"
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# Entry point (dispatched from supervisor / __main__)
# ---------------------------------------------------------------------------

class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run(cfg: Config, args) -> int:
    host = getattr(args, "host", "127.0.0.1")
    port = int(getattr(args, "port", 8765))

    runner = JobRunner(cfg)

    handler = type("Bound" + DashboardHandler.__name__, (DashboardHandler,), {})
    handler.cfg = cfg
    handler.runner = runner

    srv = _ThreadingServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"[dashboard] serving {url}")
    print(f"[dashboard] state.db: {cfg.state_db_path}")
    print(f"[dashboard] Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] bye")
    finally:
        srv.server_close()
    return 0
