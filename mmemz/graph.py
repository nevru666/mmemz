"""Bipartite memory<->entity graph -- both directions, so removal is real.


THE MEM0 DEFECT THIS MODULE EXISTS TO FIX
-----------------------------------------
mem0 stores the entity->memory edge as an **append-only list ON the entity row**
(``mem0/memory/main.py:1812``, ``linked_memory_ids``). Consequences, all three
observable in its source:

* the reverse direction is a *list*, so "how many memories link this entity?" is
  a ``len()`` on a list that only ever grows -- the count is cheap but it lies
  once anything is removed;
* merge folds one entity's list into another (``main.py:623-631``) by extending,
  which keeps duplicates and has no way to notice a memory that now links the
  survivor twice;
* the ONLY subtraction in the whole store is
  ``_remove_memory_from_entity_store`` (``main.py:652``), a full scan-and-rewrite
  of every entity row -- so removal is O(entities), and any path that forgets to
  call it leaks a phantom link forever. mem0's own delete path therefore has to
  remember to repair the inverse edge by hand.

mmemz keeps **both directions** as ``dict[str, set[str]]``. Sets, not lists, so a
re-add is idempotent and a merge is a union; and because the reverse dict is
maintained on every write, ``entity_memory_count`` is O(1) with no recomputation
and nothing to leak. That is rows 2 and 3 of the must-have table in
``deliverables/mem0-port-spec.md`` (an O(1) entity->memory reverse index, and
entity rows carrying their own link count) closed in one structure.

The maths lives elsewhere: this module is *storage*. Scoring the links it hands
out is ``mmemz.scoring``; traversal is the vendored ``mmemz.graph_walk``.

Stdlib only: ``dataclasses``, ``typing``.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from . import graph_walk

__all__ = ["BipartiteGraph", "GraphExpansion"]

class BipartiteGraph:
    """Two ``dict[str, set[str]]`` maps, kept in lockstep, plus a real subtraction.

    Invariant, asserted by the tests and holding after every public method: for
    every ``m, e``, ``e in self._mem_to_ents[m]`` iff ``m in self._ent_to_mems[e]``.
    Neither map ever holds an empty set -- a key with no edges is deleted, so
    ``entity_count`` is the number of entities that are actually linked to
    something, and iteration never yields a ghost.

    >>> g = BipartiteGraph()
    >>> g.add_memory("m1", ["acme"])
    >>> sorted(g.linked_entities("m1"))
    ['acme']
    >>> g.entity_memory_count("acme")
    1
    >>> g.remove_memory("m1")
    >>> g.entity_memory_count("acme")
    0
    """

    __slots__ = ("_mem_to_ents", "_ent_to_mems")

    def __init__(self) -> None:
        # memory_id -> {entity_id}
        self._mem_to_ents: dict[str, set[str]] = {}
        # entity_id -> {memory_id}   <- the reverse index mem0 does not expose
        self._ent_to_mems: dict[str, set[str]] = {}

    # --- writes -----------------------------------------------------------
    def add_memory(
        self, memory_id: str, entity_ids: Iterable[str]
    ) -> None:
        """Link ``memory_id`` to every entity in ``entity_ids``. Idempotent.

        Re-adding an existing pair is a set insert, so it is a no-op rather than
        a duplicate. That is the property mem0's list-form ``linked_memory_ids``
        does not have: ``main.py`` extends the list, and only the call sites'
        discipline keeps it unique.

        ``entity_ids`` may be a generator, a set, or a list; it is consumed once.
        Entities already linked to this memory are kept, so this is an upsert of
        the memory's edge set, not a replacement of it. Use
        :meth:`remove_memory` to clear, or :meth:`set_memory_entities` to replace.
        """
        ents = self._mem_to_ents.setdefault(memory_id, set())
        for eid in entity_ids:
            eid = str(eid)
            ents.add(eid)
            self._ent_to_mems.setdefault(eid, set()).add(memory_id)
        if not ents:
            # An entity-less memory is not a node in a *bipartite* graph; drop the
            # empty row so memory_count counts linked memories only.
            self._mem_to_ents.pop(memory_id, None)

    def set_memory_entities(
        self, memory_id: str, entity_ids: Iterable[str]
    ) -> None:
        """Replace ``memory_id``'s edge set, repairing both directions."""
        self.remove_memory(memory_id)
        self.add_memory(memory_id, entity_ids)

    def remove_memory(self, memory_id: str) -> bool:
        """Unlink ``memory_id`` from every entity, in **both** directions.

        THIS IS THE SUBTRACTION MEM0 LACKS. mem0's only inverse repair is
        ``_remove_memory_from_entity_store`` (``main.py:652``), which walks every
        entity row and rewrites its ``linked_memory_ids`` list -- O(entities) per
        deletion, and silently skipped by any code path that forgets to call it,
        which leaves a phantom link that keeps inflating that entity's count.

        Here it is O(degree): read the memory's entity set, delete the memory from
        each of those entities' reverse sets, then delete the memory's own row.
        Because the reverse sets *are* the counts, a removal cannot leave a stale
        count behind -- there is nothing to recompute.

        Returns ``True`` if the memory existed, ``False`` otherwise (so a caller
        can tell "already gone" from "removed" without a second lookup).
        """
        ents = self._mem_to_ents.pop(memory_id, None)
        if ents is None:
            return False
        for eid in ents:
            mems = self._ent_to_mems.get(eid)
            if mems is None:
                continue
            mems.discard(memory_id)
            if not mems:
                del self._ent_to_mems[eid]
        return True

    def merge_entities(self, keep_id: str, drop_id: str) -> bool:
        """Rewire every memory linked to ``drop_id`` onto ``keep_id``.

        The union is done through the memory rows (the authoritative forward
        direction), then the reverse row for ``drop_id`` is deleted. Doing it in
        that order matters: a memory linked to *both* names ends with one link to
        ``keep_id``, not two, because the forward side is a set. mem0's merge
        (``main.py:623-631``) extends the survivor's list, so the same memory can
        appear in it twice and inflate the hub dampener's ``num_linked``.

        ``keep_id == drop_id`` is a no-op and returns ``False``. A ``drop_id`` that
        does not exist returns ``False``. Returns ``True`` when a rewire happened.
        """
        if keep_id == drop_id:
            return False
        movers = self._ent_to_mems.pop(drop_id, None)
        if movers is None:
            return False
        keep = self._ent_to_mems.setdefault(keep_id, set())
        for mid in movers:
            keep.add(mid)
            ents = self._mem_to_ents.get(mid)
            if ents is not None:
                ents.discard(drop_id)
                ents.add(keep_id)
        return True

    # --- reads ------------------------------------------------------------
    def linked_entities(self, memory_id: str) -> frozenset[str]:
        """Entities linked to ``memory_id`` (empty frozenset if unknown)."""
        return frozenset(self._mem_to_ents.get(memory_id, ()))

    def linked_memory_ids(self, entity_id: str) -> frozenset[str]:
        """Memories linked to ``entity_id``. The inverse edge, as a callable.

        Frozen, so a caller cannot corrupt the index by mutating the result --
        the same reason mem0's list is dangerous: it hands out the live list.
        """
        return frozenset(self._ent_to_mems.get(entity_id, ()))

    def entity_memory_count(self, entity_id: str) -> int:
        """How many memories link ``entity_id``. **O(1)**, by construction.

        No walking, no recomputation, no join: the reverse dict's set length *is*
        the answer, which is what lets the scoring seam read a count at retrieval
        time without a scan. Rows 2 and 3 of the must-have table.

        >>> g = BipartiteGraph(); g.add_memory("m", ["e"]); g.entity_memory_count("e")
        1
        """
        return len(self._ent_to_mems.get(entity_id, ()))

    def degree(self, entity_id: str) -> int:
        """Alias of :meth:`entity_memory_count`, in graph vocabulary."""
        return self.entity_memory_count(entity_id)

    def memory_count(self) -> int:
        """Number of memories with at least one entity link."""
        return len(self._mem_to_ents)

    def entity_count(self) -> int:
        """Number of entities with at least one memory link."""
        return len(self._ent_to_mems)

    def __len__(self) -> int:
        """``len(graph)`` is the memory count -- memories are the primary noun."""
        return len(self._mem_to_ents)

    def __contains__(self, memory_id: object) -> bool:
        return memory_id in self._mem_to_ents

    def __repr__(self) -> str:
        return (
            f"BipartiteGraph(memories={self.memory_count()}, "
            f"entities={self.entity_count()}, "
            f"links={sum(len(v) for v in self._mem_to_ents.values())})"
        )

    # --- traversal (delegated, never reimplemented) -----------------------
    def neighbors(self, node_id: str) -> list[tuple[str, float]]:
        """``graph_walk``-shaped adjacency: ``[(nbr_node_id, weight), ...]``.

        The bipartite graph is projected onto ONE node space by prefixing memory
        ids with ``"m:"`` and entity ids with ``"e:"``, so a walk can alternate
        memory -> entity -> memory and never confuse the two id spaces. Weight is
        the Hebbian-style strength ``graph_walk`` sums along a path; here every
        edge is 1.0 because this store holds no edge strengths -- inventing a
        decay here would be a second source of truth for the maths.

        Built lazily from the two dicts. It is a *view*, not a copy: nothing is
        materialised beyond the one node's incident list.
        """
        kind, _, raw = node_id.partition(":")
        if kind == "m":
            mems, ents = self._mem_to_ents, self._ent_to_mems
            return [("e:" + e, 1.0) for e in sorted(mems.get(raw, ()))]
        if kind == "e":
            return [("m:" + m, 1.0) for m in sorted(self._ent_to_mems.get(raw, ()))]
        return []

    def has_node(self, node_id: str) -> bool:
        """Whether the prefixed node exists in this graph."""
        kind, _, raw = node_id.partition(":")
        if kind == "m":
            return raw in self._mem_to_ents
        if kind == "e":
            return raw in self._ent_to_mems
        return False

    def expand(
        self,
        memory_ids: Iterable[str],
        max_depth: int | None = None,
        max_nodes: int = 2000,
    ) -> "GraphExpansion":
        """Bounded multi-hop expansion from each seed memory.

        Delegates to the vendored :func:`mmemz.graph_walk.expand_reachable` --
        the *same* bounded BFS the target runs in production -- via the adapter
        above. Deliberately **not** a parallel implementation: a second BFS here
        would be a second set of tie-breaking rules, and the deterministic total
        order ``(depth, -path_weight, node_id)`` is the whole value of the
        vendored walk.

        Each seed is expanded from its ``"m:<id>"`` node, so the result alternates
        memory/entity and a memory two hops away (a sibling sharing an entity) is
        reachable when depth >= 2. Returns a :class:`GraphExpansion` holding one
        ``ExpansionResult`` per seed, in seed order.
        """
        seeds = [str(m) for m in memory_ids]
        results = tuple(
            graph_walk.expand_reachable(
                self, "m:" + mid, max_nodes=max_nodes, max_depth=max_depth
            )
            for mid in seeds
            if mid in self._mem_to_ents
        )
        return GraphExpansion(seeds, results)

    def is_consistent(self) -> bool:
        """Check the two directions agree exactly. Used by the tests and cheap
        enough to call from an assertion in a debug path."""
        for mid, ents in self._mem_to_ents.items():
            if not ents:
                return False
            for eid in ents:
                if mid not in self._ent_to_mems.get(eid, ()):
                    return False
        for eid, mems in self._ent_to_mems.items():
            if not mems:
                return False
            for mid in mems:
                if eid not in self._mem_to_ents.get(mid, ()):
                    return False
        return True

