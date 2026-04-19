"""Phase 3 global synthesis pass.

Two modes:

    --scan      Read-only: walk every CLEANED fn's body, collect
                `p->unk_0xNN` references, bucket by the declared type of `p`
                (from the fn signature), dump a JSON report. No source
                mutations. Feeds the later full-synthesis pass (and gives the
                operator visibility into what `unk_0xNN` fields cluster in
                which structs).

    (default)   Placeholder for the later full pass: one Opus-tier call
                across the aggregated report → emits
                `src/nonmatch/_synthesized_types.h`. Not yet implemented.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..state_db import StateDB
from .context import SegIndex


# Named-field form emitted by Claude when a struct layout is (partially)
# known: `p->unk_0x8`, `p->unk4`, `this->unk_0xAB`.
_NAMED_FIELD_RE = re.compile(
    r'\b([A-Za-z_]\w*)\s*->\s*unk_?(?:0x)?([0-9A-Fa-f]+)\b'
)

# m2c raw-offset cast, still dominant in CLEANED bodies for `void *` args:
#   *(u32 *)((char *)arg0 + 0x98)
#   *(f32 *)((char*)(arg0) + 0x27C)
# Capture: the target lhs name and the hex offset. The outer deref may or
# may not be present (rvalue vs. lvalue lookup); we match the inner `((char
# *) <name> + 0x<N>)` part which is common to both.
_RAW_OFFSET_RE = re.compile(
    r'\(\s*char\s*\*\s*\)\s*\(?\s*([A-Za-z_]\w*)\s*\)?\s*\+\s*0x([0-9A-Fa-f]+)'
)

# `struct Foo *arg0`, `daActor_c *arg0`, `fopAc_ac_c * this`, `void *arg0`.
# Capture type (up through any trailing `*` count) and the parameter name.
_PARAM_RE = re.compile(
    r'^\s*(.+?)\s*\*\s*([A-Za-z_]\w*)\s*$'
)


def _parse_signature_args(signature: str) -> dict[str, str]:
    """Given `ret fn_ADDR(t1 a1, t2 *a2, ...)`, return {name: type}.

    Only pointer args are kept — field accesses by offset only make sense
    through a pointer. Non-pointer args are omitted.
    """
    lp = signature.find('(')
    rp = signature.rfind(')')
    if lp < 0 or rp <= lp:
        return {}
    inner = signature[lp + 1:rp].strip()
    if not inner or inner == "void":
        return {}
    out: dict[str, str] = {}
    for part in inner.split(','):
        part = part.strip()
        m = _PARAM_RE.match(part)
        if not m:
            continue
        ty = m.group(1).strip()
        name = m.group(2).strip()
        out[name] = f"{ty} *"
    return out


def _scan_body(
    body: str, arg_types: dict[str, str]
) -> list[tuple[str, int, str]]:
    """Return [(bucket_key, offset_int, ref_form)] for each field ref in body.

    `ref_form` is `"named"` for `p->unk_0xNN` or `"raw"` for the
    `((char *) p + 0xNN)` m2c cast. `bucket_key` is `"<name>:<type>"`
    when `p` is a known pointer arg, else `"local:<name>"`.
    """
    hits: list[tuple[str, int, str]] = []

    for m in _NAMED_FIELD_RE.finditer(body):
        lhs, off_hex = m.group(1), m.group(2)
        try:
            off = int(off_hex, 16)
        except ValueError:
            continue
        key = f"{lhs}:{arg_types[lhs]}" if lhs in arg_types else f"local:{lhs}"
        hits.append((key, off, "named"))

    for m in _RAW_OFFSET_RE.finditer(body):
        lhs, off_hex = m.group(1), m.group(2)
        try:
            off = int(off_hex, 16)
        except ValueError:
            continue
        key = f"{lhs}:{arg_types[lhs]}" if lhs in arg_types else f"local:{lhs}"
        hits.append((key, off, "raw"))

    return hits


def scan(cfg: Config, args) -> int:
    db = StateDB(cfg.state_db_path)
    try:
        cleaned = db.get_by_state("CLEANED")
        if not cleaned:
            print("[synthesize] no CLEANED functions in state.db; nothing to scan")
            return 0

        index = SegIndex(cfg.nonmatch_root)
        index.build()

        # bucket_key -> offset -> count; plus fn-addr set per bucket.
        bucket_offsets: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        bucket_fns: dict[str, set[int]] = defaultdict(set)
        bucket_forms: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        fns_with_refs = 0
        total_refs = 0
        total_named = 0
        total_raw = 0
        missing_in_index = 0

        for row in cleaned:
            loc = index.fns.get(row.addr)
            if loc is None:
                missing_in_index += 1
                continue
            arg_types = _parse_signature_args(loc.signature)
            hits = _scan_body(loc.body, arg_types)
            if not hits:
                continue
            fns_with_refs += 1
            total_refs += len(hits)
            for key, off, form in hits:
                bucket_offsets[key][off] += 1
                bucket_fns[key].add(row.addr)
                bucket_forms[key][form] += 1
                if form == "named":
                    total_named += 1
                else:
                    total_raw += 1

        # Rank buckets: most total refs first.
        buckets_sorted = sorted(
            bucket_offsets.items(),
            key=lambda kv: -sum(kv[1].values()),
        )

        by_context: dict[str, dict] = {}
        for key, offs in buckets_sorted:
            offsets_json = {
                f"0x{off:X}": cnt
                for off, cnt in sorted(offs.items())
            }
            fn_addrs = sorted(bucket_fns[key])
            by_context[key] = {
                "total_refs": sum(offs.values()),
                "distinct_offsets": len(offs),
                "fn_count": len(fn_addrs),
                "forms": dict(bucket_forms[key]),
                "offsets": offsets_json,
                "example_addrs": [f"0x{a:08X}" for a in fn_addrs[:10]],
            }

        report = {
            "scanned_at_unix": int(time.time()),
            "cleaned_fn_count": len(cleaned),
            "fns_scanned": len(cleaned) - missing_in_index,
            "fns_missing_in_seg_index": missing_in_index,
            "fns_with_unk_refs": fns_with_refs,
            "total_unk_refs": total_refs,
            "total_named_refs": total_named,
            "total_raw_offset_refs": total_raw,
            "by_context": by_context,
        }

        out_dir = cfg.work_root / "synthesize"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = out_dir / f"scan_{stamp}.json"
        out_path.write_text(json.dumps(report, indent=2))

        print(f"[synthesize] scanned {report['fns_scanned']} CLEANED fns "
              f"({fns_with_refs} with unk_0x refs, {total_refs} total refs)")
        if buckets_sorted:
            print("[synthesize] top buckets by total refs:")
            for key, offs in buckets_sorted[:10]:
                tot = sum(offs.values())
                print(f"[synthesize]   {key:<50s} total={tot:<5d} "
                      f"offsets={len(offs):<3d} fns={len(bucket_fns[key])}")
        print(f"[synthesize] report: {out_path.relative_to(cfg.agent_root)}")
        return 0
    finally:
        db.close()


def run(cfg: Config, args) -> int:
    if getattr(args, "scan", False):
        return scan(cfg, args)
    raise NotImplementedError(
        "Full synthesis (typedef emission) not implemented — see "
        "BROWSER_PORT_PLAN_V2.md §3 Phase 3. Run with --scan for the "
        "read-only bucket report."
    )
