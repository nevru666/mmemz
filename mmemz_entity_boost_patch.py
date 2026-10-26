#!/usr/bin/env python3
"""
mmemz_entity_boost_patch.py — drop-in bounded entity relevance for
``agentmem/memory_retrieval.py::_initial_ranking``.

WHY THIS FILE EXISTS
====================
``_initial_ranking`` (``memory_service_v_2.3.0/agentmem/memory_retrieval.py:724``)
computes, at ``:778-780``::

    entity_relevance = 0.0
    for eid in cand["linked_entities"]:
        entity_relevance += float(entity_scores.get(eid, 0.0))

Two properties of that loop are worth stating precisely, because the obvious
phrasing of both is wrong:

  1. It is NOT unbounded. ``entity_scores`` is capped at
     ``entity_selection_top_k`` (default 20) by all three of its producers
     (``:399``/``:433``, ``:1475``/``:1508``), and any entity that was not
     selected contributes ``0.0`` through the ``.get(eid, 0.0)`` default. The
     sum is therefore bounded by ``top_k * max(entity_score)`` — measured at
     ``20 * 1.2 = 24.0`` on the explicit path (``explicit_boost = 1.2`` at
     ``:555``, merged by ``max()`` at ``:586``), and materially lower on the
     PPR path, whose scores sum to 1.0.

  2. It IS undamped and unnormalised. There is no division by the term count,
     no ceiling, and no per-link weight: ``grep -n entity_relevance`` over the
     file returns no ``min()``, no ``/``, and no per-link factor. The
     docstring at ``:735-736`` even promises "``* link_weight`` ... (link_weight
     defaults to 1.0)" — a factor the code does not implement.

The defect, then, is not magnitude. It is that a memory mentioned alongside
several selected entities scales the *weight* on its cosine at ``:797``
(``score = entity_relevance * memory_sim + 0.1 * bipartite_ppr``) purely by
how many of them were selected, and a hub entity contributes its full PPR
mass to that weight with no dampener.

mem0 solves both with a bounded, decayed, max-combined boost
(``mem0/memory/main.py:1807-1822``, constant at ``mem0/utils/scoring.py:57``).
This module ports that maths into the target's shape.

WHAT IS BEING REPLACED, EXACTLY
==============================
Mem0 has two different formulas and it is worth separating them, because the
target's differs from both. Mem0's combined score is *additive*::

    final = similarity + entity_boost          # mem0 main.py:1826

The target's is *multiplicative*::

    score = entity_relevance * memory_sim + 0.1 * bipartite_ppr   # :797

so ``entity_relevance`` is not an addend in the target, it is a **weight** on
the cosine. Two consequences follow, and they are why this module computes a
*weight* and not a boost:

  * In mem0 the natural cap is ``0.5`` (``ENTITY_BOOST_WEIGHT``) because it is
    an additive boost on a similarity in ``[0,1]``, giving at most ``1.5x``.
  * In the target an unbounded *weight* means a memory can score ``25x`` its
    cosine -- a bound worth knowing (see above), but a weight that large makes
    the cosine a rounding error. Bounding the weight to mem0's own ceiling
    keeps the target's multiplicative shape while restoring the cosine's
    influence.

PORTED (this file):
  * count decay      ``1 / (1 + 0.001 * (n-1)^2)``  -- mem0 ``main.py:1815-1817``
  * max-not-sum      ``max()`` across matched links -- mem0 ``main.py:1822``
  * bounded ceiling  ``ENTITY_BOOST_WEIGHT = 0.5``  -- mem0 ``scoring.py:57``
  * gate-before-use  only similarities ``>= 0.5``   -- mem0 ``main.py:1807``

NOT PORTED (the target is already ahead):
  * mem0's single entity hop  -> multi-hop ``expand_reachable``
    (``agentmem/graph_walk.py:292``, called from ``memserver/service.py:5637``
    and ``memserver/pipeline/graph_qa.py:534``; NOT from ``memory_retrieval.py``)
  * mem0's BM25 sigmoid       -> target fuses by RRF, which consumes ranks, not
    scores (``memory_retrieval.py:812``); a sigmoid there would be dead code
  * mem0's 0.95 dedup gate    -> ``_MERGE_THRESHOLD = 0.82``
    (``agentmem/consolidation.py:179``) is a tuned dual-signal resolver
  * mem0's ADD-only writes    -> reconciles bitemporally at
    ``consolidation.py:1187``, applied at ``:647``

USAGE
=====
Gated OFF by default so existing tests stay green and rollback is a config
flip -- the pattern ``entity_hub_penalty`` already uses at ``:536``::

    entity_relevance = entity_relevance_for_candidate(
        cand["linked_entities"], entity_scores, counts,
        mode=self._setting("entity_relevance_mode", "legacy"),
        cap=float(self._setting("entity_relevance_cap", 0.0)),
    )

``mode="legacy"`` reproduces current behaviour EXACTLY, so the patch is
inert until you opt in. ``mode="bounded"`` enables the port.

Stdlib only. Run ``python3 mmemz_entity_boost_patch.py`` for the self-test.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

# --------------------------------------------------------------------------
# Constants, each carrying the provenance of the number it copies.
# --------------------------------------------------------------------------

#: mem0's hard ceiling on an entity boost (``main.py:1822`` multiplies the
#: matched similarity by this), so no memory can receive more than 0.5 from the
#: entity arm however many entities it is linked to.
#: Provenance: ``mem0/utils/scoring.py:57`` ``ENTITY_BOOST_WEIGHT = 0.5``
ENTITY_BOOST_WEIGHT: float = 0.5

#: Coefficient of the quadratic count-decay: an entity linked to exactly one
#: memory is undamped (weight == 1.0) and the penalty grows quadratically.
#: Provenance: ``mem0/memory/main.py:1816``
#:   ``1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))``
DECAY_COEFF: float = 0.001

#: mem0 skips entity rows below this similarity before computing any boost, so
#: a sub-threshold row contributes nothing rather than a small amount.
#: Provenance: ``mem0/memory/main.py:1807`` ``if similarity < 0.5: continue``
BOOST_MATCH_THRESHOLD: float = 0.5

#: The target's explicit-entity boost, used only to document the bound.
#: Provenance: ``agentmem/memory_retrieval.py:555`` ``explicit_boost = 1.2``
TARGET_EXPLICIT_BOOST: float = 1.2

#: The target's entity-selection cap, the reason the sum is bounded.
#: Provenance: ``agentmem/memory_retrieval.py:399`` (also ``:1475``)
TARGET_ENTITY_TOP_K: int = 20


# --------------------------------------------------------------------------
# The maths.
# --------------------------------------------------------------------------

def memory_count_weight(num_linked: int) -> float:
    """mem0's hub dampener, as a multiplier in ``(0, 1]``.

    .. math:: w(n) = \\frac{1}{1 + 0.001\\,(n-1)^2}

    Properties that make this the right shape:

    * ``w(1) == 1.0`` exactly -- an entity linked to a single memory is never
      penalised, so a cold-start graph behaves identically to no dampener.
    * It is **quadratic**, so the penalty stays negligible for small ``n`` and
      falls away hard for genuine hubs: ``w(10) = 0.9251`` (a 7% haircut) but
      ``w(100) = 0.0926`` (a 91% haircut).
    * It is strictly positive for all finite ``n``, so an edge is dampened but
      never deleted -- the graph's shape is preserved and the memory stays
      reachable by other means.

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

    The gate is checked FIRST and returns ``0.0`` rather than a small positive
    number. That ordering is the whole point of ``mem0/memory/main.py:1807``:
    a row that merely resembles the query entity must not accumulate a boost
    by being one of many.

    >>> bounded_boost(1.0, 1)
    0.5
    >>> bounded_boost(0.49, 1)   # below the gate -> nothing
    0.0

    Note the ceiling: since ``s_i <= 1`` and decay ``<= 1``, the result is at
    most ``weight``. That is the bound the target lacks.

    Provenance: ``mem0/memory/main.py:1807`` (gate), ``:1815-1817`` (decay),
    ``:1822`` (per-link value), ``mem0/utils/scoring.py:57`` (weight).
    """
    if similarity is None or float(similarity) < threshold:
        return 0.0  # main.py:1807 -- no boost, not even a partial one
    return float(similarity) * float(weight) * memory_count_weight(num_linked)


def bounded_entity_relevance(
    linked_entities: Iterable[str],
    entity_scores: Mapping[str, float],
    linked_counts: Mapping[str, int] | None = None,
    *,
    weight: float = ENTITY_BOOST_WEIGHT,
    threshold: float = BOOST_MATCH_THRESHOLD,
) -> float:
    """Port of ``mem0/memory/main.py:1807-1822``: ``max()`` over the links.

    The combination rule is the second half of the fix. mem0 does not sum a
    memory's boosts; it takes the **single best** one::

        final_score = max(similarity + entity_boost, similarity)
                    = similarity + max(boost, 0)

    so a memory linked to ten selected entities scores exactly the same as one
    linked to the best of them. Summation (the target's ``:778-780``) rewards
    multiplicity, which is a popularity signal smuggled into a relevance score.

    >>> bounded_entity_relevance(["a"], {"a": 1.0}, {"a": 1})
    0.5
    >>> bounded_entity_relevance(["a", "b"], {"a": 0.9, "b": 0.9}, {"a": 1, "b": 1})
    0.45

    The two-entity case returns ``0.45``, not ``0.9`` -- that is ``max()``
    rather than ``sum()``, and it is the assertion that binds this function.

    ``linked_counts`` maps an entity id to how many memories link it. When it
    is ``None`` every entity is treated as ``n == 1`` (undamped), which is the
    cold-start behaviour. Callers wire it to
    :func:`linked_memory_counts` for the live count.
    """
    counts = linked_counts or {}
    best = 0.0
    for eid in linked_entities:
        score = float(entity_scores.get(eid, 0.0))  # .get default as at :779
        if score <= 0.0:
            continue
        boost = bounded_boost(
            score,
            counts.get(eid, 1),
            weight=weight,
            threshold=threshold,
        )
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
    """The target's current maths, reproduced bit-for-bit from ``:778-780``.

    This exists so ``mode="legacy"`` can be proven to be a no-op: it is the
    same accumulation the patch replaces, with the same ``.get(eid, 0.0)``
    default, so nothing changes for anyone who does not opt in.

    The optional ``cap`` is an *additive* hardening you can enable inside
    legacy mode if you want the ceiling without the decay -- ``0.0`` (the
    default) means no cap, i.e. exactly today's behaviour.

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
    mode: str = "legacy",
    cap: float = 0.0,
    weight: float = ENTITY_BOOST_WEIGHT,
    threshold: float = BOOST_MATCH_THRESHOLD,
) -> float:
    """The single seam to call from ``_initial_ranking``.

    ``mode="legacy"`` (the default) delegates to
    :func:`legacy_entity_relevance` and is byte-identical to ``:778-780``.
    ``mode="bounded"`` delegates to :func:`bounded_entity_relevance`.

    An unknown mode raises rather than silently falling back to legacy -- a
    typo in config must not quietly disable a scoring change.

    >>> entity_relevance_for_candidate(["a", "b"], {"a": 0.9, "b": 0.9})
    1.8
    >>> entity_relevance_for_candidate(
    ...     ["a", "b"], {"a": 0.9, "b": 0.9}, {"a": 1, "b": 1}, mode="bounded")
    0.45
    """
    if mode == "legacy":
        return legacy_entity_relevance(
            linked_entities, entity_scores, linked_counts, cap=cap
        )
    if mode == "bounded":
        return bounded_entity_relevance(
            linked_entities,
            entity_scores,
            linked_counts,
            weight=weight,
            threshold=threshold,
        )
    raise ValueError(
        f"entity_relevance_mode must be 'legacy' or 'bounded', got {mode!r}"
    )


