"""Shared filesystem path resolution for FuturesMind.

Centralizes the "where is the 思路2 validate data?" question so every module
(web_app, signal_analyzer, price_fetcher, scheduler) resolves it the same way:

  1. ``$THINK2_DIR`` wins when set (explicit override).
  2. Otherwise the first existing local candidate (real user data).
  3. Otherwise the bundled repo sample (``data/think2_validate``) so a fresh
     clone can still render without the local 思路2 project.
"""

import os
from pathlib import Path

_THINK2_CANDIDATES = (
    Path(os.path.expanduser("~/Desktop/思路2/validate")),
    Path(os.path.expanduser("~/Desktop/silu2/validate")),
    Path(os.path.expanduser("~/projects/silu2/validate")),
    Path(__file__).parent / "data" / "think2_validate",  # bundled sample
)


def resolve_think2_dir() -> Path:
    """Locate the 思路2 validate working directory (see module docstring)."""
    env = os.environ.get("THINK2_DIR", "").strip()
    if env:
        return Path(env)
    for candidate in _THINK2_CANDIDATES:
        if candidate.exists():
            return candidate
    return _THINK2_CANDIDATES[0]
