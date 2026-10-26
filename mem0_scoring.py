#!/usr/bin/env python3
"""Dependency-free port of mem0 v2.2.1's entity/attribute SCORING MATHS.

anchor: 92f3a80b

Source of truth: ``deliverables/mem0-entity-graph-pipeline.md`` §5 (lines 434-524)
and §6 (lines 525-707). Every formula and constant below carries the mem0
``file:line`` provenance *as recorded in that report*; nothing here was invented
or re-derived from the mem0 repo.

Scope: the six mechanical pieces of the read path that decide what a search
returns, plus the write-time merge rule they depend on.

  1. Entity count decay            main.py:1815-1817, 3493-3495
  2. BM25 -> confidence band table scoring.py:16, 31-40, 43, 54
  3. Entity boost + max_possible   scoring.py:57, 97-101, 118-119
  4. Entity dedup gate             main.py:621, 1166 / match gate main.py:1807, 3485
  5. score_and_rank ordering       scoring.py:60-139 (gate :111-112, combine :118)
  6. Pool sizing / caps / top_k    main.py:1655, 1761, 1789

No mem0 imports. No third-party imports. Pure functions + plain dicts/lists.
Run ``python3 mem0_scoring.py`` for the self-test.

NOTE ON FIDELITY: mem0's entity graph is a *ranking* device only -- entity rows
never leave the process. This module therefore models the ``{memory_id: float}``
boost dict and the final ``score``, not any response payload (report §6,
"Stated plainly: an OSS v2.2.1 response contains no ``relations`` key").
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Constants -- every one is a hard-coded module literal in mem0, none is
# configurable (report §6, "Every constant in the read path").
# ---------------------------------------------------------------------------

ENTITY_BOOST_WEIGHT: float = 0.5
"""Ceiling of a single-memory entity boost. ``mem0/utils/scoring.py:57``,
imported at ``main.py:70``. Also the ``+0.5`` added to ``max_possible``
(``scoring.py:101``)."""

ENTITY_DEDUP_COSINE: float = 0.95
"""Write-time entity-row merge cutoff -- "close enough to fuse".
Literal in the call at ``main.py:621`` and ``main.py:1166``."""

ENTITY_MATCH_THRESHOLD: float = 0.5
"""Read-time boost cutoff -- "close enough to boost". Literal in the loop at
``main.py:1807`` (async twin ``main.py:3485``). Deliberately looser than
``ENTITY_DEDUP_COSINE``; the two are different thresholds for different jobs."""

QUERY_ENTITY_CAP: int = 8
"""``query_entities[:8]`` -- entity nine and beyond are silently never looked
up. ``main.py:1761`` (async ``main.py:3437``)."""

ENTITY_SEARCH_TOP_K: int = 500
"""Entity rows harvested per query entity, because one query entity may have
been merged into up to 500 rows by the cosine-0.95 pass. ``main.py:1789``
(async ``main.py:3469``)."""

POOL_MULTIPLIER: int = 4
POOL_FLOOR: int = 60
"""``internal_limit = max(limit * 4, 60)`` -- semantic over-fetch, i.e. the size
of the reorderable pool. ``main.py:1655`` (async ``main.py:3342``). The ``60``
floor protects a ``top_k=1..15`` caller from an empty pool."""

DEFAULT_THRESHOLD: float = 0.1
"""Signature default of ``search()`` (``main.py:1399``), forwarded at
``main.py:1642``, *read* at ``scoring.py:111``. Not a ``MemoryConfig`` field."""

# ---------------------------------------------------------------------------
# §1  Entity count decay -- the anti-hub dampener
# ---------------------------------------------------------------------------


def memory_count_weight(num_linked: int) -> float:
    """Hub dampener applied to an entity's boost, by how many memories it links.

    Formula (report §5 "Count decay happens at scoring time", quoting
    ``mem0/memory/main.py:1815-1817``; async twin ``main.py:3493-3495``)::

        num_linked  = max(len(linked_memory_ids), 1)
        memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))

    This is an **anti-hub dampener, not a consolidation step**: it never touches
    a payload, never removes an id, never merges two entities. It only deflates
    the score contribution of an entity linked to very many memories. The
    adjacency list keeps every edge regardless (report §5 item 3).

    Coefficients ``0.001`` and the squaring are hard-coded literals in mem0 --
    not configurable (report §6 constants table, "Count decay" row).

    Anchors: n=1 -> exactly 1.0, n=10 -> ~0.9251, n=100 -> ~0.0926.

    Args:
        num_linked: length of the entity row's ``linked_memory_ids``. Values
            ``< 1`` are floored to 1, mirroring the ``max(..., 1)`` in the
            original, so an empty list yields a neutral 1.0 rather than >1.

    Returns:
        Multiplier in ``(0, 1]``.

    >>> memory_count_weight(1)
    1.0
    >>> round(memory_count_weight(10), 4)
    0.9251
    >>> round(memory_count_weight(100), 4)
    0.0926
    """
    n = max(int(num_linked), 1)
    return 1.0 / (1.0 + 0.001 * ((n - 1) ** 2))


# ---------------------------------------------------------------------------
# §2  BM25 -> confidence -- a LOOKUP TABLE, not a sigmoid
# ---------------------------------------------------------------------------

#: ``get_bm25_params`` band table: ``scoring.py:31-40`` (def at ``scoring.py:16``).
#: Ordered ``(max_term_count, midpoint, steepness)``; the last row is the
#: unbounded ``else`` branch.
#:
#: THIS IS A TABLE STANDING IN FOR A SIGMOID -- NOT A SIGMOID.
#:
#: mem0 does not compute a real sigmoid here. ``normalize_bm25``
#: (``scoring.py:43``, formula ``1/(1+exp(-k*(x-m)))`` at ``scoring.py:54``)
#: exists and is what the report's step 5 describes, but the *parameters* it is
#: fed are picked from this five-band table keyed on query-term count, so the
#: effective mapping is a step function over bands with fixed (m, k) per band.
#: The (midpoint, steepness) pairs are hard-coded -- "No -- hard-coded table"
#: in the report's §6 constants table. Ported here as the table itself so the
#: banding is exact and inspectable, rather than hidden inside an exp() call.
BM25_PARAM_TABLE: tuple[tuple[int | None, float, float], ...] = (
    (3, 5.0, 0.7),    # query terms <= 3   -> scoring.py:31-40
    (6, 7.0, 0.6),    # query terms <= 6
    (9, 9.0, 0.5),    # query terms <= 9
    (15, 10.0, 0.5),  # query terms <= 15
    (None, 12.0, 0.5),  # else -- unbounded
)


def get_bm25_params(term_count: int) -> tuple[float, float]:
    """Pick ``(midpoint, steepness)`` for a query from its lemmatised term count.

    Band selection reproduced verbatim from ``mem0/utils/scoring.py:31-40``
    (function defined at ``scoring.py:16``): ``<=3`` -> (5.0, 0.7), ``<=6`` ->
    (7.0, 0.6), ``<=9`` -> (9.0, 0.5), ``<=15`` -> (10.0, 0.5), else (12.0, 0.5).

    The bands are compared with ``<=`` against the term count, so the boundary
    values 3, 6, 9 and 15 belong to the band they close, not the next one. This
    is a table lookup; see the ``BM25_PARAM_TABLE`` note above -- it is **not**
    a sigmoid fit to the query, and the table stands in for one.

    Args:
        term_count: number of lemmatised query terms.

    Returns:
        ``(midpoint, steepness)`` -- the ``m`` and ``k`` of the sigmoid that
        ``normalize_bm25`` (``scoring.py:43``) would be fed.
    """
    for upper, midpoint, steepness in BM25_PARAM_TABLE:
        if upper is None or term_count <= upper:
            return midpoint, steepness
    # Unreachable: the last table row has upper=None.
    return BM25_PARAM_TABLE[-1][1], BM25_PARAM_TABLE[-1][2]


def normalize_bm25(
    raw_score: float | None,
    midpoint: float | None = None,
    steepness: float | None = None,
    term_count: int | None = None,
) -> float:
    """Map an unbounded BM25 score into ``[0, 1]``.

    Formula from ``mem0/utils/scoring.py:43`` (the expression itself at
    ``scoring.py:54``)::

        normalized = 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))

    Caller behaviour worth preserving (report §6 step 5, ``main.py:1666-1673``):

    * only **positive** raw scores are kept (``main.py:1672``) -- a missing or
      non-positive score normalises to ``0.0``, not to the sigmoid of a negative;
    * ``keyword_search`` may not exist at all: the base class stub returns
      ``None`` (``mem0/vector_stores/base.py:68`` stub, ``:83`` the ``return
      None``), so BM25 "silently degrades to absent rather than erroring".

    Args:
        raw_score: raw store BM25 score, or ``None`` when the store has no
            ``keyword_search``.
        midpoint: sigmoid midpoint; if omitted it is taken from
            ``get_bm25_params(term_count)``.
        steepness: sigmoid steepness; same fallback rule.
        term_count: used to resolve the band when midpoint/steepness are omitted.

    Returns:
        Confidence in ``[0.0, 1.0]``.
    """
    if raw_score is None or raw_score <= 0:
        return 0.0
    if midpoint is None or steepness is None:
        m, k = get_bm25_params(0 if term_count is None else term_count)
        midpoint = m if midpoint is None else midpoint
        steepness = k if steepness is None else steepness
    return 1.0 / (1.0 + math.exp(-steepness * (float(raw_score) - midpoint)))


def bm25_confidence(score_or_term_count: int) -> tuple[float, float]:
    """Apply the band table to a value and return its ``(midpoint, steepness)``.

    Thin, explicitly named alias of :func:`get_bm25_params`, kept because the
    deliverable contract words this step as "BM25 score -> confidence".

    .. warning::
       **The two readings are NOT interchangeable.** The band table is keyed on
       the number of lemmatised *query terms* (a small integer, typically 1-20).
       Feeding it a raw BM25 *score* selects a band by an unrelated quantity and
       yields a midpoint/steepness that does not match mem0's behaviour. This
       wrapper exists for naming convenience only; call
       :func:`get_bm25_params` with a real term count for mem0 fidelity.

    * **mem0 fidelity (what the source actually does):** the band key is the
      number of lemmatised *query terms* -- ``get_bm25_params(query,
      lemmatized=...)`` at ``scoring.py:16``, table ``scoring.py:31-40``.
    * **This wrapper:** applies the same five bands to whatever integer it is
      handed, which lets a caller band a raw BM25 score directly.

    Again: a table standing in for a sigmoid, not a sigmoid.

    >>> bm25_confidence(3), bm25_confidence(4), bm25_confidence(16)
    ((5.0, 0.7), (7.0, 0.6), (12.0, 0.5))
    """
    return get_bm25_params(score_or_term_count)


# ---------------------------------------------------------------------------
# §3  Entity boost, the match gate, and max_possible normalisation
# ---------------------------------------------------------------------------


def entity_boost(
    similarity: float,
    num_linked: int,
    weight: float = ENTITY_BOOST_WEIGHT,
) -> float:
    """Boost contributed by ONE entity row matched to a memory.

    From ``mem0/memory/main.py:1815-1817`` (async ``3493-3495``)::

        memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
        boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight

    with ``ENTITY_BOOST_WEIGHT = 0.5`` at ``scoring.py:57``. A perfect
    similarity of 1.0 on a single-memory entity yields exactly ``0.5``.

    The match gate is **not** applied here on purpose: it lives in
    :func:`compute_entity_boosts`, where it is an early ``continue``
    (``main.py:1807``) -- see that function for why the ordering matters.

    Args:
        similarity: cosine between the query entity's embedding and the entity
            row's embedding.
        num_linked: ``len(linked_memory_ids)`` for that row.
        weight: the global constant; exposed only so the 0.5 ceiling can be
            demonstrated, never because mem0 makes it configurable.

    Returns:
        Boost in ``[0.0, weight]``.
    """
    return float(similarity) * weight * memory_count_weight(num_linked)


def passes_entity_gate(similarity: float) -> bool:
    """Read-time gate: a row may boost only at ``similarity >= 0.5``.

    ``if similarity < 0.5: continue`` -- ``main.py:1807``, async
    ``main.py:3485``. This is the *looser* of the two entity thresholds: 0.5 is
    "close enough to boost", 0.95 (:data:`ENTITY_DEDUP_COSINE`) is "close
    enough to fuse". The boundary row is inclusive: exactly 0.5 boosts.
    """
    return float(similarity) >= ENTITY_MATCH_THRESHOLD


def passes_dedup_gate(similarity: float) -> bool:
    """Write-time gate: two entity rows fuse only at ``cosine >= 0.95``.

    ``main.py:621`` (``existing[0].score >= 0.95``) and the batch twin
    ``main.py:1166``. Inclusive at exactly 0.95. Exact normalised-text match
    wins unconditionally *before* this test (``main.py:610`` exact step,
    ``main.py:1167`` batch precedence), so cosine never overrides text
    equality.
    """
    return float(similarity) >= ENTITY_DEDUP_COSINE


def max_possible_score(
    has_bm25: bool,
    has_entity: bool,
    entity_weight: float = ENTITY_BOOST_WEIGHT,
) -> float:
    """Denominator of the final normalisation -- ``1.0``, ``1.5``, ``2.0`` or ``2.5``.

    ``scoring.py:97-101``::

        max_possible = 1.0 + (1.0 if has_bm25 else 0.0) + (ENTITY_BOOST_WEIGHT if has_entity else 0.0)
        combined     = min(raw_combined / max_possible, 1.0)     # :119

    ``has_bm25 = bool(bm25_scores)`` and ``has_entity = bool(entity_boosts)``
    (``scoring.py:97-98``) -- emptiness of the *whole* signal dict decides,
    not whether any individual candidate received one. That is why the boost's
    0.5 ceiling is half a semantic unit: it is measured against a denominator
    that grew by exactly 0.5.

    Args:
        has_bm25: ``bool(bm25_scores)``.
        has_entity: ``bool(entity_boosts)``.
        entity_weight: the constant, defaulting to :data:`ENTITY_BOOST_WEIGHT`.

    Returns:
        Denominator in ``[1.0, 2.5]``.
    """
    return 1.0 + (1.0 if has_bm25 else 0.0) + (entity_weight if has_entity else 0.0)


def compute_entity_boosts(
    query_entities: Sequence[tuple[str, str]],
    search_entity_rows: Callable[[str, str], Iterable[tuple[float, Any]]],
    scope_filter: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Query entities -> ``{memory_id: float}``. The one and only graph hop.

    Mirrors ``_compute_entity_boosts`` (``main.py:1747-1827``, docstring
    ``1748-1756``; async twin ``main.py:3433-3505``) with the embedding and
    vector-store calls lifted out behind ``search_entity_rows``.

    Ordering of the six steps is faithful and load-bearing:

    1. dedup + cap at **8** -- ``for entity_type, entity_text in
       query_entities[:8]`` (``main.py:1761``), keyed by normalised text
       (``:1762``); empty list -> ``return {}`` (``1767-1768``).
    2. scope the filters to only ``("user_id", "agent_id", "run_id")``
       (``main.py:1770``) so a boost can never cross a tenant boundary. Here
       that is ``scope_filter``; unset means unscoped, as mem0 would be with an
       empty caller filter dict.
    3. batch-embed (``main.py:1775``) -- done by the caller/callback.
    4. one entity-store search per entity at ``top_k=500`` (``main.py:1788-1790``).
    5. **gate first**: ``if similarity < 0.5: continue`` (``main.py:1807``) --
       a below-threshold row is skipped *before* any boost is computed, so it
       contributes nothing at all rather than a small amount. A non-list
       ``linked_memory_ids`` is skipped too (``1812-1813``).
    6. boost = similarity * 0.5 * count_weight (``1815-1817``), then
       ``memory_boosts[k] = max(memory_boosts.get(k, 0.0), boost)``
       (``main.py:1822``) -- the **max across all matched entities**, never a
       sum.

    Args:
        query_entities: ``(entity_type, entity_text)`` pairs, in recognition
            order; anything past the eighth is dropped silently.
        search_entity_rows: ``(entity_type, entity_text) -> iterable of
            (similarity, payload)``. Stands in for
            ``entity_store.search(..., top_k=500, filters=search_filters)``.
            Each payload must supply ``linked_memory_ids``; a missing or
            non-list value skips the row like ``main.py:1812-1813``.
        scope_filter: the narrowed tenant filter, or ``None``.

    Returns:
        ``{memory_id: float}``; empty dict when nothing matched, which then
        makes ``has_entity`` false in :func:`score_and_rank`.
    """
    if not query_entities:
        return {}

    seen: set[str] = set()
    capped: list[tuple[str, str]] = []
    for entity_type, entity_text in query_entities[:QUERY_ENTITY_CAP]:
        key = normalize_entity_text(entity_text)
        if key in seen:
            continue
        seen.add(key)
        capped.append((entity_type, entity_text))

    memory_boosts: dict[str, float] = {}
    for entity_type, entity_text in capped:
        for similarity, payload in search_entity_rows(entity_type, entity_text):
            # ---- the gate comes FIRST, before any boost arithmetic ---------
            if not passes_entity_gate(similarity):
                continue  # main.py:1807 -- no boost, not even a partial one
            # ---- only now is a boost combined ------------------------------
            linked = payload.get("linked_memory_ids") if isinstance(payload, Mapping) else None
            if not isinstance(linked, list):
                continue  # main.py:1812-1813
            boost = entity_boost(similarity, len(linked))
            for memory_id in linked:
                # mem0 skips falsy ids rather than stringifying them. Without
                # this, ``None`` would become the key ``"None"`` and ``""`` the
                # key ``""``, inventing memories mem0 never boosts
                # (``main.py:1820`` ``if memory_id:`` / ``scoring.py:107``
                # ``if mem_id is None:``).
                if not memory_id:
                    continue  # main.py:1820
                prev = memory_boosts.get(str(memory_id), 0.0)
                memory_boosts[str(memory_id)] = max(prev, boost)  # main.py:1822
    return memory_boosts