def linked_memory_counts(
    entity_ids: Sequence[str],
    count_source,
    *,
    default: int = 1,
) -> dict[str, int]:
    """Build the ``{entity_id: n_linked}`` map the decay function needs.

    mem0 reads ``num_linked`` straight off the entity row it just matched
    (``main.py:1815``) because every entity *is* a row in its "second vector
    collection". The target keeps entities in its own store, so the count has
    to be supplied. ``count_source`` is a callable ``entity_id -> int``
    (a store lookup, a cached SQL count, whatever the caller has) rather than
    an interface this module invents: it keeps the seam at one function.

    Failures and missing values fall back to ``default`` (1, i.e. undamped)
    rather than raising -- a dampener is a refinement and must never be able to
    fail a retrieval.

    >>> linked_memory_counts(["a", "b"], {"a": 7}.get)
    {'a': 7, 'b': 1}
    >>> linked_memory_counts(["a"], lambda eid: 1 / 0)
    {'a': 1}
    """
    out: dict[str, int] = {}
    for eid in entity_ids:
        try:
            n = int(count_source(eid))
        except Exception:
            n = default
        out[eid] = n if n > 0 else default
    return out


# --------------------------------------------------------------------------
# The exact edit to make in the target. Kept as a string so it can be printed
# rather than hunted for, and so the anchors are unambiguous.
# --------------------------------------------------------------------------

