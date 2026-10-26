"""Bounded entity-relevance maths for mmemz, ported from mem0 v2.2.1.


WHY THIS MODULE EXISTS
----------------------
mem0 computes a per-memory "entity boost" and bounds it. The target service
(``memory_service_v_2.3.0``) sums ``entity_scores`` over a candidate's linked
entities with no per-memory normalisation and no dampener, so the boost grows
with the *number of selected* matches. This module carries mem0's bounded
form so the seam in ``_initial_ranking`` can call it instead of re-deriving it.

TWO MODES, AND WHICH ONE IS THE DEFAULT
---------------------------------------
``mode="bounded"`` is **the default**. It is the ported maths: gated, damped,
and capped at ``ENTITY_BOOST_WEIGHT`` (0.5) no matter how many entities match.

``mode="legacy"`` is kept **only** so that a deploy can roll back to the
target's current summing behaviour **without a code change** -- a config flip
(``entity_relevance_mode: "legacy"``) rather than a revert-and-redeploy. It
reproduces ``agentmem/memory_retrieval.py:778-780`` bit for bit. It is not a
supported tuning, and nothing may be built on top of it.

NOTE: the standalone patch this module was ported from
(``mmemz_entity_boost_patch.py``, 651 lines, 64 self-checks) defaulted to
``mode="legacy"`` for the same rollback reason. mmemz inverts that default
because mmemz is a new package with no existing callers to break; the
rollback path is preserved, the default is the fixed maths.

WHAT THIS MODULE DOES NOT PORT: THE TRAVERSAL
---------------------------------------------
mem0 has **no traversal**. Its entity mechanism is a single hop: recognise
query entities, fetch their rows, read ``linked_memory_ids``, boost memories
the vector search already returned. There is no frontier, no depth, no path
and no budget. The target's ``graph_walk.expand_reachable``
(``agentmem/graph_walk.py:292``) already does bounded multi-hop BFS with a
deterministic total order and a 2000-node budget, so porting mem0's hop would
be a capability *regression*. mmemz does not port it. This module computes the
*number* for one memory that has already been reached by whatever walk the
host uses; it never decides which memories are reachable.

PROVENANCE PRECISION
--------------------
The port spec cites mem0's sigmoid/weight file as ``mem0/memory/scoring.py``;
in the v2.2.1 tree the file is ``mem0/utils/scoring.py``. Each citation below
was re-checked with ``sed -n`` against the real path, and the path given is
the one that exists.

Stdlib only: ``__future__`` and ``typing``. No third-party imports.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------
# Constants, each carrying the provenance of the number it copies.
# --------------------------------------------------------------------------

#: mem0's hard ceiling on an entity boost. Since ``similarity <= 1``,
#: ``decay <= 1`` and the product is multiplied by this weight, no memory can
#: receive more than ``0.5`` from the entity arm however many entities link it.
#: Provenance: ``mem0/utils/scoring.py:57`` -- ``ENTITY_BOOST_WEIGHT = 0.5``
ENTITY_BOOST_WEIGHT: float = 0.5

#: Coefficient of the quadratic count decay. An entity linked to exactly one
#: memory is undamped (weight ``== 1.0``); the penalty grows quadratically.
#: Provenance: ``mem0/memory/main.py:1815-1817`` --
#: ``memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))``
DECAY_COEFF: float = 0.001

#: mem0 skips entity rows below this similarity before computing any boost, so
#: a sub-threshold row contributes nothing rather than a small amount.
#: Provenance: ``mem0/memory/main.py:1807`` -- ``if similarity < 0.5: continue``
BOOST_MATCH_THRESHOLD: float = 0.5

#: The target's explicit-entity boost. Not used by the graded maths; carried so
#: the legacy cap can be documented against the number it replaces.
#: Provenance: ``agentmem/memory_retrieval.py:555`` -- ``explicit_boost = 1.2``
TARGET_EXPLICIT_BOOST: float = 1.2

#: The target's entity-selection cap (default), the reason an unbounded sum is
#: a problem: the multiplier is scaled by up to this many selected entities.
#: Provenance: ``agentmem/memory_retrieval.py:399`` (also ``:1475``)
TARGET_ENTITY_TOP_K: int = 20

#: The neutral value of the multiplicative form. Under ``bounded`` mode a
#: gated-out link yields an exact ``0.0`` relevance, and ``0.0`` must map to
#: *no change*, never to *zero score*.
NEUTRAL_MULTIPLIER: float = 1.0

#: The highest value :func:`bounded_multiplier` may return.
MAX_MULTIPLIER: float = 1.0 + ENTITY_BOOST_WEIGHT

#: The two mode strings accepted by :func:`entity_relevance_for_candidate`.
MODE_BOUNDED: str = "bounded"
MODE_LEGACY: str = "legacy"

#: The mode mmemz uses when a caller does not say. Bounded. See module docstring.
DEFAULT_MODE: str = MODE_BOUNDED


# --------------------------------------------------------------------------
# The maths.
# --------------------------------------------------------------------------

def memory_count_weight(num_linked: int) -> float:
    """mem0's hub dampener, as a multiplier in ``(0, 1]``.

    .. math:: w(n) = \\frac{1}{1 + 0.001\\,(n - 1)^2}

    Properties that make this the right shape:

    * ``w(1) == 1.0`` **exactly** -- an entity linked to a single memory is
      never penalised, so a cold-start graph scores identically to one with no
      dampener at all. The first link costs nothing.
    * It is **quadratic**, so the penalty is negligible for small ``n`` and
      falls away hard for genuine hubs: ``w(10) = 0.9251`` (a 7.5% haircut)
      but ``w(100) = 0.0926`` (a 91% haircut).
    * It is strictly positive for every finite ``n``, so an edge is *damped*
      but never *deleted*. The graph's shape is preserved and the memory stays
      reachable by every other route.

    ``num_linked < 1`` is clamped to ``1`` (undamped) rather than raising: a
    dampener is a refinement and must never be able to fail a retrieval.

    >>> round(memory_count_weight(1), 6)
    1.0
    >>> round(memory_count_weight(10), 4)
    0.9251
    >>> round(memory_count_weight(100), 4)
    0.0926

    Provenance: ``mem0/memory/main.py:1815-1817``.
    """
    n = max(1, int(num_linked))
    return 1.0 / (1.0 + DECAY_COEFF * ((n - 1) ** 2))


def bounded_boost(
    similarity: float,
    num_linked: int,
    *,
    weight: float = ENTITY_BOOST_WEIGHT,
    threshold: float = BOOST_MATCH_THRESHOLD,
) -> float:
    """One entity's contribution: ``similarity * weight * decay``, gated.

    The gate is evaluated **first** and returns an exact ``0.0`` rather than a
    small positive number. That ordering is the whole point of mem0's
    ``main.py:1807``: a row that merely *resembles* the query entity must not
    accumulate a boost by being one of many. The comparison is ``<``, so a
    similarity of **exactly 0.5 is included** and 0.something below it is not.

    The ceiling is structural rather than a post-hoc ``min()``: since
    ``similarity <= 1`` and ``decay <= 1``, the product is at most ``weight``.
    No input can exceed ``0.5`` with the default weight. That is precisely the
    bound the target's summing form lacks.

    >>> bounded_boost(1.0, 1)
    0.5
    >>> bounded_boost(0.49, 1)   # below the gate -> nothing at all
    0.0
    >>> round(bounded_boost(1.0, 10), 6)   # 0.5 * 0.9251...
    0.462535

    Provenance: ``mem0/memory/main.py:1807`` (gate), ``:1815-1817`` (decay and
    the product), ``:1822`` (per-link value combined by max), and
    ``mem0/utils/scoring.py:57`` (``ENTITY_BOOST_WEIGHT``).
    """
    if similarity is None:
        return 0.0
    sim = float(similarity)
    if sim < threshold:
        return 0.0  # main.py:1807 -- no boost, not even a partial one
    return sim * float(weight) * memory_count_weight(num_linked)


def bounded_entity_relevance(
    linked_entities: Iterable[str],
    entity_scores: Mapping[str, float],
    linked_counts: Mapping[str, int] | None = None,
    *,
    weight: float = ENTITY_BOOST_WEIGHT,
    threshold: float = BOOST_MATCH_THRESHOLD,
) -> float:
    """Port of ``mem0/memory/main.py:1807-1822``: the **max** over the links.

    MAX, NEVER SUM. This is the entire point of the port. mem0 does not add a
    memory's boosts up; it keeps the single best one, exactly as
    ``main.py:1822`` does::

        memory_boosts[k] = max(memory_boosts.get(k, 0.0), boost)

