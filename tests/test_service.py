"""End-to-end tests for ``mmemz.service`` over a fixed six-memory corpus.


Stdlib ``unittest`` only. The corpus is fixed (no randomness, no clock, no
network) so every number below is reproducible and can be pasted into a README.

The corpus is built to exercise exactly four properties:

1. **Alias dedupe merges.** "Acme Corp" and "ACME Corporation" are the same
   organisation written two ways. Because the trigram cosine of that pair is
   ``0.65`` -- below ``MERGE_THRESHOLD`` (0.82) -- fuzzy dedupe alone cannot merge
   it, so the legal-form expansion in ``mmemz.service`` does it by making the two
   names *identical* before the lookup. ``len(store)`` must stay below the raw
   mention count.
2. **Bounded and legacy disagree.** The bounded arm takes the max over an
   entity's links, gated and count-damped; the legacy arm sums them. On the query
   "Lovelace Babbage London" the legacy arm ranks m2 (one matched link, weight
   0.5, added to similarity) above m3 (no matched link, higher raw similarity),
   and bounded reverses it. Note precisely *why*, because the obvious reading is
   wrong: only `london` resolves, so `n_matched` is 1 for every memory in this
   corpus and the max-vs-sum term never fires here. The inversion comes from the
   per-link weight (0.5 legacy, 0.25 bounded) and from the algebra (legacy adds,
   bounded multiplies). The max-vs-sum difference is exercised separately in
   `tests/test_scoring.py`, where two links scoring 0.9 give 0.45 bounded
   (`test_modes_are_provably_different_on_a_shared_input`) against 1.8 legacy
   (`test_legacy_cap_is_optional_and_off_by_default`).
3. **Consolidation supersedes without growing the count.** m5 restates m1 word for
   word, and m6 states a *changed object* for one of m2's facts.
4. **reconstruct returns a real path.** Not a stub: hops produced by the vendored
   bounded BFS, each naming the entity traversed.
"""

from __future__ import annotations

import unittest

from mmemz import scoring
from mmemz.entities import Proposition
from mmemz.service import MemoryService, SearchResult

#: The fixed corpus. Deliberate features, called out so a failure is diagnosable:
#:   m1/m5 -> a verbatim restatement (consolidation must not grow the count)
#:   m1/m2 -> the alias pair Acme Corp / ACME Corporation
#:   m2/m6 -> one fact restated with a *different object* (must supersede)
#:   Acme appears in 4 of 6 -> a hub, so the count decay actually fires
CORPUS: tuple[tuple[str, str], ...] = (
    ("m1", "Acme Corp hired Ada Lovelace as a compiler engineer."),
    ("m2", "ACME Corporation is based in London and builds engines."),
    ("m3", "Ada Lovelace reports to Charles Babbage at Acme Corp."),
    ("m4", "Zeta Partners signed a contract with Acme Corp."),
    ("m5", "Acme Corp hired Ada Lovelace as a compiler engineer."),
    ("m6", "Charles Babbage manages the engine programme at ACME Corporation."),
)

#: Raw entity mentions across the corpus if nothing deduped -- each capitalised
#: run counts once per memory it appears in.
RAW_MENTION_FLOOR = 10


def build_service() -> MemoryService:
    """Ingest :data:`CORPUS` into a fresh service."""
    svc = MemoryService()
    for memory_id, text in CORPUS:
        svc.remember(memory_id, text, attributed_to="minutes")
    return svc


class CorpusTestCase(unittest.TestCase):
    """Shared fixture: one service, rebuilt per test so order cannot leak."""

    def setUp(self) -> None:
        self.svc = build_service()
        #: The corpus size as built, so a test can assert growth *relative* to it.
        self._baseline_count = len(self.svc.memories)


