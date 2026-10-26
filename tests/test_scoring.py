"""Tests for mmemz.scoring — the bounded entity-relevance maths.


Plain ``unittest``, stdlib only:

    python3 -m unittest discover -s tests -v

Every assertion here exists to pin a number that the port must not change.
The two mutation tests at the bottom (max-not-sum, and the 0.5 ceiling) are the
reason the suite exists: each one fails loudly if its mutation is applied.
"""

import math
import unittest

from mmemz import scoring
from mmemz.scoring import (
    BOOST_MATCH_THRESHOLD,
    DECAY_COEFF,
    DEFAULT_MODE,
    ENTITY_BOOST_WEIGHT,
    MAX_MULTIPLIER,
    MODE_BOUNDED,
    MODE_LEGACY,
    NEUTRAL_MULTIPLIER,
    bounded_boost,
    bounded_entity_relevance,
    bounded_multiplier,
    entity_relevance_for_candidate,
    legacy_entity_relevance,
    linked_memory_counts,
    memory_count_weight,
    score_candidate,
)


class TestMemoryCountWeight(unittest.TestCase):
    """The decay ``1/(1 + 0.001*((n-1)**2))`` (main.py:1815-1817)."""

    def test_weight_is_exactly_one_at_one_link(self):
        # A cold-start entity is never penalised: w(1) == 1.0 EXACTLY.
        self.assertEqual(memory_count_weight(1), 1.0)

    def test_weight_matches_published_values(self):
        self.assertAlmostEqual(memory_count_weight(10), 0.9251, places=4)
        self.assertAlmostEqual(memory_count_weight(100), 0.0926, places=4)

    def test_weight_is_strictly_decreasing_in_n(self):
        ns = [1, 2, 3, 5, 10, 50, 100, 1000]
        weights = [memory_count_weight(n) for n in ns]
        for earlier, later in zip(weights, weights[1:]):
            self.assertGreater(earlier, later, f"not decreasing at n={ns}")

    def test_weight_stays_strictly_positive(self):
        # Damped, never deleted: an edge must remain reachable by other means.
        for n in (1, 2, 5, 50, 1000, 100000):
            self.assertGreater(memory_count_weight(n), 0.0)

    def test_weight_clamps_nonpositive_counts_to_undamped(self):
        # A dampener must never fail a retrieval, and n < 1 is meaningless.
        self.assertEqual(memory_count_weight(0), 1.0)
        self.assertEqual(memory_count_weight(-7), 1.0)

    def test_decay_coefficient_is_mem0s(self):
        # The literal 0.001 from main.py:1816 is part of the contract.
        self.assertEqual(DECAY_COEFF, 0.001)
        self.assertAlmostEqual(memory_count_weight(11), 1.0 / (1.0 + 0.001 * 100))


class TestBoundedBoost(unittest.TestCase):
    """One entity's gated, damped contribution."""

    def test_perfect_match_single_link_is_half(self):
        self.assertEqual(bounded_boost(1.0, 1), 0.5)

    def test_gate_excludes_below_threshold(self):
        # main.py:1807 -- returns exact zero, NOT a small positive number.
        self.assertEqual(bounded_boost(0.49, 1), 0.0)

    def test_gate_includes_exactly_at_threshold(self):
        # The comparison is `<`, so 0.5 exactly is INCLUDED (boundary test).
        self.assertEqual(bounded_boost(0.5, 1), 0.25)

    def test_gate_threshold_constant_is_mem0s(self):
        self.assertEqual(BOOST_MATCH_THRESHOLD, 0.5)

    def test_boost_applies_the_decay(self):
        self.assertAlmostEqual(
            bounded_boost(1.0, 10), 0.5 * (1.0 / (1.0 + 0.001 * 81))
        )

    def test_boost_never_exceeds_the_weight_for_any_count(self):
        for n in (1, 2, 5, 50, 1000):
            self.assertLessEqual(bounded_boost(1.0, n), ENTITY_BOOST_WEIGHT)

    def test_boost_scales_linearly_with_similarity(self):
        self.assertAlmostEqual(bounded_boost(0.8, 1), 0.5 * 0.8)
        self.assertAlmostEqual(bounded_boost(1.0, 1) - bounded_boost(0.8, 1), 0.1)


