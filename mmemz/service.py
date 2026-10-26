"""The six-phase memory service: recognize, inject, extract, consolidate,
return, reconstruct -- with the reconciliation phase mem0 does not have.


PHASE MAP
---------
Every phase is a method on :class:`MemoryService`. The mem0 column is the
behaviour this phase is measured against, cited by ``file:line`` in
``mem0/memory/main.py`` as verified in ``deliverables/mem0-port-spec.md``
(which read the source directly; mem0 is not vendored in this workspace).

====================  ==========================  ============================================
phase                 method                      mem0 evidence
====================  ==========================  ============================================
1. recognize          ``recognize``               ``add()`` extracts entities then dedupes them
                                                  against the entity collection (``:621`` fuzzy
                                                  lookup, exact branch first)
2. inject             ``inject``                  ``:1807-1822`` -- per-memory entity boost,
                                                  ``max`` over links at ``:1822``, gated on a
                                                  0.5 similarity threshold at ``:1807``
3. extract            ``extract``                 LLM returns a JSON fact list per memory
                                                  (``:1244`` copies ``attributed_to`` through)
4. consolidate        ``consolidate``             **ADD-only**: ``:1209-1212`` is the ADD branch
                                                  with no update/delete counterpart
5. return             ``return_entities``         retrieval returns a flat list of memories
                                                  with scores, no entity attribution
6. reconstruct        ``reconstruct``             nothing -- mem0 has no traversal at all; a
                                                  retrieval result is a row, not a path
====================  ==========================  ============================================

WHICH PHASE IS ALREADY-BEYOND mem0
----------------------------------
**consolidate is the already-beyond phase.** The target's ``_reconcile``
(``agentmem/consolidation.py:1187``, applied at ``:647``) supersedes an old fact
with a new one instead of appending beside it. mem0's ``:1209-1212`` is the ADD
branch of the LLM-decided ``ADD/UPDATE/DELETE/NOOP`` protocol, and it is the branch
it takes whenever the model says so -- there is no deterministic reconciler, and
nothing in the store stops a restated fact from becoming a second row. So mem0
grows its memory count on a *restatement*, which is the purest possible
false-positive for a count-based dampener.

:meth:`MemoryService.consolidate` ports the target's rule in the small: same
``(subject, predicate)`` with a different object supersedes, same triple is a
restatement, and **neither grows the memory count**. The old proposition is kept
with a ``superseded_by`` stamp rather than deleted, because the fact that a fact
changed is itself information.

WHICH PHASE IS THE PORTED WIN
-----------------------------
**inject is the ported win**: the *bounded max-of-links plus count decay*. mem0
takes ``max`` over the links (``:1822``) but the target *sums* them
(``memory_retrieval.py:778-780``), so multiplicity becomes a relevance signal and
two weak matches beat one strong one. mmemz calls
``mmemz.scoring.entity_relevance_for_candidate`` with
``mode="bounded"`` -- max, never sum -- and feeds it
``graph.entity_memory_count`` so a hub entity is damped by
``1/(1+0.001(n-1)^2)``. ``mode="legacy"`` reproduces the summing behaviour exactly,
which is what makes the regression a config flip rather than a revert.

WHY reconstruct IS BEYOND mem0 TOO
---------------------------------
mem0's retrieval returns a flat list of memories with scores. There is no path,
because there is no traversal: the entity hop is one edge, and
``linked_memory_ids`` is read off a row (``:1812``). :meth:`reconstruct` returns
the *route* -- an ordered list of hops naming the entities traversed, produced by
the vendored bounded BFS in ``mmemz.graph_walk`` -- so a caller can see not just
"this memory is related" but *through what*. That is row 5 of the must-have table
and it is strictly more than mem0 can express.

Stdlib only: ``dataclasses``, ``math``, ``typing``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import scoring
from .entities import (
    Entity,
    EntityStore,
    Proposition,
    extract_propositions,
    normalize_name,
)
from .graph import BipartiteGraph, GraphExpansion

__all__ = ["MemoryService", "StoredMemory", "SearchResult", "ConsolidationReport"]

@dataclass
class StoredMemory:
    """One memory: its text, its live propositions, its superseded history.

    ``propositions`` is the live set; ``superseded`` keeps what was replaced, with
    the ``superseded_by`` stamp. Keeping the history is what lets
    :meth:`MemoryService.consolidate` *not* grow the memory count: a change is a
    move between these two lists, never a new memory.
    """

    memory_id: str
    text: str
    propositions: list[Proposition] = field(default_factory=list)
    superseded: list[Proposition] = field(default_factory=list)
    attributed_to: str | None = None

    def live_triples(self) -> set[tuple[str, str, str]]:
        """The ``(subject, predicate, object)`` keys currently live."""
        return {(p.subject, p.predicate, p.object) for p in self.propositions}

    def live_for(self, subject: str, predicate: str) -> list[Proposition]:
        """Live propositions sharing a ``(subject, predicate)`` head."""
        return [
            p
            for p in self.propositions
            if p.subject == subject and p.predicate == predicate
        ]


@dataclass
class ConsolidationReport:
    """What :meth:`MemoryService.consolidate` did. Small on purpose.

    ``added`` / ``restated`` / ``superseded`` are lists of
    ``(subject, predicate, object)`` triples; ``memory_count_delta`` is always
    ``0`` by construction, and is reported rather than assumed so a test can
    assert it without reaching into the service.
    """

    memory_id: str
    added: list[tuple[str, str, str]] = field(default_factory=list)
    restated: list[tuple[str, str, str]] = field(default_factory=list)
    superseded: list[tuple[str, str, str]] = field(default_factory=list)
    memory_count_delta: int = 0

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "added": [list(t) for t in self.added],
            "restated": [list(t) for t in self.restated],
            "superseded": [list(t) for t in self.superseded],
            "memory_count_delta": self.memory_count_delta,
        }


@dataclass
class SearchResult:
    """One ranked memory, with the score **breakdown visible**.

    ``entity_relevance`` is the injected multiplier's input and ``multiplier`` its
    bounded output, so the arithmetic is inspectable rather than a single opaque
    number: ``score == similarity * multiplier`` under bounded mode, and
    ``similarity + relevance`` under legacy (which is why the two modes disagree
    and why the breakdown has to show which one ran).
    """

    memory_id: str
    text: str
    score: float
    similarity: float
    entity_relevance: float
    multiplier: float
    linked_entities: tuple[str, ...] = ()
    linked_counts: dict[str, int] = field(default_factory=dict)
    matched_entities: tuple[str, ...] = ()
    propositions: list[Proposition] = field(default_factory=list)
    mode: str = "bounded"

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "text": self.text,
            "score": round(self.score, 6),
            "similarity": round(self.similarity, 6),
            "entity_relevance": round(self.entity_relevance, 6),
            "multiplier": round(self.multiplier, 6),
            "linked_entities": list(self.linked_entities),
            "linked_counts": dict(self.linked_counts),
            "matched_entities": list(self.matched_entities),
            "propositions": [
                {"subject": p.subject, "predicate": p.predicate, "object": p.object}
                for p in self.propositions
            ],
            "mode": self.mode,
        }


#: Capitalised run: 1+ words, each starting uppercase, allowing interior
#: lowercase/&/-. Built once at import; no ``re`` backtracking risk because every
#: component is a bounded character class.
_PROPER_NOUN = re.compile(
    r"[A-Z][A-Za-z0-9'&\-]*(?:\s+(?:of|the|and|de|van|von)?\s*[A-Z][A-Za-z0-9'&\-]*)*"
)

#: Words that start a sentence or are pronouns, never an entity on their own.
_NOT_ENTITY = frozenset(
    {
        "i", "the", "a", "an", "who", "what", "when", "where", "why", "how",
        "which", "this", "that", "these", "those", "it", "he", "she", "they",
        "we", "you", "his", "her", "their", "our", "its", "my", "your",
        "is", "are", "was", "were", "and", "or", "but", "if", "then", "there",
    }
)


def _proper_noun_runs(text: str) -> list[str]:
    """Capitalised runs in ``text``, minus sentence-initial noise.

    The over-generation is deliberate and cheap to absorb: the store dedupes, and
    a spurious entity with no propositions is a leaf, not a false edge between two
    real memories. Under-generation is the failure that cannot be repaired later,
    because the memory is already stored without the edge.

    >>> _proper_noun_runs("Acme Corp hired Ada Lovelace in London.")
    ['Acme Corp', 'Ada Lovelace', 'London']
    """
    out: list[str] = []
    for match in _PROPER_NOUN.finditer(text):
        name = normalize_name(match.group(0))
        if not name or name in _NOT_ENTITY:
            continue
        if len(name) < 2:
            continue
        out.append(match.group(0))
    return out


#: Legal-form abbreviations expanded before resolution. mem0 gets this from its
#: LLM, which knows "Corp" and "Corporation" are the same word; a rule-based
#: recogniser has to be told, because the character-trigram cosine scores the pair
#: at 0.65 -- below ``MERGE_THRESHOLD`` (0.82), so fuzzy dedupe alone cannot merge
#: it. Expanding the abbreviation *before* the lookup makes the canonical forms
#: identical, which is exact-match dedupe and needs no threshold at all.
_LEGAL_FORMS: dict[str, str] = {
    "corp": "corporation",
    "co": "company",
    "inc": "incorporated",
    "ltd": "limited",
    "llc": "limited liability company",
    "plc": "public limited company",
}


def _expand_legal_forms(name: str) -> str:
    """Rewrite a trailing legal-form abbreviation to its full word.

    >>> _expand_legal_forms("ACME Corp.")
    'ACME Corporation.'
    >>> _expand_legal_forms("Zeta Partners")
    'Zeta Partners'
    """
    words = name.split()
    if not words:
        return name
    last = words[-1].rstrip(".")
    replacement = _LEGAL_FORMS.get(last.lower())
    if replacement is None:
        return name
    return " ".join([*words[:-1], replacement.capitalize()])


#: Function words that, if present, mean a "name" is really a phrase. A subject
#: span from the rule-based splitter can swallow a preposition ("to charles
#: babbage at acme corp"); storing that as an entity puts a junk node in the graph
#: and a junk edge on every count.
_PHRASE_WORDS = frozenset(
    {
        "to", "at", "in", "on", "for", "with", "from", "by", "of", "and", "the",
        "as", "that", "which", "who", "is", "are", "was", "were", "a", "an",
        "has", "have", "had", "into", "over", "under", "about", "after",
    }
)


def _plausible_name(name: str) -> bool:
    """Whether a subject/object span looks like a name rather than a phrase.

    Two words or fewer, and no function word, is the bar. It is deliberately
    crude: the alternative to a slightly-too-strict filter is a graph full of
    sentence fragments, and a fragment is worse than a miss because it dilutes
    every count it touches.

    >>> _plausible_name("Ada Lovelace")
    True
    >>> _plausible_name("to charles babbage at acme corp")
    False
    """
    words = normalize_name(name).split()
    if not words or len(words) > 3:
        return False
    return not any(w in _PHRASE_WORDS for w in words)


def _known_mentions(text: str, canonicals: list[str]) -> list[str]:
    """Canonical names from ``canonicals`` that occur in ``text``, any casing.

    The capitalisation pass above cannot see a *lowercase* mention, but mem0's
    LLM extractor is case-insensitive: ``"acme corp announced something"`` names
    the same row as ``"Acme Corp announced something"``. Without this pass a
    memory that mentions a known entity in running text would be stored with no
    edge to it -- and an edge missed at ingest cannot be recovered later.

    Deliberately a substring test on the *normalised* text against *already
    known* canonical names only: it cannot invent an entity, so it never grows
    the store from a query, and it costs one pass over the (small) known-name
    set rather than a regex sweep of the document.

    >>> _known_mentions("Did ACME Corporation call?", ["acme corporation"])
    ['acme corporation']
    >>> _known_mentions("nothing here", ["acme corporation"])
    []
    """
    if not canonicals:
        return []
    haystack = " " + normalize_name(text) + " "
    hits: list[str] = []
    for canon in canonicals:
        if not canon:
            continue
        # ``canon`` is stored with legal forms already expanded ("acme
        # corporation"), but running text may use the abbreviation ("acme
        # corp"). Match the canonical or any abbreviation of its final word, so
        # the same pass handles both spellings without a fuzzy threshold.
        variants = {canon}
        words = canon.split()
        if words:
            stem, last = words[:-1], words[-1]
            for short, full in _LEGAL_FORMS.items():
                if full == last:
                    variants.add(" ".join([*stem, short]))
        if any(f" {v} " in haystack for v in variants):
            hits.append(canon)
    return hits


class MemoryService:
    """All six phases over one :class:`BipartiteGraph` and one ``EntityStore``.

    >>> svc = MemoryService()
    >>> r = svc.remember("m1", "Acme Corp hired Ada Lovelace.")
    >>> svc.graph.entity_memory_count("acme corp") >= 1
    True
    """

    def __init__(
        self,
        *,
        entity_store: EntityStore | None = None,
        graph: BipartiteGraph | None = None,
        mode: str = scoring.DEFAULT_MODE,
        threshold: float = scoring.BOOST_MATCH_THRESHOLD,
        weight: float = scoring.ENTITY_BOOST_WEIGHT,
    ) -> None:
        self.store: EntityStore = entity_store if entity_store is not None else EntityStore()
        self.graph: BipartiteGraph = graph if graph is not None else BipartiteGraph()
        self.mode = mode
        self.threshold = threshold
        self.weight = weight
        self.memories: dict[str, StoredMemory] = {}

    # ------------------------------------------------------------------
    # The six phases, in the report's numbering (1 recognize, 2 inject, 3
    # extract, 4 consolidate, 5 return_entities, 6 reconstruct).
    #
    # NOTE ON ORDER: the methods below are grouped by *call site*, not by phase
    # number, so the banners read 1, 3, 4, 2, 5, 6. That is deliberate -- ingest
    # (``remember``) runs 1->3->4 and query (``_rank``) runs 1->2->5, so a reader
    # following the write path never jumps over a 60-line method that belongs to
    # the read path. The phase number in each banner is the report's, so the two
    # can be read side by side.
    # ------------------------------------------------------------------

    # --- phase 1: recognize ----------------------------------------------
    def recognize(self, text: str) -> list[Entity]:
        """Phase 1 -- every entity in ``text``, deduped through the store.

        mem0 does the same job at ingest: it asks the LLM for entities, then
        looks each one up in the entity collection with a **fuzzy** fallback
        (``:621``) so two spellings of one name land on one row.

        Dedupe happens *here* and not in the caller, because that is the only way
        the graph's reverse index can be trusted: if two surface forms got two
        entity ids, ``entity_memory_count`` would split a hub into two
        half-counts and the decay would stop firing. ``EntityStore.resolve`` does
        exact-then-fuzzy at ``MERGE_THRESHOLD`` (0.82) and returns the surviving
        :class:`Entity`, so the id handed to the graph is already canonical.

        Candidate names come from two rule sources, both stand-ins for mem0's LLM
        extractor (and both labelled as such in ``mmemz.entities``): the
        subject/object of every extracted proposition, and every capitalised run.
        The second is needed because the proposition splitter only knows a fixed
        verb list, so "Acme Corp hired Ada" yields a triple while "Zeta Partners
        signed a deal" does not -- and an entity missed at ingest is an edge
        missing from the graph forever. Capitalised runs are a *superset*
        heuristic: they over-generate (a sentence-initial "Who"), which the store
        absorbs, where under-generating loses an edge silently.
        """
        seen: dict[str, Entity] = {}
        for name in _proper_noun_runs(text):
            self._absorb(name, text, seen)
        for prop in extract_propositions(text):
            for name in (prop.subject, prop.object):
                if _plausible_name(name):
                    self._absorb(name, text, seen)
        for known in _known_mentions(text, self.store.canonical_names()):
            self._absorb(known, text, seen)
        return list(seen.values())

    def _absorb(
        self, name: str, text: str, seen: dict[str, Entity]
    ) -> None:
        """Resolve one candidate name into ``seen``, keyed by normalised form."""
        if not name:
            return
        expanded = _expand_legal_forms(name)
        key = normalize_name(expanded)
        if not key or key in seen:
            return
        seen[key] = self.store.resolve(expanded, context=text)

    # --- read-only query entity resolution -------------------------------
    def _query_entities(self, text: str) -> dict[str, float]:
        """``{canonical_entity_id: similarity}`` for a query. **Creates nothing.**

        A query must not mutate the store: ``EntityStore.resolve`` creates on
        miss, so using it here would let a question invent an entity and inflate
        ``len(store)`` (and every count that a caller compares against ingest).
        Exact lookup first, then a containment pass so "Acme" finds "acme corp"
        -- a query names an entity approximately far more often than a document
        does.

        Similarity is ``1.0`` for an exact canonical/alias hit and ``0.5`` for a
        containment hit, which is exactly the gate: a containment match sits *on*
        ``BOOST_MATCH_THRESHOLD`` so it contributes only if the caller lowers the
        gate deliberately. That keeps the bounded arm's promise that a weak link
        contributes nothing by default.
        """
        out: dict[str, float] = {}
        canonicals = self.store.canonical_names()
        for name in _proper_noun_runs(text):
            key = normalize_name(name)
            if not key:
                continue
            ent = self.store.get(key)
            if ent is not None:
                out[ent.canonical] = 1.0
                continue
            for canon in canonicals:
                if key in canon or canon in key:
                    out[canon] = 0.5
        return out


    # --- phase 3: extract ------------------------------------------------
    def extract(
        self, text: str, attributed_to: str | None = None
    ) -> list[Proposition]:
        """Phase 3 -- subject-predicate-object propositions with provenance.

        ``attributed_to`` is carried as a qualifier, not as a memory field: mem0
        promotes it to one enumerated value per memory row (``:1244``), so a
        memory holding three differently-attributed facts needs three rows.
        ``Proposition.qualifiers`` lets provenance vary per *fact*. That is the
        spec's DO-NOT-PORT verdict, implemented by simply not promoting it.
        """
        return extract_propositions(text, attributed_to=attributed_to)

    # --- convenience: ingest one memory ---------------------------------
    def remember(
        self,
        memory_id: str,
        text: str,
        attributed_to: str | None = None,
    ) -> ConsolidationReport:
        """Recognize + extract + link + consolidate one memory, as ingest does.

        Returns the consolidation report. Adding the same ``memory_id`` twice is
        an idempotent *restatement*: the propositions are reconciled, the memory
        count does not move.
        """
        entities = self.recognize(text)
        props = self.extract(text, attributed_to=attributed_to)
        mem = self.memories.get(memory_id)
        if mem is None:
            mem = StoredMemory(memory_id, text, attributed_to=attributed_to)
            self.memories[memory_id] = mem
        else:
            mem.text = text
        self.graph.add_memory(memory_id, [e.canonical for e in entities])
        return self.consolidate(memory_id, props)

    def forget(self, memory_id: str) -> bool:
        """Delete a memory **and** its reverse edges. The subtraction mem0 lacks.

        mem0's only inverse repair is ``_remove_memory_from_entity_store``
        (``:652``), a scan of every entity row. Here the graph's
        :meth:`BipartiteGraph.remove_memory` is O(degree) and the count cannot go
        stale, because the reverse set *is* the count.
        """
        existed = self.graph.remove_memory(memory_id)
        self.memories.pop(memory_id, None)
        return existed

    # --- phase 4: consolidate (ALREADY-BEYOND mem0) ----------------------
    def consolidate(
        self, memory_id: str, propositions: Sequence[Proposition]
    ) -> ConsolidationReport:
        """Phase 4 -- reconcile-by-supersede. NOT mem0's ADD-only.

        mem0's ``:1209-1212`` is the ADD branch: a fact the extractor believes is
        new becomes a new memory, and a *restated* fact is new to it too, so the
        count grows on a pure restatement. That is the defect this method exists
        to close, and it closes it on three rules:

        1. **Same triple** ``(subject, predicate, object)`` as a live proposition
           -> ``restated``: no new proposition, no new memory. Count flat.
        2. **Same head** ``(subject, predicate)``, different object -> the old
           proposition is moved from ``propositions`` to ``superseded`` with a
           ``superseded_by`` stamp and the new one becomes live. Count flat: a
           fact changing is not a fact being added.
        3. **New head** -> ``added``. Still no new *memory*: the memory already
           exists, so ``memory_count_delta`` stays ``0``.

        The old proposition is retained in ``superseded`` rather than deleted,
        because "this changed" is itself knowledge; the target's ``_reconcile``
        (``consolidation.py:1187``) keeps bitemporal bounds for the same reason.

        ``memory_count_delta`` is measured, not asserted -- the caller can check
        it against ``len(service.memories)``.
        """
        report = ConsolidationReport(memory_id=memory_id)
        # Sampled BEFORE the get-or-create below. Sampling it afterwards (as 0.1.0
        # originally did) makes the field structurally incapable of reporting the
        # growth this call causes, so a caller checking it would always read 0.
        before = len(self.memories)
        mem = self.memories.get(memory_id)
        if mem is None:
            mem = StoredMemory(memory_id, "")
            self.memories[memory_id] = mem

        for prop in propositions:
            triple = (prop.subject, prop.predicate, prop.object)
            if triple in mem.live_triples():
                report.restated.append(triple)
                continue
            rivals = [
                p
                for p in mem.live_for(prop.subject, prop.predicate)
                if p.object != prop.object
            ]
            for old in rivals:
                old.qualifiers["superseded_by"] = triple[2]
                old.qualifiers["superseded_at"] = "consolidate"
                mem.propositions.remove(old)
                mem.superseded.append(old)
                report.superseded.append((old.subject, old.predicate, old.object))
            mem.propositions.append(prop)
            report.added.append(triple)

        report.memory_count_delta = len(self.memories) - before
        return report

    # --- phase 2: inject (THE PORTED WIN) --------------------------------
    def inject(
        self,
        candidate_ids: Iterable[str],
        query_entities: Mapping[str, float] | Iterable[Entity] | Iterable[str],
    ) -> dict[str, float]:
        """Phase 2 -- the per-memory entity-relevance multiplier.

        THE PORTED WIN: bounded **max over links**, never a sum. mem0 takes the
        best link (``:1822``); the target sums them (``memory_retrieval.py:778-780``),
        which makes multiplicity a relevance signal. Delegating to
        :func:`mmemz.scoring.entity_relevance_for_candidate` with
        ``mode="bounded"`` gets mem0's max plus two things mem0 lacks: a 0.5 gate
        so a sub-threshold link contributes *exactly* zero (``:1807``), and a
        hub dampener fed by ``graph.entity_memory_count`` -- the O(1) count, read
        per entity, no walking.

        ``query_entities`` accepts either a ``{entity_id: similarity}`` mapping or
        an iterable of :class:`Entity` / names. A bare name is looked up
        **read-only** -- an exact canonical or alias hit scores ``1.0`` -- and an
        unknown one is dropped rather than invented: resolving it would create an
        entity from the query, so a question could grow the store.

        Returns ``{memory_id: multiplier}``, the multiplier alone -- the
        ``[1.0, 1.5]`` factor that multiplies similarity, not the final score.
        """
        if isinstance(query_entities, Mapping):
            scores = {
                str(k): float(v) for k, v in query_entities.items() if v is not None
            }
        else:
            scores = {}
            for item in query_entities:
                if isinstance(item, Entity):
                    scores[item.canonical] = 1.0
                else:
                    ent = self.store.get(normalize_name(str(item)))
                    if ent is not None:
                        scores[ent.canonical] = 1.0

        out: dict[str, float] = {}
        for mid in candidate_ids:
            linked = self.graph.linked_entities(mid)
            counts = scoring.linked_memory_counts(
                sorted(linked), self.graph.entity_memory_count
            )
            relevance = scoring.entity_relevance_for_candidate(
                linked,
                scores,
                counts,
                mode=self.mode,
                weight=self.weight,
                threshold=self.threshold,
            )
            out[mid] = scoring.bounded_multiplier(relevance)
        return out

    # --- phase 5: return -------------------------------------------------
    def return_entities(
        self,
        query: str,
        limit: int = 5,
        mode: str | None = None,
    ) -> list[SearchResult]:
        """Phase 5 -- ranked memories, each with the entities and attributes
        behind it and the score breakdown visible.

        mem0's retrieval returns a flat list of memories with a score. The entity
        that produced the boost is not in the result, so a caller cannot tell a
        genuine match from a hub artefact, and cannot audit the arithmetic. Here
        every result carries ``similarity``, ``entity_relevance``, ``multiplier``,
        the linked entities with their **counts**, and the live propositions --
        all of which came from ``graph.entity_memory_count`` and the proposition
        store, not from a recomputation.

        Ranking uses :func:`mmemz.scoring.score_candidate`, so ``mode`` selects the
        same seam as :meth:`inject` and the two modes are directly comparable.
        """
        return [r for r, _ in self._rank(query, limit=limit, mode=mode)]

    def _rank(
        self, query: str, limit: int, mode: str | None = None
    ) -> list[tuple[SearchResult, dict[str, float]]]:
        """Score every memory once; return results paired with the entity scores.

        Ordered by ``(-score, memory_id)`` -- a **total** order, so identical
        inputs always give identical output and two modes can be compared
        element-by-element without tie-order noise hiding a real difference.
        """
        use_mode = mode or self.mode
        ent_scores: dict[str, float] = self._query_entities(query)

        results: list[tuple[SearchResult, dict[str, float]]] = []
        for mid, mem in self.memories.items():
            linked = self.graph.linked_entities(mid)
            counts = scoring.linked_memory_counts(
                sorted(linked), self.graph.entity_memory_count
            )
            similarity = self._similarity(query, mem.text)
            relevance = scoring.entity_relevance_for_candidate(
                linked,
                ent_scores,
                counts,
                mode=use_mode,
                weight=self.weight,
                threshold=self.threshold,
            )
            score = scoring.score_candidate(
                similarity,
                linked,
                ent_scores,
                counts,
                mode=use_mode,
                weight=self.weight,
                threshold=self.threshold,
            )
            multiplier = (
                scoring.bounded_multiplier(relevance)
                if use_mode != scoring.MODE_LEGACY
                else similarity + relevance
            )
            matched = tuple(sorted(set(linked) & set(ent_scores)))
            results.append(
                (
                    SearchResult(
                        memory_id=mid,
                        text=mem.text,
                        score=score,
                        similarity=similarity,
                        entity_relevance=relevance,
                        multiplier=multiplier,
                        linked_entities=tuple(sorted(linked)),
                        linked_counts=counts,
                        matched_entities=matched,
                        propositions=list(mem.propositions),
                        mode=use_mode,
                    ),
                    ent_scores,
                )
            )
        results.sort(key=lambda pair: (-pair[0].score, pair[0].memory_id))
        return results[: max(0, limit)]

    @staticmethod
    def _similarity(query: str, text: str) -> float:
        """Lexical similarity in ``[0, 1]`` via the vendored ``vecops.cosine``.

        A bag-of-tokens vectoriser, not an embedder: mmemz 0.1.0 has no model, and
        the scoring phases must be exercisable without one. ``vecops.cosine`` is
        the same primitive the entity store dedupes with, so there is one cosine
        in the package.
        """
        def vec(s: str) -> dict[str, float]:
            out: dict[str, float] = {}
            for tok in normalize_name(s).split():
                out[tok] = out.get(tok, 0.0) + 1.0
            return out

        a, b = vec(query), vec(text)
        if not a or not b:
            return 0.0
        keys = sorted(a.keys() | b.keys())
        va = [a.get(k, 0.0) for k in keys]
        vb = [b.get(k, 0.0) for k in keys]
        from .vecops import cosine as _vcos

        return max(0.0, _vcos(va, vb))

    # --- phase 6: reconstruct (BEYOND mem0) ------------------------------
    def reconstruct(
        self, memory_id: str, max_depth: int = 4
    ) -> list[dict[str, object]]:
        """Phase 6 -- assemble the memory via a real traversal, and return the path.

        **Beyond mem0**, which returns a flat list of memories and has no path to
        return: its entity hop is one edge and ``linked_memory_ids`` is read off a
        row (``:1812``), so there is nothing to traverse.

        The path comes from :meth:`BipartiteGraph.expand`, i.e. the vendored
        bounded BFS in ``mmemz.graph_walk`` -- not a stub and not a second
        implementation. Hops are ``{"from", "via", "to", "depth", "kind"}``
        entries naming the memory/entity ids traversed, so ``len(path) >= 1``
        whenever the memory is linked to anything at all. The last element is the
        memory's accumulated entity/proposition context.

        An unknown or entity-less memory returns ``[]``: an empty path is the
        honest answer when there was no traversal, and the caller can test it.
        """
        if memory_id not in self.graph:
            return []
        expansion: GraphExpansion = self.graph.expand(
            [memory_id], max_depth=max_depth, max_nodes=200
        )
        if not expansion.results:
            return []
        res = expansion.results[0]
        path: list[dict[str, object]] = []
        reached = {n.node_id: n for n in res.nodes}
        for node in res.nodes:
            if node.depth == 0 or node.parent_id is None:
                continue
            parent_kind, _, parent_raw = node.parent_id.partition(":")
            _, _, node_raw = node.node_id.partition(":")
            path.append(
                {
                    "from": parent_raw,
                    "via": "links-to",
                    "to": node_raw,
                    "depth": node.depth,
                    "kind": node.node_id.partition(":")[0],
                    "names": self._names_for(parent_kind, parent_raw, node_raw),
                }
            )
        return path

    def _names_for(
        self, parent_kind: str, parent_raw: str, node_raw: str
    ) -> list[str]:
        """Surface names for a hop: the entity's canonical name and aliases."""
        target = parent_raw if parent_kind == "e" else node_raw
        ent = self.store.get(target)
        if ent is None:
            return [target]
        return [ent.canonical, *ent.aliases]

    # --- convenience: the comparable search ------------------------------
    def search(
        self, query: str, limit: int = 5, mode: str = scoring.DEFAULT_MODE
    ) -> list[SearchResult]:
        """Recognize -> inject -> rank. The one-line way to compare the modes.

        ``mode="bounded"`` (default) is the ported behaviour: max over links, gated
        at 0.5, damped by the O(1) link count. ``mode="legacy"`` is the target's
        summing arithmetic, kept as a rollback seam. Running both over one corpus
        and diffing the scores is the regression test for the whole port.
        """
        return self.return_entities(query, limit=limit, mode=mode)

    def __len__(self) -> int:
        """Number of stored memories."""
        return len(self.memories)

    def __repr__(self) -> str:
        return (
            f"MemoryService(memories={len(self.memories)}, "
            f"entities={self.store.__len__()}, links={self.graph.memory_count()}, "
            f"mode={self.mode!r})"
        )