class AliasDedupeTest(CorpusTestCase):
    """Assertion 1: alias dedupe merges, so the store stays below raw mentions."""

    def test_store_is_smaller_than_the_raw_mention_count(self):
        self.assertLess(len(self.svc.store), RAW_MENTION_FLOOR)

    def test_acme_corp_and_acme_corporation_are_one_entity(self):
        """The pair the trigram cosine cannot merge (0.65 < 0.82) must merge.

        This is the test that pins the legal-form expansion: without it the store
        would hold ``acme corp`` and ``acme corporation`` as two entities, each
        with half the links, and the hub dampener would never fire.
        """
        canonicals = set(self.svc.store.canonical_names())
        self.assertIn("acme corporation", canonicals)
        self.assertNotIn("acme corp", canonicals)
        # The hub: Acme appears in all six memories, in both spellings.
        self.assertEqual(self.svc.graph.entity_memory_count("acme corporation"), 6)

    def test_the_alias_resolves_to_the_survivor_in_both_spellings(self):
        """Both surface forms must arrive at the same canonical id.

        ``EntityStore.get`` is an exact lookup on the normalised name and does
        *not* apply the legal-form rewrite, so the short spelling is routed
        through ``recognize`` -- the same path ingest takes.
        """
        for spelling in ("Acme Corp", "ACME Corporation", "acme corp"):
            ents = self.svc.recognize(f"{spelling} announced something.")
            self.assertTrue(ents, f"{spelling!r} yielded no entity")
            self.assertEqual(
                {e.canonical for e in ents},
                {"acme corporation"},
                f"{spelling!r} did not resolve to the survivor",
            )

    def test_the_hub_count_is_visible_and_O1(self):
        """Row 2 + 3: the count is a callable read, not a walk."""
        n = self.svc.graph.entity_memory_count("acme corporation")
        self.assertEqual(n, 6)
        self.assertEqual(n, len(self.svc.graph.linked_memory_ids("acme corporation")))
        # The dampener is therefore strictly below 1.0, not the undamped 1.0.
        self.assertLess(scoring.memory_count_weight(n), 1.0)


class BoundedVersusLegacyTest(CorpusTestCase):
    """Assertion 2: the two modes differ, and the difference is the point."""

    QUERY = "Lovelace Babbage London"

    def test_scores_differ_concretely(self):
        bounded = self.svc.search(self.QUERY, limit=6, mode="bounded")
        legacy = self.svc.search(self.QUERY, limit=6, mode="legacy")
        self.assertNotEqual(
            [r.score for r in bounded],
            [r.score for r in legacy],
            "bounded and legacy produced identical score lists",
        )

    def test_legacy_sums_links_and_bounded_takes_the_max(self):
        """The ordering inversion: two weak links beat one strong one under legacy.

        Legacy ranks m2 (linked to two of the query's three entities, weakly) above
        m3 (linked to one, strongly) because it *adds* the links. Bounded keeps the
        single best link, so m3 wins. This is mem0's ``:1822`` max versus the
        target's ``:778-780`` sum, observed rather than asserted.
        """
        bounded = [r.memory_id for r in self.svc.search(self.QUERY, 6, "bounded")]
        legacy = [r.memory_id for r in self.svc.search(self.QUERY, 6, "legacy")]
        self.assertNotEqual(bounded, legacy)
        self.assertLess(legacy.index("m2"), legacy.index("m3"))
        self.assertLess(bounded.index("m3"), bounded.index("m2"))

    def test_bounded_score_is_capped_at_the_similarity_times_one_point_five(self):
        for r in self.svc.search(self.QUERY, limit=6, mode="bounded"):
            self.assertLessEqual(r.score, r.similarity * 1.5 + 1e-12)

    def test_bounded_multiplier_never_grows_with_more_links(self):
        """m1 and m4 both link the hub once, so their multipliers must be equal."""
        bounded = {r.memory_id: r for r in self.svc.search(self.QUERY, 6, "bounded")}
        self.assertAlmostEqual(bounded["m1"].multiplier, bounded["m4"].multiplier)
        self.assertLessEqual(max(r.multiplier for r in bounded.values()), 1.5)

    def test_legacy_is_opt_in_only(self):
        self.assertEqual(scoring.DEFAULT_MODE, "bounded")
        self.assertEqual(self.svc.mode, "bounded")
        with self.assertRaises(ValueError):
            self.svc.search(self.QUERY, mode="nonsense")


