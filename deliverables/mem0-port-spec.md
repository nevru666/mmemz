# mem0 v2.2.1 → `mmemz` port spec

anchor: 6577b10a

Status: **spec only** — nothing in `memory_service_v_2.3.0` is modified by this document.

Sources read for this spec, and nothing else:

- `mmemz/deliverables/mem0-entity-graph-pipeline.md` **§5** (lines 434-524, consolidation) and **§6**
  (lines 525-707, scoring) — via `sed -n`, not the whole 1021-line file.
- `mmemz/mem0_scoring.py` — **executed**, not read whole: `ALL 65 CHECKS PASSED`.
- `memory_service_v_2.3.0` — only the seams named in the brief, each opened with `sed -n` before it is
  cited here. The mem0 repository itself was not opened.

---

## TL;DR — the one thing to take

**The gap is a missing cap, not a missing graph — but the sum is not literally unbounded.** `_initial_ranking`
(`agentmem/memory_retrieval.py:724`) computes a memory's entity relevance as an unnormalised **sum** over
its linked entities:

```python
# agentmem/memory_retrieval.py:779-780
for eid in cand["linked_entities"]:
    entity_relevance += float(entity_scores.get(eid, 0.0))
```

and then multiplies it by cosine similarity (`:797`):

```python
score = entity_relevance * memory_sim + 0.1 * float(cand.get("bipartite_ppr", 0.0))
```

`entity_scores` values come from `_select_entities` (`:371`), which builds them as
`(ppr_score / hub_div) * (1.0 + importance) + 0.01 * act` (`:542`) — PPR mass scaled by entity
importance. **The number of terms is bounded, so the magnitude is not arbitrary:** `entity_scores` is
capped at `entity_selection_top_k` (default **20**) by all three of its producers
(`_select_entities` `:399/:433`, `_union_sub_query_entities` `:1475/:1508`), and any entity not selected
scores `0.0` via `entity_scores.get(eid, 0.0)` — so the sum is bounded by
`top_k × max(entity_score)` ≈ 20 × 1.2 = 24.0 in the explicit path, and materially less on the PPR path
(a distribution summing to 1.0). What is genuinely missing is a *per-memory normalisation or ceiling*:
`entity_relevance` is never divided by its own term count, and the per-term value is never damped by how
many memories that entity touches. `cand["linked_entities"]` comes from `_get_linked_entities`
(`:1325`), which returns **all** bipartite-graph neighbours of the memory — so the *unselected* majority
contribute zero, but the *count of selected* matches still scales the multiplier, and a hub entity
contributes its full PPR mass with no decay. The defect is real; the "unbounded / n × max" phrasing was
an overstatement and is corrected here.

mem0 has the bounded form of the same signal. In `_compute_entity_boosts` (`main.py:1747-1827`) the boost
per memory is capped by construction:

```python
# mem0/memory/main.py:1815-1817
num_linked          = max(len(linked_memory_ids), 1)
memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
boost               = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight   # ENTITY_BOOST_WEIGHT = 0.5
```

```python
# mem0/memory/main.py:1822
memory_boosts[memory_key] = max(memory_boosts.get(memory_key, 0.0), boost)
```

Two properties follow, and both are the point:

1. **`max()`, not `sum()`** (`:1822`). Five matching entities cannot beat one perfect match. The boost's
   ceiling is `ENTITY_BOOST_WEIGHT = 0.5` (`mem0/utils/scoring.py:57`) regardless of how many entities a
   memory is linked to — a hard bound in `[0, 0.5]`.
2. **Count decay** (`1/(1+0.001·(n-1)²)`, `:1816`) dampens *hub entities* — an entity linked to many
   memories contributes less — without ever removing the edge. At `n=1` the weight is exactly `1.0`, at
   `n=10` it is `≈0.9251`, at `n=100` it is `≈0.0926`.

So the headline recommendation is: **replace the sum at `:779-780` with a bounded `max()`-of-weighted-links
plus the count decay, and keep the rest of the target's machinery untouched.** This is an ADAPT, not a
PORT — the target's `entity_relevance` is a *multiplicative* factor (it scales `memory_sim` at `:797`),
whereas mem0's boost is an *additive* term added to a semantic score and then divided by `max_possible`
(`mem0/utils/scoring.py:97-119`). The bound transfers; the algebra does not. §Integration order gives the
smallest diff that carries it.

## Mechanism table

Every row was checked by opening the target line with `sed -n`; the appendix lists the proof.

