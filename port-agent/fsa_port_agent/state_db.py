"""SQLite state per function. One row = one DOL function.

States (linear progression, no permuter branch):
    UNKNOWN → TRIAGED → {MATCHED_TWW | SIG_MATCHED | DECOMPILED | CLEANED | BUILDS | FAILED}

Schema intentionally flat — we query by (state, confidence, size) and join
to call_graph at analysis time.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS functions (
    addr         INTEGER PRIMARY KEY,
    name         TEXT,
    size         INTEGER,
    tag          TEXT,          -- LEAF, INTERNAL, VTABLE_THUNK, MSL, ...
    state        TEXT DEFAULT 'UNKNOWN',
    confidence   REAL DEFAULT 0.0,
    unit         TEXT,          -- source file path when assigned
    tww_source   TEXT,          -- TWW file path if matched via import
    sig          TEXT,          -- resolved signature, after cleanup
    last_error   TEXT,
    attempts     INTEGER DEFAULT 0,
    updated_at   REAL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS edges (
    caller INTEGER NOT NULL,
    callee INTEGER NOT NULL,
    PRIMARY KEY (caller, callee)
);

CREATE TABLE IF NOT EXISTS string_refs (
    addr       INTEGER NOT NULL,     -- function
    ref_addr   INTEGER NOT NULL,     -- string data address
    preview    TEXT,
    PRIMARY KEY (addr, ref_addr)
);

CREATE INDEX IF NOT EXISTS idx_state ON functions(state);
CREATE INDEX IF NOT EXISTS idx_tag ON functions(tag);

CREATE TABLE IF NOT EXISTS cleanup_attempts (
    addr       INTEGER NOT NULL,
    attempt    INTEGER NOT NULL,
    tier       TEXT    NOT NULL,          -- cheap | expensive | opus
    outcome    TEXT    NOT NULL,          -- CLEANED | FAILED_LEX | FAILED_COMPILE | PERMANENT_FAIL
    last_error TEXT,
    elapsed_s  REAL,
    batch_id   TEXT,
    ts         REAL    NOT NULL,
    PRIMARY KEY (addr, attempt)
);
CREATE INDEX IF NOT EXISTS idx_cleanup_attempts_outcome ON cleanup_attempts(outcome);
CREATE INDEX IF NOT EXISTS idx_cleanup_attempts_batch ON cleanup_attempts(batch_id);
"""


@dataclass
class FunctionRow:
    addr: int
    name: Optional[str]
    size: int
    tag: Optional[str]
    state: str
    confidence: float
    unit: Optional[str]
    sig: Optional[str]
    attempts: int = 0
    last_error: Optional[str] = None
    tww_source: Optional[str] = None


class StateDB:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.commit()
        self.conn.close()

    def upsert_function(self, **kw):
        cols = ", ".join(kw)
        placeholders = ", ".join(f":{k}" for k in kw)
        updates = ", ".join(f"{k}=excluded.{k}" for k in kw if k != "addr")
        self.conn.execute(
            f"INSERT INTO functions ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(addr) DO UPDATE SET {updates}",
            kw,
        )

    def add_edge(self, caller: int, callee: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO edges(caller, callee) VALUES (?, ?)",
            (caller, callee),
        )

    def add_string_ref(self, addr: int, ref_addr: int, preview: Optional[str] = None):
        self.conn.execute(
            "INSERT OR IGNORE INTO string_refs(addr, ref_addr, preview) VALUES (?, ?, ?)",
            (addr, ref_addr, preview),
        )

    # --- read helpers -------------------------------------------------------

    @staticmethod
    def _row_to_fn(row) -> "FunctionRow":
        return FunctionRow(
            addr=row["addr"], name=row["name"], size=row["size"],
            tag=row["tag"], state=row["state"], confidence=row["confidence"],
            unit=row["unit"], sig=row["sig"],
            attempts=row["attempts"] if "attempts" in row.keys() else 0,
            last_error=row["last_error"] if "last_error" in row.keys() else None,
            tww_source=row["tww_source"] if "tww_source" in row.keys() else None,
        )

    def get_fn_by_addr(self, addr: int) -> Optional[FunctionRow]:
        row = self.conn.execute(
            "SELECT * FROM functions WHERE addr=?", (addr,)
        ).fetchone()
        return self._row_to_fn(row) if row else None

    def get_by_state(self, state: str, limit: Optional[int] = None) -> list[FunctionRow]:
        q = "SELECT * FROM functions WHERE state=? ORDER BY addr"
        params: tuple = (state,)
        if limit:
            q += " LIMIT ?"
            params = (state, limit)
        return [self._row_to_fn(r) for r in self.conn.execute(q, params)]

    def get_callees(self, addr: int) -> list[int]:
        cur = self.conn.execute(
            "SELECT callee FROM edges WHERE caller=? ORDER BY callee", (addr,)
        )
        return [r[0] for r in cur]

    def get_callers(self, addr: int) -> list[int]:
        cur = self.conn.execute(
            "SELECT caller FROM edges WHERE callee=? ORDER BY caller", (addr,)
        )
        return [r[0] for r in cur]

    def get_string_refs(self, addr: int) -> list[tuple[int, Optional[str]]]:
        cur = self.conn.execute(
            "SELECT ref_addr, preview FROM string_refs WHERE addr=? ORDER BY ref_addr",
            (addr,),
        )
        return [(r[0], r[1]) for r in cur]

    def iter_edges(self) -> Iterator[tuple[int, int]]:
        cur = self.conn.execute("SELECT caller, callee FROM edges")
        yield from cur

    def load_edge_map(self) -> dict[int, set[int]]:
        """Return {caller: {callee, ...}} for all known edges."""
        out: dict[int, set[int]] = {}
        for caller, callee in self.iter_edges():
            out.setdefault(caller, set()).add(callee)
        return out

    def all_addrs(self) -> list[int]:
        cur = self.conn.execute("SELECT addr FROM functions ORDER BY addr")
        return [r[0] for r in cur]

    def stats(self) -> dict:
        cur = self.conn.execute(
            "SELECT state, COUNT(*) FROM functions GROUP BY state"
        )
        return dict(cur.fetchall())

    # --- cleanup attempt tracking -------------------------------------------

    def record_cleanup_attempt(
        self,
        addr: int,
        attempt: int,
        tier: str,
        outcome: str,
        last_error: Optional[str],
        elapsed_s: Optional[float],
        batch_id: Optional[str],
        ts: float,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO cleanup_attempts
               (addr, attempt, tier, outcome, last_error, elapsed_s, batch_id, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (addr, attempt, tier, outcome, last_error, elapsed_s, batch_id, ts),
        )

    def get_cleanup_attempts(
        self, batch_ids: Optional[list[str]] = None
    ) -> list[dict]:
        """Return rows grouped for dashboard consumption."""
        if batch_ids:
            placeholders = ",".join("?" * len(batch_ids))
            q = (
                f"SELECT addr, attempt, tier, outcome, last_error, elapsed_s, batch_id, ts "
                f"FROM cleanup_attempts WHERE batch_id IN ({placeholders}) ORDER BY ts DESC"
            )
            cur = self.conn.execute(q, tuple(batch_ids))
        else:
            cur = self.conn.execute(
                "SELECT addr, attempt, tier, outcome, last_error, elapsed_s, batch_id, ts "
                "FROM cleanup_attempts ORDER BY ts DESC LIMIT 2000"
            )
        return [dict(r) for r in cur]
