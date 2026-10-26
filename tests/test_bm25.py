"""Tests for mmemz.bm25 — the OPTIONAL / LATENT sigmoid normaliser.


Plain ``unittest``, stdlib only:

    python3 -m unittest discover -s tests -v
"""

import math
import unittest

from mmemz import bm25
from mmemz.bm25 import (
    BM25_SIGMOID_TABLE,
    bm25_params_for_query,
    bm25_sigmoid_params,
    is_used_by_service,
    normalize_bm25,
    normalize_bm25_for_query,
)


class TestParameterTable(unittest.TestCase):
    """The (midpoint, steepness) table from mem0/utils/scoring.py:31-40."""

    def test_table_rows_are_mem0s_values(self):
        expected = [
            (3, 5.0, 0.7),
            (6, 7.0, 0.6),
            (9, 9.0, 0.5),
            (15, 10.0, 0.5),
        ]
        for row in expected:
            self.assertIn(row, BM25_SIGMOID_TABLE)

    def test_last_row_is_the_catch_all(self):
        # The final band must absorb every longer query, not fall off the end.
        self.assertEqual(bm25_sigmoid_params(10_000), (12.0, 0.5))

    def test_bands_boundaries(self):
        self.assertEqual(bm25_sigmoid_params(1), (5.0, 0.7))
        self.assertEqual(bm25_sigmoid_params(3), (5.0, 0.7))
        self.assertEqual(bm25_sigmoid_params(4), (7.0, 0.6))
        self.assertEqual(bm25_sigmoid_params(6), (7.0, 0.6))
        self.assertEqual(bm25_sigmoid_params(7), (9.0, 0.5))
        self.assertEqual(bm25_sigmoid_params(9), (9.0, 0.5))
        self.assertEqual(bm25_sigmoid_params(10), (10.0, 0.5))
        self.assertEqual(bm25_sigmoid_params(15), (10.0, 0.5))
        self.assertEqual(bm25_sigmoid_params(16), (12.0, 0.5))

    def test_midpoint_grows_with_query_length(self):
        # Longer queries score higher raw, so the midpoint rises with them.
        midpoints = [bm25_sigmoid_params(n)[0] for n in (1, 4, 7, 10, 16)]
        for earlier, later in zip(midpoints, midpoints[1:]):
            self.assertLessEqual(earlier, later)

    def test_zero_or_negative_terms_clamps_to_one_term(self):
        self.assertEqual(bm25_sigmoid_params(0), (5.0, 0.7))
        self.assertEqual(bm25_sigmoid_params(-5), (5.0, 0.7))

    def test_query_token_count_selects_the_band(self):
        self.assertEqual(bm25_params_for_query("one two three"), (5.0, 0.7))
        self.assertEqual(
            bm25_params_for_query("a b c d e f g h i j"), (10.0, 0.5)
        )

    def test_empty_query_is_treated_as_one_term(self):
        self.assertEqual(bm25_params_for_query(""), (5.0, 0.7))
        self.assertEqual(bm25_params_for_query("   "), (5.0, 0.7))


