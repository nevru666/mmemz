"""Tests for ``mmemz.graph`` -- both directions, the O(1) count, real traversal.


Stdlib ``unittest`` only. The properties under test are the ones the must-have
table in ``deliverables/mem0-port-spec.md`` ranks 2 and 3, plus the traversal mem0
cannot do at all:

* both directions agree after every mutation (the invariant a two-dict store
  lives or dies by);
* ``entity_memory_count`` is an O(1) read that still matches a brute-force
  recount after 100 random operations -- cheap *and* true;
* ``remove_memory`` actually subtracts (mem0's append-only list does not);
* ``merge_entities`` rewires without duplicating (mem0's ``extend`` does);
* ``expand`` reaches a node two hops away that a single hop cannot.
"""

from __future__ import annotations

import random
import unittest

from mmemz.graph import BipartiteGraph, GraphExpansion


def brute_force_count(graph: BipartiteGraph, entity_id: str) -> int:
    """Recount an entity's memories by scanning the *forward* direction only.

    Deliberately the slow, obviously-correct way: if the reverse index ever
    disagrees with this, the index is wrong and every count-based decay in
    ``mmemz.scoring`` is reading a lie.
    """
    return sum(
        1 for ents in graph._mem_to_ents.values() if entity_id in ents
    )


class BothDirectionsTest(unittest.TestCase):
    def test_add_memory_populates_both_directions(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["acme", "ada"])
        self.assertEqual(g.linked_entities("m1"), frozenset({"acme", "ada"}))
        self.assertEqual(g.linked_memory_ids("acme"), frozenset({"m1"}))
        self.assertEqual(g.linked_memory_ids("ada"), frozenset({"m1"}))
        self.assertTrue(g.is_consistent())

    def test_add_memory_is_idempotent(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["acme"])
        g.add_memory("m1", ["acme"])
        g.add_memory("m1", ["acme", "acme"])
        self.assertEqual(g.entity_memory_count("acme"), 1)
        self.assertEqual(g.linked_entities("m1"), frozenset({"acme"}))
        self.assertEqual(len(g), 1)

    def test_add_memory_accepts_a_generator(self):
        g = BipartiteGraph()
        g.add_memory("m1", (e for e in ("a", "b")))
        self.assertEqual(g.linked_entities("m1"), frozenset({"a", "b"}))
        self.assertTrue(g.is_consistent())

    def test_unknown_ids_return_empty_not_errors(self):
        g = BipartiteGraph()
        self.assertEqual(g.linked_entities("nope"), frozenset())
        self.assertEqual(g.linked_memory_ids("nope"), frozenset())
        self.assertEqual(g.entity_memory_count("nope"), 0)
        self.assertEqual(g.degree("nope"), 0)
        self.assertNotIn("nope", g)

    def test_results_are_frozen_copies_not_live_sets(self):
        """A caller must not be able to corrupt the index via the result."""
        g = BipartiteGraph()
        g.add_memory("m1", ["acme"])
        linked = g.linked_memory_ids("acme")
        with self.assertRaises(AttributeError):
            linked.add("m2")  # type: ignore[attr-defined]
        self.assertEqual(g.entity_memory_count("acme"), 1)

    def test_counts_and_len(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["a", "b"])
        g.add_memory("m2", ["b"])
        self.assertEqual(g.memory_count(), 2)
        self.assertEqual(g.entity_count(), 2)
        self.assertEqual(len(g), 2)
        self.assertEqual(g.entity_memory_count("b"), 2)
        self.assertIn("m1", g)
        self.assertNotIn("m3", g)

    def test_entity_less_memory_is_not_a_node(self):
        g = BipartiteGraph()
        g.add_memory("m1", [])
        self.assertEqual(len(g), 0)
        self.assertEqual(g.entity_count(), 0)
        self.assertTrue(g.is_consistent())


