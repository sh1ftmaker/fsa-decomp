"""Signature-match MSL/Runtime/stdlib functions by byte pattern.

Complements tww_import: some stdlib functions ship identically across
many GameCube games. Fingerprint from doldecomp/dolsdk2001 / dolsdk2004
and hit FSA DOL directly without compiling anything.
"""

from ..config import Config


def run(cfg: Config, args) -> int:
    raise NotImplementedError(
        "Stdlib fingerprint match — build once from dolsdk2004, reuse forever."
    )
