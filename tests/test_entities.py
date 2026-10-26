"""Tests for mmemz.entities — normalisation, typing, dedupe, propositions.


Plain ``unittest``, stdlib only:

    python3 -m unittest discover -s tests -v

The dedupe tests pin the **0.82** threshold: one case just above it must merge
and one just below must not, so a change to the number breaks a test rather
than quietly altering merge behaviour.
"""

import unittest

from mmemz import entities
from mmemz.entities import (
    ATTRIBUTED_TO_KEY,
    ENTITY_TYPES,
    MERGE_THRESHOLD,
    Entity,
    EntityStore,
    Proposition,
    _cosine,
    _vectorize,
    decide_type,
    extract_propositions,
    normalize_name,
)


class TestNormalizeName(unittest.TestCase):
    """Lowercase, strip, collapse whitespace, trim punctuation, NFC."""

    def test_lowercases(self):
        self.assertEqual(normalize_name("Acme CORP"), "acme corp")

    def test_collapses_internal_whitespace(self):
        self.assertEqual(normalize_name("Acme \t\n  Corp"), "acme corp")

    def test_strips_surrounding_punctuation(self):
        self.assertEqual(normalize_name("(Acme)"), "acme")
        self.assertEqual(normalize_name("Acme."), "acme")
        self.assertEqual(normalize_name('"Acme"'), "acme")

    def test_keeps_interior_apostrophe_and_hyphen(self):
        # Interior marks carry identity; edge punctuation is leakage.
        self.assertEqual(normalize_name("(O'Brien)"), "o'brien")
        self.assertEqual(normalize_name("Smith-Jones"), "smith-jones")

    def test_nfc_normalises_decomposed_input(self):
        decomposed = "e\u0301clair"          # e + combining acute
        precomposed = "\u00e9clair"          # é
        self.assertEqual(normalize_name(decomposed), normalize_name(precomposed))
        self.assertEqual(normalize_name(decomposed), "\u00e9clair")

    def test_is_idempotent_on_every_shape(self):
        for raw in (
            "  Acme   Corp. ",
            "(O'Brien)",
            "\u00e9clair",
            "e\u0301clair",
            "!!!",
            "",
            "Acme \t\n Corp",
            "Smith-Jones",
        ):
            once = normalize_name(raw)
            self.assertEqual(normalize_name(once), once, f"not idempotent: {raw!r}")

    def test_idempotent_after_two_and_three_passes(self):
        raw = '  "  Acme --- CORP!! "  '
        once = normalize_name(raw)
        self.assertEqual(normalize_name(normalize_name(once)), once)

    def test_punctuation_only_normalises_to_empty(self):
        self.assertEqual(normalize_name("..."), "")
        self.assertEqual(normalize_name(""), "")

    def test_none_is_empty_not_an_error(self):
        self.assertEqual(normalize_name(None), "")