class TestMaxNeverSum(unittest.TestCase):
    """The point of the port: MAX across links (main.py:1822)."""

    def test_two_equal_entities_return_one_boost_not_two(self):
        # 0.45, not 0.9. This is the assertion that binds the module.
        self.assertAlmostEqual(
            bounded_entity_relevance(["a", "b"], {"a": 0.9, "b": 0.9}, {"a": 1, "b": 1}),
            0.45,
        )

    def test_two_entities_are_bounded_by_the_best_one(self):
        # boosts would be 0.4 and 0.35 if summed -> 0.75. Max is 0.4.
        result = bounded_entity_relevance(
            ["a", "b"], {"a": 0.8, "b": 0.7}, {"a": 1, "b": 1}
        )
        self.assertLessEqual(result, 0.4 + 1e-12)
        self.assertNotAlmostEqual(result, 0.75)

    def test_max_with_many_entities_equals_max_with_one(self):
        many = {f"e{i}": 0.6 + 0.01 * i for i in range(20)}
        counts = {k: 1 for k in many}
        one = bounded_entity_relevance(["e19"], many, counts)
        all_ = bounded_entity_relevance(list(many), many, counts)
        self.assertEqual(one, all_)

    def test_ceiling_never_exceeded_for_any_link_count(self):
        # The headline property: no number of entities exceeds 0.5.
        for n in (1, 2, 5, 50, 1000):
            scores = {f"e{i}": 1.0 for i in range(n)}
            counts = {k: 1 for k in scores}
            self.assertLessEqual(
                bounded_entity_relevance(list(scores), scores, counts),
                ENTITY_BOOST_WEIGHT,
                f"ceiling breached at n={n}",
            )

    def test_ceiling_holds_even_when_every_entity_is_a_hub(self):
        scores = {f"e{i}": 1.0 for i in range(50)}
        counts = {k: 1000 for k in scores}
        self.assertLessEqual(
            bounded_entity_relevance(list(scores), scores, counts), ENTITY_BOOST_WEIGHT
        )

    def test_decay_selects_the_best_damped_link(self):
        # 'a' has the higher similarity but is a hub; 'b' is undamped and wins.
        result = bounded_entity_relevance(
            ["a", "b"], {"a": 1.0, "b": 0.9}, {"a": 100, "b": 1}
        )
        self.assertAlmostEqual(result, 0.45)

    def test_missing_entity_defaults_to_zero_as_at_779(self):
        self.assertEqual(bounded_entity_relevance(["ghost"], {}), 0.0)

    def test_no_linked_entities_yields_zero(self):
        self.assertEqual(bounded_entity_relevance([], {}), 0.0)

    def test_zero_scoring_entities_are_skipped_without_error(self):
        self.assertEqual(
            bounded_entity_relevance(["a", "b"], {"a": 0.0, "b": 0.0}), 0.0
        )


class TestLegacyMode(unittest.TestCase):
    """Rollback path: the target's summing maths, kept callable."""

    def test_legacy_sums_where_bounded_takes_max(self):
        scores = {"a": 0.9, "b": 0.9}
        counts = {"a": 1, "b": 1}
        self.assertAlmostEqual(legacy_entity_relevance(["a", "b"], scores), 1.8)
        self.assertAlmostEqual(
            bounded_entity_relevance(["a", "b"], scores, counts), 0.45
        )

    def test_modes_are_provably_different_on_a_shared_input(self):
        scores = {"a": 0.9, "b": 0.9}
        args = (["a", "b"], scores, {"a": 1, "b": 1})
        legacy = entity_relevance_for_candidate(*args, mode=MODE_LEGACY)
        bounded = entity_relevance_for_candidate(*args, mode=MODE_BOUNDED)
        self.assertNotAlmostEqual(legacy, bounded)
        self.assertGreater(legacy, bounded)

    def test_bounded_is_the_default_mode(self):
        # The default must be the ported maths, not the legacy sum.
        self.assertEqual(DEFAULT_MODE, MODE_BOUNDED)
        args = (["a", "b"], {"a": 0.9, "b": 0.9}, {"a": 1, "b": 1})
        self.assertEqual(
            entity_relevance_for_candidate(*args),
            entity_relevance_for_candidate(*args, mode=MODE_BOUNDED),
        )

    def test_legacy_cap_is_optional_and_off_by_default(self):
        scores = {"a": 0.9, "b": 0.9}
        self.assertAlmostEqual(
            legacy_entity_relevance(["a", "b"], scores, cap=0.0), 1.8
        )
        self.assertAlmostEqual(
            legacy_entity_relevance(["a", "b"], scores, cap=1.0), 1.0
        )

    def test_legacy_reproduces_the_get_default(self):
        self.assertEqual(legacy_entity_relevance(["ghost"], {}), 0.0)

    def test_unknown_mode_raises_rather_than_falling_back(self):
        # A config typo must not silently pick either behaviour.
        with self.assertRaises(ValueError):
            entity_relevance_for_candidate(["a"], {"a": 1.0}, mode="banana")


