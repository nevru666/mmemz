# --- PROVENANCE (vendored, do not hand-edit the body) ----------------------
#
# Source:      /home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0/agentmem/vecops.py
# Source lines: 75
# Vendored on:  2026-10-26
# Why vendored rather than reimplemented: it is 75 lines of pure trigonometry-free
# arithmetic that the target already runs in production, so reimplementing cosine
# would be a second source of truth for a formula with no room for improvement.
#
# DELIBERATE, DOCUMENTED DEVIATION FROM THE SOURCE (the one non-header hunk):
# the source is "numpy-backed where available, pure-Python fallback". mmemz is
# STDLIB ONLY (``pyproject.toml`` declares ``dependencies = []`` and that must stay
# true), so the ``try: import numpy`` probe and the three ``if _HAS_NUMPY:``
# branches are removed here. What remains is *exactly* the source's documented
# fallback path -- same formulas, same edge cases, same return types. The source's
# own docstring calls that path the correctness baseline and numpy an optimisation
# for the STM read hot loop, which mmemz does not have.
#
# This file is therefore NOT byte-identical to its source, and does not claim to
# be. ``diff <source> mmemz/vecops.py`` reports 92 differing lines: this header,
# the docstring rewording above, the removed numpy probe/branches, and the
# ``_HAS_NUMPY`` constants dropped after them. No formula, edge case or return
# type was changed -- the surviving body IS the source's ``else`` path.
# ---------------------------------------------------------------------------

"""Vector ops -- pure-Python cosine similarity over ``list[float]``.

The hot read path (STM/MTM recall) scores one query against many cached key
vectors. Doing that as a Python loop of ``cosine`` calls is the #1 self-time
hotspot (``builtins.sum`` over 256-dim lists -- see ``evals/PERF_REPORT.md``).

The fix in the source is **batched**: one BLAS ``matrix @ vector`` instead of N
Python loops. Measured ~1000x on the STM read pattern (907 us -> ~1 us). mmemz
carries no numpy, so :func:`cosine_batch` here is the pure-Python loop the source
falls back to when numpy is absent: correct, deterministic, and never imported
from outside the standard library.

Keep the rest of the system on ``list[float]`` (the ``Embedder`` protocol).
"""

from __future__ import annotations

import math

__all__ = ["cosine", "cosine_batch", "normalize", "SOURCE_PATH", "SOURCE_LINES"]

#: Absolute path of the file this module was vendored from.
SOURCE_PATH = (
    "/home/mmonk/Downloads/LSTM_VS_LLM/"
    "memory_service_v_2.3.0/agentmem/vecops.py"
)

#: Line count of the source file at vendoring time.
SOURCE_LINES = 75


def _norm_list(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors (single pair)."""
    an, bn = _norm_list(a), _norm_list(b)
    return sum(x * y for x, y in zip(an, bn))


def normalize(v: list[float]) -> list[float]:
    """Return a normalized vector."""
    return _norm_list(v)


def cosine_batch(rows: list[list[float]], query: list[float]) -> list[float]:
    """Cosine similarity of ``query`` against each row in ``rows`` -- batched.

    Returns a list of floats in row order. Empty ``rows`` -> ``[]``.
    """
    if not rows:
        return []
    return [cosine(r, query) for r in rows]