# ---------------------------------------------------------------------------
# §4  Entity dedup / merge -- the only real graph consolidation
# ---------------------------------------------------------------------------


def normalize_entity_text(text: str) -> str:
    """Key used for the *exact* entity-match step.

    ``_existing_entities_by_text`` (def ``main.py:586``, dict built around
    ``main.py:610``) keys entity rows on normalised text, and
    ``_normalize_entity_text`` is reused to dedup the query entities at
    ``main.py:1762``. mem0's normalisation is a lowercase + whitespace collapse;
    reproduced here without leaning on spaCy, and deliberately without stemming
    (report §5 item 1 stresses that nothing in this path stems).
    """
    return " ".join(text.strip().lower().split())


def resolve_entity_candidate(
    candidate_text: str,
    existing_texts: Iterable[str],
    best_similar_existing: float | None,
) -> dict[str, Any]:
    """Decide what happens to a candidate entity at write time.

    Two steps, in this order -- ``_upsert_entity`` (``main.py:605``, docstring
    at ``:605``, steps at ``:610`` and ``:621``):

    1. **exact** -- normalised-text hit in the pre-built dict means "same node",
       unconditionally (``main.py:610``; the batch path states the precedence
       outright at ``main.py:1167``).
    2. **semantic** -- else ``existing[0].score >= 0.95`` merges
       (``main.py:621``; batch ``main.py:1166``).

    Merging is append-only: ``if memory_id not in linked_ids: linked_ids.append(
    memory_id)`` then ``update(vector_id=match.id, vector=None, payload=...)``
    (``main.py:623-631``); the batch path unions with ``linked |= memory_ids``
    (``main.py:1171-1173``). There is no subtraction on the write path, so a
    wrong 0.95 match is **permanent** -- no code path ever splits one entity row
    back into two (report §5, "What consolidating does NOT do").

    Args:
        candidate_text: the entity text being written.
        existing_texts: normalised texts of the already-stored rows in scope
            (the report's *10 Phase 1 hits*, ``main.py:1009-1013``).
        best_similar_existing: cosine of the nearest existing row, or ``None``
            when the store returned no neighbour at all.

    Returns:
        ``{"action": "exact_merge" | "semantic_merge" | "create",
        "similarity": float | None, "merged": bool}``.
    """
    key = normalize_entity_text(candidate_text)
    if any(normalize_entity_text(t) == key for t in existing_texts):
        return {"action": "exact_merge", "similarity": None, "merged": True}

    if best_similar_existing is not None and passes_dedup_gate(best_similar_existing):
        return {
            "action": "semantic_merge",
            "similarity": float(best_similar_existing),
            "merged": True,
        }

    return {
        "action": "create",
        "similarity": None if best_similar_existing is None else float(best_similar_existing),
        "merged": False,
    }


