# --- PROVENANCE (vendored, do not hand-edit the body) -----------------------
#
# Source:       /home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0/agentmem/graph_walk.py
# Source lines: 460
# Vendored on:  2026-10-26
# Why vendored rather than reimplemented: stdlib-only, and strictly stronger than
# mem0's single entity hop: bounded multi-hop BFS where mem0 does no traversal at all.
#
# Everything below line 1 of the source is byte-identical to it. The ONLY additions
# are this comment block and the SOURCE_PATH / SOURCE_LINES constants after __all__.
# Verify with: diff <source> mmemz/graph_walk.py
# ---------------------------------------------------------------------------

"""Deterministic bounded graph expansion + "10 trees" derivation.


Two pure, side-effect-free primitives over the merged sponge digraph
(:class:`agentmem.sponge.Sponge`), used as the *first-pass filter* that decides
WHICH parts of the graph are worth handing to an expensive downstream reader:

* :func:`expand_reachable` -- bounded, level-by-level (BFS) expansion from one
  base entity, returning every reached node with its depth, the hop that
  reached it and an accumulated path weight.
* :func:`derive_trees` -- turn that reachable set into up to ``n`` candidate
  root-to-leaf paths ("trees"), never more than the graph actually supports.

Both are deterministic: the same graph always yields the same bytes out of
``to_dict()``. Nothing here touches a DB, a network, an LLM, or any existing
retrieval path -- the module only *reads* the store through public accessors.

Design notes (kept in the code because they are load-bearing, not decoration):

* **Weight convention: SUM.** ``path_weight`` is the *sum* of the edge weights
  along the path (root = ``0.0``). Sponge weights are Hebbian strengths in
  ``(0, 1]``; a *product* would make weight decay multiplicatively with depth,
  so every deep path would lose to every shallow one no matter how strong its
  links -- the opposite of the "expand until all graph depth is covered"
  policy. A sum is depth-monotone for positive weights and therefore ranks a
  path by how much relational evidence it accumulated.
* **Total order.** Nodes sort by ``(depth, -path_weight, node_id)`` and trees by
  ``(-path_weight, depth, node_ids)`` -- the same three components (depth,
  weight, id) with the weight sign flipped for trees because "best tree" means
  *heaviest*. Every component is a total order, so ties never depend on dict or
  set iteration order.
* **Provenance is injected.** A sponge edge carries reified relation ids
  (``rels``) and episode evidence, but *not* fact ids; resolving a fact id needs
  the store/DB. So the ``fact_id`` field is optional and an optional
  ``provenance_lookup(source_id, target_id)`` callable is threaded through
  instead of hard-wiring a session into the walk.

Public API
----------
``expand_reachable(g, base_entity_id, *, max_nodes=2000, max_depth=None,
provenance_lookup=None) -> ExpansionResult``
``derive_trees(root_id, reachable, n=10) -> list[Tree]``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Sequence

__all__ = [
    "Hop",
    "ReachedNode",
    "ExpansionResult",
    "Tree",
    "expand_reachable",
    "derive_trees",
]

#: Absolute path of the file this module was vendored from.
SOURCE_PATH = "/home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0/agentmem/graph_walk.py"

#: Line count of the source file at vendoring time (verified with wc -l).
SOURCE_LINES = 460

#: ``provenance_lookup(source_id, target_id)`` -> ``None`` | fact id | ids.
ProvenanceLookup = Callable[[str, str], Any]

#: Hard ceiling on candidate paths enumerated by :func:`derive_trees`. A DAG of
#: depth d can have exponentially many root paths; this bounds the work while
#: staying deterministic (the DFS visits children in sorted order, so the cut is
#: always the same cut).
MAX_CANDIDATE_PATHS = 20_000

_TERMINATIONS = frozenset({"frontier", "budget", "depth_cap"})


def _fact_ids(raw: Any) -> tuple[str, ...]:
    """Normalise whatever a ``provenance_lookup`` returned into a tuple of ids."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    try:
        return tuple(str(x) for x in raw if x)
    except TypeError:  # a non-iterable scalar
        return (str(raw),)