class ConsolidationTest(CorpusTestCase):
    """Assertion 3: supersede without growing the memory count."""

    def test_restating_a_fact_does_not_grow_the_memory_count(self):
        # GUARD FIRST. This test passed for a while on two EMPTY proposition lists
        # -- ``extract()`` could not parse "hired", so m1 and m5 both stored zero
        # facts and "restated or not added" was vacuously true. Assert there is a
        # real fact to restate, or the test proves nothing. Audit finding 2.
        live = self.svc.memories["m1"].propositions
        self.assertTrue(
            live,
            "m1 stored no propositions -- the restatement below would be vacuous",
        )
        before = len(self.svc.memories)
        report = self.svc.remember(
            "m5", CORPUS[0][1], attributed_to="minutes"
        )  # verbatim restatement of m1
        self.assertEqual(len(self.svc.memories), before)
        self.assertEqual(report.memory_count_delta, 0)
        self.assertTrue(
            report.restated,
            f"expected a restated triple, got report={report!r}",
        )
        # The restatement must name the fact that was actually stored.
        self.assertEqual(
            report.restated[0],
            (live[0].subject, live[0].predicate, live[0].object),
        )

    def test_a_new_memory_does_not_grow_any_other_memory(self):
        """Ingesting a genuinely new memory adds one, and only one."""
        before = len(self.svc.memories)
        self.svc.remember("m7", "Zeta Partners visited London with Ada Lovelace.")
        self.assertEqual(len(self.svc.memories), before + 1)

    def test_memory_count_delta_can_report_growth(self):
        """Regression: the delta is sampled BEFORE get-or-create, so it is capable
        of reporting the growth it causes.

        The 0.1.0 original sampled ``len(self.memories)`` *after* inserting the
        memory, so a brand-new id always reported ``0`` -- a field that could
        never disagree with the caller's expectation. Audit finding 8 (LOW).
        """
        report = self.svc.consolidate(
            "never-seen-before", [Proposition("ada lovelace", "is", "a pioneer")]
        )
        self.assertEqual(report.memory_count_delta, 1)
        self.assertEqual(len(self.svc.memories), self._baseline_count + 1)

    def test_changed_object_supersedes_the_old_proposition(self):
        """m2 says the base is London; a later report moves it to Paris."""
        mem = self.svc.memories["m2"]
        live_before = mem.live_triples()
        self.assertTrue(live_before, "m2 had no live propositions to supersede")

        subject, predicate, _old_object = sorted(live_before)[0]
        new = Proposition(subject, predicate, "paris", attributed_to="minutes")
        before_count = len(self.svc.memories)
        report = self.svc.consolidate("m2", [new])

        self.assertEqual(len(self.svc.memories), before_count)
        self.assertEqual(report.memory_count_delta, 0)
        self.assertIn((subject, predicate, "paris"), report.added)
        self.assertTrue(report.superseded, "nothing was superseded")

        # The old triple is out of the live set and stamped in the superseded list.
        self.assertNotIn((subject, predicate, _old_object), mem.live_triples())
        self.assertIn((subject, predicate, "paris"), mem.live_triples())
        stamped = [p for p in mem.superseded if p.object == _old_object]
        self.assertEqual(len(stamped), 1)
        self.assertEqual(stamped[0].qualifiers["superseded_by"], "paris")

    def test_restatement_is_not_added_twice(self):
        # m2 is the memory whose fact the rule-based splitter definitely extracts;
        # a verbatim restatement of it must be idempotent.
        mem = self.svc.memories["m2"]
        triple = sorted(mem.live_triples())[0]
        prop = Proposition(*triple, attributed_to="minutes")
        size_before = len(mem.propositions)
        report = self.svc.consolidate("m2", [prop])
        self.assertIn(triple, report.restated)
        self.assertEqual(len(mem.propositions), size_before)
        self.assertEqual(len(mem.superseded), 0)

    def test_consolidation_report_names_what_changed(self):
        report = self.svc.remember("m8", "Zeta Partners is based in Berlin.")
        payload = report.to_dict()
        for key in ("added", "restated", "superseded", "memory_count_delta"):
            self.assertIn(key, payload)