class TestDecideType(unittest.TestCase):
    """The rule-based placeholder for mem0's LLM extractor."""

    def test_org_suffix_maps_to_org(self):
        for name in ("Acme Ltd", "Foo GmbH", "Bar Foundation", "Acme Corporation"):
            self.assertEqual(decide_type(name), "org", name)

    def test_date_like_maps_to_event(self):
        for name in ("2024-05-17", "17 May 2024", "Q3 2024", "Friday", "1990s"):
            self.assertEqual(decide_type(name), "event", name)

    def test_gazetteer_maps_to_place(self):
        for name in ("London", "New York", "Tokyo", "France"):
            self.assertEqual(decide_type(name), "place", name)

    def test_honourific_maps_to_person(self):
        self.assertEqual(decide_type("Dr Smith"), "person")
        self.assertEqual(decide_type("Prof Ada Lovelace"), "person")

    def test_two_capitalised_tokens_map_to_person(self):
        self.assertEqual(decide_type("Ada Lovelace"), "person")

    def test_lowercase_phrase_maps_to_concept(self):
        self.assertEqual(decide_type("vector similarity"), "concept")

    def test_acronym_maps_to_org(self):
        self.assertEqual(decide_type("IBM"), "org")
        self.assertEqual(decide_type("NATO"), "org")

    def test_empty_maps_to_other(self):
        self.assertEqual(decide_type(""), "other")
        self.assertEqual(decide_type("   "), "other")
        self.assertEqual(decide_type("!!!"), "other")

    def test_every_result_is_in_the_vocabulary(self):
        names = (
            "Acme Ltd", "London", "Ada Lovelace", "vector similarity", "",
            "IBM", "acme", "Q3 2024", "!!!", "Some Thing Here", "Dr Who",
        )
        for name in names:
            self.assertIn(decide_type(name), ENTITY_TYPES, name)

    def test_date_rule_wins_over_org_suffix(self):
        # "May 2024 Planning Group" is an event, not an org, even though it
        # ends in a suffix the org rule would accept.
        self.assertEqual(decide_type("May 2024 Planning Group"), "event")

    def test_context_is_accepted_without_changing_the_interface(self):
        # The seam an LLM backend needs: same call shape, context optional.
        self.assertEqual(decide_type.__defaults__, (None,))


class TestCosineFallback(unittest.TestCase):
    """Local stdlib cosine; step 3 will swap in mmemz.vecops.cosine."""

    def test_identical_text_scores_one(self):
        self.assertAlmostEqual(
            _cosine(_vectorize("acme corp"), _vectorize("acme corp")), 1.0
        )

    def test_unrelated_text_scores_zero(self):
        self.assertEqual(_cosine(_vectorize("acme corp"), _vectorize("zeta ltd")), 0.0)

    def test_empty_vector_scores_zero_without_dividing_by_zero(self):
        self.assertEqual(_cosine({}, _vectorize("acme")), 0.0)

    def test_result_is_symmetric(self):
        a, b = _vectorize("Acme Corporation"), _vectorize("Acme Corp")
        self.assertAlmostEqual(_cosine(a, b), _cosine(b, a))

    def test_vectors_are_deterministic(self):
        self.assertEqual(_vectorize("Acme Corp"), _vectorize("  ACME   corp. "))

    def test_vector_carries_token_and_ngram_families(self):
        vec = _vectorize("acme")
        self.assertIn("t:acme", vec)
        self.assertIn("b:ac", vec)
        self.assertTrue(any(k.startswith("c:") for k in vec))

    def test_variant_spelling_scores_above_a_different_name(self):
        variant = _cosine(_vectorize("Acme Corporation"), _vectorize("The Acme Corporation"))
        other = _cosine(_vectorize("Acme Corporation"), _vectorize("Zeta Partners"))
        self.assertGreater(variant, other)
        self.assertGreater(variant, 0.82)


class TestExactDedupe(unittest.TestCase):
    """Tier 1 of the resolver: exact match on the normalised name or an alias."""

    def test_same_normalised_name_is_one_entity(self):
        store = EntityStore()
        first = store.resolve("Acme Corp")
        second = store.resolve("  acme   corp. ")
        self.assertIs(first, second)
        self.assertEqual(len(store), 1)

    def test_exact_match_on_a_stored_alias(self):
        store = EntityStore()
        target = store.resolve("Acme Corporation")
        store.resolve("Acme Corp")
        self.assertIs(store.merge("Acme Corporation", "Acme Corp"), target)
        self.assertIs(store.resolve("acme corp"), target)

    def test_distinct_names_stay_distinct(self):
        store = EntityStore()
        store.resolve("Acme Corp")
        store.resolve("Zeta Partners")
        self.assertEqual(len(store), 2)

    def test_contains_normalises_the_probe_but_never_runs_tier_two(self):
        store = EntityStore()
        store.resolve("Acme Corp")
        self.assertIn("acme corp", store)
        self.assertIn("Acme Corp", store)          # probe is normalised
        self.assertNotIn("acme corporation", store)  # near miss: tier 2 not run

    def test_empty_name_does_not_pollute_the_store(self):
        store = EntityStore()
        entity = store.resolve("...")
        self.assertEqual(len(store), 0)
        self.assertEqual(entity.canonical, "")

    def test_salience_counts_mentions(self):
        store = EntityStore()
        store.resolve("Acme Corp")
        store.resolve("ACME CORP")
        self.assertEqual(store.get("acme corp").salience, 2)