# --- result types ---------------------------------------------------------
@dataclass(frozen=True)
class Hop:
    """One traversed edge, as the store actually exposes it.

    ``edge_id`` is the merged edge's identity: ``add_or_strengthen`` merges an
    entity pair direction-agnostically into ONE bounded edge, so the stored
    ``(source, target)`` orientation of the digraph *is* that identity
    (``"src|tgt"``). ``rel_ids`` carries the reified :class:`agentmem.models.
    Relation` ids living on that edge, and ``fact_id`` the optional provenance
    resolved by the caller's lookup.
    """

    edge_id: str
    source: str
    target: str
    weight: float
    fact_id: str | None = None
    rel_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "weight": self.weight,
            "fact_id": self.fact_id,
            "rel_ids": list(self.rel_ids),
        }


@dataclass(frozen=True)
class ReachedNode:
    """A node reached by :func:`expand_reachable`.

    ``depth`` is its BFS depth (root = 0), ``path_weight`` the SUM of edge
    weights from the root, and ``parent_id``/``edge_id``/``fact_id`` describe the
    hop that first reached it at that minimum depth.
    """

    node_id: str
    depth: int
    path_weight: float
    parent_id: str | None = None
    edge_id: str | None = None
    source: str | None = None
    target: str | None = None
    hop_weight: float = 0.0
    fact_id: str | None = None
    rel_ids: tuple[str, ...] = ()

    @property
    def order_key(self) -> tuple[int, float, str]:
        """The expansion total order: ``(depth, -path_weight, node_id)``."""
        return (self.depth, -self.path_weight, self.node_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "depth": self.depth,
            "path_weight": self.path_weight,
            "parent_id": self.parent_id,
            "edge_id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "hop_weight": self.hop_weight,
            "fact_id": self.fact_id,
            "rel_ids": list(self.rel_ids),
        }


@dataclass(frozen=True)
class ExpansionResult:
    """Everything :func:`expand_reachable` reached, plus how it stopped."""

    root_id: str
    nodes: tuple[ReachedNode, ...]
    terminated_by: str
    max_nodes: int
    max_depth: int | None = None

    def __post_init__(self) -> None:
        if self.terminated_by not in _TERMINATIONS:
            raise ValueError(
                f"terminated_by must be one of {sorted(_TERMINATIONS)}, "
                f"got {self.terminated_by!r}"
            )

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Iterator[ReachedNode]:
        return iter(self.nodes)

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(n.node_id for n in self.nodes)

    @property
    def max_depth_reached(self) -> int:
        return max((n.depth for n in self.nodes), default=0)

    def get(self, node_id: str) -> ReachedNode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def by_depth(self) -> dict[int, list[ReachedNode]]:
        out: dict[int, list[ReachedNode]] = {}
        for n in self.nodes:
            out.setdefault(n.depth, []).append(n)
        return out

    def as_hops(self) -> dict[str, Hop]:
        """``node_id -> Hop`` for every non-root node (the edge that reached it)."""
        out: dict[str, Hop] = {}
        for n in self.nodes:
            if n.depth == 0 or n.edge_id is None:
                continue
            out[n.node_id] = Hop(
                edge_id=n.edge_id,
                source=n.source or "",
                target=n.target or "",
                weight=n.hop_weight,
                fact_id=n.fact_id,
                rel_ids=n.rel_ids,
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "terminated_by": self.terminated_by,
            "max_nodes": self.max_nodes,
            "max_depth": self.max_depth,
            "num_nodes": len(self.nodes),
            "max_depth_reached": self.max_depth_reached,
            "nodes": [n.to_dict() for n in self.nodes],
        }


@dataclass(frozen=True)
class Tree:
    """One candidate tree: a concrete root-to-leaf path plus its hops."""

    root_id: str
    path: tuple[str, ...]
    hops: tuple[Hop, ...]
    path_weight: float

    @property
    def leaf_id(self) -> str:
        return self.path[-1]

    @property
    def depth(self) -> int:
        return len(self.path) - 1

    @property
    def order_key(self) -> tuple[float, int, tuple[str, ...]]:
        """The tree total order: ``(-path_weight, depth, path)``."""
        return (-self.path_weight, self.depth, self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "path": list(self.path),
            "hops": [h.to_dict() for h in self.hops],
            "path_weight": self.path_weight,
            "leaf_id": self.leaf_id,
            "depth": self.depth,
        }