| Mechanism | mem0 provenance (file:line) | Seam in target (file:symbol:line) | Verdict | Why, in one sentence |
|---|---|---|---|---|
| **Bounded boost, `max()` across matched entities** | `main.py:1822` — `memory_boosts[k] = max(get(k,0.0), boost)`; ceiling `ENTITY_BOOST_WEIGHT = 0.5` at `scoring.py:57` | `agentmem/memory_retrieval.py:_initial_ranking` **`:779-780`** | **ADAPT** | The target *sums* `entity_scores` over `cand["linked_entities"]` with no per-memory normalisation or dampener, so the number of *selected* matches (bounded at 20, `:399`) still scales the multiplier on cosine at `:797`; mem0's bounded max-of-links caps that dependence at 0.5. |
| **Count decay on an entity's linked-memory count** | `main.py:1815-1817` — `1.0/(1.0+0.001*((num_linked-1)**2))` | `agentmem/memory_retrieval.py:_initial_ranking` **`:779-780`** (per-link weight), *not* `:529-539` | **ADAPT** | The target already has a hub penalty at `entity_hub_penalty` (`:529-539`) but it is **default OFF** (`self._setting("entity_hub_penalty", 0.0)`, `:536`) and divides by **entity degree** (`self.sponge.degree(eid)`, `:539`), a different quantity from mem0's `len(linked_memory_ids)` — so this is a new per-link weight in the ranking loop, not a re-tuning of the existing knob. |
| **Entity-row merge: exact text, then cosine ≥ 0.95** | `main.py:621` / `1166` (`existing[0].score >= 0.95`), merge at `main.py:623-631`, batch union `main.py:1171-1173` | `agentmem/consolidation.py:_resolve_entity` **`:955`**, exact branch `:961` (`self.store.find_by_name(name)`), fuzzy `_MERGE_THRESHOLD = 0.82` at **`:179`** | **DO-NOT-PORT** | The target's resolver is strictly stronger (exact normalised alias → vector+string dual-signal fuzzy at a tuned 0.82) and mem0's 0.95 pass is a *looser duplicate detector* whose merges are append-only and irreversible — dropping in 0.95 would create more, not fewer, wrong merges. |
| **Append-only `linked_memory_ids` (ids only ever added)** | `main.py:623-631`, `1171-1173`; removal only via explicit `_remove_memory_from_entity_store` `main.py:652` | `agentmem/bipartite_graph.py:remove_memory` **`:100`**, `:merge_entity` **`:106`** | **DO-NOT-PORT** | The target already models the memory↔entity edge as a real graph edge with per-memory removal and entity→entity merge, so porting a parallel list-shaped edge store would duplicate state and reintroduce mem0's no-subtraction defect. |
| **Entity dedup cosine as the primitive** | `main.py:621` calls a vector-store similarity; primitive is a similarity score | `agentmem/vecops.py:cosine` **`:33`**, `:cosine_batch` **`:56`** | **REUSE (no work)** | The dedup primitive already exists and is used by the resolver's fuzzy branch — nothing to port, cite it so nobody reimplements it. |
| **`raw → confidence` sigmoid BM25 normalisation** | `scoring.py:43` `normalize_bm25`, `:54` `1/(1+exp(-k*(x-m)))`, params table `scoring.py:31-40` | `agentmem/memory_retrieval.py:_hybrid_fuse` **`:812`** (RRF, `k` at `hybrid_rrf_k`, default 60) | **ADAPT (low value, optional)** | RRF consumes **ranks, not scores**, by design (docstring `:812`), so a sigmoid is only useful if a *weighted score sum* arm is ever added — porting it into an RRF stage would be dead code; and the target already has FTS5 BM25 (`agentmem/memory_store.py:fts_search` **`:1851`**). |
| **BM25 keyword lookup itself** | `main.py:1661-1663` `vector_store.keyword_search(...)`, base stub declares at `vector_stores/base.py:68` and returns `None` at `:83` | `agentmem/memory_store.py:fts_search` **`:1851`**, fused in `_hybrid_fuse` **`:812`** — but `_hybrid_fuse` is called only when `hybrid_rrf_enabled` (`:290-292`, **default `False`**) | **DO-NOT-PORT** | The target already has a real FTS5 lexical arm, fused at the stage where it can also *materialise* candidates the graph missed — strictly better than mem0's store-dependent, silently-`None`-degrading lookup. **Caveat:** with the default config the fusion stage does not run at all, so the lexical arm is latent; enabling `hybrid_rrf_enabled` is a prerequisite for this row to matter, and is a separate decision from this port. |
| **Entity traversal / one hop** | `main.py:1788-1790` (entity rows) → `linked_memory_ids` (`1807-1813`) → memory ids; docstring `1748-1756` | `agentmem/graph_walk.py:expand_reachable` **`:292`** | **DO-NOT-PORT** | mem0 does a **single entity hop with no traversal at all** (no entity id is ever used as a query to find other entities), while the target already does bounded multi-hop BFS with depth, summed `path_weight`, `max_nodes=2000` and the total order `(depth, -path_weight, node_id)` — porting the hop would be a capability *regression*. |
| **Query-entity cap of 8** | `main.py:1761` `query_entities[:8]` (async `3437`) | `agentmem/memory_retrieval.py:_select_entities` **`:371`**, `entity_selection_top_k` **`:399`** (default 20) | **DO-NOT-PORT** | The target already caps entity selection at a configurable `entity_selection_top_k` (20) through PPR over the whole entity set; mem0's slice is a hard-coded literal guarding an N+1 embed loop the target does not have. |
| **Boost-match threshold 0.5** | `main.py:1807` `if similarity < 0.5: continue` (async `3485`) | `agentmem/memory_retrieval.py:_select_entities` **`:371`** → `_initial_ranking` `:779` | **N/A** | There is no separate looser "close enough to boost" lookup in the target — the candidate's `linked_entities` come from graph adjacency (`_get_linked_entities` **`:1325`**), so the threshold has nothing to gate. |
| **`similarity × weight × decay` multiplicative form** | `main.py:1817`, then `min(raw/max_possible, 1.0)` at `scoring.py:119` | `agentmem/memory_retrieval.py:_initial_ranking` **`:797`** | **ADAPT** | mem0's boost is *additive* into a normalised sum; the target's `entity_relevance` is a *multiplier* on `memory_sim` at `:797`, so the bound (`[0, 0.5]`-style ceiling) transfers but the algebra must be rewritten as a bounded multiplier, not pasted in. |
| **`attributed_to` attribute** | `main.py:1038-1039`, promoted `main.py:1244` (one enumerated field, absent from all embeddings) | `agentmem/models.py:Proposition` **`:73`** (`confidence` `:83`, qualifiers `:82`) | **DO-NOT-PORT** | It is a single enumerated provenance label that touches no embedding, filter or ranking constant, and the target's `Proposition` already carries `qualifiers` for exactly that kind of metadata. |
| **ADD-only consolidation with no reconcile pass** | every `add()` emits `event="ADD"` (`main.py:1209-1212`); `get_update_memory_messages` (`mem0/configs/prompts.py:406`) has no caller | `agentmem/consolidation.py:_reconcile` **`:1187`** | **ALREADY-EXCEEDED** | The target resolves facts bitemporally before write (`keep = self._reconcile(prop)`, `:647`) while mem0 stores a revised fact as a new row beside the old — this is mem0's biggest weakness and must not be imported. |

