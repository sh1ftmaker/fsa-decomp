"""Paths, model tiers, budgets. Edit before first run."""

from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass
class Config:
    # Sibling repos. Keep as relative-to-Desktop defaults; override via env if moved.
    fsa_root: Path = Path(os.environ.get("FSA_ROOT", Path.home() / "Desktop" / "fsa-decomp"))
    tww_root: Path = Path(os.environ.get("TWW_ROOT", Path.home() / "Desktop" / "tww"))
    agent_root: Path = field(init=False)

    # Derived FSA paths
    asm_root: Path = field(init=False)
    src_root: Path = field(init=False)
    nonmatch_root: Path = field(init=False)
    splits_path: Path = field(init=False)
    symbols_path: Path = field(init=False)
    configure_py: Path = field(init=False)
    dol_path: Path = field(init=False)
    state_db_path: Path = field(init=False)

    # Tools in fsa-decomp/tools/ (we call them as subprocesses)
    compile_search_py: Path = field(init=False)
    m2c_batch_py: Path = field(init=False)
    fix_nonmatch_py: Path = field(init=False)

    # LLM tier hints (metadata only — we don't call the API directly).
    # Recorded in work_queue meta.json so the driver (Claude Code / subagents)
    # knows which model to spawn. Subscription-plan only: no API key.
    cheap_model: str = "claude-haiku-4-5"
    expensive_model: str = "claude-sonnet-4-6"
    synthesis_model: str = "claude-opus-4-7"

    # Work queue root (prompts in, responses out).
    work_root: Path = field(init=False)

    # Batching
    max_prompt_chars: int = 32000
    max_chunk_functions: int = 12

    # Budgets
    max_attempts_per_func: int = 3
    token_budget_per_func: int = 30000

    def __post_init__(self):
        self.agent_root = Path(__file__).resolve().parent.parent
        self.asm_root = self.fsa_root / "build" / "G4SE01" / "asm"
        self.src_root = self.fsa_root / "src"
        self.nonmatch_root = self.src_root / "nonmatch"
        self.splits_path = self.fsa_root / "config" / "G4SE01" / "splits.txt"
        self.symbols_path = self.fsa_root / "config" / "G4SE01" / "symbols.txt"
        self.configure_py = self.fsa_root / "configure.py"
        self.dol_path = self.fsa_root / "orig" / "sys" / "main.dol"
        self.state_db_path = self.agent_root / "state.db"
        self.work_root = self.agent_root / "work"
        self.compile_search_py = self.fsa_root / "tools" / "compile_search.py"
        self.m2c_batch_py = self.fsa_root / "tools" / "m2c_batch.py"
        self.fix_nonmatch_py = self.fsa_root / "tools" / "fix_nonmatch.py"
