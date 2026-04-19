"""Phase 3 global synthesis pass.

Runs once after cleanup drains. Single LLM call (Opus tier) sees
high-connectivity functions + candidate struct layouts collected during
cleanup, produces unified typedefs + naming conventions.

Output: src/nonmatch/_synthesized_types.h (included by all seg files).
"""

from ..config import Config


def run(cfg: Config, args) -> int:
    raise NotImplementedError(
        "Global synthesis — see BROWSER_PORT_PLAN_V2.md §3 Phase 3."
    )