def _incident(g, nid):
    """Incident edges as ``(nbr, edge_id, src, tgt, weight, rel_ids)``.

    Prefers the additive read-only ``Sponge.iter_edges`` (real stored
    orientation); falls back to the older public ``neighbors`` for duck-typed
    graphs, where the edge id can only be synthesised from the pair.
    """
    f = getattr(g, "iter_edges", None)
    if f is not None:
        return list(f(nid))
    nb = getattr(g, "neighbors", None)
    if nb is None:
        raise TypeError("graph needs iter_edges(nid) or neighbors(nid)")
    return [(v, "%s|%s" % (nid, v), nid, v, float(w), ()) for v, w in nb(nid)]


def _has_node(g, nid):
    """Whether the store holds ``nid`` (``neighbors`` cannot tell an isolated
    node from an unknown one -- both are ``[]``)."""
    inner = getattr(g, "g", None)
    if inner is not None and hasattr(inner, "__contains__"):
        return nid in inner
    probe = getattr(g, "has_node", None)
    return bool(probe(nid)) if probe is not None else True


def _better(a, b):
    """Deterministic pick between two hops reaching one node at equal depth:
    heavier accumulated path, then edge id, then parent id."""
    return ((-a.path_weight, a.edge_id or "", a.parent_id or "")
            < (-b.path_weight, b.edge_id or "", b.parent_id or ""))


def expand_reachable(g, base_entity_id, *, max_nodes=2000, max_depth=None,
                     provenance_lookup=None):
    """Bounded deterministic breadth-first expansion over the sponge digraph.

    Walks outward ONE LEVEL AT A TIME until the frontier empties or a budget is
    hit. ``max_depth=None`` grows until the frontier empties (cover as much
    graph depth as needed); ``max_nodes`` (counting the root) is the safety
    budget. Ordering is the total order ``(depth, -path_weight, node_id)``, so
    identical graphs give byte-identical ``to_dict()`` output.

    ``path_weight`` convention: the **SUM** of edge weights along the path from
    the root (root = ``0.0``). Sponge weights are Hebbian strengths in
    ``(0, 1]``; a product would shrink multiplicatively with depth so every deep
    path would lose to every shallow one regardless of link strength, which
    contradicts unbounded-depth expansion. A sum is depth-monotone for positive
    weights and ranks a path by accumulated relational evidence.

    Cycle safety: a ``visited`` set on node id -- each node is expanded exactly
    once at its minimum depth, self-loops are ignored, and a cycle cannot hang
    the walk or duplicate a node. ``terminated_by`` is ``"frontier"``,
    ``"budget"`` or ``"depth_cap"``.

    ``fact_id`` is optional: the sponge edge carries no fact id, so the caller
    may inject ``provenance_lookup(source_id, target_id)`` returning one fact id
    (or an iterable -- the first is kept). No DB session is reached from here
    and the field is never invented. An unknown root yields an empty result;
    a known-but-isolated root yields exactly one node at depth 0.
    """
    if max_nodes <= 0:
        raise ValueError("max_nodes must be >= 1")
    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth must be >= 0 or None")

    def fact(src, tgt):
        if provenance_lookup is None:
            return None
        ids = _fact_ids(provenance_lookup(src, tgt))
        return ids[0] if ids else None

    if not _has_node(g, base_entity_id):
        return ExpansionResult(base_entity_id, (), "frontier", max_nodes,
                               max_depth)

    reached = {base_entity_id: ReachedNode(base_entity_id, 0, 0.0)}
    frontier = [base_entity_id]
    terminated_by, depth = "frontier", 0
    while frontier:
        if max_depth is not None and depth >= max_depth:
            terminated_by = "depth_cap"
            break
        depth += 1
        level = {}
        for pid in frontier:
            parent = reached[pid]
            for nbr, eid, src, tgt, w, rels in _incident(g, pid):
                if nbr == pid or nbr in reached:
                    continue          # self-loop / already expanded (cycle)
                w = float(w)
                cand = ReachedNode(nbr, depth, parent.path_weight + w, pid,
                                   eid, src, tgt, w, fact(src, tgt),
                                   tuple(rels))
                prev = level.get(nbr)
                if prev is None or _better(cand, prev):
                    level[nbr] = cand
        if not level:
            break                     # frontier emptied
        lvl = sorted(level.values(), key=lambda n: n.order_key)
        room = max_nodes - len(reached)
        if len(lvl) > room:
            lvl = lvl[:max(room, 0)]
            terminated_by = "budget"
        for node in lvl:
            reached[node.node_id] = node
        frontier = [n.node_id for n in lvl]
        if terminated_by == "budget":
            break
    nodes = tuple(sorted(reached.values(), key=lambda n: n.order_key))
    return ExpansionResult(base_entity_id, nodes, terminated_by, max_nodes,
                           max_depth)