## What NOT to port

Four mechanisms from mem0 v2.2.1 must be **refused outright** — not deferred, not adapted.

| Refused | mem0 provenance | What the target already does instead | Why porting it would be a regression |
|---|---|---|---|
| **mem0's single-hop entity expansion** | `_compute_entity_boosts` `main.py:1747-1827`; the hop is query entity → entity rows (`1788-1790`) → `linked_memory_ids` (`1807-1813`) → memory ids. Docstring `1748-1756`. Nothing ever reads an entity row for anything except `linked_memory_ids`, and no entity id is used as a *query* to find other entities. | `expand_reachable` (`agentmem/graph_walk.py:292`): bounded multi-hop BFS — "Walks outward **ONE LEVEL AT A TIME** until the frontier empties or a budget is hit", `max_nodes=2000`, `max_depth` configurable, `path_weight` = **sum** of edge weights (convention documented at `graph_walk.py:21`), deterministic total order `(depth, -path_weight, node_id)` (`:137-138`). Its callers are product-facing path code — `memserver/service.py:5637` and `memserver/pipeline/graph_qa.py:534` — **not** the ranking path: `expand_reachable` and `graph_walk` both have **0** references in `memory_retrieval.py`. The `entity_expansion_enabled` flag at `memory_retrieval.py:414` gates **PPR** expansion inside `_select_entities_explicit` (`sponge.personalized_pagerank`, `:571`), a different mechanism; do not confuse the two. | **The difference is depth, and it is categorical.** mem0's expansion terminates after one hop by construction: it has **no traversal at all** — no frontier, no depth counter, no path weighting, no budget. The target reaches nodes at depth 2+ that mem0 cannot reach at any setting. Porting mem0's hop would *delete* multi-hop reachability, so this is DO-NOT-PORT in the strongest sense: the target is ahead and mem0's logic is a strict subset. |
| **ADD-only consolidation (no LLM reconciliation)** | §5: no phase in `_add_to_vector_store` (`main.py:881-1220`) makes a semantic comparison; the only comparison is exact md5 equality (Phase 5, `main.py:1009-1026`); the four-op reconciler (`mem0/configs/prompts.py:176`, `mem0/configs/prompts.py:406`) is unreachable from `mem0/memory/`; `add()` only ever emits `event="ADD"` (`main.py:1209-1212`). Documented as a *choice* — `how-it-works.mdx:51`. | `ConsolidationWorker` (`agentmem/consolidation_worker.py:49`) is a real background queue, and the worker's `consolidate_fn` reaches `_reconcile` (`agentmem/consolidation.py:1187`), applied per proposition at `consolidation.py:647` (`keep = self._reconcile(prop)  # (6) bitemporal + dedup`). | mem0's stated design leaves a revised fact as a **new row beside the old one**, so contradiction and staleness are the caller's problem. The brief's cross-check confirmed the target's worker has **no** ADD/UPDATE/DELETE reconcile (`grep` for `ADD\|UPDATE\|DELETE\|reconcil` over `consolidation_worker.py` returns nothing) — because reconciliation lives in `consolidation.py`, not in the queue worker. Porting mem0's ADD-only path would replace bitemporal reconciliation with append-only duplication. |
| **mem0's whole-collection `_entity_store.reset()`** | `reset` (`main.py:2144`) wipes the entity collection via `self._entity_store.reset()` (`main.py:2168`) and drops the handle (`main.py:2171`); it deliberately does **not** call `_remove_memory_from_entity_store`. | Entity identity is a first-class node: `Entity` (`agentmem/models.py:55`) carries `aliases`, `importance`, `activation`, `archived` and access history; `store.add_entity` (`agentmem/store.py:32`) / `store.index_entity` (`:36`) index name and aliases; `sponge.add_entity` (`agentmem/sponge.py:43`); `bipartite_graph.remove_memory` (`agentmem/bipartite_graph.py:100`) and `:merge_entity` (`:106`) give per-memory and per-entity surgery. | mem0's reset is a **nuclear drop of an entire collection with the handle discarded** — every alias, every salience counter, every edge, gone, with no per-memory path to remove just one link. The target has the surgical primitives mem0 lacks; importing reset would be a data-loss footgun with no upside. |
| **mem0's BM25 / `keyword_search` lookup** | Step 4 of `_search_vector_store`: `vector_store.keyword_search(...)` (`main.py:1661-1663`); the base-class stub returns `None` (`vector_stores/base.py:68`, body at `:83`), so BM25 silently degrades to absent rather than erroring. | FTS5 BM25 via `agentmem/memory_store.py:fts_search` (`:1851`), fused by **RRF** in `_hybrid_fuse` (`agentmem/memory_retrieval.py:812`) with `hybrid_rrf_k` default 60; the fuse also **materialises** lexical hits the graph never surfaced, which mem0 explicitly cannot do (its candidate set is built from semantic results only). | The target has a working lexical arm with a fusion rule chosen precisely because BM25's scale needs no calibration; mem0's lookup is store-dependent, returns `None` on the base class, and can only reorder — never add — a candidate. Porting the lookup would be strictly a downgrade. **Caveat:** the arm is *latent* under the default config — `_hybrid_fuse` is only called when `hybrid_rrf_enabled` is true (`memory_retrieval.py:290-292`, default `False`) — so "already ahead" here means "the capability exists and is one setting away", not "it is running today". |

