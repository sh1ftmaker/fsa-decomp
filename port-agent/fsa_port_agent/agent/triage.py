"""Phase 1 — populate state DB + call graph + classify.

Inputs:  orig/sys/main.dol, build/G4SE01/asm/auto_*_text.s
Outputs: state.db populated with ~11,500 fn rows + edges + string_refs.
         No LLM calls. Runs in seconds-to-minutes.

Classification tags (refined later by sig_match / tww_import):
    LEAF           — no outgoing `bl`, pure compute or syscall thunk.
    CONSTRUCTOR    — contains __register_global_object (C++ ctor w/ vtable).
    VTABLE_THUNK   — trivial: a few ops + blr (getter/setter/forwarder).
    INTERNAL       — everything else.
"""

from ..config import Config
from ..state_db import StateDB
from .. import call_graph


# Thunk heuristic: very small + very few memory ops.
_THUNK_MAX_BYTES = 48
_THUNK_MAX_MEMOPS = 5


def classify(asm_path, raw_text: str | None = None) -> str:
    text = raw_text if raw_text is not None else asm_path.read_text(errors="ignore")
    bl_count = len(call_graph._BL_RE.findall(text))
    if bl_count == 0:
        return "LEAF"
    if "__register_global_object" in text:
        return "CONSTRUCTOR"
    memops = text.count("lwz") + text.count("stw") + text.count("lfs") + text.count("stfs")
    if asm_path.stat().st_size <= _THUNK_MAX_BYTES and memops < _THUNK_MAX_MEMOPS:
        return "VTABLE_THUNK"
    return "INTERNAL"


def run(cfg: Config, args) -> int:
    db = StateDB(cfg.state_db_path)
    n = 0
    n_edges = 0
    n_strrefs = 0

    for asm in call_graph.iter_asm_files(cfg.asm_root):
        if args.limit and n >= args.limit:
            break
        addr = call_graph.file_addr(asm)
        text = asm.read_text(errors="ignore")

        tag = classify(asm, raw_text=text)
        db.upsert_function(
            addr=addr, size=asm.stat().st_size, tag=tag, state="TRIAGED",
        )

        for sym in call_graph._BL_RE.findall(text):
            callee = call_graph.callee_addr(sym)
            if callee is not None:
                db.add_edge(addr, callee)
                n_edges += 1

        for ref in call_graph.parse_data_refs(asm):
            db.add_string_ref(addr, ref, None)
            n_strrefs += 1

        n += 1

    # Topological stats — cheap sanity check that the call graph is DAG-ish.
    edge_map = db.load_edge_map()
    order = call_graph.topo_bottom_up(edge_map)
    covered = len(order)
    total_nodes = len({x for x in edge_map} | {c for cs in edge_map.values() for c in cs})
    cycle_frac = 1.0 - (covered / total_nodes) if total_nodes else 0.0

    db.close()
    print(f"[triage] populated {n} functions, {n_edges} edges, "
          f"{n_strrefs} string refs → {cfg.state_db_path}")
    print(f"[triage] topo: {covered}/{total_nodes} nodes reachable "
          f"({cycle_frac*100:.1f}% in cycles)")
    return 0