def _path_to(node, reached):
    """Root path + hops for ``node`` from the BFS parent links.

    Each reached node has exactly ONE parent (the hop that first reached it at
    its minimum depth), so the path is unique and reconstructing it is
    deterministic without enumerating exponentially many walks. The ``seen``
    guard is belt-and-braces: a malformed parent cycle cannot loop here.
    """
    path, hops, seen, cur = [node.node_id], [], {node.node_id}, node
    while cur.parent_id is not None:
        parent = reached.get(cur.parent_id)
        if parent is None or parent.node_id in seen:
            break
        hops.append(Hop(cur.edge_id or "", cur.source or "", cur.target or "",
                        cur.hop_weight, cur.fact_id, cur.rel_ids))
        path.append(parent.node_id)
        seen.add(parent.node_id)
        cur = parent
    path.reverse()
    hops.reverse()
    return tuple(path), tuple(hops)


def _is_prefix(short, long):
    return len(short) < len(long) and long[:len(short)] == short


def derive_trees(root_id, reachable, n=10):
    """Up to ``n`` candidate trees -- concrete root->leaf paths -- from ``reachable``.

    Each ``Tree`` carries ``path`` (ordered node ids root->leaf) and ``hops``
    (one :class:`Hop` per hop: edge id + optional ``fact_id``).

    Selection: **best tree per distinct first hop, then fill the remaining slots
    by descending path weight.** Plain weight-top-N returns N near-identical
    paths that differ only in the last node -- one strong first hop with N
    leaves -- so "N trees" degenerates into one tree and the reader learns
    nothing from N-1 of them. Diversifying on the first hop forces the candidate
    set to open on different branches before leftover slots go to raw weight.

    Prefix elimination: a candidate that is a strict prefix of another candidate
    is dropped before selection, so a truncated path is never returned beside its
    extension. Ordering is ``(-path_weight, depth, path)`` -- the same (depth,
    weight, node id) components as the expansion total order -- so two calls on
    one graph return the same trees in the same order.

    Fewer than ``n`` distinct trees means exactly that many are returned:
    nothing is padded and no path is fabricated. ``n <= 0``, or a root absent
    from ``reachable``, yields ``[]``. Prefix elimination is O(k^2) in the
    reached-node count, which is why the expansion above is budgeted.
    """
    if n <= 0:
        return []
    reached = {x.node_id: x for x in reachable.nodes}
    if root_id not in reached:
        return []
    cands = []
    for node in reachable.nodes[:MAX_CANDIDATE_PATHS]:
        if node.depth == 0 or node.node_id == root_id:
            continue
        path, hops = _path_to(node, reached)
        if len(path) < 2 or path[0] != root_id or len(hops) != len(path) - 1:
            continue
        cands.append(Tree(root_id, path, hops, node.path_weight))
    if not cands:
        return []
    # Keep a candidate unless it is itself a strict prefix of another candidate:
    # the EXTENSION survives, the truncated path is dropped.
    maximal = [t for t in cands
               if not any(_is_prefix(t.path, o.path)
                          for o in cands if o is not t)]
    maximal.sort(key=lambda t: t.order_key)
    picked, firsts = [], set()
    for tree in maximal:                       # pass 1: one per first hop
        if tree.path[1] in firsts:
            continue
        firsts.add(tree.path[1])
        picked.append(tree)
        if len(picked) >= n:
            return picked
    rest = []                                  # pass 2: by descending weight
    for tree in maximal:
        if tree in picked:
            continue
        rest.append(tree)
        if len(picked) + len(rest) >= n:
            break
    return picked + rest