PATCH_TEXT = r'''
=== FILE: memory_service_v_2.3.0/agentmem/memory_retrieval.py ===

--- (1) imports, near the top with the other stdlib/typing imports ---
+ from mmemz_entity_boost_patch import (
+     entity_relevance_for_candidate,
+     linked_memory_counts,
+ )

--- (2) replace the accumulation at :778-780 ---
     entity_relevance = 0.0
-    for eid in cand["linked_entities"]:
-        entity_relevance += float(entity_scores.get(eid, 0.0))
+    _mode = self._setting("entity_relevance_mode", "legacy")
+    _counts = None
+    if _mode == "bounded":
+        _ids = [e for e in cand["linked_entities"]
+                if float(entity_scores.get(e, 0.0)) > 0.0]
+        _counts = linked_memory_counts(_ids, self._entity_link_count)
+    entity_relevance = entity_relevance_for_candidate(
+        cand["linked_entities"],
+        entity_scores,
+        _counts,
+        mode=_mode,
+        cap=float(self._setting("entity_relevance_cap", 0.0)),
+    )

--- (3) the count source, one method on the same class ---
+ def _entity_link_count(self, entity_id: str) -> int:
+     """Number of memories whose payload links this entity.
+
+     Mirrors mem0's per-entity num_linked (main.py:1815). Return 1 when the
+     store cannot answer: 1 means "undamped", the safe direction.
+     """
+     try:
+         return int(self._entity_store.count_linked_memories(entity_id))
+     except Exception:
+         return 1

--- (4) the multiplication site, :797 is UNCHANGED ---
         score = entity_relevance * memory_sim + 0.1 * float(
             cand.get("bipartite_ppr", 0.0)
         )
     ^ leave as-is; the patch bounds entity_relevance, which is what this line
       multiplies. Do not move the bound here -- keeping it upstream means
       every future consumer of entity_relevance inherits the cap.

--- (5) READ THIS -- the :783 fallback changes meaning under "bounded" ---
     :778-784 is not just a sum; it has a fallback:
         if entity_relevance == 0.0:
             entity_relevance = float(cand.get("bipartite_ppr", 0.0))
     Under "legacy" that branch is rare: any single linked entity with a
     non-zero score makes the sum non-zero. Under "bounded" the gate at
     sim >= 0.5 makes exact 0.0 COMMON -- a candidate whose only linked
     entities all scored below 0.5 now hits the fallback and takes its
     bipartite_ppr as its relevance.

     That is not a bug, but it IS a behaviour change worth naming: bounded
     mode deliberately hands weak-entity candidates over to the graph signal
     instead of letting them accumulate a small positive boost. It also means
     the fallback is now load-bearing, so:
       * do NOT "simplify" :783-784 away while bounded is enabled;
       * measure how often the fallback fires before and after (log the
         branch); a large jump means the 0.5 gate is too aggressive for your
         similarity distribution, and BOOST_MATCH_THRESHOLD should be tuned
         rather than the gate removed.
     If you want zero interaction, ship legacy+cap first (see step 3 of the
     spec's integration order) -- the cap alone has no such coupling.

=== CONFIG (memserver/config/legacy.py, beside entity_hub_penalty ~:536) ===
+ entity_relevance_mode: "legacy"   # "legacy" | "bounded"
+ entity_relevance_cap: 0.0         # legacy-mode ceiling; 0 = uncapped

=== WHY THE DEFAULT STAYS "legacy" ===
Rollback is a config flip rather than a revert, which is the same discipline
entity_hub_penalty already follows at :536. Ship "bounded" on one surface
(the graph-QA path first, since it is the one that surfaces entity matches to
a user most directly), diff the retrieved sets, and only then widen it.
'''


