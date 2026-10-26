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
| `mmemz/entities.py` | written | Name normalisation, entity-type decision, `EntityStore` with exact-then-fuzzy dedupe (cosine >= 0.82), and `Proposition(subject, predicate, object)` carrying `attributed_to` as a qualifier. |
| `mmemz/scoring.py` | written | mem0 v2.2.1's **entity-relevance arm only**: bounded (ceiling 0.5, `1/(1+0.001(n-1)^2)` count decay, max across links) plus the legacy summing arm behind an opt-in mode. Imports `__future__` and `typing` only. It does **not** implement similarity or recency/importance weighting — those live in `service.py` (`_similarity`) and are not ported. |
| `mmemz/bm25.py` | written (latent) | Pure-Python BM25 lexical scoring for the fallback/lexical arm, with no numpy or scipy in the import path. |
| `mmemz/graph.py` | written | The bipartite store: two `dict[str, set[str]]` maps (memory->entities and the reverse), so `entity_memory_count` is O(1) and `remove_memory` is a real subtraction. Unweighted edges, no link cap; supersede lives in `service.consolidate`, not here. |
| `mmemz/service.py` | written | The six phases (`recognize`, `inject`, `extract`, `consolidate`, `return_entities`, `reconstruct`) plus `search`/`remember`/`forget`, and the bounded-vs-legacy mode flag. It wires graph + scoring; it does **not** call `bm25` (`is_used_by_service()` is `False`). |
| `mmemz/graph_walk.py` | vendored | Vendored, stdlib-only copy of `memory_service_v_2.3.0`'s multi-hop traversal — bounded frontier expansion with cycle guards. |
| `mmemz/vecops.py` | vendored (-numpy) | Vendored, stdlib-only copy of `memory_service_v_2.3.0`'s vector helpers: `cosine`, `cosine_batch`, `normalize`. There is no top-k helper in either tree. |

## Provenance

Two source trees are read, neither is modified:

- `/home/mmonk/Downloads/mem0-main(3)/mem0-main` — mem0 **v2.2.1**. Source of
  the entity/attribute memory maths: how entities are pulled out of a fact, how
  an attribute is matched against what is already stored, and how the entity
  arm is scored. Note that the arithmetic itself carries no numeric-stack
  dependency: the entity arm is `math` only. (mem0's *module* also imports
  qdrant/networkx/pydantic around it — mmemz takes the arithmetic and leaves
  those behind.)
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
2. **A per-memory bound on the entity arm's *value*.** A memory's entity boost
   is capped at 0.5 and damped by its own link count, so no amount of linking
   can push one memory's arm above that ceiling. This is a bound on the score,
   **not on the cost**: the arm still iterates the memory's links (O(links) per
   candidate, plus one O(links) count pass), measured at ~2 us for 10 links and
   ~1.8 ms for 10 000. Bounding the *work* would need a cached per-entity
   aggregate, which 0.1.0 does not have. This is the honest statement of the
   rule: the value is bounded, the traversal is not.
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
are the same arithmetic except at two terms — the per-link weight and how the
links combine — and together they invert the ranking, which is the whole reason
the port keeps both:

```
bounded:  m3 0.3849 (sim 0.3849, rel 0.0000, mult 1.0000)
          m2 0.2406 (sim 0.1925, rel 0.2500, mult 1.2500, matched=('london',))
legacy:   m2 0.6925 (sim 0.1925, rel 0.5000, mult 0.6925, matched=('london',))
          m3 0.3849 (sim 0.3849, rel 0.0000, mult 0.3849)
```

What actually produces that inversion, measured rather than described: **only
one** of the three query words resolves to an entity (`london`, at 0.5), and no
memory in the corpus matches two query entities (`max n_matched == 1`). So the
max-versus-sum term **never fires on this query** — it needs two matched links
to differ. The split comes from the two other differences:

- **the per-link weight**: the same link scores `0.5` legacy and `0.25` bounded;
- **the algebra**: legacy *adds* relevance to similarity (`0.1925 + 0.50 = 0.6925`,
  and it then reads `mult` as the total — so `mult < 1` *lowers* the score);
  bounded *multiplies* by a bounded factor (`0.1925 x 1.25 = 0.2406`).

`m2` therefore leads under legacy on the strength of its one weak link while
bounded keeps `m3`'s higher raw similarity on top. The max-vs-sum distinction is
real and separately tested -- with two links scoring `0.9` each,
`bounded_entity_relevance` returns `0.45` (the max link, held under the 0.5
ceiling) where `legacy_entity_relevance` returns `1.8` (the sum,
`test_legacy_cap_is_optional_and_off_by_default`) -- but it is not what this table
shows. mem0 v2.2.1 takes the max (`main.py:1822`); the target sums
(`memory_retrieval.py:778-780`). Bounded is the default.

**Consolidate — and its real limit.** Reconciliation is keyed on the *memory id*,
and it supersedes rather than appends when the same head gets a new object:

```
remember("m1", "Ada Lovelace is based in London.")
remember("m1", "Ada Lovelace is based in Manchester.")
  live:       [('ada lovelace', 'is', 'based in manchester')]
  superseded: [('ada lovelace', 'is', 'based in london')]     len(memories) == 1
```

The old proposition is retained with a `superseded_by` stamp instead of being
deleted, because "this changed" is itself knowledge — the same reason the
target's `_reconcile` keeps bitemporal bounds (`consolidation.py:1187`).
mem0's `:1209-1212` is the ADD branch that grows on a restatement.

**Two honest limits on that claim**, both measured:

1. The same text under a **different id** still accumulates — `remember("m1", …)`
   then `remember("m5", …)` with identical text leaves `len(memories) == 2`. The
   dedupe is per-id reconciliation, not cross-id memory deduplication. mem0's
   ADD-only defect is closed for *restatement of a known id*, and not otherwise.
2. `extract()` is a small lexical splitter (a `_PREDICATE_VERBS` list plus
   `_plausible_name`), not an LLM. It returns `[]` for a sentence whose verb is
   not in that list, and a memory with no propositions has nothing to reconcile —
   it links to its entities and stores no facts. The list covers the verbs this
   corpus needs (`hired`, `is`, `works`, `manages`, `reports`, `owns`, `signed`,
   `acquired`, …); a sentence outside it silently stores no fact. (This bit the
   test suite: until `hired` was added, `m1` and `m5` both stored zero
   propositions, so the restatement test passed on two empty lists and proved
   nothing. The test now asserts the fact exists first — see
   `test_restating_a_fact_does_not_grow_the_memory_count`.) In production the
   extractor is meant to be the model call; this one exists so the store's
   semantics are testable without a network.

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
the module-level `_expand_legal_forms` makes them textually identical before lookup, so
`len(store)` stays below the raw mention count.
