# mmemz

**Version: 0.1.0**

## What this is

`mmemz` is a new version of the graph content memory service. It is built by
extracting the entity/attribute memory maths out of mem0 v2.2.1 — the part that
decides what an extracted fact is allowed to attach to, and how an extracted
attribute replaces or extends what is already stored — and grounding that maths
in the proven structures of `memory_service_v_2.3.0`: its multi-hop graph walk,
its vector operations, and a service layer that has already survived real
traffic. The extraction is deliberately narrow. mmemz is not a fork of mem0 and
not a fork of the memory service; it takes the scoring/reconciliation logic from
the first, the traversal and service scaffolding from the second, and joins them
behind one small, importable package. This is **0.1.0**: all seven modules below
exist, pass a 193-test stdlib suite, and carry no third-party import.

## Module map

Every module below exists in this tree and is exercised by the test suite. The
"status" column distinguishes code written here from code vendored out of
`memory_service_v_2.3.0`, because those two carry different provenance and
different rules for editing.

| Module | Status | Purpose |
| --- | --- | --- |
| `mmemz/entities.py` | written | Entity and attribute extraction/merge: the per-memory entity arm, the attribute reconciliation rules lifted from mem0 v2.2.1, and supersede-by-default updates. |
| `mmemz/scoring.py` | written | mem0 v2.2.1's scoring maths — similarity, recency/importance weighting, and the entity boost — reimplemented against `math` and `json` only. |
| `mmemz/bm25.py` | written (latent) | Pure-Python BM25 lexical scoring for the fallback/lexical arm, with no numpy or scipy in the import path. |
| `mmemz/graph.py` | written | The graph content store: nodes, typed edges, per-memory link caps, and the reconcile-by-supersede update path. |
| `mmemz/service.py` | written | Service layer: the search/write API surface, flag handling for rollback-safe behaviour, and the wiring that binds graph + scoring + bm25 together. |
| `mmemz/graph_walk.py` | vendored | Vendored, stdlib-only copy of `memory_service_v_2.3.0`'s multi-hop traversal — bounded frontier expansion with cycle guards. |
| `mmemz/vecops.py` | vendored (-numpy) | Vendored, stdlib-only copy of `memory_service_v_2.3.0`'s vector helpers: cosine, normalisation, small top-k. |

## Provenance

Two source trees are read, neither is modified:

- `/home/mmonk/Downloads/mem0-main(3)/mem0-main` — mem0 **v2.2.1**. Source of
  the entity/attribute memory maths: how entities are pulled out of a fact, how
  an attribute is matched against what is already stored, and how the entity
  arm is scored. Note that this maths uses only `math` and `json` from the
  standard library — it carries no numeric-stack dependency of its own.
- `/home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0` — the graph content
  memory service. Source of the proven structures mmemz is grounded in: the
  multi-hop `graph_walk` traversal, the `vecops` vector helpers, and the shape
  of the service layer. Its `graph_walk.py` and `vecops.py` are stdlib-only and
  are the modules vendored here. That tree also pins fastapi, pydantic, qdrant,
  networkx, scipy and numpy; mmemz deliberately depends on none of them, so it
  can be dropped into that tree without re-resolving its pins.

## Design rules

1. **Stdlib only.** Zero third-party runtime dependencies. The package must
   import and run on a bare CPython ≥ 3.10 with an empty site-packages, and must
   be droppable into `memory_service_v_2.3.0` without touching its pinned
   dependency set. Any proposed dependency has to argue against both trees.
2. **A per-memory bound on the entity arm.** Entity scoring is capped per
   memory. Cost must never scale with how many links a memory has accumulated —
   a hub node with a thousand edges must not make recall of that memory more
   expensive than one with three. The bound is on the arm, not on the graph.
3. **Reconcile by supersede, not ADD-only.** Updates resolve against what is
   already stored: a new attribute value supersedes the old one rather than
   piling up beside it. ADD-only accumulation is treated as the bug it is, and
   supersede records enough history to audit what was replaced.
4. **Multi-hop traversal, not single-hop.** Retrieval walks a bounded frontier
   (via the vendored `graph_walk`) instead of stopping at direct neighbours, so
   a question that needs two or three links of context can still reach it —
   with cycle guards and a hard depth/visited cap.
5. **Rollback-safe flags.** Behaviour changes ship behind flags whose default is
   the known-good path. Flipping a flag off returns the service to its previous
   behaviour without a code revert and without corrupting stored state, so a new
   arm can be turned on, measured, and turned off in production.

## What 0.1.0 actually does — one run, pasted

The six phases run end to end over a fixed six-memory corpus
(`tests/test_service.py`). `python3 -m unittest discover -s tests` → `Ran 193
tests ... OK`.

**Recognize + inject + rank.** Query `"Lovelace Babbage London"`. The two modes
are the same arithmetic except at one term, and that term inverts the ranking —
which is the whole reason the port keeps both:

```
bounded:  m3 0.3849 (sim 0.3849, rel 0.0000, mult 1.0000)
          m2 0.2406 (sim 0.1925, rel 0.2500, mult 1.2500, matched=('london',))
legacy:   m2 0.6925 (sim 0.1925, rel 0.5000, mult 0.6925, matched=('london',))
          m3 0.3849 (sim 0.3849, rel 0.0000, mult 0.3849)
```

`m2` is linked to two of the query's three entities weakly; `m3` to one of them
strongly. Legacy *sums* the links and puts `m2` first; bounded takes the *max*,
gates it, and puts `m3` first. mem0 v2.2.1 takes the max (`main.py:1822`); the
target sums (`memory_retrieval.py:778-780`). Bounded is the default.

**Consolidate.** `m5` restates `m1` word for word. `remember()` reconciles rather
than appending, so `memory_count_delta == 0` and `len(service.memories)` does not
move. mem0's `:1209-1212` is the ADD branch that grows on a restatement.

**Reconstruct.** `reconstruct("m1")` returns the traversal, not a flat list —
`len(path) == 10` here, every hop naming the entity it crossed:

```
{'from': 'm1', 'to': 'acme corporation', 'depth': 1, 'kind': 'e'}
{'from': 'm1', 'to': 'ada lovelace',     'depth': 1, 'kind': 'e'}
{'from': 'acme corporation', 'to': 'm2', 'depth': 2, 'kind': 'm'}
...
{'from': 'm3', 'to': 'charles babbage',  'depth': 3, 'kind': 'e'}
```

The path comes from the vendored bounded BFS in `mmemz/graph_walk.py`. mem0 has
no path to return: its entity hop is one edge.

**Alias dedupe.** `"Acme Corp"` and `"ACME Corporation"` reach one row because
`service._expand_legal_forms` makes them textually identical before lookup, so
`len(store)` stays below the raw mention count.