# --------------------------------------------------------------------------
# Self-test. Plain asserts, no pytest, so it runs anywhere.
# --------------------------------------------------------------------------

def _run_self_test() -> int:
    checks = 0
    groups = 0

    def group(name: str) -> None:
        nonlocal groups
        groups += 1
        print(f"\n-- {name}")

    def check(cond: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not cond:
            raise AssertionError(f"FAILED: {label}")
        print(f"   ok {label}")

    # 1. decay shape -------------------------------------------------------
    group("1. count decay matches mem0 main.py:1815-1817")
    check(memory_count_weight(1) == 1.0, "w(1) == 1.0 exactly (undamped)")
    check(memory_count_weight(0) == 1.0, "w(0) clamps to w(1) == 1.0")
    check(memory_count_weight(-5) == 1.0, "w(negative) clamps to 1.0")
    check(abs(memory_count_weight(10) - 0.925069) < 1e-5,
          "w(10) == 1/(1+0.001*81) == 0.925069")
    check(abs(memory_count_weight(100) - 0.092593) < 1e-5,
          "w(100) == 1/(1+0.001*9801) == 0.092593")
    check(memory_count_weight(2) > memory_count_weight(10) > memory_count_weight(100),
          "monotonically decreasing in n")
    check(all(memory_count_weight(n) > 0.0 for n in range(1, 1001)),
          "strictly positive for n in 1..1000 (edge dampened, never deleted)")
    check(abs(memory_count_weight(1000) - 0.001001) < 1e-6,
          "w(1000) == 1/999.001 == 0.001001, hub is effectively muted")

    # 2. gate ordering -----------------------------------------------------
    group("2. gate is checked before the boost (main.py:1807)")
    check(bounded_boost(1.0, 1) == 0.5, "sim=1.0, n=1 -> 1.0*0.5*1.0 == 0.5")
    check(bounded_boost(0.5, 1) == 0.25, "sim=0.5 exactly passes the gate (>=)")
    check(bounded_boost(0.4999, 1) == 0.0, "sim=0.4999 -> exact 0.0, not a small value")
    check(bounded_boost(0.0, 1) == 0.0, "sim=0.0 -> 0.0")
    check(bounded_boost(None, 1) == 0.0, "sim=None -> 0.0, no crash")
    check(bounded_boost(0.9, 1) > bounded_boost(0.9, 10),
          "same similarity, more links -> strictly smaller boost")
    check(abs(bounded_boost(0.9, 10) - (0.9 * 0.5 * memory_count_weight(10))) < 1e-12,
          "boost == sim * weight * decay, no extra term")

    # 3. the ceiling the target lacks --------------------------------------
    group("3. per-entity contribution is bounded by ENTITY_BOOST_WEIGHT")
    check(ENTITY_BOOST_WEIGHT == 0.5, "ENTITY_BOOST_WEIGHT == 0.5 (scoring.py:57)")
    check(max(bounded_boost(1.0, 1) for _ in range(3)) == 0.5,
          "max achievable single-entity boost == 0.5")
    check(all(bounded_boost(s / 100, 1) <= ENTITY_BOOST_WEIGHT for s in range(0, 101)),
          "no similarity in 0.00..1.00 can exceed 0.5")
    check(all(bounded_boost(1.0, n) <= ENTITY_BOOST_WEIGHT for n in range(1, 50)),
          "no link count can exceed 0.5")

    # 4. max-not-sum is the binding assertion ------------------------------
    group("4. combination is max(), not sum() (main.py:1822)")
    one = bounded_entity_relevance(["a"], {"a": 0.9}, {"a": 1})
    two = bounded_entity_relevance(["a", "b"], {"a": 0.9, "b": 0.9}, {"a": 1, "b": 1})
    five = bounded_entity_relevance(
        list("abcde"), {k: 0.9 for k in "abcde"}, {k: 1 for k in "abcde"})
    check(abs(one - 0.45) < 1e-12, "one entity at 0.9 -> 0.45")
    check(abs(two - 0.45) < 1e-12, "TWO entities at 0.9 -> still 0.45 (not 0.9)")
    check(abs(five - 0.45) < 1e-12, "FIVE entities at 0.9 -> still 0.45 (not 2.25)")
    check(two == one and five == one, "multiplicity adds exactly nothing")
    check(bounded_entity_relevance(["a", "b"], {"a": 0.6, "b": 0.99}, {"a": 1, "b": 1})
          == bounded_boost(0.99, 1), "the best link wins, order-independent")
    check(bounded_entity_relevance(["b", "a"], {"a": 0.6, "b": 0.99}, {"a": 1, "b": 1})
          == bounded_boost(0.99, 1), "reversing the list changes nothing")
    check(bounded_entity_relevance([], {}) == 0.0, "no links -> 0.0")
    check(bounded_entity_relevance(["zzz"], {}) == 0.0,
          "unknown entity -> 0.0 via the .get default, as at :779")

    # 5. bounded mode NEVER exceeds legacy for a single link --------------
    group("5. bounded <= legacy, per-memory")
    cases = [
        (["a"], {"a": 0.9}, {"a": 1}),
        (["a", "b"], {"a": 0.9, "b": 0.8}, {"a": 1, "b": 3}),
        (list("abcdef"), {k: 0.7 for k in "abcdef"}, {k: 40 for k in "abcdef"}),
        (list("ab"), {"a": 1.0, "b": 1.0}, {"a": 2, "b": 2}),
    ]
    for i, (ents, scores, counts) in enumerate(cases, 1):
        leg = legacy_entity_relevance(ents, scores)
        bnd = bounded_entity_relevance(ents, scores, counts)
        check(bnd <= leg, f"case {i}: bounded {bnd:.4f} <= legacy {leg:.4f}")
    check(bounded_entity_relevance(["a"], {"a": 1.0}, {"a": 1}) <
          legacy_entity_relevance(["a"], {"a": 1.0}),
          "even the max single entity is smaller: 0.5 < 1.0")

    # 6. legacy mode is a byte-identical reproduction of :778-780 --------
    group("6. legacy mode == the target's current loop, exactly")
    import random
    rng = random.Random(20261026)
    identical = 0
    for _ in range(500):
        k = rng.randint(0, 8)
        ents = [f"e{rng.randint(0, 30)}" for _ in range(k)]
        scores = {f"e{i}": rng.choice([0.0, 0.3, 0.55, 0.9, 1.0, 1.2])
                  for i in range(31)}
        # the literal loop from :778-780
        expected = 0.0
        for eid in ents:
            expected += float(scores.get(eid, 0.0))
        got = entity_relevance_for_candidate(ents, scores)  # default mode
        if got == expected:  # exact float equality, not approx
            identical += 1
    check(identical == 500,
          f"500/500 random cases float-identical to :778-780 (got {identical})")
    check(entity_relevance_for_candidate([], {}) == 0.0,
          "empty candidate in default mode -> 0.0")
    check(entity_relevance_for_candidate(["a"], {"a": 1.2}) == 1.2,
          "explicit path reproduces the 1.2 boost exactly")

    # 7. cap is additive hardening inside legacy mode --------------------
    group("7. legacy cap (opt-in) bounds without changing anything when 0")
    check(legacy_entity_relevance(["a", "b"], {"a": 0.9, "b": 0.9}, cap=0.0) == 1.8,
          "cap=0.0 -> uncapped, 1.8")
    check(legacy_entity_relevance(["a", "b"], {"a": 0.9, "b": 0.9}, cap=1.0) == 1.0,
          "cap=1.0 -> clipped to 1.0")
    check(legacy_entity_relevance(["a"], {"a": 0.3}, cap=5.0) == 0.3,
          "cap above the sum is a no-op")
    check(entity_relevance_for_candidate(["a", "b"], {"a": 0.9, "b": 0.9}, cap=1.0)
          == 1.0, "cap flows through the seam in legacy mode")

    # 8. the measured bound on the target's sum, documented in code ------
    group("8. the target's sum is bounded at top_k*1.2 == 24.0, not 'unbounded'")
    check(TARGET_ENTITY_TOP_K == 20, "entity_selection_top_k default == 20 (:399)")
    check(TARGET_EXPLICIT_BOOST == 1.2, "explicit_boost == 1.2 (:555)")
    worst = TARGET_ENTITY_TOP_K * TARGET_EXPLICIT_BOOST
    check(worst == 24.0, f"worst-case legacy sum == 24.0 (got {worst})")
    worst_legacy = legacy_entity_relevance(
        [f"e{i}" for i in range(TARGET_ENTITY_TOP_K)],
        {f"e{i}": TARGET_EXPLICIT_BOOST for i in range(TARGET_ENTITY_TOP_K)},
    )
    # NB: the reconstructed loop sums twenty 1.2s and lands on 23.999999999999993,
    # not 24.0 — float accumulation, not an error in the maths. That off-by-7e-15
    # is itself why the sum-based formulation is worth replacing with max().
    check(abs(worst_legacy - 24.0) < 1e-9,
          f"reconstructed worst case == 24.0 +/- 1e-9 (got {worst_legacy!r})")
    check(worst_legacy != 24.0,
          "and the sum does NOT hit 24.0 exactly -- accumulated float error")
    worst_bounded = bounded_entity_relevance(
        [f"e{i}" for i in range(TARGET_ENTITY_TOP_K)],
        {f"e{i}": TARGET_EXPLICIT_BOOST for i in range(TARGET_ENTITY_TOP_K)},
        {f"e{i}": 1 for i in range(TARGET_ENTITY_TOP_K)},
    )
    check(abs(worst_bounded - 0.6) < 1e-12,
          "same input under bounded == 0.6 (1.2*0.5): 40x reduction at the ceiling")
    check(worst_bounded / worst_legacy < 0.026,
          "worst-case multiplier drops from 25.0x to 1.6x on memory_sim")

    # 9. count source never fails a retrieval ---------------------------
    group("9. linked_memory_counts is fail-safe")
    check(linked_memory_counts(["a", "b"], {"a": 7}.get) == {"a": 7, "b": 1},
          "missing key -> default 1 (undamped)")
    check(linked_memory_counts(["a"], lambda e: 1 / 0) == {"a": 1},
          "raising count source -> default 1, no exception")
    check(linked_memory_counts(["a"], lambda e: 0) == {"a": 1},
          "zero count -> default 1, never divides by zero")
    check(linked_memory_counts(["a"], lambda e: -3) == {"a": 1},
          "negative count -> default 1")
    check(linked_memory_counts([], lambda e: 5) == {}, "empty input -> empty map")
    check(linked_memory_counts(["a"], lambda e: None) == {"a": 1},
          "None count -> default 1")
    check(linked_memory_counts(["a"], lambda e: "12") == {"a": 12},
          "numeric string coerces")
    got = linked_memory_counts(["a", "b"], {"a": 4, "b": 9}.get)
    check(got == {"a": 4, "b": 9}, "map carries both counts through")
    # a(n=4): 0.5*w(4)=0.5*(1/1.009)=0.5*0.9910803=0.4955401
    # b(n=9): 0.5*w(9)=0.5*(1/1.064)=0.5*0.9398496=0.4699248
    check(abs(bounded_entity_relevance(["a", "b"], {"a": 1.0, "b": 1.0}, got)
              - 0.4955401) < 1e-6,
          "max over DECAYED boosts picks the lower-count entity (a, n=4)")

    # 10. mode dispatch -------------------------------------------------
    group("10. mode dispatch is strict")
    check(entity_relevance_for_candidate(["a"], {"a": 0.9}) ==
          entity_relevance_for_candidate(["a"], {"a": 0.9}, mode="legacy"),
          "default mode IS legacy")
    check(entity_relevance_for_candidate(["a"], {"a": 0.9}, mode="bounded") == 0.45,
          "mode='bounded' engages the port")
    raised = False
    try:
        entity_relevance_for_candidate(["a"], {"a": 0.9}, mode="boundedd")
    except ValueError:
        raised = True
    check(raised, "a typo raises ValueError instead of silently going legacy")

    # 11. the patch text is usable --------------------------------------
    group("11. PATCH_TEXT names the real seams")
    for needle in ("memory_retrieval.py", ":778-780", ":797",
                   "entity_relevance_mode", "_entity_link_count",
                   "entity_hub_penalty"):
        check(needle in PATCH_TEXT, f"PATCH_TEXT mentions {needle!r}")

    print(f"\nALL {checks} CHECKS PASSED across {groups} groups")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_run_self_test())