def merge_linked_memory_ids(existing: Iterable[Any], incoming: Iterable[Any]) -> list[str]:
    """Append-only union of an entity row's ``linked_memory_ids``.

    ``if memory_id not in linked_ids: linked_ids.append(memory_id)``
    (``main.py:623-631``) / batch ``linked |= memory_ids`` then
    ``payload["linked_memory_ids"] = sorted(linked)`` (``main.py:1171-1173``).
    Ids are only ever **added**; the adjacency list keeps every edge, which is
    what later feeds the count-decay dampener in
    :func:`memory_count_weight`.

    >>> merge_linked_memory_ids(["a", "b"], ["b", "c"])
    ['a', 'b', 'c']
    """
    return sorted({str(m) for m in existing} | {str(m) for m in incoming})


# ---------------------------------------------------------------------------
# §5  score_and_rank -- where the boost becomes a number
# ---------------------------------------------------------------------------


def rank_candidate(
    semantic_score: float,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    bm25_score: float = 0.0,
    entity_boost_value: float = 0.0,
    max_possible: float = 1.0,
) -> dict[str, Any]:
    """Score ONE candidate, gating **before** combining.

    ``mem0/utils/scoring.py:60-139``::

        max_possible = 1.0 + (1.0 if has_bm25 else 0.0) + (ENTITY_BOOST_WEIGHT if has_entity else 0.0)  # :97-101
        semantic_score = result.get("score") or 0.0
        if semantic_score < threshold:          # :111-112  <- the decisive gate
            continue
        raw_combined = semantic_score + bm25_score + entity_boost                # :118
        combined     = min(raw_combined / max_possible, 1.0)                     # :119

    **The gate is on the semantic score alone and it runs first.** mem0's own
    docstring says so (``scoring.py:74-75``): *"Threshold gates the semantic
    score BEFORE combining -- candidates below the threshold are excluded even
    if BM25/entity would boost them."* So a memory that the entity graph matched
    at similarity 1.0, with a perfect BM25 score, is still dropped if its cosine
    is 0.099. **The boost cannot rescue a candidate, only promote one that
    already cleared the bar.**

    Structurally, that ordering is *unmissable* here: the below-threshold branch
    returns ``gate_passed=False`` with its boost forced to ``0.0`` before any
    addition of ``bm25_score`` or ``entity_boost_value`` can occur.

    Args:
        semantic_score: the vector-store cosine for this candidate.
        threshold: gate value; ``search()`` default 0.1 (``main.py:1399``),
            read at ``scoring.py:111``.
        bm25_score: normalised BM25 for this candidate (``0.0`` if absent).
        entity_boost_value: this candidate's entry from the boost dict, or 0.0.
        max_possible: denominator from :func:`max_possible_score`.

    Returns:
        Dict with ``score``, ``gate_passed``, and the ``explain`` fields mem0
        attaches at ``scoring.py:126-135`` (``semantic_score``, ``bm25_score``,
        ``entity_boost``, ``raw_score``, ``max_possible_score``,
        ``final_score``, ``threshold``).
    """
    sem = float(semantic_score or 0.0)
    bm25 = float(bm25_score or 0.0)
    boost = float(entity_boost_value or 0.0)

    # ---- STEP 1: gate on semantics ALONE. Nothing is combined yet. ---------
    if sem < threshold:  # scoring.py:111-112
        return {
            "score": 0.0,
            "gate_passed": False,
            "semantic_score": sem,
            "bm25_score": bm25,
            "entity_boost": 0.0,  # NOT applied: it never got a chance to be
            "raw_score": 0.0,
            "max_possible_score": float(max_possible),
            "final_score": 0.0,
            "threshold": float(threshold),
        }

    # ---- STEP 2: only survivors are combined and normalised ---------------
    raw_combined = sem + bm25 + boost  # scoring.py:118
    final = min(raw_combined / float(max_possible), 1.0)  # scoring.py:119
    return {
        "score": final,
        "gate_passed": True,
        "semantic_score": sem,
        "bm25_score": bm25,
        "entity_boost": boost,
        "raw_score": raw_combined,
        "max_possible_score": float(max_possible),
        "final_score": final,
        "threshold": float(threshold),
    }