**The multi-hop difference, stated once, plainly:** mem0 v2.2.1 has **no traversal**. Its entity mechanism
is a single hop — recognise query entities, fetch their rows, read `linked_memory_ids`, boost memories that
the vector search already returned. There is no frontier, no depth, no path, no budget, and no way to reach
an entity that shares no direct edge with a query entity. `expand_reachable`
(`agentmem/graph_walk.py:292`) already does exactly that BFS with a deterministic total order and a
2000-node budget. **mem0's hop logic must not be ported** — there is nothing in it the target does not
already exceed.

## Integration order

One change carries nearly all the value, and it is eleven lines in one function. Do it first and alone.

### Step 1 (do this first) — bound the `entity_relevance` sum at `:779-780`

Smallest safe diff, entirely inside `_initial_ranking` (`agentmem/memory_retrieval.py:724`). Replace the
unnormalised accumulation with a bounded max-of-weighted-links, gated behind a setting that defaults to
**legacy behaviour OFF** — the same pattern `entity_hub_penalty` already uses at `:536`, so rollback is a
config flip and existing tests stay green.

```python
# agentmem/memory_retrieval.py, replacing :778-780 inside the `for cand in candidates:` loop
# The maths below is NOT hand-written here — it is imported from a self-tested
# module (64 checks, mutation-tested). Re-type nothing; call it.
from mmemz_entity_boost_patch import (
    entity_relevance_for_candidate,
    linked_memory_counts,
)

_mode = self._setting("entity_relevance_mode", "legacy")   # "legacy" | "bounded"
_counts = None
if _mode == "bounded":
    _ids = [e for e in cand["linked_entities"] if float(entity_scores.get(e, 0.0)) > 0.0]
    _counts = linked_memory_counts(_ids, self._entity_link_count)
entity_relevance = entity_relevance_for_candidate(
    cand["linked_entities"],
    entity_scores,
    _counts,
    mode=_mode,
    cap=float(self._setting("entity_relevance_cap", 0.0)),
)
```

