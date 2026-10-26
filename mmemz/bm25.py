"""OPTIONAL / LATENT: mem0's sigmoid BM25 normaliser, with no caller.


OPTIONAL / LATENT
-----------------
This module is **OPTIONAL / LATENT**. Nothing in mmemz calls it and nothing
should, yet. It is provided for exactly one future purpose: a **weighted
score-sum arm**, where heterogeneous arms (semantic cosine, lexical BM25) are
added together and therefore *must* be put on a common scale first.

IT IS **NEVER WIRED INTO AN RRF STAGE**. Reciprocal Rank Fusion consumes
**ranks, not scores**:

    score(d) = sum over arms of 1 / (k + rank_arm(d))

The magnitudes of the underlying arms never enter that expression -- only
their order -- so normalising a BM25 score to ``[0, 1]`` with a sigmoid would
change no rank, reorder nothing, and alter no output. Inside an RRF stage a
sigmoid is dead code: it would run, cost time, and be unobservable. The target
service fuses with RRF (``agentmem/memory_retrieval.py:812``), so porting this
into its fusion stage would be pure waste.

:func:`is_used_by_service` returns ``False`` for that reason. An honest marker
in the code beats a comment nobody reads: a future maintainer can call it and
be told, by the program rather than by a docstring, that this module is not on
the serving path.

PROVENANCE
----------
* sigmoid body -- ``mem0/utils/scoring.py:43-54``, expression at ``:54``::

      1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))

* parameter table -- ``mem0/utils/scoring.py:31-40`` (the ``if/elif`` chain
  inside ``get_bm25_params``, ``:16``). Values transcribed below, not guessed.

The port spec cites this file as ``mem0/memory/scoring.py``; in the v2.2.1 tree
it lives at ``mem0/utils/scoring.py``. Line numbers were re-checked with
``sed -n`` against the real path.

Stdlib only: ``math``.
"""

from __future__ import annotations

import math

#: mem0's query-length -> ``(midpoint, steepness)`` table, transcribed from
#: ``mem0/utils/scoring.py:31-40``. Each row is ``(max_terms, midpoint,
#: steepness)``: the first row whose ``max_terms`` is ``>=`` the query's term
#: count wins, and the last row is the catch-all for longer queries.
#: Longer queries produce higher raw BM25 scores, so mem0 raises the midpoint
#: to keep the sigmoid's 0.5 point where the score distribution actually sits.
#: The steepness falls as the midpoint rises, widening the transition band so a
#: long query is not compressed into a near-binary 0/1 by a high midpoint.
BM25_SIGMOID_TABLE: tuple[tuple[int, float, float], ...] = (
    (3, 5.0, 0.7),    # scoring.py:31-32 -- num_terms <= 3
    (6, 7.0, 0.6),    # scoring.py:33-34 -- num_terms <= 6
    (9, 9.0, 0.5),    # scoring.py:35-36 -- num_terms <= 9
    (15, 10.0, 0.5),  # scoring.py:37-38 -- num_terms <= 15
    (2**31 - 1, 12.0, 0.5),  # scoring.py:39-40 -- else
)


def bm25_sigmoid_params(num_terms: int) -> tuple[float, float]:
    """Return ``(midpoint, steepness)`` for a query of ``num_terms`` terms.

    A direct transcription of mem0's ``if/elif`` chain
    (``mem0/utils/scoring.py:31-40``) as a table lookup, so the numbers live in
    one place and can be compared row by row against the original.

    ``num_terms`` below 1 is clamped to 1 -- an empty query is a one-term query
    as far as the table is concerned, which is what mem0's
    ``len(...) if ... else 1`` does at ``:30``.

    >>> bm25_sigmoid_params(1)
    (5.0, 0.7)
    >>> bm25_sigmoid_params(9)
    (9.0, 0.5)
    >>> bm25_sigmoid_params(100)
    (12.0, 0.5)

    Provenance: ``mem0/utils/scoring.py:31-40``.
    """
    n = max(1, int(num_terms))
    for max_terms, midpoint, steepness in BM25_SIGMOID_TABLE:
        if n <= max_terms:
            return midpoint, steepness
    # Unreachable while the table's last row is the catch-all, but a future
    # edit that deletes that row must still return a usable pair.
    return BM25_SIGMOID_TABLE[-1][1], BM25_SIGMOID_TABLE[-1][2]


def bm25_params_for_query(query: str) -> tuple[float, float]:
    """``(midpoint, steepness)`` for a raw query string.

    Counts whitespace-separated tokens. mem0 lemmatises first
    (``scoring.py:26-29``, importing ``lemmatize_for_bm25``); mmemz has no
    lemmatiser and will not gain one for a latent module, so this counts the
    query as typed. The difference is small and only shifts a midpoint by one
    band; the function exists so the seam has a stable signature when a
    lemmatiser is ever passed in.

    >>> bm25_params_for_query("graph memory service")
    (5.0, 0.7)
    >>> bm25_params_for_query("")
    (5.0, 0.7)

    Provenance: ``mem0/utils/scoring.py:16-40``.
    """
    terms = [t for t in str(query).split() if t]
    return bm25_sigmoid_params(len(terms) if terms else 1)


