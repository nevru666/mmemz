# Verification pass — five corrections + three UNEVIDENCED items

anchor: 5b0292b0

Read-only pass. No artefact was modified. Scratch mutants lived in
`.shrimp/scratch/verify-5b0292b0/` and are deleted.

Repo shorthand used below: `T=/home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0`,
`M="/home/mmonk/Downloads/mem0-main(3)/mem0-main"`.

## Part 1 — the five corrections

### 1. Spec headline (was WRONG) — CORRECTED CLAIM HOLDS

Commands and output:

```
$ awk 'NR>=776 && NR<=800' $T/agentmem/memory_retrieval.py
778:             entity_relevance = 0.0
779:             for eid in cand["linked_entities"]:
780:                 entity_relevance += float(entity_scores.get(eid, 0.0))
783:             if entity_relevance == 0.0:
784:                 entity_relevance = float(cand.get("bipartite_ppr", 0.0))
797:             score = entity_relevance * memory_sim + 0.1 * float(
798:                 cand.get("bipartite_ppr", 0.0)
799:             )
rc=0
```

```
$ sed -n '399p' $T/agentmem/memory_retrieval.py
        top_k_entities = self._setting("entity_selection_top_k", 20)
$ awk 'NR>=430 && NR<=433' $T/agentmem/memory_retrieval.py
430:         sorted_entities = sorted(
431:             merged.items(), key=lambda kv: kv[1], reverse=True
432:         )
433:         result = dict(sorted_entities[:top_k_entities])
$ awk 'NR>=1474 && NR<=1475' $T/agentmem/memory_retrieval.py
1474:         weight = float(self._setting("sponge_decompose_part_weight", 1.0))
1475:         top_k = int(self._setting("entity_selection_top_k", 20))
$ awk 'NR>=1508 && NR<=1510' $T/agentmem/memory_retrieval.py
1508:         if len(merged) > top_k:
1509:             merged = dict(sorted(merged.items(),
1510:                                  key=lambda kv: kv[1], reverse=True)[:top_k])
rc=0
```

- Cap at both cited sites: REAL (`:433` truncates the merged dict; `:1508` truncates the union).
- `.get(eid, 0.0)` default: REAL (`:780`), so unselected entities contribute 0.0.
- `entity_selection_top_k` default 20: REAL — `$ grep -n entity_selection_top_k $T/memserver/config/legacy.py`
  → `1570:    entity_selection_top_k: int = int(_get("ENTITY_SELECTION_TOP_K", "20"))` (rc=0).
- `1.2` explicit boost: REAL — `sponge`-side `$ sed -n '555p'` → `        explicit_boost = 1.2` (rc=0), and
  `_select_entities_explicit` merges with `max()` at `:585-586`, so a PPR score (<=1.0) cannot lift it above
  1.2. Hence `20 x 1.2 = 24.0` is a genuine upper bound on the explicit path.

**Narrowed claim survives.** Between `:780` and `:797` the only statement touching `entity_relevance` is the
*floor* at `:783-784` (substitute `bipartite_ppr` when the sum is zero) — there is no division by term count,
no `min(...)` ceiling, and no per-link dampener. `$ grep -n entity_relevance $T/agentmem/memory_retrieval.py`
(rc=0) returns only `:735` (docstring), `:778`, `:780`, `:783`, `:784`, `:797`, `:801` (stored), `:1131`,
`:1166` (echoed in output) — no normalisation exists. The hub divisor at `:536-542` is `entity_hub_penalty`
default `0.0` (`$ sed -n '493p' $T/memserver/config/legacy.py` → `    entity_hub_penalty: float = float(_get("ENTITY_HUB_PENALTY", "0.0"))`, rc=0), i.e. OFF by default, so "a hub entity contributes its full PPR mass with no decay" is true of the default config. Not overclaiming; if anything it underclaims (it does not say the divisor exists-but-defaults-off beyond the table row).