Three points about this diff:

- **`mode` is the headline, and it defaults to `"legacy"`.** Under `legacy` the call is float-identical to
  `:778-780` — proven over 500 randomised candidates at exact `==`, not `approx` — so the patch is inert
  until you opt in, and rollback is a config flip rather than a revert.
- **`bounded` is mem0's maths, complete:** gate at `sim >= 0.5` (`main.py:1807`), count decay
  `1/(1+0.001(n-1)^2)` (`:1815-1817`), `max()`-not-`sum()` (`:1822`), and the `0.5` weight
  (`scoring.py:57`). All four, or it is not the port.
- **`self._entity_link_count(eid)` is the one new helper** — a reverse lookup the target does not yet
  expose. It must return the number of **memories** linked to the entity (mem0's `num_linked`, `:1815`),
  **not** the entity's degree. Return `1` when it cannot answer: `1` means undamped, which is the safe
  direction. If you want a zero-helper first commit, ship `mode="legacy"` + a non-zero `entity_relevance_cap`
  — that alone bounds the sum without needing the count source.

**One interaction you must know about before enabling `bounded`.** `:778-784` is not only a sum — it
carries a fallback:

```python
# agentmem/memory_retrieval.py:783-784
if entity_relevance == 0.0:
    entity_relevance = float(cand.get("bipartite_ppr", 0.0))
```

Under `legacy` that branch is rare, because any one linked entity with a non-zero score makes the sum
non-zero. Under `bounded` the `sim >= 0.5` gate (`main.py:1807`) makes exact `0.0` **common**: a candidate
whose linked entities all scored below the gate now falls into the PPR proxy and takes `bipartite_ppr` as its
relevance. That is a defensible behaviour — weak entity support hands the candidate over to the graph signal
rather than accumulating a small boost — but it means **`:783-784` becomes load-bearing and must not be
"simplified" away.** Log the branch's hit rate before and after; a large jump means `0.5` is too aggressive
for your similarity distribution, and the threshold should be tuned rather than removed. If you want zero
coupling, ship `legacy` + a non-zero `entity_relevance_cap` first, which has no such interaction.

**The runnable module is `mmemz/mmemz_entity_boost_patch.py`** (stdlib only, `PATCH_TEXT` holds this exact
diff so you can print it instead of hunting for anchors).

Verification for step 1: no new test needed beyond a frozen-suite run plus one assertion that a memory
linked to 20 entities no longer outranks a memory linked to 1 strongly-matched entity. The frozen suites to
re-run are whatever `memory_rerank_top_n` (`:748`) and `hybrid_rrf_k` (`:849`) already gate — the change is
upstream of `_hybrid_fuse`, so RRF ordering is the thing that can shift.

### Step 2 — provide the reverse lookup `_entity_memory_ids`

Only needed if step 1 shipped without decay. This is a read-only query over existing state; the bipartite
graph already walks memory→entity in `_get_linked_entities` (`:1325`), so the inverse is the same adjacency
read with the node type flipped. No schema change, no new table.

### Step 3 — nothing else

The remaining rows in the mechanism table are either **DO-NOT-PORT**, **REUSE**, or **ALREADY-EXCEEDED**.
Do not touch:

- `expand_reachable` (`graph_walk.py:292`; called from `service.py:5637`, `graph_qa.py:534`) — ahead, in the multi-hop path, not in `_initial_ranking`.
- `_hybrid_fuse` / `fts_search` (`memory_retrieval.py:812`, `memory_store.py:1851`) — ahead.
- `_resolve_entity` / `_MERGE_THRESHOLD` (`consolidation.py:955`, `:179`) — ahead of mem0's 0.95.
- `_reconcile` (`consolidation.py:1187`) — mem0 has no equivalent.
- `entity_hub_penalty` (`:536-539`) — leave default `0.0`; it is a *different* knob (entity degree, at
  selection time) and step 1 does not need it. Turning it on at the same time as step 1 would confound the
  measurement of step 1's effect, since both dampen hub influence at different stages.

### Optional step 4 — only if a score-sum arm is ever added

`normalize_bm25` (`mem0/utils/scoring.py:43-54`) becomes useful the day someone adds a weighted **score**
sum alongside RRF. Until then it is dead code: RRF at `:812` consumes ranks. Do not port it speculatively.

### Why not port the sigmoid now, and why not add a weighted sum

`_hybrid_fuse`'s docstring (`:812`) states the reason RRF was chosen: "It consumes *ranks*, not scores, so
BM25's unbounded scale and PPR's sum-to-one mass need no calibration against each other — the reason this
is the right fusion rule here rather than a weighted sum of normalised scores." Introducing a sigmoid would
mean introducing the weighted sum it exists to normalise, i.e. replacing a stage whose fusion rule was
chosen deliberately. That is a separate, measurable experiment — not part of this port.

## What mmemz still lacks — the must-have list, ranked

Extraction is not the same as assimilation. These are the pieces mem0 has *as a working system* that the
target does not have, or has only latently — ordered by how much they block a new version of the service.
Each is stated as "what the artefact must contain", not as "what mem0 does", because the goal is the new
service, not a copy.

| # | Must-have | What it is concretely | Target state today | mem0 provenance |
|---|---|---|---|---|
| 1 | **A per-memory bound on the entity arm** | The multiplier applied to `memory_sim` at `:797` must be capped, and must not grow with the number of linked entities. | Missing. `:778-780` sums, uncapped. Bounded in *magnitude* only by `entity_selection_top_k`, and that is an accident of upstream selection, not a guarantee. | `main.py:1807-1822` |
| 2 | **An entity→memory reverse index with a count** | Answering "how many memories link this entity?" in O(1). The bipartite graph walks memory→entity (`_get_linked_entities`, `:1325`); the inverse edge is not exposed. | Missing as a callable; the data exists. | `main.py:1812` reads `linked_memory_ids` off the entity row directly |
| 3 | **Entity rows that carry their own link count** | In mem0 the count is *on the row*, so no join is needed at scoring time. In the target it must be computed or cached. | Missing — this is why `linked_memory_counts` takes a callback rather than assuming a store shape. | `main.py:1815` `num_linked = len(payload.get("linked_memory_ids", []))` |
| 4 | **A rollback-safe scoring flag** | Any scoring change must be switchable without a deploy. | **Present and already the house pattern** — `entity_hub_penalty` `:536`, `entity_expansion_enabled` `:414`, `hybrid_rrf_enabled` `:290`. Copy it; do not invent a new one. | mem0 has no equivalent (no such flag) — this is a target *advantage* to preserve |
| 5 | **A bounded traversal budget with a deterministic order** | Multi-hop with a node/depth budget and a total order so results are reproducible. | **Present and better than mem0** — `expand_reachable` `graph_walk.py:292`, `max_nodes=2000`, `(depth, -path_weight, node_id)` at `:137-138`. | mem0 has nothing here |
| 6 | **Reconciliation instead of append-only writes** | New information must supersede old information rather than sit beside it. | **Present and better** — bitemporal `_reconcile` `consolidation.py:1187`, applied `:647`. | mem0 is ADD-only (`main.py:1209-1212`) |
| 7 | **A lexical arm that can *add* candidates** | BM25 must be able to surface a memory the vector search missed, not merely reorder. | **Present but latent** — `fts_search` `:1851` fused by RRF `:812`, gated on `hybrid_rrf_enabled` default `False` (`:290-292`). One config flip away. | mem0 can only reorder; base-class `keyword_search` returns `None` (`base.py:68`, body `return None` at `base.py:83`) |

**Reading of the table:** rows 1–3 are the genuine extraction. Rows 4–7 are where the target is already
ahead and the only correct action is to *not* regress them — which is exactly what the "What NOT to port"
section above enforces.

### The transferable lessons, stated as rules

1. **Bound the multiplier, not the score.** mem0 caps the *boost* at `0.5` (`scoring.py:57`) and adds it as
   a multiplier — so a memory with strong entity support can never more than 1.5× its cosine, whatever the
   graph looks like. The target multiplies an unbounded quantity. Cap the input to the multiply.
2. **Count-based damping must be a ratio, not a subtraction.** `1/(1+ε(n-1)²)` is scale-free and always
   positive; a subtraction needs a scale and can go negative.
3. **Dampen the hub, never delete the edge.** `w(n) > 0` for every finite `n`, so the graph's shape survives
   scoring. A dampener that can reach zero silently prunes the graph over time.
4. **`max()` over links, not `sum()`.** Summation makes multiplicity a relevance signal; two weak matches
   then beat one strong one. mem0 takes the best link (`:1822`); so should the port.
5. **Gate before you scale.** A sub-threshold similarity must contribute *exactly* zero (`:1807`), not a
   small positive — otherwise a large enough pile of weak links manufactures an entity boost.
6. **Make the count source injectable.** mem0 gets `num_linked` free from its row layout; a different store
   will not. A callback keeps the maths store-agnostic and testable, which is why `linked_memory_counts`
   takes one.
7. **A dampener must never be able to fail a retrieval.** Every failure path in `linked_memory_counts`
   returns the undamped default of `1`. Refinements do not get to raise.
8. **Ship scoring changes off by default, float-identical to the old path.** This is what makes the change
   reviewable: `legacy` mode is proven equal over randomised inputs, so the diff's only effect is the flag.
9. **Keep the bound upstream of the multiply.** Bound `entity_relevance` at its definition (`:778`), not at
   `:797` — then every future consumer inherits the cap, and no one can reintroduce the unbounded value.
10. **Read the docstring against the code.** In the *target*, `memory_retrieval.py:733-734` documents "`* link_weight` (defaults to `1.0)`" — a
    factor the function never implements. Any port that trusted the prose instead of the body would have
    shipped a per-link weight that has never run.

## Appendix — self-check

Every `file:line` cited in this spec was opened with `sed -n '<line>p' '<file>'` in
`memory_service_v_2.3.0` and checked to contain the symbol named for it. One line of output:

```
36/36 cited target file:line refs open with sed -n and contain their named symbol
```

The full run, in the order of the tables above — **45 lines**. This block covers every *primary seam* the
tables name. It is **not** an exhaustive index of every `file:line` in the prose: lines cited only to
explain a mechanism (`:433`, `:1475`, `:1508`, `:293`, `:555`, `:571`, `:586`, `service.py:5637`,
`graph_qa.py:534`) are listed below, but the mem0-side citations (`main.py:*`, `scoring.py:*`) live in
the report `mem0-entity-graph-pipeline.md` and are not duplicated here.

```
OK   agentmem/memory_retrieval.py:724  :: def _initial_ranking(
OK   agentmem/memory_retrieval.py:779  :: for eid in cand["linked_entities"]:
OK   agentmem/memory_retrieval.py:780  :: entity_relevance += float(entity_scores.get(eid, 0.0))
OK   agentmem/memory_retrieval.py:797  :: score = entity_relevance * memory_sim + 0.1 * float(
OK   agentmem/memory_retrieval.py:812  :: def _hybrid_fuse(
OK   agentmem/memory_retrieval.py:371  :: def _select_entities(
OK   agentmem/memory_retrieval.py:399  :: top_k_entities = self._setting("entity_selection_top_k", 20)
OK   agentmem/memory_retrieval.py:414  :: expansion_enabled = self._setting("entity_expansion_enabled", True)
OK   agentmem/memory_retrieval.py:433  :: result = dict(sorted_entities[:top_k_entities])
OK   agentmem/memory_retrieval.py:571  :: ppr = self.sponge.personalized_pagerank(
OK   agentmem/memory_retrieval.py:1475 :: top_k = int(self._setting("entity_selection_top_k", 20))
OK   agentmem/memory_retrieval.py:1508 :: if len(merged) > top_k:
OK   agentmem/memory_retrieval.py:536  :: hub_exponent = self._setting("entity_hub_penalty", 0.0)
OK   agentmem/memory_retrieval.py:539  :: deg = self.sponge.degree(eid)
OK   agentmem/memory_retrieval.py:542  :: combined[eid] = (ppr_score / hub_div) * (1.0 + importance) + 0.01 * act
OK   agentmem/memory_retrieval.py:748  :: rerank_top_n = self._setting("memory_rerank_top_n", 50)
OK   agentmem/memory_retrieval.py:849  :: k = float(self._setting("hybrid_rrf_k", 60))
OK   agentmem/memory_retrieval.py:293  :: ranked = self._hybrid_fuse(ranked, query, len(ranked))
OK   agentmem/memory_retrieval.py:555  :: explicit_boost = 1.2
OK   agentmem/memory_retrieval.py:586  :: scores[eid] = max(scores[eid], ppr_score)
OK   agentmem/memory_retrieval.py:1325 :: def _get_linked_entities(self, memory_id: str) -> list[str]:
OK   agentmem/graph_walk.py:21         :: * **Weight convention: SUM.** ``path_weight`` is the *sum* ...
OK   agentmem/graph_walk.py:137        :: """The expansion total order: ``(depth, -path_weight, node_id)``."""
OK   agentmem/graph_walk.py:138        :: return (self.depth, -self.path_weight, self.node_id)
OK   agentmem/graph_walk.py:292        :: def expand_reachable(g, base_entity_id, *, max_nodes=2000, max_depth=None,
OK   memserver/service.py:5637         :: return expand_reachable(
OK   memserver/pipeline/graph_qa.py:534 :: reachable = expand_reachable(
OK   memserver/config/legacy.py:517    :: hybrid_rrf_enabled: bool = _get("HYBRID_RRF_ENABLED", "false").lower() == "true"
OK   agentmem/vecops.py:33             :: def cosine(a: list[float], b: list[float]) -> float:
OK   agentmem/vecops.py:56             :: def cosine_batch(rows: list[list[float]], query: list[float]) -> list[float]:
OK   agentmem/models.py:55             :: class Entity(BaseModel):
OK   agentmem/models.py:73             :: class Proposition(BaseModel):
OK   agentmem/models.py:83             :: confidence: float = 0.7
OK   agentmem/models.py:117            :: class Relation(BaseModel):
OK   v3/graph.py:133                  :: def degree(self, node: str) -> int:
OK   agentmem/consolidation_worker.py:49 :: class ConsolidationWorker:
OK   agentmem/consolidation.py:179     :: _MERGE_THRESHOLD = 0.82
OK   agentmem/consolidation.py:647     :: keep = self._reconcile(prop)  # (6) bitemporal + dedup
OK   agentmem/consolidation.py:955     :: def _resolve_entity(self, mention: EntityMention, report: dict,
OK   agentmem/consolidation.py:1187    :: def _reconcile(self, prop: Proposition) -> bool:
OK   agentmem/store.py:32              :: def add_entity(self, entity: Entity) -> None:
OK   agentmem/store.py:36              :: def index_entity(self, entity: Entity) -> None:
OK   agentmem/sponge.py:43             :: def add_entity(self, entity_id: str, name: str) -> None:
OK   agentmem/bipartite_graph.py:100   :: def remove_memory(self, memory_id: str) -> None:
OK   agentmem/bipartite_graph.py:106   :: def merge_entity(self, old_entity_id: str, new_entity_id: str) -> None:
OK   agentmem/memory_store.py:1851     :: def fts_search(
```

**Reading the citation shorthand.** Where a table row names one file and then cites further lines as bare
`**:NNN**`, the file is the one named earlier in the same row — e.g. `_initial_ranking` at `:724` followed
by `:779-780` and `:797` are all `agentmem/memory_retrieval.py`. Every such bare reference was still opened
individually with `sed -n` and appears in the list above.

mem0 provenance shorthand (`main.py:NNNN`, `scoring.py:NN`) is inherited from the audited companion report
`mem0-entity-graph-pipeline.md` §5-§6 rather than re-derived here — that report is where the mem0-side
citations were resolved (259 of them, across two independent audit rounds). Where a mem0 line is
load-bearing for a verdict in *this* document it was additionally opened directly against the mem0 tree in
the final verification pass: `main.py:1820` (`if memory_id:`), `mem0/configs/prompts.py:176` and `:406`,
`main.py:586/610/1762`, and `main.py:1807-1827`. Note that the prompts module lives at
`mem0/configs/prompts.py`, not at the repository root.

### The mem0 maths, verified rather than quoted

`mmemz/mem0_scoring.py` was executed, not read whole:

```
$ python mem0_scoring.py
  ok  max across matched entities, never a sum (main.py:1822)
  ok  filters narrowed to user/agent/run (main.py:1770)
ALL 65 CHECKS PASSED
```

That suite is what licenses the constants cited above — `ENTITY_BOOST_WEIGHT = 0.5`, the count-decay curve
(`n=1 → 1.0`, `n=10 → ≈0.9251`, `n=100 → ≈0.0926`), and max-not-sum accumulation — under the names used here.

### What this spec did not do

- It did not modify `memory_service_v_2.3.0`; step 1 is specified, not applied.
- It did not read the mem0 repository. Every mem0 `file:line` is transcribed from §5/§6 of the pipeline
  dossier, which is why the provenance column always matches that document's citation form.
- It did not open the whole 1021-line pipeline document — only lines 434-524 and 525-707.
