"""Five-phase orchestrator. Dispatches to phase modules."""

from .config import Config


def run_phase(cfg: Config, args) -> int:
    phase = args.phase
    if phase == "all":
        for p in ("triage", "import", "decompile", "hal", "build"):
            args.phase = p
            rc = run_phase(cfg, args)
            if rc != 0:
                return rc
        return 0

    if phase == "triage":
        from .agent import triage
        return triage.run(cfg, args)
    if phase == "import":
        from .importers import tww_import
        return tww_import.run(cfg, args)
    if phase == "decompile":
        from .agent import cleanup
        return cleanup.run(cfg, args)
    if phase == "hal":
        from .hal import scaffold
        return scaffold.run(cfg, args)
    if phase == "build":
        from .agent import build
        return build.run(cfg, args)
    if phase == "dashboard":
        from .dashboard import server
        return server.run(cfg, args)
    if phase == "verify":
        from .agent import verify
        return verify.run(cfg, args)

    raise ValueError(f"unknown phase: {phase}")