One MINOR numbering defect carried into the corrected sentence: the spec says "*all three* of its producers
(`_select_entities` `:399/:433`, `_union_sub_query_entities` `:1475/:1508`)" — the two cited pairs enumerate
**two** functions. A third capping producer does exist (`_select_entities_explicit`, `:592-595`,
`return dict(sorted_scores[:top_k])`), but it is not cited. So the count is supportable and the citation is
incomplete.

### 2. `bm25_confidence` warning — PRESENT AND FACTUALLY RIGHT

```
$ sed -n '216,231p' mem0_scoring.py
216:    .. warning::
217:       **The two readings are NOT interchangeable.** The band table is keyed on
218:       the number of lemmatised *query terms* (a small integer, typically 1-20).
219:       Feeding it a raw BM25 *score* selects a band by an unrelated quantity and
...
224:    * **mem0 fidelity (what the source actually does):** the band key is the
225:      number of lemmatised *query terms* -- ``get_bm25_params(query,
226:      lemmatized=...)`` at ``scoring.py:16``, table ``scoring.py:31-40``.
rc=0
$ grep -n "def get_bm25_params\|num_terms = len\|return 1.0 / (1.0\|ENTITY_BOOST_WEIGHT = " $M/mem0/utils/scoring.py
16:def get_bm25_params(query: str, *, lemmatized: Optional[str] = None) -> tuple:
29:    num_terms = len(lemmatized.split()) if lemmatized else 1
43:def normalize_bm25(raw_score: float, midpoint: float, steepness: float) -> float:
54:    return 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))
57:ENTITY_BOOST_WEIGHT = 0.5
rc=0
$ sed -n '31,40p' $M/mem0/utils/scoring.py   # bands 3/6/9/15, else 12.0/0.5 — five bands, last is `else`
rc=0
```

`scoring.py:16` def, `:29` keyed on `len(lemmatized.split())`, table `:31-40`, sigmoid `:43/:54` — every
element of the warning matches the source. Correct.

### 3. Falsy linked-memory ids — BEHAVIOUR MATCHES; THE LINE CITATION IS OFF BY ONE

```
$ sed -n '1819,1822p' "$M/mem0/memory/main.py"
1819:                        for memory_id in linked_memory_ids:
1820:                            if memory_id:
1821:                                memory_key = str(memory_id)
1822:                                memory_boosts[memory_key] = max(memory_boosts.get(memory_key, 0.0), boost)
$ sed -n '107p' "$M/mem0/utils/scoring.py"
        if mem_id is None:
rc=0
$ sed -n '396,402p' mem0_scoring.py
396:                # mem0 skips falsy ids rather than stringifying them. Without
399:                # (``main.py:1821`` ``if memory_id:`` / ``scoring.py:107``
401:                if not memory_id:
402:                    continue  # main.py:1821
rc=0
```

Behaviour: mem0's guard is `if memory_id:` — falsy → skipped. Both `None` and `""` are falsy, so mem0
silently drops both; the port's `if not memory_id: continue` drops exactly the same two, and would otherwise
have minted the keys `"None"` / `""` via the `str(...)` at `:404-405`. Match confirmed. `scoring.py:107`
(`if mem_id is None:`) is quoted accurately as to content.

Off-by-one: `if memory_id:` sits on **main.py:1820**, not `:1821` (`:1821` is `memory_key = str(memory_id)`).
The port carries `main.py:1821` as the citation at `mem0_scoring.py:399`, `:402` and `:897`.

### 4. Three new assertions — BIND, EACH WITH ITS OWN LABEL

```
$ python3 mem0_scoring.py            # rc=0
  ok  default gate is 0.1, not 0.0 -- a 0.05 semantic score is rejected
ALL 65 CHECKS PASSED
```

Count is 65, as expected. Mutants built by byte-replacement on a copy
(`.shrimp/scratch/verify-5b0292b0/mut{A,B,C}.py`), each run separately:

```
### mutA (removed `min(..., 1.0)` at the rank_candidate return, :564)
rc=1
AssertionError: FAILED: min(raw/max_possible, 1.0) clamps -- an under-stated denominator cannot inflate past 1.0
### mutB (DEFAULT_THRESHOLD 0.1 -> 0.0, :69)
rc=1
AssertionError: FAILED: default gate is 0.1, not 0.0 -- a 0.05 semantic score is rejected
### mutC (deleted `if not memory_id: continue`, :401-402)
rc=1
AssertionError: FAILED: falsy memory ids skipped, not stringified (main.py:1821 `if memory_id:`)
```

Each died in `check()` (`raise AssertionError(f"FAILED: {label}")`) with its own named label — no incidental
crash, no label cross-talk. The three assertions genuinely bind their three mutations.

### 5. RRF default-off caveat — PRESENT, AND THE CODE MATCHES

```
$ awk 'NR>=287 && NR<=294' $T/agentmem/memory_retrieval.py
287:        # --- Stage 3.5: hybrid fusion (P1.2, optional) ----------------------
289:        # rather than unioning separate arms.  Off by default, so the legacy
291:        rrf_enabled = self._setting("hybrid_rrf_enabled", False)
292:        if rrf_enabled:
293:            ranked = self._hybrid_fuse(ranked, query, len(ranked))
rc=0
$ sed -n '517p' $T/memserver/config/legacy.py
    hybrid_rrf_enabled: bool = _get("HYBRID_RRF_ENABLED", "false").lower() == "true"
rc=0
```

Default `False` in code (`:291`) *and* in config (`legacy.py:517`, `"false"`). Spec lines 93 and 110 say the
fuse "is called only when `hybrid_rrf_enabled`" with default `False`, and line 110 states the interpretation
explicitly: "'already ahead' here means 'the capability exists and is one setting away', not 'it is running
today'" — i.e. latent, not running. That is the right framing, and it is also true of the second call site
(`:1571`, same `_hybrid_fuse`, same gate). MINOR: the cited range `:290-292` stops one line short of the
actual `_hybrid_fuse` call at `:293`.

(The brief names `agentmem/config/legacy.py`; the file is `memserver/config/legacy.py`. Brief error, not a
spec error — the spec cites no config path for this default.)

## Part 2 — the three UNEVIDENCED items, now closed

### 6. `py_compile` — PASSES

```
$ python3 -m py_compile mem0_scoring.py
py_compile rc=0
```

### 7. `expand_reachable` is genuinely multi-hop — CONFIRMED FROM CODE

```
$ awk 'NR>=292 && NR<=293' $T/agentmem/graph_walk.py
292: def expand_reachable(g, base_entity_id, *, max_nodes=2000, max_depth=None,
293:                      provenance_lookup=None):
$ awk 'NR>=336 && NR<=368' $T/agentmem/graph_walk.py
338:     while frontier:
339:         if max_depth is not None and depth >= max_depth:
340:             terminated_by = "depth_cap"
342:         depth += 1
344:         for pid in frontier:
346:             for nbr, eid, src, tgt, w, rels in _incident(g, pid):
347:                 if nbr == pid or nbr in reached:
348:                     continue          # self-loop / already expanded (cycle)
350:                 cand = ReachedNode(nbr, depth, parent.path_weight + w, pid,
353:                 prev = level.get(nbr)
354:                 if prev is None or _better(cand, prev):
359:         room = max_nodes - len(reached)
360:         if len(lvl) > room:
362:             terminated_by = "budget"
366:         if terminated_by == "budget":
367:             break
368:     nodes = tuple(sorted(reached.values(), key=lambda n: n.order_key))
rc=0
```

From the code, not the docstring: a `frontier` list expanded level by level (`:338`, `:344`, `:365`), an
explicit `depth` counter compared against `max_depth` (`:339-342`), accumulated `path_weight` as a **sum**
(`:350`, `parent.path_weight + w`), a node budget `max_nodes=2000` enforced via `room = max_nodes -
len(reached)` (`:359-362`), and a `visited` set (`nbr in reached`, `:347`) so it terminates. Bounded BFS with
depth limit, summed path weight and a node budget: CONFIRMED.