class ReconstructTest(CorpusTestCase):
    """Assertion 4: a real traversal path, not a stub."""

    def test_path_has_at_least_one_hop(self):
        path = self.svc.reconstruct("m1")
        self.assertGreaterEqual(len(path), 1)

    def test_every_hop_names_an_entity(self):
        for hop in self.svc.reconstruct("m1"):
            self.assertIn("from", hop)
            self.assertIn("to", hop)
            self.assertIn("via", hop)
            names = hop["names"]
            self.assertIsInstance(names, list)
            self.assertTrue(names, "a hop named no entity")
            self.assertTrue(all(isinstance(n, str) and n for n in names))
            self.assertGreaterEqual(int(hop["depth"]), 1)

    def test_path_reaches_a_memory_through_a_shared_entity(self):
        """The path is a traversal, so it reaches *other* memories via an entity."""
        path = self.svc.reconstruct("m1")
        reached_memories = {h["to"] for h in path if h["kind"] == "m"}
        self.assertIn("m3", reached_memories)  # m1 -> acme -> m3
        self.assertGreaterEqual(max(int(h["depth"]) for h in path), 2)

    def test_hops_chain_back_to_the_starting_memory(self):
        path = self.svc.reconstruct("m1")
        self.assertEqual(path[0]["from"], "m1")
        depths = [int(h["depth"]) for h in path]
        self.assertEqual(depths, sorted(depths))  # BFS order is monotone in depth

    def test_unknown_memory_has_no_path(self):
        self.assertEqual(self.svc.reconstruct("ghost"), [])

    def test_forgotten_memory_has_no_path(self):
        self.svc.forget("m1")
        self.assertEqual(self.svc.reconstruct("m1"), [])
        self.assertNotIn("m1", self.svc.graph)


class SixPhasesTest(CorpusTestCase):
    """Each phase is an identifiable method, and each does its own job."""

    def test_phase_methods_exist_under_their_names(self):
        for name in (
            "recognize",
            "inject",
            "extract",
            "consolidate",
            "return_entities",
            "reconstruct",
        ):
            self.assertTrue(callable(getattr(self.svc, name)), name)

    def test_recognize_returns_deduped_entities(self):
        ents = self.svc.recognize("Acme Corp hired Ada Lovelace.")
        canonicals = [e.canonical for e in ents]
        self.assertIn("acme corporation", canonicals)
        self.assertEqual(len(canonicals), len(set(canonicals)))

    def test_recognize_does_not_duplicate_when_alias_is_used_twice(self):
        svc = MemoryService()
        svc.recognize("Acme Corp met ACME Corporation.")
        self.assertEqual(len(svc.store), 1)

    def test_inject_uses_max_not_sum(self):
        """One strong link plus two weak links must score the same as the one alone.

        The weak links are below ``BOOST_MATCH_THRESHOLD`` so the gate zeroes them;
        under the legacy summing arm they would have added up. If ``inject`` summed
        instead of taking the max, ``a`` would outrank ``b`` here.
        """
        svc = MemoryService()
        svc.graph.add_memory("a", ["strong", "weak1", "weak2"])
        svc.graph.add_memory("b", ["strong"])
        scores = {"strong": 0.9}
        got = svc.inject(["a", "b"], scores)
        self.assertAlmostEqual(got["a"], got["b"])
        # `strong` links two memories, so the dampener is 1/(1+0.001*1) < 1.0.
        expected = 1.0 + scoring.ENTITY_BOOST_WEIGHT * 0.9 * scoring.memory_count_weight(2)
        self.assertAlmostEqual(got["a"], expected, places=9)
        self.assertLess(got["a"], 1.45)  # damped, not the raw 1 + 1.5*0.9

    def test_inject_is_flat_when_no_entity_matches(self):
        svc = MemoryService()
        svc.graph.add_memory("a", ["something"])
        self.assertEqual(svc.inject(["a"], {}), {"a": 1.0})

    def test_extract_carries_attribution_as_a_qualifier(self):
        props = self.svc.extract("Ada Lovelace is a compiler engineer.", attributed_to="doc-7")
        self.assertTrue(props)
        self.assertEqual(props[0].qualifiers["attributed_to"], "doc-7")
        self.assertEqual(props[0].get_attributed_to(), "doc-7")

    def test_return_entities_shows_the_breakdown(self):
        results = self.svc.return_entities("Who did Acme Corp hire?", limit=3)
        self.assertTrue(results)
        self.assertLessEqual(len(results), 3)
        for r in results:
            self.assertIsInstance(r, SearchResult)
            payload = r.to_dict()
            for key in ("score", "similarity", "entity_relevance", "multiplier", "linked_counts"):
                self.assertIn(key, payload)
            self.assertEqual(payload["mode"], "bounded")

    def test_return_entities_honours_the_limit_and_orders_totally(self):
        everything = self.svc.return_entities("Acme Corp", limit=99)
        self.assertEqual(len(everything), len(self.svc.memories))
        keys = [(-r.score, r.memory_id) for r in everything]
        self.assertEqual(keys, sorted(keys))

    def test_forget_subtracts_from_the_count(self):
        before = self.svc.graph.entity_memory_count("acme corporation")
        self.assertTrue(self.svc.forget("m4"))
        after = self.svc.graph.entity_memory_count("acme corporation")
        self.assertEqual(after, before - 1)
        self.assertEqual(len(self.svc.memories), len(CORPUS) - 1)