def normalize_bm25(raw_score: float, midpoint: float, steepness: float) -> float:
    """Squash an unbounded BM25 score into ``(0, 1)`` with a logistic sigmoid.

    .. math:: f(x) = \\frac{1}{1 + e^{-k (x - m)}}

    where ``x`` is ``raw_score``, ``m`` is ``midpoint`` and ``k`` is
    ``steepness``. The properties that matter downstream:

    * ``f(m) == 0.5`` exactly -- the midpoint *is* the half-way point, so
      ``m`` can be read as "the score that should map to average".
    * Monotonically increasing in ``x`` for ``k > 0``: it never reorders two
      BM25 scores, it only rescales them. That is the only reason a
      normaliser is safe in front of a fusion stage at all.
    * Saturating and bounded: it approaches 1.0 as ``x -> +inf`` and 0.0 as
      ``x -> -inf``, and never leaves ``[0, 1]`` for finite input. Huge raw
      scores stop dominating a weighted sum.
    * ``f(m + d) + f(m - d) == 1.0`` -- symmetric about the midpoint, so the
      rescaling does not favour high or low scores.

    ``steepness=0`` returns the constant ``0.5`` (the sigmoid degenerates,
    which is correct rather than an error). A non-finite ``raw_score`` is
    handled explicitly instead of letting ``math.exp`` raise ``OverflowError``
    on a large negative difference: ``+inf`` saturates to 1.0 and ``-inf`` to
    0.0, so a single broken score cannot kill a retrieval.

    .. warning::
       **OPTIONAL / LATENT.** This is not on the serving path and must never be
       placed in an RRF stage -- RRF consumes ranks, not scores, so a sigmoid
       there would be dead code. See the module docstring.

    >>> normalize_bm25(5.0, 5.0, 0.7)   # the midpoint maps to 0.5
    0.5
    >>> round(normalize_bm25(10.0, 5.0, 0.7), 6)
    0.970688
    >>> round(normalize_bm25(0.0, 5.0, 0.7), 6)
    0.029312

    Provenance: ``mem0/utils/scoring.py:43-54``, expression at ``:54``.
    """
    x = float(raw_score)
    m = float(midpoint)
    k = float(steepness)
    if x != x or m != m:  # NaN in either -> no usable signal
        return 0.5
    if x == float("inf"):
        return 1.0
    if x == float("-inf"):
        return 0.0
    exponent = -k * (x - m)
    if exponent > 709.0:  # exp overflows past ~709.78; sigmoid saturates
        return 0.0
    if exponent < -709.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(exponent))


def normalize_bm25_for_query(query: str, raw_score: float) -> float:
    """Normalise ``raw_score`` using the parameters ``query`` selects.

    The convenience wrapper mem0's ``score_and_rank`` performs inline
    (``mem0/utils/scoring.py:96-101``): pick the band from the query, then
    squash the raw score with it. Keeping it separate from
    :func:`bm25_sigmoid_params` means the parameter choice is testable without
    a score and the maths is testable without a query.

    >>> normalize_bm25_for_query("graph memory service", 5.0)
    0.5
    >>> round(normalize_bm25_for_query("graph memory service", 6.0), 6)
    0.668188

    Provenance: ``mem0/utils/scoring.py:16-40`` (parameters),
    ``:43-54`` (sigmoid).
    """
    midpoint, steepness = bm25_params_for_query(query)
    return normalize_bm25(raw_score, midpoint, steepness)


def is_used_by_service() -> bool:
    """``False``: nothing on mmemz's serving path calls this module.

    This is an executable marker, not a comment, because comments lie by
    omission -- a comment saying "unused" survives long after something starts
    using it. A function that a caller (or a test, or a code review) can call
    and get ``False`` from cannot rot silently: the day this module is wired
    in, this function is the one thing that must be changed, and changing it is
    a visible diff.

    The reason it is unused is structural, not a matter of taste. mmemz fuses
    with **Reciprocal Rank Fusion**::

        score(d) = sum over arms of 1 / (k + rank_arm(d))

    RRF reads only *ranks*. The magnitude of a BM25 score never enters the
    expression, so sigmoid-normalising it to ``[0, 1]`` cannot change a rank,
    cannot reorder anything, and cannot alter any output. Inside an RRF stage,
    this module would be dead code that runs and is unobservable.

    It becomes live only if a **weighted score-sum** arm is added (adding
    heterogeneous arms requires a shared scale) -- a different fusion rule
    from the one mmemz ships.

    Returns:
        ``False``, always, until a weighted score-sum arm exists.

    Provenance: RRF at ``agentmem/memory_retrieval.py:812``; the sigmoid it
    would displace is ``mem0/utils/scoring.py:54``.
    """
    return False


__all__ = [
    "BM25_SIGMOID_TABLE",
    "bm25_sigmoid_params",
    "bm25_params_for_query",
    "normalize_bm25",
    "normalize_bm25_for_query",
    "is_used_by_service",
]