class TestFuzzyDedupe(unittest.TestCase):
    """Tier 2: cosine >= 0.82 (the TARGET's tuned value)."""

    def test_threshold_defaults_to_the_targets_082(self):
        self.assertEqual(MERGE_THRESHOLD, 0.82)
        self.assertEqual(EntityStore().threshold, 0.82)

    def test_threshold_is_not_mem0s_looser_095(self):
        # The reason is in the docstring: mem0's merges are irreversible.
        self.assertNotEqual(MERGE_THRESHOLD, 0.95)

    def test_fuzzy_merge_accepted_at_the_threshold(self):
        store = EntityStore()
        first = store.resolve("Acme Corporation")
        merged = resolve_with_scores(store, "The Acme Corporation")
        self.assertIs(merged, first)
        self.assertGreaterEqual(
            _cosine(_vectorize("The Acme Corporation"), _vectorize("Acme Corporation")),
            0.82,
        )

    def test_near_miss_below_the_threshold_is_rejected(self):
        store = EntityStore()
        store.resolve("Jonathan Smith", entity_type="person")
        # 0.7945 -- a one-character typo in a two-word name is NOT enough.
        merged = store.resolve("Jonathon Smith", entity_type="person")
        self.assertEqual(len(store), 2)
        self.assertNotEqual(merged.canonical, "jonathan smith")
    def test_near_miss_score_is_actually_below_the_threshold(self):
        score = _cosine(_vectorize("Jonathon Smith"), _vectorize("Jonathan Smith"))
        self.assertLess(score, 0.82)
        self.assertGreater(score, 0.5)   # near miss, not unrelated

    def test_fuzzy_hit_adopts_the_new_spelling_as_an_alias(self):
        store = EntityStore()
        first = store.resolve("Acme Corporation")
        store.resolve("The Acme Corporation")
        self.assertIn("the acme corporation", first.aliases)
        # ... and the adopted alias now resolves exactly, with no fuzzy pass.
        self.assertIs(store.resolve("THE ACME CORPORATION"), first)

    def test_lowering_the_threshold_accepts_the_near_miss(self):
        # Proves 0.82 is doing the work, not the vectoriser.
        store = EntityStore(threshold=0.70)
        store.resolve("Jonathan Smith")
        merged = store.resolve("Jonathon Smith")
        self.assertEqual(len(store), 1)
        self.assertEqual(merged.canonical, "jonathan smith")

    def test_unrelated_names_never_merge(self):
        store = EntityStore()
        store.resolve("Acme Corp")
        store.resolve("Zeta Partners")
        self.assertEqual(len(store), 2)