def score_and_rank(
    semantic_results: Sequence[Mapping[str, Any]],
    bm25_scores: Mapping[str, float] | None = None,
    entity_boosts: Mapping[str, float] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Full ranking pass: gate, combine, normalise, sort, truncate.

    ``mem0/utils/scoring.py:60-139``, invoked from ``_search_vector_store`` step
    8 (``main.py:1694-1701``, async ``3381-3389``). Sorting is
    ``scored.sort(key=lambda x: x["score"], reverse=True)`` then ``[:top_k]``
    (``scoring.py:138-139``).

    ``has_bm25 = bool(bm25_scores)`` / ``has_entity = bool(entity_boosts)``
    (``scoring.py:97-98``) are computed over the whole dicts, exactly as mem0
    does -- so passing an empty dict and passing ``None`` are the same thing, and
    an entity dict that is non-empty makes *every* surviving candidate's
    denominator 2.5 even if that one candidate got no boost.

    Note the entity store can only **reorder, never add**: mem0 builds
    ``candidates`` from ``semantic_results`` only (``main.py:1681-1691``), so a
    boosted memory that cosine search missed entirely is invisible -- the boost
    finds nothing to attach to. That is why this function iterates
    ``semantic_results`` and merely *looks up* boosts by id.

    Args:
        semantic_results: candidate dicts with ``id`` and ``score``.
        bm25_scores: ``{id: normalised_bm25}``; may be ``None`` (store lacks
            ``keyword_search``).
        entity_boosts: ``{id: boost}`` from :func:`compute_entity_boosts`.
        threshold: semantic gate, applied before combination.
        top_k: truncate to this many results; ``None`` keeps all.

    Returns:
        Ranked list, score descending, each entry carrying ``score_details``
        keys inline plus the original ``id``.
    """
    bm25_map = dict(bm25_scores or {})
    boost_map = dict(entity_boosts or {})
    has_bm25 = bool(bm25_map)  # scoring.py:97
    has_entity = bool(boost_map)  # scoring.py:98
    denominator = max_possible_score(has_bm25, has_entity)  # scoring.py:97-101

    scored: list[dict[str, Any]] = []
    for result in semantic_results:
        memory_id = str(result.get("id"))
        item = rank_candidate(
            result.get("score") or 0.0,
            threshold=threshold,
            bm25_score=bm25_map.get(memory_id, 0.0),
            entity_boost_value=boost_map.get(memory_id, 0.0),
            max_possible=denominator,
        )
        if not item["gate_passed"]:
            continue  # scoring.py:111-112 -- excluded even with a big boost
        item["id"] = memory_id
        scored.append(item)

    scored.sort(key=lambda x: x["score"], reverse=True)  # scoring.py:138
    return scored if top_k is None else scored[:top_k]  # scoring.py:139


# ---------------------------------------------------------------------------
# §6  Pool sizing, caps, top_k -- the literals around the maths
# ---------------------------------------------------------------------------


def internal_search_limit(limit: int) -> int:
    """Semantic over-fetch: ``max(limit * 4, 60)``.

    ``main.py:1655`` (async ``main.py:3342``). The 4x is **headroom for
    reordering**: steps 5-8 can only reorder what step 3 returned, and a memory
    ranked 40th by cosine can outrank the top hit once BM25 and the entity boost
    land. The 60 floor protects a ``top_k=1..15`` caller from an empty
    reordering pool. Not configurable -- a literal; only the outer ``limit``
    (= caller ``top_k``, ``main.py:1477``) varies.

    >>> internal_search_limit(1), internal_search_limit(20), internal_search_limit(100)
    (60, 80, 400)
    """
    return max(int(limit) * POOL_MULTIPLIER, POOL_FLOOR)


def entity_search_top_k() -> int:
    """``top_k=500`` for the entity-store search -- ``main.py:1789``, async ``3469``.

    ``500`` because one query entity may have been merged into up to 500
    distinct entity rows by the cosine-0.95 write-time pass.
    """
    return ENTITY_SEARCH_TOP_K


def cap_query_entities(
    query_entities: Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Apply the ``[:8]`` cap -- ``main.py:1761``, async ``3437``.

    Entity nine and beyond are silently never looked up: no warning, no error,
    they simply do not participate in the boost.
    """
    return list(query_entities)[:QUERY_ENTITY_CAP]


def tenant_scope_filter(filters: Mapping[str, Any] | None) -> dict[str, Any]:
    """Narrow a caller filter to only the identity keys.

    ``search_filters`` is rebuilt from only ``("user_id", "agent_id", "run_id")``
    (``main.py:1770``), so an entity boost can never cross a tenant boundary even
    if the caller's filter dict carried something wider.
    """
    source = filters or {}
    return {
        key: source[key]
        for key in ("user_id", "agent_id", "run_id")
        if key in source
    }


# ---------------------------------------------------------------------------
# Self-test -- run ``python3 mem0_scoring.py``; exits 0 on success.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _checks = 0

    def check(condition: bool, label: str) -> None:
        global _checks
        _checks += 1
        if not condition:
            raise AssertionError(f"FAILED: {label}")
        print(f"  ok  {label}")

    print("§1 count decay -- three anchor values to 1e-12")
    check(memory_count_weight(1) == 1.0, "n=1 is exactly 1.0")
    check(
        abs(memory_count_weight(10) - 0.9250693802035153) < 1e-12,
        "n=10 == 0.9250693802035153",
    )
    check(
        abs(memory_count_weight(100) - 0.09258401999814832) < 1e-12,
        "n=100 == 0.09258401999814832",
    )
    check(memory_count_weight(0) == 1.0, "n=0 floors to 1.0 (max(n,1))")
    check(memory_count_weight(1) == memory_count_weight(0), "empty list is neutral")

    print("§2 BM25 band table -- exact boundaries 3,4,6,7,9,10,15,16")
    # (input, expected midpoint, expected steepness) -- scoring.py:31-40
    _bands = [
        (3, 5.0, 0.7),
        (4, 7.0, 0.6),
        (6, 7.0, 0.6),
        (7, 9.0, 0.5),
        (9, 9.0, 0.5),
        (10, 10.0, 0.5),
        (15, 10.0, 0.5),
        (16, 12.0, 0.5),
    ]
    for _n, _m, _k in _bands:
        check(
            bm25_confidence(_n) == (_m, _k) and get_bm25_params(_n) == (_m, _k),
            f"terms/score {_n} -> midpoint {_m}, steepness {_k}",
        )
    check(bm25_confidence(0) == (5.0, 0.7), "below the first band still takes it")
    check(normalize_bm25(None) == 0.0, "absent keyword_search -> 0.0 (base.py:83)")
    check(normalize_bm25(-2.0, 5.0, 0.7) == 0.0, "non-positive raw score dropped (1666-1673)")
    check(
        0.0 < normalize_bm25(5.0, 5.0, 0.7) < 1.0,
        "midpoint maps into (0,1) via scoring.py:54",
    )
    check(
        abs(normalize_bm25(5.0, 5.0, 0.7) - 0.5) < 1e-15,
        "raw == midpoint is exactly 0.5",
    )

    print("§3 boost ceiling, max_possible, and the gate's inclusivity")
    check(entity_boost(1.0, 1) == 0.5, "perfect similarity on 1 memory == 0.5")
    check(
        abs(entity_boost(1.0, 10) - 0.5 * 0.9250693802035153) < 1e-15,
        "n=10 dampens to 0.5 * count_weight",
    )
    check(max_possible_score(False, False) == 1.0, "no signals -> max_possible 1.0")
    check(max_possible_score(True, False) == 2.0, "bm25 only -> 2.0")
    check(max_possible_score(False, True) == 1.5, "entity only -> 1.5")
    check(max_possible_score(True, True) == 2.5, "both -> 2.5")
    check(passes_entity_gate(0.5), "match gate inclusive at exactly 0.5")
    check(not passes_entity_gate(0.4999999), "match gate rejects below 0.5")

    print("§4 dedup: accept at exactly 0.95, reject at 0.5")
    check(passes_dedup_gate(0.95), "dedup accepts exactly 0.95")
    check(not passes_dedup_gate(0.5), "dedup rejects exactly 0.5 (the *match* threshold)")
    check(not passes_dedup_gate(0.9499999), "dedup rejects just under 0.95")
    check(passes_dedup_gate(1.0), "dedup accepts a perfect match")
    check(
        passes_entity_gate(0.5) and not passes_dedup_gate(0.5),
        "0.5 boosts but never fuses -- two different thresholds",
    )
    _exact = resolve_entity_candidate("Alice Smith", ["alice  smith"], None)
    check(_exact["action"] == "exact_merge", "exact normalised text wins with no cosine")
    check(
        resolve_entity_candidate("Alice Smith", ["alice"], 0.95)["action"] == "semantic_merge",
        "0.95 neighbour merges",
    )
    check(
        resolve_entity_candidate("Alice Smith", ["alice"], 0.94)["action"] == "create",
        "0.94 neighbour creates a new row",
    )
    check(
        merge_linked_memory_ids(["a", "b"], ["b", "c"]) == ["a", "b", "c"],
        "linked ids union append-only (main.py:1171-1173)",
    )

    print("§5 gate-before-combination -- a below-threshold item gets NO boost")
    _below = rank_candidate(
        0.099, threshold=0.1, bm25_score=1.0, entity_boost_value=0.5, max_possible=2.5
    )
    check(_below["gate_passed"] is False, "0.099 < 0.1 is dropped")
    check(_below["entity_boost"] == 0.0, "the 0.5 boost was never applied")
    check(_below["score"] == 0.0, "a perfect BM25 + full boost cannot rescue it")
    # The counterfactual: if the boost had been added FIRST and gated after, this
    # candidate would have cleared the bar. mem0 does not do that.
    check(
        (0.099 + 1.0 + 0.5) >= 0.1,
        "counterfactual is above threshold, proving the ordering is decisive",
    )
    _above = rank_candidate(
        0.1, threshold=0.1, bm25_score=1.0, entity_boost_value=0.5, max_possible=2.5
    )
    check(_above["gate_passed"] is True, "exactly threshold passes (gate is strict <)")
    check(_above["entity_boost"] == 0.5, "survivor does receive the boost")
    check(abs(_above["score"] - min((0.1 + 1.0 + 0.5) / 2.5, 1.0)) < 1e-15, "combined/normalised")
    check(_above["raw_score"] == 1.6, "raw_combined == sem + bm25 + boost (scoring.py:118)")

    print("§5 score_and_rank end-to-end")
    _results = [
        {"id": "m1", "score": 0.8},
        {"id": "m2", "score": 0.05},  # below gate, holds the biggest boost
        {"id": "m3", "score": 0.6},
    ]
    _ranked = score_and_rank(
        _results,
        bm25_scores={"m1": 0.5},
        entity_boosts={"m1": 0.25, "m2": 0.5, "m3": 0.5},
        threshold=0.1,
        top_k=2,
    )
    check(_ranked[0]["id"] == "m1", "boosted + bm25 candidate ranks first")
    check(all(r["id"] != "m2" for r in _ranked), "boosted-but-gated m2 never appears")
    check(len(_ranked) == 2, "top_k truncation (scoring.py:139)")
    check(
        abs(_ranked[0]["max_possible_score"] - 2.5) < 1e-15,
        "non-empty boost dict -> denominator 2.5 for everyone",
    )
    check(
        [r["score"] for r in _ranked] == sorted([r["score"] for r in _ranked], reverse=True),
        "score descending (scoring.py:138)",
    )
    check(
        all(r["max_possible_score"] == 1.0 for r in score_and_rank(_results, threshold=0.1)),
        "no signals -> denominator 1.0",
    )

    print("§6 pool sizing, [:8] cap, top_k=500, tenant scoping")
    check(internal_search_limit(1) == 60, "max(1*4, 60) == 60 -- the floor")
    check(internal_search_limit(15) == 60, "max(15*4, 60) == 60 -- floor still wins")
    check(internal_search_limit(16) == 64, "16 is where the multiplier takes over")
    check(internal_search_limit(500) == 2000, "max(500*4, 60) == 2000")
    check(entity_search_top_k() == 500, "entity-store top_k == 500")
    _many = [(t, f"e{i}") for i, t in enumerate(["PERSON"] * 12)]
    check(len(cap_query_entities(_many)) == 8, "[:8] cap keeps exactly 8")
    check(
        compute_entity_boosts(_many, lambda t, e: []) == {},
        "empty entity rows -> {} -> has_entity False",
    )
    _dup = [("PERSON", "Alice"), ("PERSON", " alice "), ("ORG", "Acme")]
    _dupe_boosts = compute_entity_boosts(
        _dup, lambda t, e: [(1.0, {"linked_memory_ids": ["m1"]})]
    )
    check(
        _dupe_boosts == {"m1": 0.5},
        "duplicate query text collapses; max() keeps one 0.5, never 1.0",
    )
    check(
        compute_entity_boosts(
            [("PERSON", "Alice")], lambda t, e: [(0.5, {"linked_memory_ids": ["m9"]})]
        )
        == {"m9": 0.25},
        "similarity 0.5 at the gate -> boost 0.25 (not zero)",
    )
    check(
        compute_entity_boosts(
            [("PERSON", "Alice")], lambda t, e: [(0.49, {"linked_memory_ids": ["m9"]})]
        )
        == {},
        "similarity 0.49 -> row skipped entirely by the gate",
    )
    check(
        compute_entity_boosts(
            [("PERSON", "Alice")], lambda t, e: [(0.9, {"linked_memory_ids": "not-a-list"})]
        )
        == {},
        "non-list linked_memory_ids skipped (main.py:1812-1813)",
    )
    check(
        compute_entity_boosts(
            [("PERSON", "Alice"), ("ORG", "Acme")],
            lambda t, e: [
                (1.0, {"linked_memory_ids": ["m1"]}),
                (0.7, {"linked_memory_ids": ["m1"]}),
            ],
        )
        == {"m1": 0.5},
        "max across matched entities, never a sum (main.py:1822)",
    )
    check(
        compute_entity_boosts(
            [("PERSON", "Alice")], lambda t, e: [(1.0, {"linked_memory_ids": [None, ""]})]
        )
        == {},
        "falsy memory ids skipped, not stringified (main.py:1820 `if memory_id:`)",
    )
    check(
        tenant_scope_filter({"user_id": "u1", "created_at": "x", "agent_id": "a1"})
        == {"user_id": "u1", "agent_id": "a1"},
        "filters narrowed to user/agent/run (main.py:1770)",
    )

    # --- the two paths the first audit round's mutation testing left alive ----
    # Without a clamp assertion, deleting ``min(..., 1.0)`` at scoring.py:119
    # is undetectable: raw/max_possible can exceed 1.0 only when the caller
    # under-states max_possible, so force that case explicitly.
    check(
        max_possible_score(has_bm25=False, has_entity=False) == 1.0
        and rank_candidate(
            semantic_score=0.9,
            entity_boost_value=0.5,
            max_possible=1.0,  # deliberately under-stated
        )["final_score"]
        == 1.0,
        "min(raw/max_possible, 1.0) clamps -- an under-stated denominator cannot inflate past 1.0",
    )
    # ``search()`` gates at 0.1 by default (main.py:1399). A default of 0.0
    # would admit rows mem0 rejects, so assert the constant *and* its effect.
    check(
        DEFAULT_THRESHOLD == 0.1
        and rank_candidate(
            semantic_score=0.05, entity_boost_value=0.0, threshold=DEFAULT_THRESHOLD
        )["gate_passed"]
        is False,
        "default gate is 0.1, not 0.0 -- a 0.05 semantic score is rejected",
    )

    print(f"\nALL {_checks} CHECKS PASSED")
    raise SystemExit(0)
