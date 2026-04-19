"""Phase 4 — generate stub platform layer for emscripten.

Creates fsa-decomp/src/platform/ with empty-but-compilable shims for:
    gx/       — Dolphin software-GX port (drop-in) OR hand shims to WebGL2
    audio/    — JAudio → miniaudio (emscripten-friendly)
    input/    — PAD → SDL2 gamepad
    fs/       — DVD → emscripten FS + fetch + Asyncify
    thread/   — OSThread → pthreads under SharedArrayBuffer
    wasm_main.c — emscripten entry + main loop pump

This phase only scaffolds; real implementations land incrementally during
Phase 5's emcc fix-wave loop.
"""

from pathlib import Path

from ..config import Config


STUBS = {
    "gx/gx_stub.c": "/* GX → WebGL2 shims. See BROWSER_PORT_PLAN_V2.md §3 Phase 4. */\n",
    "audio/audio_stub.c": "/* JAudio → miniaudio. */\n",
    "input/input_stub.c": "/* PAD → SDL gamepad. */\n",
    "fs/fs_stub.c": "/* DVD → emscripten FS. */\n",
    "thread/thread_stub.c": "/* OSThread → pthreads. */\n",
    "wasm_main.c": '#include <emscripten.h>\nint main(void) { return 0; }\n',
}


def run(cfg: Config, args) -> int:
    plat = cfg.src_root / "platform"
    for rel, content in STUBS.items():
        p = plat / rel
        if p.exists():
            continue
        if args.dry_run:
            print(f"[hal] would create {p}")
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        print(f"[hal] created {p}")
    return 0