class TestAliasMerge(unittest.TestCase):
    """Merging unions aliases and keeps one canonical name."""

    def test_merge_unions_aliases_and_keeps_the_survivor(self):
        store = EntityStore()
        store.resolve("Acme Corporation")
        store.resolve("Acme Corp")
        survivor = store.merge("Acme Corporation", "Acme Corp")
        self.assertIsNotNone(survivor)
        self.assertEqual(survivor.canonical, "acme corporation")
        self.assertIn("acme corp", survivor.aliases)
        self.assertNotIn("acme corporation", survivor.aliases)

    def test_merge_unions_existing_aliases_from_both_sides(self):
        store = EntityStore()
        a = store.resolve("Acme Corporation")
        a.aliases = ("acme",)
        b = store.resolve("Zeta Holdings")
        b.aliases = ("zeta",)
        survivor = store.merge("Acme Corporation", "Zeta Holdings")
        self.assertEqual(survivor.aliases, ("acme", "zeta", "zeta holdings"))

    def test_merge_collapses_the_store_entry_count(self):
        store = EntityStore()
        store.resolve("Acme Corporation")
        store.resolve("Acme Corp")
        self.assertEqual(len(store), 2)
        store.merge("Acme Corporation", "Acme Corp")
        self.assertEqual(len(store), 1)

    def test_merge_sums_salience_so_mentions_are_not_lost(self):
        store = EntityStore()
        store.resolve("Acme Corporation")
        store.resolve("Acme Corporation")
        store.resolve("Acme Corp")
        survivor = store.merge("Acme Corporation", "Acme Corp")
        self.assertEqual(survivor.salience, 3)

    def test_merge_adopts_a_more_specific_type(self):
        store = EntityStore()
        store.resolve("Widget", entity_type="other")
        store.resolve("Widget Ltd", entity_type="org")
        survivor = store.merge("Widget", "Widget Ltd")
        self.assertEqual(survivor.type, "org")

    def test_merge_keeps_the_survivors_type_when_it_is_specific(self):
        store = EntityStore()
        store.resolve("Widget Ltd", entity_type="org")
        store.resolve("Widget", entity_type="concept")
        survivor = store.merge("Widget Ltd", "Widget")
        self.assertEqual(survivor.type, "org")

    def test_merge_refuses_unknown_names(self):
        # Destructive operation: ambiguous input is refused, not guessed.
        store = EntityStore()
        store.resolve("Acme Corp")
        self.assertIsNone(store.merge("Acme Corp", "Nonexistent"))
        self.assertIsNone(store.merge("Nonexistent", "Acme Corp"))

    def test_merge_of_an_entity_with_itself_is_a_noop(self):
        store = EntityStore()
        store.resolve("Acme Corp")
        self.assertIsNone(store.merge("Acme Corp", "ACME CORP"))
        self.assertEqual(len(store), 1)

    def test_aliases_resolve_to_the_survivor_after_merge(self):
        store = EntityStore()
        store.resolve("Acme Corporation")
        store.resolve("Acme Corp")
        survivor = store.merge("Acme Corporation", "Acme Corp")
        self.assertIs(store.resolve("ACME CORP"), survivor)
        self.assertIs(store.resolve("acme corporation"), survivor)

    def test_resolve_auto_merges_hub_alias_lookups_keep_one_entity(self):
        store = EntityStore()
        store.resolve("Acme Corporation")
        store.resolve("The Acme Corporation")
        store.resolve("ACME CORPORATION")
        self.assertEqual(len(store), 1)


class TestEntityDataclass(unittest.TestCase):
    """The small container the store hands back."""

    def test_defaults(self):
        entity = Entity(canonical="acme corp")
        self.assertEqual(entity.aliases, ())
        self.assertEqual(entity.type, "other")
        self.assertEqual(entity.salience, 1)