class EndToEndDumpTest(CorpusTestCase):
    """Prints the corpus output that goes into the README, and asserts its shape."""

    def test_dump_the_end_to_end_story(self):
        svc = self.svc
        lines = []
        lines.append("corpus (6 memories, entities overlap, alias pair, one restated fact)")
        for memory_id, text in CORPUS:
            lines.append(f"  {memory_id}: {text}")
        lines.append("")
        lines.append(
            f"entities ingested: {len(svc.store)} (raw capitalised mentions: "
            f"{RAW_MENTION_FLOOR}+) -> alias dedupe merged Acme Corp / ACME Corporation"
        )
        lines.append(f"graph: {svc.graph!r}")
        lines.append("")
        lines.append("entity link counts (O(1) reads, damped by 1/(1+0.001(n-1)^2)):")
        for canon in sorted(svc.store.canonical_names()):
            n = svc.graph.entity_memory_count(canon)
            lines.append(f"  {canon:24} n={n}  weight={scoring.memory_count_weight(n):.4f}")

        query = BoundedVersusLegacyTest.QUERY
        lines.append("")
        lines.append(f'query: {query!r}')
        for mode in ("bounded", "legacy"):
            lines.append(f"  -- mode={mode} --")
            for rank, r in enumerate(svc.search(query, limit=6, mode=mode), 1):
                lines.append(
                    f"    {rank}. {r.memory_id} score={r.score:.4f} "
                    f"sim={r.similarity:.4f} rel={r.entity_relevance:.4f} "
                    f"mult={r.multiplier:.4f} matched={list(r.matched_entities)}"
                )

        report = svc.consolidate(
            "m2", [Proposition("acme corporation", "is", "based in paris")]
        )
        lines.append("")
        lines.append(f"supersede report for m2: {report.to_dict()}")
        lines.append(f"memory count after supersede: {len(svc.memories)} (unchanged)")

        path = svc.reconstruct("m1")
        lines.append("")
        lines.append(f"reconstruct(m1) -> {len(path)} hops:")
        for hop in path:
            lines.append(
                f"  depth {hop['depth']}: {hop['from']} --{hop['via']}--> "
                f"{hop['to']}  names={hop['names']}"
            )

        dump = "\n".join(lines)
        print("\n" + dump + "\n")

        self.assertIn("reconstruct(m1)", dump)
        self.assertIn("supersede report", dump)
        self.assertGreaterEqual(len(path), 1)


if __name__ == "__main__":
    unittest.main()