mem0 has no traversal:

```
$ cd "$M" && for p in "def walk" "max_hops" "reachab" "neighbors"; do printf "%s: " "$p"; grep -rn --include=*.py -iE "$p" mem0/ | wc -l; done
def walk: 0
max_hops: 0
reachab: 0
neighbors: 13
$ grep -rn --include=*.py -iE "neighbors" mem0/ | grep -vE "vector_stores/|configs/vector_stores" | wc -l
0
rc=0
```

All 13 `neighbors` hits are vector-store ANN options (`k_nearest_neighbors`, `find_neighbors`,
`nearest_neighbors`) — none is a graph walk. `def walk`, `max_hops`, `reachab`: zero hits. CONFIRMED.

**But the spec's wiring claim for this seam is WRONG** (see finding 9).

### 8. `_reconcile` exists and is reachable — CONFIRMED

```
$ sed -n '1187p' $T/agentmem/consolidation.py
    def _reconcile(self, prop: Proposition) -> bool:
$ sed -n '647p' $T/agentmem/consolidation.py
                keep = self._reconcile(prop)  # (6) bitemporal + dedup
$ grep -n "_reconcile" $T/agentmem/consolidation.py
647:                keep = self._reconcile(prop)  # (6) bitemporal + dedup
1187:    def _reconcile(self, prop: Proposition) -> bool:
rc=0
```

Definition and call site both real; `:647` sits inside `consolidate()` (`enclosing: 482:  def
consolidate(self, session_id: str, ...)`, rc=0), so it is on the write path. mem0's reconciler is
definition-only:

```
$ cd "$M" && grep -rn "get_update_memory_messages" --include=*.py .
./tests/configs/test_prompts.py:4:def test_get_update_memory_messages():
... (8 hits, all in tests/configs/test_prompts.py)
./mem0/configs/prompts.py:406:def get_update_memory_messages(retrieved_old_memory_dict, response_content, custom_update_memory_prompt=None):
rc=0
```

No caller in the package — only tests. ADD-only: CONFIRMED.

## Additional findings (found while opening the above)

9. **WRONG — the spec wires `expand_reachable` into the retrieval path; it is not.** Spec line 107 says
   `expand_reachable` ("graph_walk.py:292") is "Driven from `_select_entities` via `entity_expansion_enabled`
   (`memory_retrieval.py:414`)"; line 175 repeats the pairing; lines 115-116 present it as the target's
   multi-hop ranking mechanism.
   ```
   $ grep -cn "expand_reachable" $T/agentmem/memory_retrieval.py   -> 0
   $ grep -cn "graph_walk"        $T/agentmem/memory_retrieval.py   -> 0   (rc=0 for both)
   $ awk 'NR>=413 && NR<=417' $T/agentmem/memory_retrieval.py
   413:         if entity_ids:
   414:             expansion_enabled = self._setting("entity_expansion_enabled", True)
   415:             explicit_scores = self._select_entities_explicit(
   $ sed -n '571p' $T/agentmem/memory_retrieval.py
               ppr = self.sponge.personalized_pagerank(
   ```
   `expansion_enabled` at `:414` gates **PPR** expansion (`self.sponge.personalized_pagerank`, `:571`), not
   `expand_reachable`. `expand_reachable` is called only from `memserver/service.py:5637`,
   `memserver/pipeline/graph_qa.py:534` and `shrimp_on_memory_service/scripts/multihop_s38_graph_walk.py:202`
   (`$ grep -rn "expand_reachable(" $T --include=*.py | grep -vE "/tests/|/.shrimp/"`, rc=0) — a service
   method and a separate QA pipeline, not `_select_entities`. The verdict (DO-NOT-PORT, target ahead) still
   stands; the *mechanism* attributed to the read path is wrong. The nearest true statement is that the
   retrieval path reaches depth>1 via unbounded PPR plus `Sponge.typed_walk` (`sponge.py:226`), and the
   latter is flag-gated OFF (`legacy.py:623`: `sponge_typed_walk_expand: bool = _get("SPONGE_TYPED_WALK_EXPAND", "0") == "1"`, rc=0).