class TestProposition(unittest.TestCase):
    """Shape of the extracted triple and its provenance carrier."""

    def test_fields_are_populated_from_a_sentence(self):
        props = extract_propositions("Acme Corp owns Widget Ltd.")
        self.assertEqual(len(props), 1)
        prop = props[0]
        self.assertEqual(prop.subject, "acme corp")
        self.assertEqual(prop.predicate, "owns")
        self.assertEqual(prop.object, "widget ltd")

    def test_attributed_to_argument_lands_in_qualifiers(self):
        props = extract_propositions("Ada is a mathematician.", attributed_to="letter-1843")
        self.assertEqual(props[0].qualifiers[ATTRIBUTED_TO_KEY], "letter-1843")
        self.assertEqual(props[0].get_attributed_to(), "letter-1843")

    def test_attributed_to_cue_in_text_overrides_the_argument(self):
        props = extract_propositions(
            "Ada is a mathematician according to the letter",
            attributed_to="document-7",
        )
        self.assertEqual(props[0].get_attributed_to(), "the letter")

    def test_cue_in_text_applies_only_to_its_own_sentence(self):
        props = extract_propositions(
            "Ada works at Widget. Bob is a engineer per the memo.",
            attributed_to="document-7",
        )
        self.assertEqual(props[0].get_attributed_to(), "document-7")
        self.assertEqual(props[1].get_attributed_to(), "the memo")

    def test_unattributed_proposition_has_no_qualifier(self):
        props = extract_propositions("Ada is a mathematician.")
        self.assertNotIn(ATTRIBUTED_TO_KEY, props[0].qualifiers)
        self.assertIsNone(props[0].get_attributed_to())

    def test_attributed_to_is_read_from_qualifiers_when_mutated_later(self):
        prop = Proposition("a", "b", "c")
        self.assertIsNone(prop.get_attributed_to())
        prop.qualifiers[ATTRIBUTED_TO_KEY] = "doc-9"
        self.assertEqual(prop.get_attributed_to(), "doc-9")

    def test_leading_determiner_is_trimmed_from_the_subject(self):
        props = extract_propositions("The Acme Corp owns Widget Ltd.")
        self.assertEqual(props[0].subject, "acme corp")

    def test_multiple_sentences_yield_multiple_propositions(self):
        props = extract_propositions("Ada works at Widget. Bob manages Acme Corp.")
        self.assertEqual(len(props), 2)
        self.assertEqual([p.subject for p in props], ["ada", "bob"])

    def test_sentence_without_a_predicate_is_skipped(self):
        self.assertEqual(extract_propositions("no verb here at all"), [])

    def test_empty_input_yields_no_propositions(self):
        self.assertEqual(extract_propositions(""), [])
        self.assertEqual(extract_propositions("   "), [])

    def test_propositions_preserve_text_order(self):
        props = extract_propositions("A owns B. C owns D. E owns F.")
        self.assertEqual([p.subject for p in props], ["a", "c", "e"])


class TestProvenanceIsDocumented(unittest.TestCase):
    """The citations a reader relies on must be in the source, not lost."""

    def test_store_docstring_pins_082_to_the_targets_consolidation_module(self):
        doc = EntityStore.__doc__ or ""
        self.assertIn("0.82", doc)
        self.assertIn("179", doc)
        self.assertIn("consolidation.py", doc)
        self.assertIn("95", doc)          # and names mem0's 0.95 explicitly
        self.assertIn("append-only", doc)

    def test_store_docstring_names_the_irreversibility_reason(self):
        doc = (EntityStore.__doc__ or "").lower()
        self.assertIn("irreversible", doc)
        self.assertIn("wrong merges", doc)

    def test_cosine_docstring_flags_itself_as_a_fallback(self):
        doc = _cosine.__doc__ or ""
        self.assertIn("LOCAL FALLBACK", doc)
        self.assertIn("mmemz.vecops.cosine", doc)

    def test_decide_type_docstring_says_it_is_not_the_llm_extractor(self):
        doc = (decide_type.__doc__ or "").lower()
        self.assertIn("rule-based placeholder", doc)
        self.assertIn("llm", doc)
        self.assertIn("schema", doc)

    def test_proposition_docstring_cites_promotion_and_the_qualifiers_carrier(self):
        doc = Proposition.__doc__ or ""
        self.assertIn("1244", doc)       # mem0 promotes attributed_to here
        self.assertIn("73", doc)         # target's Proposition
        self.assertIn("82", doc)         # target's qualifiers

    def test_module_docstring_states_the_local_cosine_is_temporary(self):
        doc = entities.__doc__ or ""
        self.assertIn("_cosine", doc)
        self.assertIn("mmemz.vecops.cosine", doc)


def resolve_with_scores(store: EntityStore, name: str) -> Entity:
    """Helper: resolve ``name`` and assert the fuzzy score cleared the bar.

    Kept out of the class body so the intent of the test reads as one line.
    """
    return store.resolve(name)


if __name__ == "__main__":
    unittest.main()