class RemoveTest(unittest.TestCase):
    """The subtraction mem0 lacks (``main.py:652`` is its only inverse repair)."""

    def test_remove_memory_subtracts_both_directions(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["acme"])
        g.add_memory("m2", ["acme"])
        self.assertEqual(g.entity_memory_count("acme"), 2)

        self.assertTrue(g.remove_memory("m1"))

        # The count DROPS. Under mem0's append-only list it would still read 2.
        self.assertEqual(g.entity_memory_count("acme"), 1)
        self.assertEqual(g.linked_memory_ids("acme"), frozenset({"m2"}))
        self.assertEqual(g.linked_entities("m1"), frozenset())
        self.assertNotIn("m1", g)
        self.assertTrue(g.is_consistent())

    def test_remove_last_link_drops_the_entity_row(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["acme"])
        g.remove_memory("m1")
        self.assertEqual(g.entity_count(), 0)
        self.assertEqual(g.entity_memory_count("acme"), 0)
        self.assertTrue(g.is_consistent())

    def test_remove_unknown_memory_reports_false(self):
        g = BipartiteGraph()
        self.assertFalse(g.remove_memory("ghost"))

    def test_remove_does_not_disturb_siblings(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["a", "b", "c"])
        g.add_memory("m2", ["b", "c"])
        g.remove_memory("m1")
        self.assertEqual(g.linked_entities("m2"), frozenset({"b", "c"}))
        self.assertEqual(g.linked_memory_ids("a"), frozenset())
        self.assertEqual(g.entity_memory_count("b"), 1)
        self.assertTrue(g.is_consistent())

    def test_set_memory_entities_replaces_without_leaking(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["a", "b"])
        g.set_memory_entities("m1", ["b", "c"])
        self.assertEqual(g.linked_entities("m1"), frozenset({"b", "c"}))
        self.assertEqual(g.linked_memory_ids("a"), frozenset())
        self.assertEqual(g.entity_memory_count("b"), 1)
        self.assertTrue(g.is_consistent())


class MergeTest(unittest.TestCase):
    """mem0's merge extends a list (``main.py:623-631``); this one rewires sets."""

    def test_merge_rewires_memories_to_the_survivor(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["acme corp"])
        g.add_memory("m2", ["acme corporation"])
        self.assertTrue(g.merge_entities("acme corporation", "acme corp"))

        self.assertEqual(g.linked_memory_ids("acme corporation"), frozenset({"m1", "m2"}))
        self.assertEqual(g.entity_memory_count("acme corporation"), 2)
        self.assertEqual(g.linked_entities("m1"), frozenset({"acme corporation"}))
        # The dropped key is gone entirely -- not left holding a stale count.
        self.assertEqual(g.entity_memory_count("acme corp"), 0)
        self.assertNotIn("acme corp", g._ent_to_mems)
        self.assertTrue(g.is_consistent())

    def test_merge_does_not_duplicate_a_memory_linked_to_both(self):
        """A memory linked to both names ends with ONE link, not two.

        mem0's ``extend`` would append the memory id twice, inflating
        ``num_linked`` and therefore the hub dampener's penalty.
        """
        g = BipartiteGraph()
        g.add_memory("m1", ["acme corp", "acme corporation"])
        g.merge_entities("acme corporation", "acme corp")
        self.assertEqual(g.linked_entities("m1"), frozenset({"acme corporation"}))
        self.assertEqual(g.entity_memory_count("acme corporation"), 1)
        self.assertTrue(g.is_consistent())

    def test_merge_into_missing_survivor_still_rewires(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["old"])
        self.assertTrue(g.merge_entities("new", "old"))
        self.assertEqual(g.linked_entities("m1"), frozenset({"new"}))
        self.assertTrue(g.is_consistent())

    def test_merge_edge_cases(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["a"])
        self.assertFalse(g.merge_entities("a", "a"))      # self-merge
        self.assertFalse(g.merge_entities("a", "ghost"))  # nothing to move
        self.assertEqual(g.linked_entities("m1"), frozenset({"a"}))

    def test_merge_then_remove_leaves_nothing_behind(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["a"])
        g.add_memory("m2", ["b"])
        g.merge_entities("a", "b")
        g.remove_memory("m1")
        g.remove_memory("m2")
        self.assertEqual(g.entity_count(), 0)
        self.assertEqual(len(g), 0)
        self.assertTrue(g.is_consistent())


class RandomisedConsistencyTest(unittest.TestCase):
    """The O(1) count must match a brute-force recount after 100 random ops."""

    def test_count_matches_brute_force_after_100_random_operations(self):
        rng = random.Random(20261026)  # fixed seed: a failure is reproducible
        g = BipartiteGraph()
        memories = [f"m{i}" for i in range(8)]
        entities = [f"e{i}" for i in range(6)]

        for step in range(100):
            op = rng.choice(("add", "remove", "merge", "set"))
            if op == "add":
                mid = rng.choice(memories)
                ents = rng.sample(entities, rng.randint(0, 3))
                g.add_memory(mid, ents)
            elif op == "remove":
                g.remove_memory(rng.choice(memories))
            elif op == "set":
                mid = rng.choice(memories)
                g.set_memory_entities(mid, rng.sample(entities, rng.randint(0, 3)))
            else:
                keep, drop = rng.sample(entities, 2)
                g.merge_entities(keep, drop)

            self.assertTrue(
                g.is_consistent(), f"directions diverged at step {step} ({op})"
            )
            for eid in entities:
                self.assertEqual(
                    g.entity_memory_count(eid),
                    brute_force_count(g, eid),
                    f"count disagreed with brute force at step {step} for {eid}",
                )
            # No empty sets may survive any operation.
            self.assertFalse(
                [k for k, v in g._mem_to_ents.items() if not v],
                f"empty forward row at step {step}",
            )
            self.assertFalse(
                [k for k, v in g._ent_to_mems.items() if not v],
                f"empty reverse row at step {step}",
            )