class TestZeroFallback(unittest.TestCase):
    """The load-bearing ``0.0 -> 1.0`` branch of the multiplicative form."""

    def test_zero_relevance_maps_to_neutral_multiplier(self):
        # 0.0 is poison in a product: it must mean "no change", not "no score".
        self.assertEqual(bounded_multiplier(0.0), NEUTRAL_MULTIPLIER)
        self.assertEqual(NEUTRAL_MULTIPLIER, 1.0)

    def test_negative_relevance_also_maps_to_neutral(self):
        self.assertEqual(bounded_multiplier(-0.3), 1.0)

    def test_nan_relevance_does_not_poison_the_sort_key(self):
        self.assertEqual(bounded_multiplier(float("nan")), 1.0)

    def test_full_relevance_maps_to_the_max_multiplier(self):
        self.assertEqual(bounded_multiplier(ENTITY_BOOST_WEIGHT), 1.5)
        self.assertEqual(MAX_MULTIPLIER, 1.5)

    def test_multiplier_is_clamped_at_the_ceiling(self):
        # Feeds a relevance far above the ceiling on purpose: without the
        # clamp this returns 1.9 and the [1.0, 1.5] range is broken.
        self.assertEqual(bounded_multiplier(0.9), 1.5)
        self.assertEqual(bounded_multiplier(50.0), 1.5)

    def test_gated_out_candidate_scores_on_similarity_alone(self):
        # Every link below the gate -> relevance 0.0 -> multiplier 1.0.
        score = score_candidate(0.7, ["a", "b"], {"a": 0.4, "b": 0.49})
        self.assertAlmostEqual(score, 0.7)

    def test_no_entity_evidence_changes_nothing(self):
        self.assertEqual(score_candidate(0.7, [], {}), 0.7)

    def test_best_link_boosts_a_perfect_candidate_to_the_max(self):
        self.assertAlmostEqual(score_candidate(1.0, ["a"], {"a": 1.0}, {"a": 1}), 1.5)

    def test_legacy_score_keeps_the_targets_additive_form(self):
        score = score_candidate(
            0.5, ["a", "b"], {"a": 0.9, "b": 0.9}, mode=MODE_LEGACY
        )
        self.assertAlmostEqual(score, 0.5 + 1.8)


class TestLinkedMemoryCounts(unittest.TestCase):
    """The count map that feeds the decay, and its never-raise contract."""

    def test_counts_come_from_the_caller(self):
        self.assertEqual(
            linked_memory_counts(["a", "b"], {"a": 7}.get), {"a": 7, "b": 1}
        )

    def test_failure_falls_back_to_undamped(self):
        self.assertEqual(linked_memory_counts(["a"], lambda eid: 1 / 0), {"a": 1})

    def test_nonpositive_count_falls_back_to_undamped(self):
        self.assertEqual(linked_memory_counts(["a"], lambda eid: 0), {"a": 1})


class TestProvenanceIsDocumented(unittest.TestCase):
    """Cheap guard that the citations a reader depends on are present."""

    def test_module_docstring_states_the_modes_and_the_traversal_refusal(self):
        doc = scoring.__doc__ or ""
        self.assertIn("bounded", doc)
        self.assertIn("legacy", doc)
        self.assertIn("rollback", doc)
        self.assertIn("traversal", doc.lower())

    def test_relevance_docstring_cites_max_at_1822(self):
        self.assertIn("1822", bounded_entity_relevance.__doc__)

    def test_boost_docstring_cites_the_gate_and_decay(self):
        doc = bounded_boost.__doc__
        self.assertIn("1807", doc)
        self.assertIn("1815-1817", doc)


class TestMutationsAreCaught(unittest.TestCase):
    """Both required mutations must fail this suite. See the mutation report."""

    def test_mutation_a_sum_instead_of_max_fails_here(self):
        # If bounded_entity_relevance summed its links this would be 0.9.
        result = bounded_entity_relevance(
            ["a", "b"], {"a": 0.9, "b": 0.9}, {"a": 1, "b": 1}
        )
        self.assertAlmostEqual(result, 0.45, places=12)
        self.assertLess(result, 0.6, "max-across-links looks like a sum")

    def test_mutation_b_ceiling_removal_fails_here(self):
        # If the 0.5 clamp were dropped, 0.9 + 1.0 == 1.9 would escape.
        self.assertLessEqual(
            bounded_multiplier(0.9), MAX_MULTIPLIER, "ceiling is not enforced"
        )
        self.assertLessEqual(
            bounded_multiplier(1e6), MAX_MULTIPLIER, "ceiling is not enforced"
        )


if __name__ == "__main__":
    unittest.main()