10. **WRONG (stale evidence) — the spec's own transcript says 62 checks; the module prints 65.** Spec lines 11
    and 262 read `ALL 62 CHECKS PASSED`; `python3 mem0_scoring.py` now prints `ALL 65 CHECKS PASSED` (rc=0,
    above). The three new assertions raised the count and the spec's evidence block was not updated with them.

11. **WRONG — the appendix's completeness claim is false.** Spec line 202 says "36/36 cited target file:line
    refs open with sed -n" and line 205 "every `file:line` this document cites".
    ```
    $ for pat in ":433" ":1475" ":1508" ":290" ":291"; do printf "%s: " "$pat"; awk 'NR>=204 && NR<=248' deliverables/mem0-port-spec.md | grep -c -- "$pat"; done
    :433: 0
    :1475: 0
    :1508: 0
    :290: 0
    :291: 0
    rc=1   (grep -c's rc for the last zero-count)
    $ awk 'NR>=204 && NR<=248 && /^OK /{c++} END{print c}' deliverables/mem0-port-spec.md
    36
    ```
    The block does hold 36 `OK` lines, but the corrected headline's own new citations (`:433`, `:1475`,
    `:1508`) and the RRF gate range (`:290-292`) are absent from it. I opened all of those lines in this pass
    and they are correct — so the defect is the "every ... this document cites" claim, not the line numbers.

12. **MINOR — mem0 path shorthand.** Spec lines 99/108 cite `prompts.py:406` and `prompts.py:176`; the file is
    `mem0/configs/prompts.py` (there is no `mem0/prompts.py` —
    `$ find . -name prompts.py` → `./mem0/configs/prompts.py`, rc=0). `:176` is
    `DEFAULT_UPDATE_MEMORY_PROMPT = """You are a smart memory manager ...` and `:406` the reconciler def, both
    correct as line numbers.

13. **MINOR — brief rule misdirected.** The brief asks about `_normalize` "at `:41` in the spec's headline
    paragraph". The spec contains no `_normalize` at all
    (`$ grep -n "_normalize" deliverables/mem0-port-spec.md` → no hits, rc=1); `:41` is the
    `20 × 1.2 = 24.0` bound, which survives (finding 1). The only `_normalize` in the pair of artefacts is
    `mem0_scoring.py:418` (`_normalize_entity_text`), used in §4's docstring.

14. **UNEVIDENCED — mem0 lines behind `mem0_scoring.py:416-419`.** That docstring cites
    `_existing_entities_by_text` def `main.py:586`, dict built around `main.py:610`, and
    `_normalize_entity_text` reuse at `main.py:1762`. I did not open any of those three lines in this pass, so
    I do not report on them either way.

Also UNEVIDENCED by design: everything in the appendix I was not asked to re-open (e.g. `v3/graph.py:133`) —
prior audit `d1419830` is trusted for those and was not repeated.

## Verdict on the pass

All five corrections are present and, on their substance, correct — the narrowed `entity_relevance` claim
survives (no normalisation, no ceiling, no dampener, count of *selected* only, bound ~24.0 explicit / ~2.x
PPR). Two of the five carry a wrong line number next to a right claim (`main.py:1821` vs `:1820`;
`:290-292` vs `:293`). The RRF default-off framing is exactly right. All three UNEVIDENCED items close
positively. Nothing in the doc-sweep layer is stale except its own evidence block (62 → 65) and the
completeness claim in the appendix; the one substantive defect is finding 9, the wiring of `expand_reachable`
into `_select_entities`, which the code does not do.