class GraphExpansion:
    """The result of :meth:`BipartiteGraph.expand` -- one walk per seed memory.

    A thin, *transparent* wrapper: it holds the vendored
    :class:`mmemz.graph_walk.ExpansionResult` objects untouched, so nothing about
    their ordering or budget semantics is redefined here. Its only job is to
    translate the prefixed node ids ("m:"/"e:") back into the caller's id space
    and to answer "which memories did we reach?" without the caller reparsing
    prefixes.

    >>> g = BipartiteGraph()
    >>> g.add_memory("m1", ["acme"]); g.add_memory("m2", ["acme"])
    >>> exp = g.expand(["m1"], max_depth=2)
    >>> sorted(exp.memory_ids())
    ['m1', 'm2']
    """

    __slots__ = ("seeds", "results")

    def __init__(self, seeds, results) -> None:
        self.seeds: tuple[str, ...] = tuple(seeds)
        self.results: tuple[graph_walk.ExpansionResult, ...] = tuple(results)

    def __len__(self) -> int:
        """Number of seed expansions (not the number of nodes reached)."""
        return len(self.results)

    def __iter__(self):
        return iter(self.results)

    def __bool__(self) -> bool:
        return bool(self.results)

    @property
    def node_ids(self) -> tuple[str, ...]:
        """Every reached node id across all seeds, prefixed, deduped, ordered."""
        out, seen = [], set()
        for res in self.results:
            for nid in res.node_ids:
                if nid not in seen:
                    seen.add(nid)
                    out.append(nid)
        return tuple(out)

    @property
    def max_depth_reached(self) -> int:
        """Deepest hop count reached by any seed (0 when nothing was reached)."""
        return max((r.max_depth_reached for r in self.results), default=0)

    def memory_ids(self) -> tuple[str, ...]:
        """Memory ids reached, in traversal order, seeds included."""
        return tuple(
            nid[2:] for nid in self.node_ids if nid.startswith("m:")
        )

    def entity_ids(self) -> tuple[str, ...]:
        """Entity ids reached, in traversal order."""
        return tuple(
            nid[2:] for nid in self.node_ids if nid.startswith("e:")
        )

    def paths(self) -> list[tuple[str, ...]]:
        """Root-to-node paths, as ``derive_trees`` yields them: real traversals.

        Unprefixed, longest first, one per distinct first hop then by descending
        path weight -- the vendored selection rule, reused rather than re-tuned.
        """
        out: list[tuple[str, ...]] = []
        for res in self.results:
            for tree in graph_walk.derive_trees(res.root_id, res):
                out.append(tuple(n.partition(":")[2] for n in tree.path))
        return out

    def to_dict(self):
        """Serialise the underlying vendored results verbatim."""
        return {
            "seeds": list(self.seeds),
            "results": [r.to_dict() for r in self.results],
        }

    def __repr__(self) -> str:
        return (
            f"GraphExpansion(seeds={len(self.seeds)}, "
            f"nodes={len(self.node_ids)}, "
            f"max_depth={self.max_depth_reached})"
        )