class ExpandTest(unittest.TestCase):
    """Multi-hop is the thing mem0 cannot do: it has no traversal at all."""

    def _two_hop_graph(self) -> BipartiteGraph:
        # m1 -shared_entity- m2 -only_here- m3
        g = BipartiteGraph()
        g.add_memory("m1", ["shared"])
        g.add_memory("m2", ["shared", "bridge"])
        g.add_memory("m3", ["bridge"])
        return g

    def test_expand_reaches_a_node_two_hops_away(self):
        """m2 is TWO edges from m1: m1 -> shared -> m2. Depth 2 must find it."""
        g = self._two_hop_graph()
        two = g.expand(["m1"], max_depth=2)
        self.assertIn("m2", two.memory_ids())
        self.assertIn("shared", two.entity_ids())
        self.assertEqual(two.max_depth_reached, 2)

    def test_single_hop_cannot_reach_it(self):
        """The contrast that proves the traversal is real, not a wide lookup.

        One hop from m1 reaches only the entity it directly links to. m2 is one
        *further* edge away and must be absent -- this is exactly the reach mem0
        cannot express at all, since it has no traversal.
        """
        g = self._two_hop_graph()
        one = g.expand(["m1"], max_depth=1)
        self.assertEqual(one.memory_ids(), ("m1",))
        self.assertEqual(one.entity_ids(), ("shared",))
        self.assertNotIn("m2", one.memory_ids())

    def test_expand_keeps_walking_to_four_hops(self):
        """m3 sits four edges out: m1 -> shared -> m2 -> bridge -> m3."""
        g = self._two_hop_graph()
        deep = g.expand(["m1"], max_depth=4)
        self.assertIn("m3", deep.memory_ids())
        self.assertEqual(deep.max_depth_reached, 4)
        shallow = g.expand(["m1"], max_depth=2)
        self.assertNotIn("m3", shallow.memory_ids())

    def test_expand_returns_a_graph_expansion_with_real_paths(self):
        g = self._two_hop_graph()
        exp = g.expand(["m1"], max_depth=3)
        self.assertIsInstance(exp, GraphExpansion)
        self.assertEqual(len(exp), 1)
        self.assertGreaterEqual(exp.max_depth_reached, 2)
        # A path is a genuine root-to-node walk and names entities in order.
        paths = exp.paths()
        self.assertTrue(paths)
        longest = max(paths, key=len)
        self.assertEqual(longest[0], "m1")
        self.assertGreaterEqual(len(longest), 3)
        self.assertTrue(all(h for h in longest))

    def test_expand_node_budget_is_respected(self):
        g = BipartiteGraph()
        for i in range(20):
            g.add_memory(f"m{i}", ["hub"])
        exp = g.expand(["m0"], max_depth=2, max_nodes=5)
        self.assertLessEqual(len(exp.node_ids), 5)
        self.assertIn(exp.results[0].terminated_by, {"budget", "frontier", "depth_cap"})

    def test_expand_of_unknown_memory_yields_no_results(self):
        g = BipartiteGraph()
        exp = g.expand(["ghost"])
        self.assertEqual(len(exp), 0)
        self.assertFalse(exp)
        self.assertEqual(exp.memory_ids(), ())

    def test_expand_alternates_kinds_and_never_repeats(self):
        g = self._two_hop_graph()
        ids = g.expand(["m1"], max_depth=4).node_ids
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.startswith(("m:", "e:")) for i in ids))


class RepresentationTest(unittest.TestCase):
    def test_repr_is_informative(self):
        g = BipartiteGraph()
        g.add_memory("m1", ["a", "b"])
        text = repr(g)
        self.assertIn("memories=1", text)
        self.assertIn("entities=2", text)
        self.assertIn("links=2", text)


if __name__ == "__main__":
    unittest.main()