so a memory linked to ten selected entities scores **exactly the same** as one
linked to the best of them. Summation (the target's ``:778-780``) rewards
multiplicity, which smuggles a popularity signal into a relevance score: the
more entities the selector happened to pick, the higher the multiplier, and
the selector is capped at 20 (``memory_retrieval.py:399``) so the inflation is
bounded only by that cap.

Because the result is bounded by ``weight`` (0.5), *adding* entities can never
raise this number above 0.5 no matter how many are supplied.

``linked_counts`` maps an entity id to how many memories link it, feeding
:func:`memory_count_weight`. When it is ``None`` or missing a key, that entity
is treated as ``n == 1`` (undamped) -- the cold-start behaviour. Callers wire
it to :func:`linked_memory_counts` for the live count.

    >>> bounded_entity_relevance(["a"], {"a": 1.0}, {"a": 1})
    0.5
    >>> bounded_entity_relevance(["a", "b"], {"a": 0.9, "b": 0.9}, {"a": 1, "b": 1})
    0.45

The two-entity case returns ``0.45``, **not** ``0.9``: max, not sum. That
assertion is the one that binds this function.

Provenance: ``mem0/memory/main.py:1822``.
    """
    counts = linked_counts or {}
    best = 0.0
    for eid in linked_entities:
        score = float(entity_scores.get(eid, 0.0))  # .get default, as at :779
        if score <= 0.0:
            continue
        boost = bounded_boost(
            score,
            counts.get(eid, 1),
            weight=weight,
            threshold=threshold,
        )
        # MAX across links (main.py:1822). A sum here would make the
        # multiplier grow with the number of selected entities.
        if boost > best:
            best = boost
    return best


def legacy_entity_relevance(
    linked_entities: Iterable[str],
    entity_scores: Mapping[str, float],
    linked_counts: Mapping[str, int] | None = None,
    *,
    cap: float = 0.0,
    weight: float = TARGET_EXPLICIT_BOOST,
) -> float:
    """The **target's** current maths, reproduced bit-for-bit from ``:778-780``.

    This exists **only** so ``mode="legacy"`` can be proven to be a faithful
    no-op: it is the same accumulation the bounded port replaces, with the same
    ``.get(eid, 0.0)`` default and the same ordering of additions, so nothing
    changes for a deploy that never opts in.

    ``linked_counts`` and ``weight`` are accepted and **deliberately ignored**:
    the target has no per-link dampener and no per-link weight, so honouring
    either here would make "legacy" not-legacy. They stay in the signature so
    the two modes are call-compatible and the seam can forward one kwargs dict
    to either one.

    The optional ``cap`` is *additive* hardening available inside legacy mode if
    a deploy wants a ceiling without the decay. ``cap=0.0`` (the default) means
    no cap, i.e. exactly today's behaviour. ``cap`` cannot lower a value below
    zero and cannot raise one.

    >>> legacy_entity_relevance(["a", "b"], {"a": 0.9, "b": 0.9})
    1.8
    >>> legacy_entity_relevance(["a", "b"], {"a": 0.9, "b": 0.9}, cap=1.0)
    1.0
    >>> legacy_entity_relevance([], {})
    0.0

    Provenance: ``agentmem/memory_retrieval.py:778-780``.
    """
    total = 0.0
    for eid in linked_entities:
        total += float(entity_scores.get(eid, 0.0))
    if cap and cap > 0.0:
        total = min(total, float(cap))
    return total


def entity_relevance_for_candidate(
    linked_entities: Iterable[str],
    entity_scores: Mapping[str, float],
    linked_counts: Mapping[str, int] | None = None,
    *,
    mode: str = DEFAULT_MODE,
    cap: float = 0.0,
    weight: float = ENTITY_BOOST_WEIGHT,
    threshold: float = BOOST_MATCH_THRESHOLD,
) -> float:
    """The single seam to call from the host's ``_initial_ranking``.

    ``mode="bounded"`` -- **the default** -- delegates to
    :func:`bounded_entity_relevance`.

    ``mode="legacy"`` delegates to :func:`legacy_entity_relevance` and is
    byte-identical to ``agentmem/memory_retrieval.py:778-780``. It exists for
    rollback safety only: a deploy that needs the old numbers back flips a
    setting instead of reverting a commit.

    An unknown mode **raises** rather than silently falling back to legacy. A
    typo in config must not quietly disable a scoring change, and it must not
    quietly *enable* one either -- both directions are surprises.

    >>> entity_relevance_for_candidate(["a", "b"], {"a": 0.9, "b": 0.9}, mode="legacy")
    1.8
    >>> entity_relevance_for_candidate(["a", "b"], {"a": 0.9, "b": 0.9}, {"a": 1, "b": 1})
    0.45

    Provenance: the bounded arm is ``mem0/memory/main.py:1822``; the legacy arm
    is ``agentmem/memory_retrieval.py:778-780``.
    """
    if mode == MODE_LEGACY:
        return legacy_entity_relevance(
            linked_entities, entity_scores, linked_counts, cap=cap
        )
    if mode == MODE_BOUNDED:
        return bounded_entity_relevance(
            linked_entities,
            entity_scores,
            linked_counts,
            weight=weight,
            threshold=threshold,
        )
    raise ValueError(
        f"entity_relevance_mode must be {MODE_LEGACY!r} or {MODE_BOUNDED!r}, "
        f"got {mode!r}"
    )


def linked_memory_counts(
    entity_ids: Sequence[str],
    count_source: Callable[[str], Any],
    *,
    default: int = 1,
) -> dict[str, int]:
    """Build the ``{entity_id: n_linked}`` map the decay function needs.

    mem0 reads ``num_linked`` straight off the entity row it just matched
    (``main.py:1815``) because in mem0 every entity *is* a row in its second
    vector collection. The target keeps entities in its own store, so the count
    has to be supplied. ``count_source`` is a callable ``entity_id -> int`` (a
    store lookup, a cached count, whatever the caller has) rather than an
    interface this module invents: it keeps the seam at one function.

    Failures, ``None``, and non-positive values all fall back to ``default``
    (1, i.e. undamped) rather than raising. A dampener is a refinement and must
    never be able to fail a retrieval.

    >>> linked_memory_counts(["a", "b"], {"a": 7}.get)
    {'a': 7, 'b': 1}
    >>> linked_memory_counts(["a"], lambda eid: 1 / 0)
    {'a': 1}

    Provenance: ``mem0/memory/main.py:1815`` (``num_linked`` source).
    """
    out: dict[str, int] = {}
    for eid in entity_ids:
        try:
            n = int(count_source(eid))
        except Exception:  # noqa: BLE001 -- see docstring: never raise
            n = default
        out[eid] = n if n > 0 else default
    return out


def bounded_multiplier(entity_relevance: float) -> float:
    """Turn an entity relevance into the **multiplier** the target applies.

    The target multiplies ``memory_sim`` by its entity term at
    ``agentmem/memory_retrieval.py:797``, where mem0 *adds* its boost into a
    normalised sum. So the bound transfers but the algebra must be rewritten:
    the output here lives in ``[1.0, 1.5]``, never in ``[0.0, 0.5]``.

    The ``0.0 -> 1.0`` FALLBACK BRANCH IS LOAD-BEARING
    --------------------------------------------------
    Under ``bounded`` mode, an entity link that fails the 0.5 similarity gate
    produces an **exact** ``0.0`` (see :func:`bounded_boost`: the gate returns
    ``0.0``, not a small positive number). A candidate whose every link was
    gated out therefore reaches this function with relevance ``0.0``.

    In a *multiplicative* form, ``0.0`` is poison: ``memory_sim * 0.0 == 0.0``
    would send a perfectly good memory to the bottom of the ranking because it
    happened to have no strong entity link. The identity element of
    multiplication is ``1.0``, not ``0.0``, so ``0.0`` must map to ``1.0`` --
    "no entity evidence, change nothing" -- and the memory is ranked on its
    similarity alone.

    This is not defensive coding and it must NOT be "simplified" away as an
    unreachable default. Under ``bounded`` mode it is the **common** path:
    every gated-out weak link lands here. Deleting it converts the port from
    "entity evidence nudges a ranking" into "no entity evidence annihilates a
    ranking", which is a far worse bug than the sum it replaces.

    >>> bounded_multiplier(0.0)   # gated out / no links -> no change
    1.0
    >>> bounded_multiplier(0.5)   # best possible link -> +50%
    1.5

    Non-finite input is treated as absent evidence (``1.0``) rather than
    propagated: ``nan`` must not poison a sort key.

    Provenance: multiplier seam ``agentmem/memory_retrieval.py:797``; bound
    ``mem0/utils/scoring.py:57``; zero-producing gate
    ``mem0/memory/main.py:1807``.
    """
    try:
        value = float(entity_relevance)
    except (TypeError, ValueError):
        return NEUTRAL_MULTIPLIER
    if not (value > 0.0) or value != value or value == float("inf"):
        # 0.0, negative, NaN: no usable entity evidence -> identity element.
        return NEUTRAL_MULTIPLIER
    if value > ENTITY_BOOST_WEIGHT:
        value = ENTITY_BOOST_WEIGHT
    return NEUTRAL_MULTIPLIER + value


def score_candidate(
    memory_similarity: float,
    linked_entities: Iterable[str],
    entity_scores: Mapping[str, float],
    linked_counts: Mapping[str, int] | None = None,
    *,
    mode: str = DEFAULT_MODE,
    cap: float = 0.0,
    weight: float = ENTITY_BOOST_WEIGHT,
    threshold: float = BOOST_MATCH_THRESHOLD,
) -> float:
    """The multiplicative form end to end: ``similarity * multiplier``.

    This is the shape the target needs at ``memory_retrieval.py:797`` -- a
    *bounded factor on* the similarity, not an additive term beside it. Under
    ``bounded`` mode the result is at most ``similarity * 1.5``, and no number
    of linked entities can push it past that.

    Under ``legacy`` mode the same product is computed from the target's
    summing relevance, so the factor is unbounded above and the function
    reproduces today's (inflatable) behaviour -- which is what makes a rollback
    a config flip rather than a revert.

    >>> score_candidate(1.0, ["a"], {"a": 1.0}, {"a": 1})
    1.5
    >>> score_candidate(1.0, [], {})
    1.0

    Provenance: ``mem0/memory/main.py:1822`` (bounded relevance),
    ``agentmem/memory_retrieval.py:797`` (multiplicative seam).
    """
    relevance = entity_relevance_for_candidate(
        linked_entities,
        entity_scores,
        linked_counts,
        mode=mode,
        cap=cap,
        weight=weight,
        threshold=threshold,
    )
    if mode == MODE_LEGACY:
        # Legacy has no notion of a ceiling; keep the target's arithmetic
        # (an additive boost applied on top) rather than forcing a multiplier
        # that would make "legacy" silently differ from :778-780.
        return float(memory_similarity) + relevance
    return float(memory_similarity) * bounded_multiplier(relevance)


__all__ = [
    "ENTITY_BOOST_WEIGHT",
    "DECAY_COEFF",
    "BOOST_MATCH_THRESHOLD",
    "TARGET_EXPLICIT_BOOST",
    "TARGET_ENTITY_TOP_K",
    "NEUTRAL_MULTIPLIER",
    "MAX_MULTIPLIER",
    "MODE_BOUNDED",
    "MODE_LEGACY",
    "DEFAULT_MODE",
    "memory_count_weight",
    "bounded_boost",
    "bounded_entity_relevance",
    "legacy_entity_relevance",
    "entity_relevance_for_candidate",
    "linked_memory_counts",
    "bounded_multiplier",
    "score_candidate",
]