class TestSigmoidMaths(unittest.TestCase):
    """``1/(1+exp(-k*(x-m)))`` — scoring.py:54."""

    def test_midpoint_maps_to_exactly_one_half(self):
        # The defining property: m is the score that normalises to 0.5.
        for midpoint, steepness in ((5.0, 0.7), (7.0, 0.6), (12.0, 0.5)):
            self.assertEqual(normalize_bm25(midpoint, midpoint, steepness), 0.5)

    def test_monotonic_increasing_in_raw_score(self):
        xs = [0.0, 1.0, 2.5, 5.0, 7.5, 10.0, 20.0, 50.0]
        ys = [normalize_bm25(x, 5.0, 0.7) for x in xs]
        for earlier, later in zip(ys, ys[1:]):
            self.assertLess(earlier, later)

    def test_monotonicity_holds_for_every_band(self):
        for num_terms in (1, 6, 12, 30):
            midpoint, steepness = bm25_sigmoid_params(num_terms)
            previous = -1.0
            for step in range(-40, 41):
                x = midpoint + step * 0.5
                value = normalize_bm25(x, midpoint, steepness)
                self.assertGreaterEqual(value, previous)
                previous = value

    def test_output_is_bounded_in_unit_interval(self):
        for x in (-1e9, -1000.0, -1.0, 0.0, 5.0, 1e9):
            value = normalize_bm25(x, 5.0, 0.7)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_saturates_towards_one_and_zero(self):
        self.assertGreater(normalize_bm25(1000.0, 5.0, 0.7), 0.999)
        self.assertLess(normalize_bm25(-1000.0, 5.0, 0.7), 0.001)

    def test_symmetry_about_the_midpoint(self):
        # f(m+d) + f(m-d) == 1.0: the rescaling does not favour either tail.
        midpoint, steepness = 7.0, 0.6
        for d in (0.5, 2.0, 9.0):
            high = normalize_bm25(midpoint + d, midpoint, steepness)
            low = normalize_bm25(midpoint - d, midpoint, steepness)
            self.assertAlmostEqual(high + low, 1.0, places=12)

    def test_zero_steepness_degenerates_to_a_constant_half(self):
        for x in (0.0, 3.0, 100.0):
            self.assertEqual(normalize_bm25(x, 5.0, 0.0), 0.5)

    def test_infinities_saturate_instead_of_overflowing(self):
        # math.exp would raise OverflowError without the guards.
        self.assertEqual(normalize_bm25(float("inf"), 5.0, 0.7), 1.0)
        self.assertEqual(normalize_bm25(float("-inf"), 5.0, 0.7), 0.0)

    def test_nan_yields_the_neutral_half(self):
        self.assertEqual(normalize_bm25(float("nan"), 5.0, 0.7), 0.5)

    def test_matches_the_direct_expression(self):
        midpoint, steepness, x = 5.0, 0.7, 9.25
        expected = 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))
        self.assertAlmostEqual(normalize_bm25(x, midpoint, steepness), expected)

    def test_query_convenience_wrapper_agrees_with_the_pieces(self):
        query = "graph memory service"
        midpoint, steepness = bm25_params_for_query(query)
        self.assertEqual(
            normalize_bm25_for_query(query, 5.0),
            normalize_bm25(5.0, midpoint, steepness),
        )

    def test_never_reorders_two_raw_scores(self):
        # The only reason a normaliser is safe before fusion: order preserved.
        midpoint, steepness = 9.0, 0.5
        raw = [1.0, 4.0, 8.9, 9.0, 9.1, 15.0, 30.0]
        norm = [normalize_bm25(x, midpoint, steepness) for x in raw]
        self.assertEqual(sorted(range(len(raw)), key=lambda i: raw[i]),
                         sorted(range(len(norm)), key=lambda i: norm[i]))


class TestLatentMarker(unittest.TestCase):
    """This module must advertise that it is not on the serving path."""

    def test_is_used_by_service_is_false(self):
        self.assertIs(is_used_by_service(), False)

    def test_module_docstring_marks_optional_latent_in_capitals(self):
        doc = bm25.__doc__ or ""
        self.assertIn("OPTIONAL / LATENT", doc)

    def test_module_docstring_forbids_use_in_an_rrf_stage(self):
        doc = (bm25.__doc__ or "").upper()
        self.assertIn("NEVER WIRED INTO AN RRF STAGE", doc)
        self.assertIn("RANKS", doc)

    def test_marker_docstring_explains_the_rank_vs_score_reason(self):
        doc = is_used_by_service.__doc__ or ""
        self.assertIn("RF", doc.upper())
        self.assertIn("rank", doc.lower())


if __name__ == "__main__":
    unittest.main()
